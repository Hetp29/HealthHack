"""Medication regimens, dose events, reminders and education content."""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import AwareDatetime, Field, field_validator
from sqlalchemy import and_, select, update
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import (
    V2Context, audit, get_conn, get_ctx, request_id, require_processing_consent,
)
from backend.app.v2.schemas import Strict
from backend.app.v2.services import medications as service

router = APIRouter(tags=["medications"])

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MedicationClass = Literal["metformin", "insulin", "glp1_receptor_agonist", "sulfonylurea",
                          "sglt2_inhibitor", "other"]


class _RegimenFields(Strict):
    @field_validator("schedule_times", check_fields=False)
    @classmethod
    def _times(cls, value):
        if value is None:
            return value
        if any(not _HHMM.match(v) for v in value):
            raise ValueError("schedule_times must be 24-hour 'HH:MM' strings")
        return sorted(set(value))

    @field_validator("start_date", "end_date", check_fields=False)
    @classmethod
    def _date(cls, value):
        if value is None:
            return value
        if not _DATE.match(value):
            raise ValueError("dates must be 'YYYY-MM-DD'")
        dt.date.fromisoformat(value)
        return value

    @field_validator("timezone", check_fields=False)
    @classmethod
    def _tz(cls, value):
        if value is not None and not core.is_valid_timezone(value):
            raise ValueError("not a known IANA timezone")
        return value


class RegimenCreate(_RegimenFields):
    name: str = Field(min_length=1, max_length=160)
    medication_class: MedicationClass | None = None
    form: str | None = Field(default=None, max_length=40)
    dose_amount: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    dose_unit: str | None = Field(default=None, max_length=24)
    frequency: str | None = Field(default=None, max_length=80)
    schedule_times: list[str] = Field(default_factory=list, max_length=12)
    #: Defaults to the account timezone.
    timezone: str | None = Field(default=None, max_length=64)
    start_date: str | None = None
    end_date: str | None = None
    reminders_enabled: bool = False
    prescriber_note: str | None = Field(default=None, max_length=500)


class RegimenPatch(_RegimenFields):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    medication_class: MedicationClass | None = None
    form: str | None = Field(default=None, max_length=40)
    dose_amount: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    dose_unit: str | None = Field(default=None, max_length=24)
    frequency: str | None = Field(default=None, max_length=80)
    schedule_times: list[str] | None = Field(default=None, max_length=12)
    timezone: str | None = Field(default=None, max_length=64)
    start_date: str | None = None
    end_date: str | None = None
    reminders_enabled: bool | None = None
    prescriber_note: str | None = Field(default=None, max_length=500)
    status: Literal["current", "discontinued"] | None = None


class DoseEvent(Strict):
    regimen_id: str
    action: Literal["taken", "skipped", "snoozed", "unknown"]
    #: The reminder slot this answers, if any. Omit for an unscheduled dose.
    scheduled_at: AwareDatetime | None = None
    #: Only meaningful with `taken`. Defaults to now.
    actual_at: AwareDatetime | None = None
    snoozed_until: AwareDatetime | None = None
    #: Client-generated id. Re-sending the same id returns the stored event.
    client_event_id: str = Field(min_length=8, max_length=80)


def _check_dose_unit(amount: float | None, unit: str | None) -> None:
    if (amount is None) != (unit is None):
        raise core.invalid("dose_amount and dose_unit must be given together.",
                           {"fields": ["dose_amount", "dose_unit"]})


@router.get("/medications", summary="Medication regimens")
def list_regimens(
    status: Literal["current", "discontinued"] | None = None,
    user: dict = Depends(require_processing_consent),
    conn: Connection = Depends(get_conn),
) -> dict:
    r = t.medication_regimens
    query = select(r).where(r.c.user_id == user["user_id"])
    if status is not None:
        query = query.where(r.c.status == status)
    rows = conn.execute(query.order_by(r.c.created_at)).mappings().all()
    return {"regimens": [service.regimen_payload(dict(x)) for x in rows]}


@router.post("/medications", status_code=201, summary="Add a medication regimen")
def create_regimen(
    body: RegimenCreate,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
    rid: str = Depends(request_id),
) -> dict:
    _check_dose_unit(body.dose_amount, body.dose_unit)
    now = ctx.now()
    values = {**body.model_dump(), "regimen_id": core.new_id("rgm"), "user_id": user["user_id"],
              "timezone": body.timezone or user["timezone"], "status": "current",
              "created_at": now, "updated_at": now}
    conn.execute(t.medication_regimens.insert().values(**values))
    audit(conn, ctx, user_id=user["user_id"], action="medication.regimen_created",
          entity_type="regimen", entity_id=values["regimen_id"], request_id=rid)
    return service.regimen_payload(values)


@router.patch("/medications/{regimen_id}", summary="Edit or discontinue a regimen")
def patch_regimen(
    regimen_id: str,
    body: RegimenPatch,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
    rid: str = Depends(request_id),
) -> dict:
    regimen = service.get_regimen(conn, user["user_id"], regimen_id)
    changes = body.model_dump(exclude_unset=True)
    for required in ("name", "schedule_times", "timezone", "reminders_enabled", "status"):
        if required in changes and changes[required] is None:
            changes.pop(required)
    merged = {**regimen, **changes}
    _check_dose_unit(merged["dose_amount"], merged["dose_unit"])
    if changes:
        changes["updated_at"] = ctx.now()
        conn.execute(update(t.medication_regimens)
                     .where(t.medication_regimens.c.regimen_id == regimen_id).values(**changes))
        audit(conn, ctx, user_id=user["user_id"], action="medication.regimen_updated",
              entity_type="regimen", entity_id=regimen_id, request_id=rid,
              detail={"fields": sorted(k for k in changes if k != "updated_at")})
    return service.regimen_payload({**regimen, **changes})


@router.post("/medication-doses", summary="Record what the user did about a dose")
def record_dose(
    body: DoseEvent,
    response: Response,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
    rid: str = Depends(request_id),
) -> dict:
    """The only way a dose becomes `taken`. Idempotent on `client_event_id`: a retry
    returns the stored event (200) instead of creating a second one (201)."""
    user_id = user["user_id"]
    d = t.medication_doses
    replay = conn.execute(select(d).where(and_(
        d.c.user_id == user_id, d.c.client_event_id == body.client_event_id))).mappings().first()
    if replay is not None:
        return {**service.dose_payload(dict(replay)), "replayed": True}

    regimen = service.get_regimen(conn, user_id, body.regimen_id)
    now = ctx.now()
    if body.action == "snoozed":
        if body.snoozed_until is None or body.snoozed_until <= now:
            raise core.invalid("snoozed_until must be a future time.", {"field": "snoozed_until"})
    elif body.snoozed_until is not None:
        raise core.invalid("snoozed_until applies only to action 'snoozed'.", {"field": "snoozed_until"})
    if body.actual_at is not None and body.action != "taken":
        raise core.invalid("actual_at applies only to action 'taken'.", {"field": "actual_at"})
    if body.actual_at is not None and body.actual_at > now + dt.timedelta(minutes=5):
        raise core.invalid("actual_at cannot be in the future.", {"field": "actual_at"})

    values = {
        "action": body.action,
        "actual_at": (body.actual_at or now) if body.action == "taken" else None,
        "snoozed_until": body.snoozed_until if body.action == "snoozed" else None,
        "actor": "user", "provenance": "user_action",
        "client_event_id": body.client_event_id, "recorded_at": now,
    }
    slot = None
    if body.scheduled_at is not None:
        slot = conn.execute(select(d).where(and_(
            d.c.regimen_id == regimen["regimen_id"],
            d.c.scheduled_at == body.scheduled_at))).mappings().first()
    if slot is not None:
        conn.execute(update(d).where(d.c.dose_id == slot["dose_id"]).values(**values))
        stored = {**dict(slot), **values}
        response.status_code = 200
    else:
        stored = {"dose_id": core.new_id("dos"), "regimen_id": regimen["regimen_id"],
                  "user_id": user_id, "scheduled_at": body.scheduled_at, **values}
        conn.execute(d.insert().values(**stored))
        response.status_code = 201
    audit(conn, ctx, user_id=user_id, action=f"medication.dose_{body.action}",
          entity_type="dose", entity_id=stored["dose_id"], request_id=rid)
    return {**service.dose_payload(stored), "replayed": False}


@router.get("/medication-doses", summary="Dose history")
def list_doses(
    regimen_id: str | None = None,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(require_processing_consent),
    conn: Connection = Depends(get_conn),
) -> dict:
    d = t.medication_doses
    query = select(d).where(d.c.user_id == user["user_id"])
    if regimen_id is not None:
        query = query.where(d.c.regimen_id == regimen_id)
    if start is not None:
        query = query.where(d.c.recorded_at >= start)
    if end is not None:
        query = query.where(d.c.recorded_at <= end)
    rows = conn.execute(query.order_by(d.c.recorded_at.desc(), d.c.dose_id).limit(limit)).mappings().all()
    return {"doses": [service.dose_payload(dict(r)) for r in rows]}


@router.get("/medication-reminders/due", summary="Reminder slots awaiting a decision")
def due_reminders(
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    """Server-side schedule, not a browser timer. A slot stays here until the user
    answers it with `POST /medication-doses`; it is never marked taken by itself."""
    rows = service.due_reminders(conn, ctx, user["user_id"])
    names = {}
    for row in rows:
        if row["regimen_id"] not in names:
            names[row["regimen_id"]] = service.get_regimen(conn, user["user_id"], row["regimen_id"])
    return {"as_of": core.rfc3339(ctx.now()), "due": [
        {**service.dose_payload(r),
         "regimen": {"name": names[r["regimen_id"]]["name"],
                     "dose": {"amount": names[r["regimen_id"]]["dose_amount"],
                              "unit": names[r["regimen_id"]]["dose_unit"]}}}
        for r in rows
    ]}


@router.get("/medication-education", summary="General, source-attributed medication information")
def education(_: dict = Depends(require_processing_consent)) -> dict:
    return {"content_version": service.EDUCATION_VERSION,
            "review_status": service.EDUCATION_REVIEW_STATUS,
            "limitation": service.EDUCATION_LIMITATION,
            "individualized_effect_forecast_available": False,
            "entries": service.EDUCATION}
