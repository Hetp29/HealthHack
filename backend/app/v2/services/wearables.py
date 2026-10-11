"""Normalized wearable observations.

Stored for display and for future research. **Nothing in this table is read by the
forecast path.** The frozen model has one wearable-shaped input (`hr_mean_30min_bpm`),
trained on CGMacros's Fitbit minute-level stream; whether any other provider's heart
rate means the same thing is unverified, so none is supplied. See DECISIONS D-008.
"""

from __future__ import annotations

import datetime as dt
import math

from sqlalchemy import and_, select, update
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import V2Context
from backend.app.v2.providers.base import WearableObservation

#: metric -> the one unit it is stored in.
METRIC_UNITS: dict[str, str] = {
    "heart_rate": "bpm",
    "resting_heart_rate": "bpm",
    "steps_daily": "count",
    "sleep_duration": "min",
    "hrv_rmssd": "ms",
    "hrv_sdnn": "ms",
    "recovery_score": "percent",
    "workout": "min",
}
FUTURE_SKEW = dt.timedelta(minutes=5)


def ingest(conn: Connection, ctx: V2Context, user_id: str, provider: str,
           observations: list[WearableObservation], *, data_class: str = "user_device") -> dict:
    now = ctx.now()
    rejected: dict[str, int] = {}
    accepted: dict[tuple[str, str], WearableObservation] = {}

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for obs in observations:
        if obs.metric not in METRIC_UNITS:
            reject("unsupported_metric")
        elif obs.unit != METRIC_UNITS[obs.metric]:
            reject("unexpected_unit")
        elif obs.observed_at is None or obs.observed_at.tzinfo is None:
            reject("missing_or_naive_timestamp")
        elif obs.observed_at > now + FUTURE_SKEW:
            reject("future_timestamp")
        elif obs.value is None or isinstance(obs.value, bool) or not math.isfinite(obs.value) or obs.value < 0:
            reject("invalid_value")
        elif not obs.source_record_id:
            reject("missing_source_record_id")
        else:
            accepted[(obs.metric, obs.source_record_id)] = obs

    w = t.wearable_observations
    inserted = updated = duplicates = 0
    for (metric, source_id), obs in accepted.items():
        existing = conn.execute(select(w.c.observation_id, w.c.value).where(and_(
            w.c.user_id == user_id, w.c.provider == provider, w.c.metric == metric,
            w.c.source_record_id == source_id))).first()
        if existing is None:
            conn.execute(w.insert().values(
                observation_id=core.new_id("wob"), user_id=user_id, provider=provider,
                source_record_id=source_id[:160], metric=metric, value=float(obs.value),
                unit=obs.unit, observed_at=obs.observed_at, observed_end_at=obs.observed_end_at,
                received_at=now, detail=obs.detail or {}, data_class=data_class))
            inserted += 1
        elif abs(existing[1] - float(obs.value)) < 1e-9:
            duplicates += 1
        else:
            # Providers revise summaries (a sleep is re-scored, a step count grows
            # through the day). The latest value for the same source record wins.
            conn.execute(update(w).where(w.c.observation_id == existing[0]).values(
                value=float(obs.value), received_at=now, detail=obs.detail or {},
                observed_end_at=obs.observed_end_at))
            updated += 1
    return {"received": len(observations), "inserted": inserted, "updated": updated,
            "duplicates": duplicates, "rejected": sum(rejected.values()),
            "rejected_by_reason": rejected}


def payload(row: dict) -> dict:
    return {
        "observation_id": row["observation_id"], "provider": row["provider"],
        "metric": row["metric"], "value": row["value"], "unit": row["unit"],
        "observed_at": core.rfc3339(row["observed_at"]),
        "observed_end_at": core.rfc3339(row["observed_end_at"]),
        "received_at": core.rfc3339(row["received_at"]),
        "detail": row["detail"], "data_class": row["data_class"],
        "used_by_forecast_model": False,
    }
