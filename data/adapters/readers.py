"""Read raw source files into row dicts, and checksum source inputs.

Supported: .csv, .tsv, .json (array of objects, or {"records": [...]}) and .jsonl.
.xlsx/.parquet are read through pandas when its optional engines are installed.
Values are returned as found (CSV -> strings); coercion is the validator's job.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class RawRow:
    source_file: str  # path relative to the source root
    source_row: int  # 1-based data row number (header excluded)
    values: dict


class SourceReadError(RuntimeError):
    pass


def resolve_files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    """Expand glob patterns relative to ``root``; deterministic order; must stay inside root."""
    root = root.resolve()
    found: list[Path] = []
    for pattern in patterns:
        matches = sorted(p for p in root.glob(pattern) if p.is_file())
        for match in matches:
            resolved = match.resolve()
            if root not in resolved.parents and resolved != root:
                raise SourceReadError(f"{pattern!r} escapes the source directory")
            if resolved not in found:
                found.append(resolved)
    return found


def iter_rows(path: Path, root: Path) -> Iterator[RawRow]:
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle, delimiter="\t" if suffix == ".tsv" else ",")
            for i, row in enumerate(reader, start=1):
                yield RawRow(rel, i, dict(row))
    elif suffix == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            for i, line in enumerate((ln for ln in handle if ln.strip()), start=1):
                yield RawRow(rel, i, json.loads(line))
    elif suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("records"), list):
            payload = payload["records"]
        if not isinstance(payload, list):
            raise SourceReadError(f"{rel}: expected a JSON array of objects")
        for i, row in enumerate(payload, start=1):
            yield RawRow(rel, i, dict(row))
    elif suffix in (".xlsx", ".xls", ".parquet"):
        try:
            import pandas as pd

            frame = pd.read_parquet(path) if suffix == ".parquet" else pd.read_excel(path)
        except ImportError as exc:  # optional engines (openpyxl / pyarrow)
            raise SourceReadError(f"{rel}: reading {suffix} needs an optional engine: {exc}") from exc
        frame = frame.astype(object).where(frame.notna(), None)
        for i, row in enumerate(frame.to_dict(orient="records"), start=1):
            yield RawRow(rel, i, row)
    else:
        raise SourceReadError(f"{rel}: unsupported file type {suffix!r}")


def read_header(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return next(csv.reader(handle, delimiter="\t" if suffix == ".tsv" else ","), [])
    for row in iter_rows(path, path.parent):
        return list(row.values)
    return []


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_inputs(root: Path, files: list[Path]) -> str:
    """Checksum over (relative path, content) of every input file, in sorted order."""
    root = root.resolve()
    digest = hashlib.sha256()
    for path in sorted({p.resolve() for p in files}):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha256_file(path).encode())
        digest.update(b"\0")
    return digest.hexdigest()
