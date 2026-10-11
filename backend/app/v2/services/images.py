"""Meal photo validation, sanitization and storage.

An upload is untrusted bytes. Before anything is stored or sent to a third party it is

  1. size-checked,
  2. identified by its leading bytes (the declared Content-Type is not believed),
  3. fully decoded, so a truncated or hostile file fails here and not downstream,
  4. re-encoded to a fresh JPEG.

Step 4 is the privacy step as much as the safety one: re-encoding drops EXIF, which on
a phone photo includes GPS coordinates and device identifiers. Only the sanitized
image is kept and only the sanitized image is ever sent to the vision model.
"""

from __future__ import annotations

import hashlib
import io
import os
import pathlib
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from backend.app.v2 import core
from backend.app.v2.settings import V2Settings

#: Longest side after sanitization. Ample for food recognition and bounds provider cost.
MAX_SIDE_PIXELS = 2048
JPEG_QUALITY = 88

_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


@dataclass(frozen=True)
class SanitizedImage:
    data: bytes
    content_type: str
    width: int
    height: int
    sha256: str
    source_format: str


def sniff_content_type(data: bytes) -> str | None:
    for signature, content_type in _SIGNATURES:
        if data.startswith(signature):
            return content_type
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"heim", b"heis"):
            return "image/heic"
        if brand in (b"avif", b"avis"):
            return "image/avif"
    return None


def sanitize_image(data: bytes, declared_content_type: str | None, settings: V2Settings) -> SanitizedImage:
    if not data:
        raise core.invalid("The upload was empty.", {"field": "body"})
    if len(data) > settings.max_image_bytes:
        raise core.V2Error(
            413, "image_too_large", "The image exceeds the upload size limit.",
            {"max_bytes": settings.max_image_bytes},
        )

    sniffed = sniff_content_type(data)
    if sniffed in ("image/heic", "image/avif"):
        raise core.V2Error(
            415, "unsupported_image_type",
            "HEIC/AVIF images are not supported. Upload a JPEG, PNG or WebP.",
            {"detected": sniffed, "supported": ["image/jpeg", "image/png", "image/webp"]},
        )
    if sniffed is None:
        raise core.V2Error(
            415, "unsupported_image_type", "The upload is not a JPEG, PNG or WebP image.",
            {"supported": ["image/jpeg", "image/png", "image/webp"]},
        )
    declared = (declared_content_type or "").split(";")[0].strip().lower()
    if declared and declared not in ("application/octet-stream", sniffed, "image/jpg"):
        raise core.V2Error(
            415, "content_type_mismatch",
            "The declared Content-Type does not match the image content.",
            {"declared": declared, "detected": sniffed},
        )

    try:
        # `verify` walks the file structure without decoding pixels; it leaves the
        # image object unusable, hence the second open.
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        if width * height > settings.max_image_pixels:
            raise core.V2Error(
                413, "image_too_large", "The image has too many pixels.",
                {"max_pixels": settings.max_image_pixels},
            )
        if width < 64 or height < 64:
            raise core.invalid("The image is too small to analyze.", {"min_side_pixels": 64})
        image.load()
    except core.V2Error:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError) as error:
        raise core.V2Error(
            422, "invalid_image", "The image could not be decoded.",
            {"reason": type(error).__name__},
        ) from error

    source_format = (image.format or "").lower()
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        # Flatten transparency onto white rather than letting it turn black.
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        else:
            image = image.convert("RGB")
    image.thumbnail((MAX_SIDE_PIXELS, MAX_SIDE_PIXELS))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    clean = buffer.getvalue()
    return SanitizedImage(
        data=clean, content_type="image/jpeg", width=image.width, height=image.height,
        sha256=hashlib.sha256(clean).hexdigest(), source_format=source_format,
    )


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #


class ImageStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> bool: ...


def _safe_key(key: str) -> str:
    parts = key.split("/")
    if not parts or any(p in ("", ".", "..") or not p.replace("_", "").replace(".", "").isalnum() for p in parts):
        raise ValueError(f"unsafe object key {key!r}")
    return key


class LocalImageStore:
    """Private files on local disk. For development and single-node use."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = pathlib.Path(root)

    def _path(self, key: str) -> pathlib.Path:
        return self.root / _safe_key(key)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Created owner-only from the start, not chmod-ed after the bytes are on disk.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.exists():
            return False
        path.unlink()
        return True


class S3ImageStore:
    """Private S3 objects with server-side encryption.

    NOT EXERCISED: there is no AWS account or bucket yet, and `boto3` is not a pinned
    dependency. Written to the interface so Het's bucket can be bound by setting
    `GLIMPSE_IMAGE_S3_BUCKET`; it must be verified against the real bucket before any
    deployment relies on it.
    """

    def __init__(self, bucket: str, prefix: str) -> None:
        import boto3  # noqa: PLC0415 -- optional dependency, imported only when used

        self._client = boto3.client("s3")
        self.bucket = bucket
        self.prefix = prefix

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self.bucket, Key=self.prefix + _safe_key(key), Body=data,
            ContentType=content_type, ServerSideEncryption="AES256",
        )

    def get(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self.prefix + _safe_key(key))
        except self._client.exceptions.NoSuchKey:
            return None
        return response["Body"].read()

    def delete(self, key: str) -> bool:
        self._client.delete_object(Bucket=self.bucket, Key=self.prefix + _safe_key(key))
        return True


def build_image_store(settings: V2Settings) -> ImageStore:
    if settings.image_s3_bucket:
        return S3ImageStore(settings.image_s3_bucket, settings.image_s3_prefix)
    return LocalImageStore(settings.image_dir)
