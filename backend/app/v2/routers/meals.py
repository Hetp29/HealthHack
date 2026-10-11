"""Meals, forecasts and outcomes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import AwareDatetime, Field
from sqlalchemy import and_, select, update
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import (
    V2Context, audit, get_conn, get_ctx, read_limited_body, request_id,
    require_processing_consent,
)
from backend.app.v2.schemas import Macros, Strict
from backend.app.v2.services import meals as meal_service
from backend.app.v2.services import meal_provenance, orchestrator, outcomes

router = APIRouter(tags=["meals"])


class MealCreate(Strict):
    occurred_at: AwareDatetime
    note: str | None = Field(default=None, max_length=500)
    #: Supplying all five values creates the meal already confirmed (manual entry).
    macros: Macros | None = None


class PredictRequest(Strict):
    #: Opt-in to forecasting from an AI estimate nobody reviewed. The stored forecast
    #: is labelled, and its outcome never becomes personal history.
    allow_unconfirmed_estimate: bool = False


class ConsumptionUpdate(Strict):
    consumption_status: Literal["as_logged", "partial", "not_eaten"]


def _check_note(request: Request, note: str | None) -> None:
    """The replay's provenance label is not the caller's to write (DECISIONS D-019)."""
    if meal_provenance.has_reserved_prefix(note) and not meal_provenance.is_replay_request(request.headers):
        raise core.invalid("That note begins with wording reserved for recorded dataset meals.",
                           {"field": "note"})


async def _raw_body(request: Request, ctx: V2Context = Depends(get_ctx)) -> bytes:
    return await read_limited_body(request, ctx.settings.max_image_bytes, "image_too_large")


@router.post("/meals", status_code=201, summary="Log a meal without a photo")
def create_meal(
    request: Request,
    body: MealCreate,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    _check_note(request, body.note)
    meal = meal_service.create_meal(conn, ctx, user["user_id"], body.occurred_at, "manual", body.note)
    if body.macros is not None:
        meal_service.confirm(conn, ctx, user["user_id"], meal, body.macros.model_dump())
        meal = meal_service.get_owned(conn, user["user_id"], meal["meal_id"])
    return meal_service.meal_payload(conn, meal)


@router.post(
    "/meals/images", status_code=201, summary="Upload a meal photo and create its meal",
    openapi_extra={"requestBody": {"required": True, "content": {
        "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
        "image/png": {"schema": {"type": "string", "format": "binary"}},
        "image/webp": {"schema": {"type": "string", "format": "binary"}}}}},
)
def upload_meal_image(
    request: Request,
    occurred_at: AwareDatetime = Query(description="When the meal is eaten, RFC 3339 with offset"),
    note: str | None = Query(default=None, max_length=500),
    data: bytes = Depends(_raw_body),
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    """The request body is the raw image bytes; `Content-Type` is the image type.

    The image is validated, decoded and re-encoded before anything is stored. EXIF
    metadata, including location, is discarded.
    """
    _check_note(request, note)
    meal = meal_service.create_meal(conn, ctx, user["user_id"], occurred_at, "photo", note)
    meal_service.attach_photo(conn, ctx, user["user_id"], meal["meal_id"], data,
                              request.headers.get("content-type"))
    return meal_service.meal_payload(conn, meal)


@router.get("/meals", summary="List meals, newest first")
def list_meals(
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    include_voided: bool = False,
    user: dict = Depends(require_processing_consent),
    conn: Connection = Depends(get_conn),
) -> dict:
    query = select(t.meals).where(t.meals.c.user_id == user["user_id"])
    if not include_voided:
        query = query.where(t.meals.c.status != "voided")
    if start is not None:
        query = query.where(t.meals.c.occurred_at >= start)
    if end is not None:
        query = query.where(t.meals.c.occurred_at <= end)
    rows = conn.execute(
        query.order_by(t.meals.c.occurred_at.desc(), t.meals.c.meal_id).limit(limit)
    ).mappings().all()
    return {"meals": [meal_service.meal_payload(conn, dict(r)) for r in rows]}


@router.get("/meals/{meal_id}", summary="One meal with its photo, estimate and confirmation")
def get_meal(
    meal_id: str,
    user: dict = Depends(require_processing_consent),
    conn: Connection = Depends(get_conn),
) -> dict:
    return meal_service.meal_payload(conn, meal_service.get_owned(conn, user["user_id"], meal_id))


@router.get("/meals/{meal_id}/image", summary="The stored (sanitized) meal photo",
            response_class=Response)
def get_meal_image(
    meal_id: str,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> Response:
    meal_service.get_owned(conn, user["user_id"], meal_id)
    photo = meal_service._photo(conn, meal_id)
    data = ctx.image_store.get(photo["object_key"]) if photo else None
    if data is None:
        raise core.not_found("meal photo")
    return Response(content=data, media_type=photo["content_type"],
                    headers={"Cache-Control": "private, no-store"})


@router.delete("/meals/{meal_id}/image", status_code=204, summary="Delete the meal photo only")
def delete_meal_image(
    meal_id: str,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
    rid: str = Depends(request_id),
) -> Response:
    meal_service.get_owned(conn, user["user_id"], meal_id)
    if not meal_service.delete_photo(conn, ctx, meal_id):
        raise core.not_found("meal photo")
    audit(conn, ctx, user_id=user["user_id"], action="meal.photo_deleted",
          entity_type="meal", entity_id=meal_id, request_id=rid)
    return Response(status_code=204)


@router.post("/meals/{meal_id}/analyze", summary="Estimate nutrition from the meal photo")
def analyze_meal(
    meal_id: str,
    force: bool = Query(default=False, description="Run a new analysis even if one exists"),
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    """Sends the photo -- and nothing else about the user -- to the vision model.

    The result is an estimate for the user to review. It is not passed to the glucose
    model until confirmed.
    """
    meal = meal_service.get_owned(conn, user["user_id"], meal_id)
    estimate = meal_service.analyze(conn, ctx, user["user_id"], meal, force=force)
    return {"meal_id": meal_id, "estimate": meal_service.estimate_payload(estimate)}


@router.patch("/meals/{meal_id}/confirm", summary="Confirm the meal's nutrition values")
def confirm_meal(
    meal_id: str,
    body: Macros,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    """All five values are required and all are editable. Each call records a new
    revision; a forecast already made keeps the revision it was made from."""
    meal = meal_service.get_owned(conn, user["user_id"], meal_id)
    meal_service.confirm(conn, ctx, user["user_id"], meal, body.model_dump())
    return meal_service.meal_payload(conn, meal_service.get_owned(conn, user["user_id"], meal_id))


@router.patch("/meals/{meal_id}/consumption", summary="Record whether the meal was eaten as logged")
def update_consumption(
    meal_id: str,
    body: ConsumptionUpdate,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    meal = meal_service.get_owned(conn, user["user_id"], meal_id)
    conn.execute(update(t.meals).where(t.meals.c.meal_id == meal_id)
                 .values(consumption_status=body.consumption_status, updated_at=ctx.now()))
    # What was eaten decides whether the outcome can be trusted as history.
    conn.execute(update(t.response_episodes).where(and_(
        t.response_episodes.c.meal_id == meal_id,
        t.response_episodes.c.status.in_(("scored", "insufficient_data"))))
        .values(status="pending", eligible_for_personalization=False))
    return meal_service.meal_payload(conn, {**meal, "consumption_status": body.consumption_status})


@router.delete("/meals/{meal_id}", status_code=204, summary="Delete a meal")
def delete_meal(
    meal_id: str,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
    rid: str = Depends(request_id),
) -> Response:
    """Removes the photo and withdraws the meal from history. Forecasts already made
    for it are kept as records but their outcomes are voided."""
    meal_service.get_owned(conn, user["user_id"], meal_id)
    meal_service.delete_photo(conn, ctx, meal_id)
    conn.execute(update(t.meals).where(t.meals.c.meal_id == meal_id)
                 .values(status="voided", updated_at=ctx.now()))
    conn.execute(update(t.response_episodes).where(t.response_episodes.c.meal_id == meal_id)
                 .values(status="voided", eligible_for_personalization=False,
                         ineligibility_reasons=["meal_voided"]))
    audit(conn, ctx, user_id=user["user_id"], action="meal.voided", entity_type="meal",
          entity_id=meal_id, request_id=rid)
    return Response(status_code=204)


@router.post("/meals/{meal_id}/predict", summary="Forecast the glucose response to a confirmed meal")
def predict_meal(
    meal_id: str,
    body: PredictRequest | None = None,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
    rid: str = Depends(request_id),
) -> dict:
    """Runs the frozen fs-1.3 model and stores the forecast with the exact inputs used.

    Idempotent: one forecast exists per confirmation revision, and a repeat request
    returns the stored one with `replayed: true`. `forecast` is exactly the contract
    v1.0 `ForecastResponse`.
    """
    return orchestrator.predict_meal(
        conn, ctx, user, meal_id,
        allow_unconfirmed_estimate=bool(body and body.allow_unconfirmed_estimate),
        request_id=rid,
    )


@router.get("/meals/{meal_id}/outcome", summary="Observed response versus the forecast")
def meal_outcome(
    meal_id: str,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    meal = meal_service.get_owned(conn, user["user_id"], meal_id)
    outcomes.score_due(conn, ctx, user["user_id"])
    hypothetical = meal_provenance.is_hypothetical(conn, user["user_id"], meal)
    row = conn.execute(
        select(t.response_episodes, t.predictions.c.forecast)
        .select_from(t.response_episodes.join(t.predictions))
        .where(t.response_episodes.c.meal_id == meal_id)
        .order_by(t.predictions.c.created_at.desc()).limit(1)
    ).mappings().first()
    if hypothetical:
        # The recording that follows belongs to what the participant really ate, so
        # there is no outcome -- which is reported, not left looking like "pending".
        prediction = conn.execute(
            select(t.predictions.c.prediction_id, t.predictions.c.forecast)
            .where(t.predictions.c.meal_id == meal_id)
            .order_by(t.predictions.c.created_at.desc()).limit(1)
        ).mappings().first()
        if prediction is not None:
            return outcomes.not_applicable_payload(
                dict(row) if row is not None else None, dict(prediction), meal_id)
    if row is None:
        raise core.conflict("no_prediction", "This meal has no forecast, so there is no outcome.",
                            {"meal_status": meal["status"]})
    return outcomes.episode_payload(dict(row), {"forecast": row["forecast"]})


@router.get("/predictions", summary="List stored forecasts, newest first")
def list_predictions(
    limit: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(require_processing_consent),
    conn: Connection = Depends(get_conn),
) -> dict:
    rows = conn.execute(
        select(t.predictions).where(t.predictions.c.user_id == user["user_id"])
        .order_by(t.predictions.c.created_at.desc(), t.predictions.c.prediction_id).limit(limit)
    ).mappings().all()
    return {"predictions": [orchestrator.prediction_payload(conn, dict(r)) for r in rows]}


@router.get("/predictions/{prediction_id}", summary="One stored forecast")
def get_prediction(
    prediction_id: str,
    user: dict = Depends(require_processing_consent),
    conn: Connection = Depends(get_conn),
) -> dict:
    row = conn.execute(select(t.predictions).where(and_(
        t.predictions.c.prediction_id == prediction_id,
        t.predictions.c.user_id == user["user_id"]))).mappings().first()
    if row is None:
        raise core.not_found("prediction")
    return orchestrator.prediction_payload(conn, dict(row))


@router.post("/predictions/{prediction_id}/reproduce",
             summary="Re-run the frozen model on the stored inputs and compare")
def reproduce_prediction(
    prediction_id: str,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    return orchestrator.reproduce(conn, ctx, user["user_id"], prediction_id)

