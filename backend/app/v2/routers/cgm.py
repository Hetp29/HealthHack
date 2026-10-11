"""Glucose observations: push, file import, read-back and freshness."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AwareDatetime, Field
from sqlalchemy import select
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import (
    V2Context, get_conn, get_ctx, read_limited_body, require_processing_consent,
)
from backend.app.v2.providers import cgm_files
from backend.app.v2.schemas import GlucoseUnit, Strict
from backend.app.v2.services import glucose

router = APIRouter(tags=["cgm"])

MAX_IMPORT_BYTES = 15 * 1024 * 1024
MAX_PUSH_READINGS = 5000


class ReadingIn(Strict):
    observed_at: AwareDatetime
    value: float = Field(allow_inf_nan=False)
    #: Required. A bare number is never assumed to be mg/dL or mmol/L.
    unit: GlucoseUnit
    record_kind: Literal["cgm", "scan", "fingerstick"] = "cgm"
    source_record_id: str | None = Field(default=None, max_length=128)


class ReadingsPush(Strict):
    #: `demo` is stored and shown as demonstration data and is never reported as a
    #: live device. `healthkit_bridge` is for a native iOS client that read HealthKit.
    source: Literal["manual_entry", "healthkit_bridge", "demo"]
    device_id: str = Field(default="unknown", min_length=1, max_length=128)
    readings: list[ReadingIn] = Field(min_length=1, max_length=MAX_PUSH_READINGS)


async def _import_body(request: Request) -> bytes:
    return await read_limited_body(request, MAX_IMPORT_BYTES, "file_too_large")


@router.post("/cgm/readings", summary="Push glucose readings")
def push_readings(
    body: ReadingsPush,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    """Idempotent. Re-sending readings already stored reports them as duplicates; a
    changed value for a stored instant is recorded as a correction."""
    readings = [
        glucose.IncomingReading(
            value=r.value, unit=r.unit, observed_at=r.observed_at, device_id=body.device_id,
            record_kind=r.record_kind, source_record_id=r.source_record_id,
            source_timestamp=r.observed_at.isoformat(),
        )
        for r in body.readings
    ]
    return glucose.ingest_readings(
        conn, ctx, user["user_id"], body.source, readings, source_format="json",
        data_class="demo" if body.source == "demo" else "user_device",
    )


@router.post(
    "/cgm/import", summary="Import a CGM export file the user downloaded",
    openapi_extra={"requestBody": {"required": True, "content": {
        "text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
def import_file(
    format: Literal["dexcom_clarity_csv", "libreview_csv"] = Query(),
    date_order: Literal["MDY", "DMY"] | None = Query(
        default=None, description="LibreView only: how the export writes dates"),
    data: bytes = Depends(_import_body),
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    """The request body is the CSV file. Its timestamps have no UTC offset and are
    interpreted in the account's timezone; ambiguous or nonexistent local times
    (DST changes) are rejected and listed, not guessed."""
    if not data:
        raise core.invalid("The upload was empty.", {"field": "body"})
    if format == "libreview_csv":
        readings = cgm_files.parse_libreview(data, user["timezone"], date_order)
    else:
        readings = cgm_files.parse_dexcom_clarity(data, user["timezone"])
    if not readings:
        raise core.invalid("No glucose rows were found in the file.", {"format": format})
    return glucose.ingest_readings(
        conn, ctx, user["user_id"], format, readings, source_format=format,
        source_sha256=hashlib.sha256(data).hexdigest(),
    )


@router.get("/cgm/readings", summary="Stored glucose readings in a time range")
def list_readings(
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    unit: GlucoseUnit = "mg/dL",
    limit: int = Query(default=2000, ge=1, le=5000),
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    end = end or ctx.now()
    start = start or end - dt.timedelta(hours=24)
    if start > end:
        raise core.invalid("start must not be later than end.", {"field": "start"})
    # Newest first under the limit, then returned ascending for plotting.
    rows = glucose.fetch(conn, user["user_id"], start, end, limit=limit, newest_first=True)
    rows.reverse()
    return {
        "start": core.rfc3339(start), "end": core.rfc3339(end), "unit": unit,
        "truncated": len(rows) == limit,
        "readings": [glucose.observation_payload(r, unit) for r in rows],
        "gaps": glucose.find_gaps([r for r in rows if r["record_kind"] == "cgm"], start, end),
    }


@router.get("/cgm/status", summary="Freshness, coverage and which sensor forecasts use")
def cgm_status(
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    return glucose.status(conn, ctx, user["user_id"])


@router.get("/cgm/imports", summary="History of ingestion batches")
def list_imports(
    limit: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(require_processing_consent),
    conn: Connection = Depends(get_conn),
) -> dict:
    rows = conn.execute(
        select(t.import_batches).where(t.import_batches.c.user_id == user["user_id"])
        .order_by(t.import_batches.c.received_at.desc()).limit(limit)
    ).mappings().all()
    return {"imports": [
        {"batch_id": r["batch_id"], "provider": r["provider"], "kind": r["kind"],
         "source_format": r["source_format"], "data_class": r["data_class"],
         "received_at": core.rfc3339(r["received_at"]), "counts": r["counts"],
         "rejections": r["rejections"]}
        for r in rows
    ]}
