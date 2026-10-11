"""Provider-independent glucose observation store.

Every source -- a file export, a native bridge, a provider API -- is reduced to a list
of `IncomingReading` and passed through `ingest_readings`. That one function owns unit
conversion, validation, de-duplication and correction handling, so no adapter can
store a reading that skipped them.

What this module will not do:
  * guess a unit, or a UTC offset;
  * store a non-finite, future-dated or physiologically implausible value;
  * overwrite a value without recording what it replaced;
  * fill a gap. A missing interval is reported as missing.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from sqlalchemy import and_, select, update
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import V2Context

#: A reading stamped later than "now" by more than this is rejected as a clock error.
FUTURE_SKEW = dt.timedelta(minutes=5)
#: Two values for the same instant closer than this are the same reading.
SAME_VALUE_TOLERANCE_MG_DL = 0.05
#: A hole between consecutive readings longer than this is reported as a gap.
GAP_MINUTES = 15.0
FRESH_MINUTES = 15.0
DELAYED_MINUTES = 60.0
MAX_REPORTED_REJECTIONS = 50

RECORD_KINDS = ("cgm", "scan", "fingerstick")

#: provider -> (label, kind of connection it implies)
SOURCE_PROVIDERS = {
    "manual_entry": "Values typed in by the user",
    "dexcom_clarity_csv": "Dexcom Clarity CSV export",
    "libreview_csv": "LibreView CSV export",
    "healthkit_bridge": "Apple Health via a native iOS bridge",
    "dexcom_api": "Dexcom API",
    "demo": "Labelled demonstration data",
    "research_cgmacros": "CGMacros research dataset",
}


@dataclass
class IncomingReading:
    value: float | None
    unit: str | None
    observed_at: dt.datetime | None
    device_id: str = "unknown"
    record_kind: str = "cgm"
    source_record_id: str | None = None
    source_timestamp: str | None = None
    source_timezone: str | None = None
    quality_flags: list[str] = field(default_factory=list)
    #: Set by a parser that already knows the row is unusable (e.g. a "High" marker).
    reject_reason: str | None = None


def _normalize(reading: IncomingReading, now: dt.datetime) -> tuple[dict | None, str | None]:
    if reading.reject_reason:
        return None, reading.reject_reason
    if reading.record_kind not in RECORD_KINDS:
        return None, "unknown_record_kind"
    observed = reading.observed_at
    if observed is None:
        return None, "missing_timestamp"
    if observed.tzinfo is None or observed.utcoffset() is None:
        return None, "naive_timestamp"
    observed = observed.astimezone(core.UTC).replace(microsecond=0)
    if observed > now + FUTURE_SKEW:
        return None, "future_timestamp"
    value = reading.value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None, "nonfinite_value"
    if reading.unit not in ("mg/dL", "mmol/L"):
        return None, "unknown_unit"
    mg_dl, conversion = core.glucose_to_mg_dl(float(value), reading.unit)
    if not (core.GLUCOSE_MIN_MG_DL <= mg_dl <= core.GLUCOSE_MAX_MG_DL):
        return None, "out_of_range"
    device = (reading.device_id or "unknown").strip()[:128] or "unknown"
    return {
        "device_id": device,
        "record_kind": reading.record_kind,
        "source_record_id": (reading.source_record_id or None),
        "observed_at": observed,
        "glucose_mg_dl": round(mg_dl, 3),
        "source_value": float(value),
        "source_unit": reading.unit,
        "conversion": conversion,
        "source_timestamp": reading.source_timestamp,
        "source_timezone": reading.source_timezone,
        "quality_flags": sorted(set(reading.quality_flags)),
    }, None


def ensure_connection(
    conn: Connection, ctx: V2Context, user_id: str, provider: str, kind: str = "cgm"
) -> str:
    """The connection row for a non-OAuth source, created on first use."""
    existing = conn.execute(
        select(t.device_connections.c.connection_id).where(
            and_(t.device_connections.c.user_id == user_id,
                 t.device_connections.c.provider == provider)
        )
    ).scalar()
    if existing:
        return existing
    connection_id = core.new_id("dev")
    now = ctx.now()
    conn.execute(
        t.device_connections.insert().values(
            connection_id=connection_id, user_id=user_id, provider=provider, kind=kind,
            status="pending", scopes=[], created_at=now, updated_at=now,
        )
    )
    return connection_id


def ingest_readings(
    conn: Connection,
    ctx: V2Context,
    user_id: str,
    provider: str,
    readings: list[IncomingReading],
    *,
    source_format: str,
    data_class: str = "user_device",
    connection_id: str | None = None,
    source_sha256: str | None = None,
) -> dict:
    """Validate and store a batch. Idempotent: replaying a batch stores nothing new."""
    now = ctx.now()
    batch_id = core.new_id("imp")
    if connection_id is None:
        connection_id = ensure_connection(conn, ctx, user_id, provider)

    # The batch row exists before any observation points at it; its counts are
    # filled in once the batch has been processed.
    conn.execute(
        t.import_batches.insert().values(
            batch_id=batch_id, user_id=user_id, connection_id=connection_id,
            provider=provider, kind="cgm", source_format=source_format,
            data_class=data_class, source_sha256=source_sha256, received_at=now,
            counts={}, rejections=[],
        )
    )

    rejections: list[dict] = []
    rejected_by_reason: dict[str, int] = {}

    def reject(index: int, reason: str) -> None:
        rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1
        if len(rejections) < MAX_REPORTED_REJECTIONS:
            rejections.append({"index": index, "reason": reason})

    # Normalize, then collapse repeats inside the batch itself.
    accepted: dict[tuple, dict] = {}
    duplicates = 0
    for index, reading in enumerate(readings):
        row, reason = _normalize(reading, now)
        if row is None:
            reject(index, reason or "invalid")
            continue
        key = (row["device_id"], row["record_kind"], row["observed_at"])
        earlier = accepted.get(key)
        if earlier is None:
            accepted[key] = row
        elif abs(earlier["glucose_mg_dl"] - row["glucose_mg_dl"]) <= SAME_VALUE_TOLERANCE_MG_DL:
            duplicates += 1
        else:
            # Two different values for one instant in one upload: there is no basis
            # for choosing between them, so the later one is refused, not averaged.
            reject(index, "conflicting_duplicate_in_batch")

    inserted = corrected = 0
    touched: list[dt.datetime] = []
    if accepted:
        times = [row["observed_at"] for row in accepted.values()]
        existing_rows = conn.execute(
            select(t.glucose_observations).where(
                and_(
                    t.glucose_observations.c.user_id == user_id,
                    t.glucose_observations.c.provider == provider,
                    t.glucose_observations.c.observed_at >= min(times),
                    t.glucose_observations.c.observed_at <= max(times),
                )
            )
        ).mappings().all()
        existing = {
            (r["device_id"], r["record_kind"], r["observed_at"]): r for r in existing_rows
        }

        to_insert: list[dict] = []
        for key, row in sorted(accepted.items(), key=lambda kv: kv[0][2]):
            current = existing.get(key)
            if current is None:
                to_insert.append({
                    **row, "observation_id": core.new_id("glu"), "user_id": user_id,
                    "provider": provider, "received_at": now, "data_class": data_class,
                    "batch_id": batch_id, "correction_count": 0,
                })
                touched.append(row["observed_at"])
                continue
            if abs(current["glucose_mg_dl"] - row["glucose_mg_dl"]) <= SAME_VALUE_TOLERANCE_MG_DL:
                duplicates += 1
                continue
            # The source now reports a different value for an instant already stored.
            conn.execute(
                t.glucose_corrections.insert().values(
                    correction_id=core.new_id("cor"),
                    observation_id=current["observation_id"], user_id=user_id,
                    previous_glucose_mg_dl=current["glucose_mg_dl"],
                    previous_source_value=current["source_value"],
                    previous_received_at=current["received_at"],
                    corrected_at=now, batch_id=batch_id,
                )
            )
            conn.execute(
                update(t.glucose_observations)
                .where(t.glucose_observations.c.observation_id == current["observation_id"])
                .values(
                    glucose_mg_dl=row["glucose_mg_dl"], source_value=row["source_value"],
                    source_unit=row["source_unit"], conversion=row["conversion"],
                    received_at=now, batch_id=batch_id,
                    correction_count=current["correction_count"] + 1,
                    quality_flags=sorted(set(current["quality_flags"]) | set(row["quality_flags"]) | {"corrected"}),
                )
            )
            corrected += 1
            touched.append(row["observed_at"])
        if to_insert:
            conn.execute(t.glucose_observations.insert(), to_insert)
            inserted = len(to_insert)

    rejected = sum(rejected_by_reason.values())
    counts = {
        "received": len(readings), "inserted": inserted, "duplicates": duplicates,
        "corrected": corrected, "rejected": rejected,
        "rejected_by_reason": rejected_by_reason,
    }
    conn.execute(
        update(t.import_batches).where(t.import_batches.c.batch_id == batch_id)
        .values(counts=counts, rejections=rejections)
    )

    connection_values: dict = {"last_sync_at": now, "updated_at": now}
    if inserted or duplicates or corrected:
        connection_values["last_success_at"] = now
        connection_values["last_error_code"] = None
        if data_class == "user_device":
            # Only now -- with real observations stored -- is the source "connected".
            connection_values["status"] = "connected"
            connection_values["authentic_data_received_at"] = now
    conn.execute(
        update(t.device_connections)
        .where(and_(t.device_connections.c.connection_id == connection_id,
                    t.device_connections.c.user_id == user_id))
        .values(**connection_values)
    )

    if touched:
        # A late or corrected reading can change an outcome that was already scored.
        from backend.app.v2.services import outcomes

        outcomes.reopen_affected_episodes(conn, user_id, min(touched), max(touched))

    return {"batch_id": batch_id, "provider": provider, "data_class": data_class,
            "received_at": core.rfc3339(now), "counts": counts, "rejections": rejections,
            "rejections_truncated": rejected > len(rejections)}


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #


def observation_payload(row: dict, unit: str = "mg/dL") -> dict:
    return {
        "observation_id": row["observation_id"],
        "observed_at": core.rfc3339(row["observed_at"]),
        "received_at": core.rfc3339(row["received_at"]),
        "glucose_mg_dl": row["glucose_mg_dl"],
        "glucose": round(core.mg_dl_to_unit(row["glucose_mg_dl"], unit), 2),
        "unit": unit,
        "source": {"value": row["source_value"], "unit": row["source_unit"],
                   "conversion": row["conversion"], "timestamp": row["source_timestamp"],
                   "timezone": row["source_timezone"]},
        "provider": row["provider"],
        "device_id": row["device_id"],
        "record_kind": row["record_kind"],
        "quality_flags": row["quality_flags"],
        "data_class": row["data_class"],
        "corrected": row["correction_count"] > 0,
    }


def fetch(
    conn: Connection, user_id: str, start: dt.datetime | None, end: dt.datetime | None,
    *, record_kinds: tuple[str, ...] = RECORD_KINDS, limit: int | None = None,
    newest_first: bool = False,
) -> list[dict]:
    g = t.glucose_observations
    query = select(g).where(and_(g.c.user_id == user_id, g.c.record_kind.in_(record_kinds)))
    if start is not None:
        query = query.where(g.c.observed_at >= start)
    if end is not None:
        query = query.where(g.c.observed_at <= end)
    order = (g.c.observed_at.desc(), g.c.observation_id) if newest_first else (g.c.observed_at, g.c.observation_id)
    query = query.order_by(*order)
    if limit is not None:
        query = query.limit(limit)
    return [dict(r) for r in conn.execute(query).mappings()]


def select_model_series(
    conn: Connection, user_id: str, as_of: dt.datetime, lookback: dt.timedelta,
    baseline_tolerance_minutes: float,
) -> tuple[list[dict], dict]:
    """The single CGM series a forecast at `as_of` may use, and why it was chosen.

    Only continuous (`cgm`) records at or before `as_of` are read: scans and
    fingersticks are stored but are not the 5-minute series the model was trained on.

    When more than one device reported in the window, exactly one is used -- mixing
    two sensors would interleave their independent calibration errors into every
    slope and variability feature. The device with the most recent reading inside the
    baseline tolerance wins; the choice and the existence of the others are recorded.
    """
    rows = fetch(conn, user_id, as_of - lookback, as_of, record_kinds=("cgm",))
    by_device: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        by_device.setdefault((row["provider"], row["device_id"]), []).append(row)
    if not by_device:
        return [], {"device": None, "other_devices": [], "multi_device_overlap": False}

    tolerance = dt.timedelta(minutes=baseline_tolerance_minutes)

    def rank(item):
        key, series = item
        latest = series[-1]["observed_at"]
        return (as_of - latest <= tolerance, latest, len(series), key)

    (provider, device_id), series = max(by_device.items(), key=rank)
    others = [
        {"provider": p, "device_id": d, "readings": len(s)}
        for (p, d), s in sorted(by_device.items()) if (p, d) != (provider, device_id)
    ]
    return series, {
        "device": {"provider": provider, "device_id": device_id,
                   "data_class": series[-1]["data_class"]},
        "other_devices": others,
        "multi_device_overlap": bool(others),
    }


def freshness(age_minutes: float | None) -> str:
    if age_minutes is None:
        return "no_data"
    if age_minutes <= FRESH_MINUTES:
        return "fresh"
    if age_minutes <= DELAYED_MINUTES:
        return "delayed"
    return "stale"


def find_gaps(rows: list[dict], start: dt.datetime, end: dt.datetime) -> list[dict]:
    """Intervals longer than `GAP_MINUTES` with no reading, including both edges."""
    gaps: list[dict] = []
    previous = start
    for moment in [r["observed_at"] for r in rows] + [end]:
        minutes = (moment - previous).total_seconds() / 60.0
        if minutes > GAP_MINUTES:
            gaps.append({"start": core.rfc3339(previous), "end": core.rfc3339(moment),
                         "minutes": round(minutes, 1)})
        previous = moment
    return gaps


def status(conn: Connection, ctx: V2Context, user_id: str) -> dict:
    now = ctx.now()
    day = dt.timedelta(hours=24)
    series, choice = select_model_series(conn, user_id, now, day, FRESH_MINUTES)
    latest = series[-1] if series else None
    if latest is None:
        # Nothing in the last day; report the most recent reading there is, if any.
        older = fetch(conn, user_id, None, now, record_kinds=("cgm",), limit=1, newest_first=True)
        latest = older[0] if older else None
    age = None if latest is None else round((now - latest["observed_at"]).total_seconds() / 60.0, 1)
    state = freshness(age)

    intervals = []
    if len(series) >= 3:
        stamps = [r["observed_at"] for r in series]
        intervals = sorted((b - a).total_seconds() / 60.0 for a, b in zip(stamps, stamps[1:]))
    cadence = intervals[len(intervals) // 2] if intervals else None
    expected = (24 * 60 / cadence) if cadence else None
    return {
        "as_of": core.rfc3339(now),
        "freshness": state,
        "latest": None if latest is None else observation_payload(latest),
        "latest_age_minutes": age,
        "forecast_ready": state == "fresh",
        "forecast_requirement": (
            "A forecast needs a CGM reading no more than 15 minutes before the meal time."
        ),
        "model_series": choice,
        "last_24h": {
            "readings": len(series),
            "median_interval_minutes": None if cadence is None else round(cadence, 1),
            "coverage": None if not expected else round(min(1.0, len(series) / expected), 3),
            "gaps": find_gaps(series, now - day, now) if series else [],
        },
        "data_class": None if latest is None else latest["data_class"],
    }
