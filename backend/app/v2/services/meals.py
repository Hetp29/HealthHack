"""Meal records: photo, AI estimate, user confirmation.

Three separate, immutable things are kept for a meal and never merged:

  * the photo (sanitized);
  * what the AI estimated (`meal_estimates`);
  * what the user confirmed (`meal_confirmations`, revision-numbered).

The glucose model only ever sees a confirmation.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import V2Context
from backend.app.v2.schemas import MACRO_FIELDS
from backend.app.v2.services import images, vision

#: A meal may be logged slightly ahead of the first bite, not planned days ahead: a
#: forecast needs a sensor reading from the 15 minutes before it.
MAX_FUTURE = dt.timedelta(minutes=15)
MAX_PAST = dt.timedelta(days=365)


def check_occurred_at(occurred_at: dt.datetime, now: dt.datetime) -> dt.datetime:
    occurred_at = occurred_at.astimezone(core.UTC)
    if occurred_at > now + MAX_FUTURE:
        raise core.invalid("occurred_at is too far in the future.",
                           {"field": "occurred_at", "max_future_minutes": 15})
    if occurred_at < now - MAX_PAST:
        raise core.invalid("occurred_at is too far in the past.", {"field": "occurred_at"})
    return occurred_at


def get_owned(conn: Connection, user_id: str, meal_id: str) -> dict:
    row = conn.execute(
        select(t.meals).where(and_(t.meals.c.meal_id == meal_id, t.meals.c.user_id == user_id))
    ).mappings().first()
    if row is None:
        raise core.not_found("meal")
    return dict(row)


def create_meal(conn: Connection, ctx: V2Context, user_id: str, occurred_at: dt.datetime,
                entry_method: str, note: str | None) -> dict:
    now = ctx.now()
    values = dict(
        meal_id=core.new_id("meal"), user_id=user_id,
        occurred_at=check_occurred_at(occurred_at, now), entry_method=entry_method,
        status="draft", note=note, current_confirmation_id=None,
        consumption_status="as_logged", created_at=now, updated_at=now,
    )
    conn.execute(t.meals.insert().values(**values))
    return values


def attach_photo(conn: Connection, ctx: V2Context, user_id: str, meal_id: str,
                 data: bytes, declared_content_type: str | None) -> dict:
    clean = images.sanitize_image(data, declared_content_type, ctx.settings)
    photo_id = core.new_id("pho")
    key = f"{user_id}/{photo_id}.jpg"
    ctx.image_store.put(key, clean.data, clean.content_type)
    values = dict(
        photo_id=photo_id, meal_id=meal_id, user_id=user_id, object_key=key,
        content_type=clean.content_type, byte_size=len(clean.data), sha256=clean.sha256,
        width=clean.width, height=clean.height, created_at=ctx.now(), deleted_at=None,
    )
    try:
        conn.execute(t.meal_photos.insert().values(**values))
    except Exception:
        ctx.image_store.delete(key)  # do not leave an orphaned object behind
        raise
    return values


def _photo(conn: Connection, meal_id: str) -> dict | None:
    row = conn.execute(
        select(t.meal_photos).where(and_(t.meal_photos.c.meal_id == meal_id,
                                         t.meal_photos.c.deleted_at.is_(None)))
    ).mappings().first()
    return dict(row) if row else None


def latest_estimate(conn: Connection, meal_id: str) -> dict | None:
    row = conn.execute(
        select(t.meal_estimates).where(t.meal_estimates.c.meal_id == meal_id)
        .order_by(t.meal_estimates.c.created_at.desc(), t.meal_estimates.c.estimate_id.desc())
        .limit(1)
    ).mappings().first()
    return dict(row) if row else None


def analyze(conn: Connection, ctx: V2Context, user_id: str, meal: dict, *, force: bool) -> dict:
    """Run the vision model on the meal's photo and store what it said."""
    if meal["status"] == "voided":
        raise core.conflict("meal_voided", "This meal was deleted.")
    photo = _photo(conn, meal["meal_id"])
    if photo is None:
        raise core.conflict("meal_has_no_photo",
                            "This meal has no photo to analyze. Enter the values manually.")

    existing = latest_estimate(conn, meal["meal_id"])
    if existing is not None and not force:
        return existing  # idempotent: a retry does not buy a second analysis

    if ctx.vision is None:
        raise core.V2Error(503, "vision_not_configured",
                           "Meal analysis is not configured. Enter the values manually.")

    since = ctx.now() - dt.timedelta(hours=24)
    used = conn.execute(
        select(func.count()).select_from(t.meal_estimates).where(and_(
            t.meal_estimates.c.user_id == user_id, t.meal_estimates.c.created_at > since))
    ).scalar() or 0
    if used >= ctx.settings.vision_daily_limit:
        raise core.V2Error(429, "analysis_limit_reached",
                           "The daily meal analysis limit has been reached.",
                           {"limit": ctx.settings.vision_daily_limit, "window_hours": 24})

    data = ctx.image_store.get(photo["object_key"])
    if data is None:
        raise core.V2Error(500, "photo_unavailable", "The stored photo could not be read.")

    result = ctx.vision.analyze(data)
    totals, ranges, warnings = vision.validate_estimate(result.estimate)
    values = dict(
        estimate_id=core.new_id("est"), meal_id=meal["meal_id"], user_id=user_id,
        provider="openai", ai_model_requested=result.model_requested,
        ai_model_reported=result.model_reported, prompt_version=vision.PROMPT_VERSION,
        status="ok" if totals is not None else "not_food",
        structured={**result.estimate.model_dump(), "ranges": ranges},
        warnings=warnings, usage=result.usage, created_at=ctx.now(),
        **{name: (None if totals is None else totals[name]) for name in MACRO_FIELDS},
    )
    conn.execute(t.meal_estimates.insert().values(**values))
    if meal["status"] == "draft" and totals is not None:
        conn.execute(update(t.meals).where(t.meals.c.meal_id == meal["meal_id"])
                     .values(status="analyzed", updated_at=ctx.now()))
    return values


def _next_revision(conn: Connection, meal_id: str) -> int:
    current = conn.execute(
        select(func.max(t.meal_confirmations.c.revision))
        .where(t.meal_confirmations.c.meal_id == meal_id)
    ).scalar()
    return (current or 0) + 1


def confirm(conn: Connection, ctx: V2Context, user_id: str, meal: dict, macros: dict,
            *, source: str | None = None) -> dict:
    """Record a new confirmation revision. Earlier revisions are left untouched."""
    if meal["status"] == "voided":
        raise core.conflict("meal_voided", "This meal was deleted.")
    estimate = latest_estimate(conn, meal["meal_id"])
    usable = estimate is not None and estimate["status"] == "ok"
    if source is None:
        if not usable:
            source = "manual"
        elif all(abs(float(macros[n]) - float(estimate[n])) < 1e-9 for n in MACRO_FIELDS):
            source = "ai_accepted"
        else:
            source = "ai_edited"
    values = dict(
        confirmation_id=core.new_id("cnf"), meal_id=meal["meal_id"], user_id=user_id,
        revision=_next_revision(conn, meal["meal_id"]), source=source,
        based_on_estimate_id=estimate["estimate_id"] if usable else None,
        confirmed_at=ctx.now(), **{n: float(macros[n]) for n in MACRO_FIELDS},
    )
    conn.execute(t.meal_confirmations.insert().values(**values))
    conn.execute(update(t.meals).where(t.meals.c.meal_id == meal["meal_id"]).values(
        status="confirmed", current_confirmation_id=values["confirmation_id"],
        updated_at=ctx.now()))
    return values


def confirm_from_estimate_unreviewed(conn: Connection, ctx: V2Context, user_id: str, meal: dict) -> dict:
    """The explicit opt-in path: forecast from an estimate nobody reviewed.

    Recorded with source `ai_unconfirmed`, which labels the resulting prediction and
    makes its outcome ineligible as personal history.
    """
    estimate = latest_estimate(conn, meal["meal_id"])
    if estimate is None or estimate["status"] != "ok":
        raise core.conflict("no_usable_estimate",
                            "There is no AI estimate for this meal to forecast from.")
    macros = {n: estimate[n] for n in MACRO_FIELDS}
    if any(macros[n] > vision.SUPPORTED_MAX[n] for n in MACRO_FIELDS):
        raise core.conflict("estimate_out_of_range",
                            "The AI estimate is outside the supported range; enter values manually.")
    confirm(conn, ctx, user_id, meal, macros, source="ai_unconfirmed")
    return get_owned(conn, user_id, meal["meal_id"])


def delete_photo(conn: Connection, ctx: V2Context, meal_id: str) -> bool:
    photo = _photo(conn, meal_id)
    if photo is None:
        return False
    ctx.image_store.delete(photo["object_key"])
    conn.execute(update(t.meal_photos).where(t.meal_photos.c.photo_id == photo["photo_id"])
                 .values(deleted_at=ctx.now()))
    return True


def estimate_payload(row: dict | None) -> dict | None:
    if row is None:
        return None
    structured = row["structured"]
    totals = None if row["status"] != "ok" else {n: row[n] for n in MACRO_FIELDS}
    return {
        "estimate_id": row["estimate_id"],
        "status": row["status"],
        "is_estimate": True,
        "needs_user_confirmation": True,
        "totals": totals,
        "ranges": structured.get("ranges") or None,
        "items": structured.get("items", []),
        "overall_confidence": structured.get("overall_confidence"),
        "scene": structured.get("scene"),
        "assumptions": structured.get("assumptions", []),
        "ambiguities": structured.get("ambiguities", []),
        "clarification_questions": structured.get("clarification_questions", []),
        "not_food_reason": structured.get("not_food_reason"),
        "warnings": row["warnings"],
        "provenance": {
            "provider": row["provider"], "ai_model_requested": row["ai_model_requested"],
            "ai_model_reported": row["ai_model_reported"],
            "prompt_version": row["prompt_version"],
            "method": "vision model estimate from one photograph; not a measured or "
                      "database-verified nutrition value",
        },
        "units": {"carbs_g": "g", "protein_g": "g", "fat_g": "g", "fiber_g": "g",
                  "calories_kcal": "kcal"},
        "created_at": core.rfc3339(row["created_at"]),
    }


def meal_payload(conn: Connection, meal: dict) -> dict:
    photo = _photo(conn, meal["meal_id"])
    confirmation = None
    if meal["current_confirmation_id"]:
        row = conn.execute(select(t.meal_confirmations).where(
            t.meal_confirmations.c.confirmation_id == meal["current_confirmation_id"])
        ).mappings().one()
        confirmation = {
            "confirmation_id": row["confirmation_id"], "revision": row["revision"],
            "source": row["source"], "confirmed_at": core.rfc3339(row["confirmed_at"]),
            "based_on_estimate_id": row["based_on_estimate_id"],
            **{n: row[n] for n in MACRO_FIELDS},
        }
    prediction_id = None
    if confirmation:
        prediction_id = conn.execute(select(t.predictions.c.prediction_id).where(and_(
            t.predictions.c.meal_id == meal["meal_id"],
            t.predictions.c.confirmation_id == confirmation["confirmation_id"]))).scalar()
    return {
        "meal_id": meal["meal_id"],
        "occurred_at": core.rfc3339(meal["occurred_at"]),
        "entry_method": meal["entry_method"],
        "status": meal["status"],
        "note": meal["note"],
        "consumption_status": meal["consumption_status"],
        "photo": None if photo is None else {
            "photo_id": photo["photo_id"], "content_type": photo["content_type"],
            "width": photo["width"], "height": photo["height"], "byte_size": photo["byte_size"],
            "url": f"/api/v2/meals/{meal['meal_id']}/image",
        },
        "estimate": estimate_payload(latest_estimate(conn, meal["meal_id"])),
        "confirmation": confirmation,
        "prediction_id": prediction_id,
        "created_at": core.rfc3339(meal["created_at"]),
        "updated_at": core.rfc3339(meal["updated_at"]),
    }
