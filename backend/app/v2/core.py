"""Shared primitives for the v2 layer: ids, clock, errors, hashing, units.

Small on purpose. Everything here is used by more than one router.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import math
import os
import secrets
import uuid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = dt.timezone.utc

#: 1 mmol/L of glucose in mg/dL (molar mass 180.16 g/mol).
MMOL_L_TO_MG_DL = 18.016

#: The range a glucose value must fall in to be stored. Matches the bounds the frozen
#: contract already applies to `premeal_glucose_mg_dl`.
GLUCOSE_MIN_MG_DL = 20.0
GLUCOSE_MAX_MG_DL = 600.0


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def rfc3339(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def is_valid_timezone(name: str | None) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False
    return True


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #


class V2Error(Exception):
    """A deliberate client-facing failure.

    Serialized as `{code, message, request_id, details}`. `details` must never echo a
    submitted health value: it carries field names, ids, limits and state names only.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def not_found(entity: str) -> V2Error:
    # The same response whether the row is absent or belongs to someone else, so an
    # id cannot be probed for existence across accounts.
    return V2Error(404, "not_found", f"No such {entity}.", {"entity": entity})


def invalid(message: str, details: dict[str, Any] | None = None) -> V2Error:
    return V2Error(422, "invalid_request", message, details)


def conflict(code: str, message: str, details: dict[str, Any] | None = None) -> V2Error:
    return V2Error(409, code, message, details)


# --------------------------------------------------------------------------- #
# numbers and units
# --------------------------------------------------------------------------- #


def require_finite(value: Any, field: str) -> float:
    """Coerce to float, rejecting NaN, infinities, booleans and non-numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise invalid(f"{field} must be a number.", {"field": field})
    number = float(value)
    if not math.isfinite(number):
        raise invalid(f"{field} must be a finite number.", {"field": field})
    return number


def glucose_to_mg_dl(value: float, unit: str) -> tuple[float, str]:
    """Return `(mg_dl, conversion_label)`.

    The unit must be stated. A bare number is never guessed to be one or the other:
    5.5 and 99 are both plausible readings and mean very different things.
    """
    if unit == "mg/dL":
        return float(value), "none"
    if unit == "mmol/L":
        return float(value) * MMOL_L_TO_MG_DL, f"mmol/L*{MMOL_L_TO_MG_DL}"
    raise invalid(
        "Glucose unit must be 'mg/dL' or 'mmol/L'.", {"field": "unit", "allowed": ["mg/dL", "mmol/L"]}
    )


def mg_dl_to_unit(value: float, unit: str) -> float:
    return float(value) if unit == "mg/dL" else float(value) / MMOL_L_TO_MG_DL


# --------------------------------------------------------------------------- #
# passwords and tokens
# --------------------------------------------------------------------------- #

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()
