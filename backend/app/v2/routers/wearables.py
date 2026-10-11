"""Wearable observations: native-bridge upload and read-back."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, Field
from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import V2Context, get_conn, get_ctx, require_processing_consent
from backend.app.v2.providers.base import WearableObservation
from backend.app.v2.schemas import Strict
from backend.app.v2.services import glucose, wearables

router = APIRouter(tags=["wearables"])

Metric = Literal["heart_rate", "resting_heart_rate", "steps_daily", "sleep_duration",
                 "hrv_rmssd", "hrv_sdnn", "recovery_score", "workout"]


class ObservationIn(Strict):
    metric: Metric
    value: float = Field(ge=0, allow_inf_nan=False)
    unit: str = Field(max_length=24)
    observed_at: AwareDatetime
    observed_end_at: AwareDatetime | None = None
    #: The sample's stable id on the device (for HealthKit, the sample UUID).
    source_record_id: str = Field(min_length=1, max_length=160)


class ObservationsPush(Strict):
    source: Literal["healthkit_bridge", "demo"]
    observations: list[ObservationIn] = Field(min_length=1, max_length=5000)


@router.post("/wearables/observations", summary="Upload wearable samples from a native bridge")
def push_observations(
    body: ObservationsPush,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    """The upload half of the Apple Health bridge. A browser cannot read HealthKit; a
    native iOS client reads the types the user granted and posts them here.
    Idempotent on `source_record_id`.

    These values are stored and displayed. They are not inputs to the forecast."""
    observations = [
        WearableObservation(metric=o.metric, value=o.value, unit=o.unit, observed_at=o.observed_at,
                            observed_end_at=o.observed_end_at, source_record_id=o.source_record_id)
        for o in body.observations
    ]
    data_class = "demo" if body.source == "demo" else "user_device"
    connection_id = glucose.ensure_connection(conn, ctx, user["user_id"], body.source, kind="wearable")
    counts = wearables.ingest(conn, ctx, user["user_id"], body.source, observations,
                              data_class=data_class)
    if data_class == "user_device" and (counts["inserted"] or counts["duplicates"] or counts["updated"]):
        now = ctx.now()
        conn.execute(t.device_connections.update()
                     .where(t.device_connections.c.connection_id == connection_id)
                     .values(status="connected", last_sync_at=now, last_success_at=now,
                             authentic_data_received_at=now, updated_at=now))
    return {"source": body.source, "data_class": data_class, "counts": counts,
            "units": wearables.METRIC_UNITS}


@router.get("/wearables/observations", summary="Stored wearable observations")
def list_observations(
    metric: Metric | None = None,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    end = end or ctx.now()
    start = start or end - dt.timedelta(days=7)
    w = t.wearable_observations
    query = select(w).where(and_(w.c.user_id == user["user_id"],
                                 w.c.observed_at >= start, w.c.observed_at <= end))
    if metric is not None:
        query = query.where(w.c.metric == metric)
    rows = conn.execute(query.order_by(w.c.observed_at, w.c.observation_id).limit(limit)).mappings().all()
    return {"start": core.rfc3339(start), "end": core.rfc3339(end),
            "observations": [wearables.payload(dict(r)) for r in rows],
            "units": wearables.METRIC_UNITS,
            "note": "Wearable data is stored for display. It is not an input to the forecast model."}
