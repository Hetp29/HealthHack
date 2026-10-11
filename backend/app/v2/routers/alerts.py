"""Alert settings, alert events and notification records."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field, field_validator
from sqlalchemy import and_, select, update
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import V2Context, get_conn, get_ctx, require_processing_consent
from backend.app.v2.schemas import GlucoseUnit, Strict
from backend.app.v2.services import alerts as service

router = APIRouter(tags=["alerts"])

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
#: A forecast-level alert outside this range would fire always or never.
THRESHOLD_MIN_MG_DL, THRESHOLD_MAX_MG_DL = 100.0, 400.0


class Threshold(Strict):
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: GlucoseUnit


class AlertSettings(Strict):
    enabled: bool
    threshold: Threshold
    channels: list[Literal["in_app", "push", "email"]] = Field(min_length=1, max_length=3)
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    cooldown_minutes: int = Field(default=60, ge=0, le=1440)

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def _hhmm(cls, value):
        if value is not None and not _HHMM.match(value):
            raise ValueError("must be 24-hour 'HH:MM'")
        return value


def _settings_payload(rule: dict) -> dict:
    return {
        "enabled": rule["enabled"],
        "threshold": {"value": rule["threshold_source_value"], "unit": rule["threshold_source_unit"],
                      "mg_dl": rule["threshold_mg_dl"]},
        "channels": [
            {"channel": c, "available": c in service.AVAILABLE_CHANNELS} for c in rule["channels"]
        ],
        "available_channels": sorted(service.AVAILABLE_CHANNELS),
        "quiet_hours": {"start": rule["quiet_hours_start"], "end": rule["quiet_hours_end"]},
        "cooldown_minutes": rule["cooldown_minutes"],
        "updated_at": core.rfc3339(rule["updated_at"]),
        "informational_only": True,
        "disclaimer": service.DISCLAIMER,
    }


@router.get("/alert-settings", summary="Forecast alert rule")
def get_settings(user: dict = Depends(require_processing_consent),
                 conn: Connection = Depends(get_conn)) -> dict:
    return _settings_payload(service.load_rule(conn, user["user_id"]))


@router.put("/alert-settings", summary="Replace the forecast alert rule")
def put_settings(
    body: AlertSettings,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    mg_dl, _ = core.glucose_to_mg_dl(body.threshold.value, body.threshold.unit)
    if not (THRESHOLD_MIN_MG_DL <= mg_dl <= THRESHOLD_MAX_MG_DL):
        raise core.invalid("threshold is outside the supported range.",
                           {"field": "threshold", "min_mg_dl": THRESHOLD_MIN_MG_DL,
                            "max_mg_dl": THRESHOLD_MAX_MG_DL})
    if (body.quiet_hours_start is None) != (body.quiet_hours_end is None):
        raise core.invalid("quiet_hours_start and quiet_hours_end must be given together.",
                           {"fields": ["quiet_hours_start", "quiet_hours_end"]})
    values = {
        "enabled": body.enabled, "threshold_mg_dl": round(mg_dl, 3),
        "threshold_source_value": body.threshold.value,
        "threshold_source_unit": body.threshold.unit,
        "channels": list(dict.fromkeys(body.channels)),
        "quiet_hours_start": body.quiet_hours_start, "quiet_hours_end": body.quiet_hours_end,
        "cooldown_minutes": body.cooldown_minutes, "updated_at": ctx.now(),
    }
    a = t.alert_rules
    exists = conn.execute(select(a.c.user_id).where(a.c.user_id == user["user_id"])).first()
    if exists:
        conn.execute(update(a).where(a.c.user_id == user["user_id"]).values(**values))
    else:
        conn.execute(a.insert().values(user_id=user["user_id"], **values))
    return _settings_payload(values)


@router.get("/alerts", summary="Forecast alert events, newest first")
def list_alerts(
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(require_processing_consent),
    conn: Connection = Depends(get_conn),
) -> dict:
    a = t.alert_events
    query = select(a).where(a.c.user_id == user["user_id"])
    if status is not None:
        query = query.where(a.c.status == status)
    rows = conn.execute(query.order_by(a.c.created_at.desc(), a.c.alert_id).limit(limit)).mappings().all()
    return {"alerts": [service.alert_payload(dict(r)) for r in rows]}


@router.post("/alerts/{alert_id}/acknowledge", summary="Mark an alert as seen")
def acknowledge(
    alert_id: str,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    a = t.alert_events
    row = conn.execute(select(a).where(and_(
        a.c.alert_id == alert_id, a.c.user_id == user["user_id"]))).mappings().first()
    if row is None:
        raise core.not_found("alert")
    row = dict(row)
    if row["acknowledged_at"] is None:
        row["acknowledged_at"] = ctx.now()
        conn.execute(update(a).where(a.c.alert_id == alert_id)
                     .values(acknowledged_at=row["acknowledged_at"]))
    return service.alert_payload(row)


@router.get("/notifications", summary="What was delivered, on which channel")
def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(require_processing_consent),
    conn: Connection = Depends(get_conn),
) -> dict:
    n = t.notification_deliveries
    rows = conn.execute(select(n).where(n.c.user_id == user["user_id"])
                        .order_by(n.c.created_at.desc(), n.c.delivery_id).limit(limit)).mappings().all()
    return {"notifications": [
        {"delivery_id": r["delivery_id"], "source_type": r["source_type"],
         "source_id": r["source_id"], "channel": r["channel"], "status": r["status"],
         "detail": r["detail"], "created_at": core.rfc3339(r["created_at"])}
        for r in rows
    ]}
