"""Medication regimens, dose events and reminder slots.

Two records that must never be confused:

  * a **regimen** is what was prescribed -- a name, a dose, a schedule;
  * a **dose event** is something that happened -- the user said taken, skipped or
    snoozed.

A regimen is not evidence that anything was taken. Neither is a reminder: a reminder
slot is created with action `scheduled` and stays that way until the user acts on it.
There is no code path that turns `scheduled` into `taken` without an explicit request
from the user, and the database refuses an `actual_at` on anything but `taken`.

Nothing here reaches the forecast model.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import V2Context
from backend.app.v2.services import alerts

#: How far back reminder slots are created when the schedule is evaluated.
REMINDER_LOOKBACK = dt.timedelta(hours=24)


def regimen_payload(row: dict) -> dict:
    return {
        "regimen_id": row["regimen_id"], "name": row["name"],
        "medication_class": row["medication_class"], "form": row["form"],
        "dose": {"amount": row["dose_amount"], "unit": row["dose_unit"]},
        "frequency": row["frequency"], "schedule_times": row["schedule_times"],
        "timezone": row["timezone"], "start_date": row["start_date"], "end_date": row["end_date"],
        "status": row["status"], "reminders_enabled": row["reminders_enabled"],
        "prescriber_note": row["prescriber_note"],
        "created_at": core.rfc3339(row["created_at"]), "updated_at": core.rfc3339(row["updated_at"]),
    }


def dose_payload(row: dict) -> dict:
    return {
        "dose_id": row["dose_id"], "regimen_id": row["regimen_id"],
        "scheduled_at": core.rfc3339(row["scheduled_at"]), "action": row["action"],
        # Present only for an explicit `taken`. Null means "not confirmed taken".
        "actual_at": core.rfc3339(row["actual_at"]),
        "snoozed_until": core.rfc3339(row["snoozed_until"]),
        "actor": row["actor"], "provenance": row["provenance"],
        "client_event_id": row["client_event_id"], "recorded_at": core.rfc3339(row["recorded_at"]),
    }


def get_regimen(conn: Connection, user_id: str, regimen_id: str) -> dict:
    row = conn.execute(select(t.medication_regimens).where(and_(
        t.medication_regimens.c.regimen_id == regimen_id,
        t.medication_regimens.c.user_id == user_id))).mappings().first()
    if row is None:
        raise core.not_found("medication regimen")
    return dict(row)


def _slot_instant(day: dt.date, hhmm: str, zone: ZoneInfo) -> dt.datetime:
    """A local wall-clock time on a date, as a UTC instant.

    DST: a time that does not exist (spring-forward) resolves to the instant the
    clock jumps to; a time that occurs twice (fall-back) resolves to its FIRST
    occurrence. Either way exactly one reminder is produced for the slot.
    """
    hour, minute = (int(part) for part in hhmm.split(":"))
    local = dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=0)
    return local.astimezone(core.UTC)


def materialize_reminders(conn: Connection, ctx: V2Context, user_id: str | None = None) -> int:
    """Create a `scheduled` dose slot for every reminder time that has now passed.

    Idempotent: the (regimen, scheduled_at) pair is unique, so running this from a
    request and from the worker at the same moment creates each slot once.
    Prescribed-time reminders are NOT held back by alert quiet hours -- silently
    suppressing them would need consent that has not been designed (DECISIONS D-011).
    """
    now = ctx.now()
    r = t.medication_regimens
    query = select(r).where(and_(r.c.status == "current", r.c.reminders_enabled.is_(True)))
    if user_id is not None:
        query = query.where(r.c.user_id == user_id)
    created = 0
    for regimen in conn.execute(query).mappings().all():
        zone = ZoneInfo(regimen["timezone"])
        local_today = now.astimezone(zone).date()
        for day in (local_today - dt.timedelta(days=1), local_today):
            text = day.isoformat()
            if regimen["start_date"] and text < regimen["start_date"]:
                continue
            if regimen["end_date"] and text > regimen["end_date"]:
                continue
            for hhmm in regimen["schedule_times"]:
                slot = _slot_instant(day, hhmm, zone)
                if not (now - REMINDER_LOOKBACK < slot <= now) or slot < regimen["created_at"]:
                    continue
                dose_id = core.new_id("dos")
                try:
                    with conn.begin_nested():
                        conn.execute(t.medication_doses.insert().values(
                            dose_id=dose_id, regimen_id=regimen["regimen_id"],
                            user_id=regimen["user_id"], scheduled_at=slot, action="scheduled",
                            actual_at=None, snoozed_until=None, actor="system",
                            provenance="reminder_schedule", client_event_id=None, recorded_at=now))
                except IntegrityError:
                    continue  # slot already exists
                alerts.record_delivery(conn, ctx, regimen["user_id"], "reminder", dose_id, "in_app")
                created += 1
    return created


def due_reminders(conn: Connection, ctx: V2Context, user_id: str) -> list[dict]:
    """Slots awaiting a decision: still `scheduled`, or snoozed until a time now past."""
    materialize_reminders(conn, ctx, user_id)
    now = ctx.now()
    d = t.medication_doses
    rows = conn.execute(
        select(d).where(and_(
            d.c.user_id == user_id, d.c.scheduled_at.is_not(None),
            or_(d.c.action == "scheduled",
                and_(d.c.action == "snoozed", d.c.snoozed_until <= now)),
        )).order_by(d.c.scheduled_at)
    ).mappings().all()
    return [dict(r) for r in rows]


#: Versioned, source-attributed class-level information. Deliberately general: it
#: describes what a medication class is documented to do, and says nothing about what
#: any individual should do or what their glucose will be.
EDUCATION_VERSION = "2026-10-10.draft1"
EDUCATION_REVIEW_STATUS = "not_clinically_reviewed"
EDUCATION_LIMITATION = (
    "We do not have a validated individualized medication-effect forecast. This is "
    "general information about a medication class, not advice about your treatment. "
    "Do not start, stop or change any medication or dose based on it. Talk to the "
    "clinician who prescribes your medication."
)
_MEDLINEPLUS = "MedlinePlus, U.S. National Library of Medicine"
EDUCATION: list[dict] = [
    {"medication_class": "metformin", "title": "Metformin",
     "summary": "Lowers blood glucose mainly by reducing the amount of glucose the liver "
                "releases and by improving the body's response to insulin. Used on its own "
                "it does not usually cause low blood glucose.",
     "hypoglycemia_note": "Low blood glucose is uncommon with metformin alone; the risk "
                          "depends on what else is taken with it.",
     "sources": [{"publisher": _MEDLINEPLUS, "title": "Metformin",
                  "url": "https://medlineplus.gov/druginfo/meds/a696005.html"}]},
    {"medication_class": "insulin", "title": "Insulin",
     "summary": "Replaces or supplements the body's own insulin and lowers blood glucose. "
                "Types differ in how quickly they start and how long they act.",
     "hypoglycemia_note": "Insulin can cause low blood glucose. Timing and amount relative "
                          "to food and activity are set by the prescriber.",
     "sources": [{"publisher": _MEDLINEPLUS, "title": "Insulin Human Injection",
                  "url": "https://medlineplus.gov/druginfo/meds/a682611.html"}]},
    {"medication_class": "glp1_receptor_agonist", "title": "GLP-1 receptor agonists",
     "summary": "Help the pancreas release insulin when blood glucose is high, reduce "
                "glucagon, and slow how quickly the stomach empties, which can change how "
                "fast glucose rises after a meal.",
     "hypoglycemia_note": "Low blood glucose is more likely when combined with insulin or "
                          "a sulfonylurea.",
     "sources": [{"publisher": _MEDLINEPLUS, "title": "Semaglutide Injection",
                  "url": "https://medlineplus.gov/druginfo/meds/a618008.html"}]},
    {"medication_class": "sulfonylurea", "title": "Sulfonylureas",
     "summary": "Lower blood glucose by causing the pancreas to release more insulin.",
     "hypoglycemia_note": "Can cause low blood glucose, particularly when meals are "
                          "delayed or skipped.",
     "sources": [{"publisher": _MEDLINEPLUS, "title": "Glipizide",
                  "url": "https://medlineplus.gov/druginfo/meds/a684060.html"}]},
    {"medication_class": "sglt2_inhibitor", "title": "SGLT2 inhibitors",
     "summary": "Lower blood glucose by causing the kidneys to remove more glucose in the urine.",
     "hypoglycemia_note": "Low blood glucose is uncommon alone; the risk depends on what "
                          "else is taken with it.",
     "sources": [{"publisher": _MEDLINEPLUS, "title": "Empagliflozin",
                  "url": "https://medlineplus.gov/druginfo/meds/a614043.html"}]},
]
