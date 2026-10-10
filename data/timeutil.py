"""Timestamp parsing and UTC normalization.

Policy (contract v1.0): every stored timestamp is offset-aware UTC. A timestamp that
arrives without an offset is *rejected*, unless a mapping config explicitly names the
timezone the source was recorded in. Even then, local wall times that are ambiguous
(DST fall-back) or nonexistent (DST spring-forward) are rejected, not guessed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

# Fixed-width storage format: lexicographic order == chronological order in SQLite.
STORAGE_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class TimestampError(ValueError):
    """Raised with a machine-readable ``code`` when a timestamp cannot be trusted."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def is_valid_timezone(name: str | None) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _localize(naive: datetime, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    early = naive.replace(tzinfo=tz, fold=0)
    late = naive.replace(tzinfo=tz, fold=1)
    if early.utcoffset() != late.utcoffset():
        # Either the wall time occurs twice (fall-back) or never (spring-forward).
        roundtrip = early.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
        if roundtrip != naive:
            raise TimestampError("nonexistent_local_time", f"{naive} does not exist in {tz_name}")
        raise TimestampError("ambiguous_local_time", f"{naive} occurs twice in {tz_name}")
    return early


def parse_timestamp(value, *, fmt: str | None = None, assume_tz: str | None = None) -> datetime:
    """Parse ``value`` into an aware UTC datetime.

    ``fmt``: ``None``/``""`` for ISO 8601 / RFC3339, ``"unix_s"`` or ``"unix_ms"`` for
    epoch numbers (inherently UTC), or a ``strptime`` pattern.
    ``assume_tz``: IANA zone used *only* for offset-naive values. ``None`` rejects them.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise TimestampError("missing_timestamp", "timestamp is missing")

    if isinstance(value, datetime):
        dt = value
    elif fmt in ("unix_s", "unix_ms"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise TimestampError("unparseable_timestamp", f"not an epoch number: {value!r}")
        if number != number or number in (float("inf"), float("-inf")):
            raise TimestampError("unparseable_timestamp", "non-finite epoch value")
        if fmt == "unix_ms":
            number /= 1000.0
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=number)
    else:
        text = str(value).strip()
        try:
            if fmt:
                dt = datetime.strptime(text, fmt)
            else:
                dt = datetime.fromisoformat(text.replace("z", "Z"))
        except ValueError:
            raise TimestampError("unparseable_timestamp", f"cannot parse timestamp {text!r}")

    if dt.tzinfo is None or dt.utcoffset() is None:
        if not assume_tz:
            raise TimestampError("naive_timestamp", "timestamp has no UTC offset")
        if not is_valid_timezone(assume_tz):
            raise TimestampError("naive_timestamp", f"cannot localize: unknown timezone {assume_tz!r}")
        dt = _localize(dt, assume_tz)
    return dt.astimezone(UTC)


def to_storage(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("refusing to store an offset-naive datetime")
    return dt.astimezone(UTC).strftime(STORAGE_FORMAT)


def from_storage(text: str) -> datetime:
    return datetime.strptime(text, STORAGE_FORMAT).replace(tzinfo=UTC)


def to_rfc3339(dt: datetime) -> str:
    """UTC RFC3339 with ``Z``; sub-second digits only when present."""
    dt = dt.astimezone(UTC)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def require_aware(dt: datetime | None, name: str) -> datetime | None:
    """Validate a caller-supplied query bound. Naive input is a programming error."""
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        raise TypeError(f"{name} must be a datetime")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{name} must be offset-aware (got naive {dt.isoformat()})")
    return dt.astimezone(UTC)


SOURCE_LOCAL_UNKNOWN_TZ = "source_local_unknown_tz"


def local_minutes_of_day(timestamp: datetime, time_basis: str, timezone_name: str | None) -> float | None:
    """Minutes since local midnight for a canonical timestamp, or None when unknowable.

    * ``source_local_unknown_tz``: the canonical value encodes the source's local wall
      clock under a +00:00 label, so its clock reading IS the local time of day.
    * ``utc`` with a known IANA timezone: convert, then read the clock.
    * ``utc`` with no timezone: the local time of day is unknown -> None (do not guess).
    """
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be offset-aware")
    if time_basis == SOURCE_LOCAL_UNKNOWN_TZ:
        local = timestamp.astimezone(UTC)
    elif time_basis == "utc" and timezone_name:
        local = timestamp.astimezone(ZoneInfo(timezone_name))
    elif time_basis == "utc":
        return None
    else:
        raise ValueError(f"unknown time_basis {time_basis!r}")
    return local.hour * 60 + local.minute + local.second / 60
