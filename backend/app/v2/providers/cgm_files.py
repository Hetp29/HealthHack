"""Parsers for CGM exports a user downloads themselves and uploads to GLIMPSE.

This is the import path that needs no provider approval: the user is authorized to
export their own data and chooses to give it to us.

STATUS: written against the published column layouts of the two exports and tested
with fixture files built to those layouts. **Not yet verified against a genuine
export from a real account** -- do that before describing either as supported.

Both exports write local wall-clock time with no UTC offset. The timestamps are
localized with the account's IANA timezone. A wall-clock time that occurs twice (DST
fall-back) or never (spring-forward) cannot be resolved and is rejected, not guessed.
"""

from __future__ import annotations

import csv
import datetime as dt
import io

from data.timeutil import TimestampError, parse_timestamp

from backend.app.v2 import core
from backend.app.v2.services.glucose import IncomingReading

MAX_ROWS = 200_000


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise core.invalid("The file is not UTF-8 or UTF-16 text.", {"field": "body"})


def _localize(text: str, fmt: str | None, tz: str) -> tuple[dt.datetime | None, str | None]:
    try:
        return parse_timestamp(text, fmt=fmt, assume_tz=tz), None
    except TimestampError as error:
        return None, error.code


def _number(text: str) -> float | None:
    try:
        return float(text.strip().replace(",", "."))
    except (ValueError, AttributeError):
        return None


def parse_dexcom_clarity(data: bytes, tz: str) -> list[IncomingReading]:
    """Dexcom Clarity "Export" CSV.

    Uses rows whose Event Type is `EGV`. The glucose column header carries the unit.
    Out-of-range sensor markers ("Low"/"High") are rejected: the true value is unknown
    and substituting the sensor limit would manufacture a reading.
    """
    reader = csv.reader(io.StringIO(_decode(data)))
    header = next(reader, None)
    if not header:
        raise core.invalid("The file is empty.", {"format": "dexcom_clarity_csv"})
    columns = {name.strip().lower(): i for i, name in enumerate(header)}

    def find(prefix: str) -> int | None:
        return next((i for name, i in columns.items() if name.startswith(prefix)), None)

    ts_col, type_col, glucose_col = find("timestamp"), find("event type"), find("glucose value")
    device_col, tx_col = find("source device id"), find("transmitter id")
    if ts_col is None or type_col is None or glucose_col is None:
        raise core.invalid(
            "This does not look like a Dexcom Clarity export.",
            {"format": "dexcom_clarity_csv", "required_columns": ["Timestamp", "Event Type", "Glucose Value"]},
        )
    unit_header = header[glucose_col].lower()
    unit = "mmol/L" if "mmol" in unit_header else "mg/dL" if "mg/dl" in unit_header else None
    if unit is None:
        raise core.invalid("The glucose column does not state its unit.", {"format": "dexcom_clarity_csv"})

    readings: list[IncomingReading] = []
    for row in reader:
        if len(readings) >= MAX_ROWS:
            raise core.V2Error(413, "file_too_large", "The export has too many rows.", {"max_rows": MAX_ROWS})
        if len(row) <= max(ts_col, type_col, glucose_col) or row[type_col].strip().upper() != "EGV":
            continue
        raw_ts, raw_value = row[ts_col].strip(), row[glucose_col].strip()
        device = (row[device_col].strip() if device_col is not None and len(row) > device_col else "") or \
                 (row[tx_col].strip() if tx_col is not None and len(row) > tx_col else "") or "dexcom"
        reading = IncomingReading(value=None, unit=unit, observed_at=None, device_id=device,
                                  source_timestamp=raw_ts, source_timezone=tz)
        if raw_value.lower() in ("low", "high"):
            reading.reject_reason = "sensor_range_marker"
        else:
            reading.value = _number(raw_value)
            if reading.value is None:
                reading.reject_reason = "unparseable_value"
        if reading.reject_reason is None:
            reading.observed_at, reason = _localize(raw_ts, None, tz)
            reading.reject_reason = reason
        readings.append(reading)
    return readings


_LIBRE_FORMATS = {
    "MDY": ("%m-%d-%Y %I:%M %p", "%m-%d-%Y %H:%M"),
    "DMY": ("%d-%m-%Y %H:%M", "%d-%m-%Y %I:%M %p"),
}


def _infer_libre_date_order(samples: list[str]) -> str | None:
    """`MDY` or `DMY` when the file itself settles it, else `None`."""
    first_over_12 = second_over_12 = False
    for sample in samples:
        parts = sample.split(" ")[0].split("-")
        if len(parts) != 3:
            continue
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        first_over_12 |= a > 12
        second_over_12 |= b > 12
    if first_over_12 and not second_over_12:
        return "DMY"
    if second_over_12 and not first_over_12:
        return "MDY"
    return None


def parse_libreview(data: bytes, tz: str, date_order: str | None = None) -> list[IncomingReading]:
    """LibreView "Download glucose data" CSV.

    Line 1 is export metadata; line 2 is the header. Record Type 0 is the automatic
    historic series (stored as `cgm`); Record Type 1 is a manual scan (stored as
    `scan`, kept out of the model's series).

    The date is written day-first or month-first depending on the account's region,
    and nothing in the file says which. It is inferred only when some date in the
    file is unambiguous; otherwise the caller must state `date_order`.
    """
    lines = _decode(data).splitlines()
    header_index = next(
        (i for i, line in enumerate(lines[:5]) if "device timestamp" in line.lower()), None
    )
    if header_index is None:
        raise core.invalid("This does not look like a LibreView export.",
                           {"format": "libreview_csv", "required_columns": ["Device Timestamp", "Record Type"]})
    reader = csv.reader(io.StringIO("\n".join(lines[header_index:])))
    header = next(reader)
    columns = {name.strip().lower(): i for i, name in enumerate(header)}

    def find(prefix: str) -> int | None:
        return next((i for name, i in columns.items() if name.startswith(prefix)), None)

    ts_col, type_col = find("device timestamp"), find("record type")
    historic_col, scan_col = find("historic glucose"), find("scan glucose")
    device_col, serial_col = find("device"), find("serial number")
    if ts_col is None or type_col is None or historic_col is None:
        raise core.invalid("This does not look like a LibreView export.", {"format": "libreview_csv"})
    unit_header = header[historic_col].lower()
    unit = "mmol/L" if "mmol" in unit_header else "mg/dL" if "mg/dl" in unit_header else None
    if unit is None:
        raise core.invalid("The glucose column does not state its unit.", {"format": "libreview_csv"})

    rows = [row for row in reader if len(row) > max(ts_col, type_col)]
    if len(rows) > MAX_ROWS:
        raise core.V2Error(413, "file_too_large", "The export has too many rows.", {"max_rows": MAX_ROWS})
    if date_order is None:
        date_order = _infer_libre_date_order([row[ts_col].strip() for row in rows])
    if date_order not in _LIBRE_FORMATS:
        raise core.V2Error(
            422, "ambiguous_date_format",
            "The export's dates could be day-first or month-first. State date_order.",
            {"allowed": ["MDY", "DMY"]},
        )

    readings: list[IncomingReading] = []
    for row in rows:
        record_type = row[type_col].strip()
        if record_type not in ("0", "1"):
            continue
        column = historic_col if record_type == "0" else scan_col
        if column is None or len(row) <= column or not row[column].strip():
            continue
        serial = row[serial_col].strip() if serial_col is not None and len(row) > serial_col else ""
        device = serial or (row[device_col].strip() if device_col is not None else "") or "libre"
        raw_ts = row[ts_col].strip()
        reading = IncomingReading(
            value=_number(row[column]), unit=unit, observed_at=None, device_id=device,
            record_kind="cgm" if record_type == "0" else "scan",
            source_timestamp=raw_ts, source_timezone=tz,
        )
        if reading.value is None:
            reading.reject_reason = "unparseable_value"
        else:
            reason = "unparseable_timestamp"
            for fmt in _LIBRE_FORMATS[date_order]:
                reading.observed_at, reason = _localize(raw_ts, fmt, tz)
                if reason != "unparseable_timestamp":
                    break
            reading.reject_reason = reason
        readings.append(reading)
    return readings


PARSERS = {"dexcom_clarity_csv": parse_dexcom_clarity, "libreview_csv": parse_libreview}
