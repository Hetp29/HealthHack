"""Confirmed meal -> frozen forecast -> immutable record.

The forecast itself is produced by `ml.inference.forecast`, the frozen engine, called
with the loaded frozen artifact. This module chooses the inputs, records exactly what
they were, and stores the result. It adds no modelling of its own.

Reproducibility: the snapshot holds the complete history the engine was given. With
the same artifact (identified by its SHA-256) `reproduce` re-runs the engine on the
stored inputs and must obtain the stored forecast exactly.
"""

from __future__ import annotations

import datetime as dt
import logging
import math

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from backend.app import errors as v1_errors
from backend.app.schemas import ForecastResponse
from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import (
    CONSENT_PERSONALIZATION, MODEL_ID, V2Context, has_consent, participant_id_for,
)
from backend.app.v2.services import alerts, history, outcomes, personalization
from ml.config import HORIZONS
from ml.features import FEATURE_NAMES, FeatureError, InsufficientPremealContext, build_features
from ml.inference import forecast as frozen_forecast
from ml.provenance import describe_time_basis

logger = logging.getLogger("glucotwin.v2.orchestrator")

#: A forecast requested this long after the meal is retrospective, not a prediction
#: the user could have acted on. It is labelled as such and never raises an alert.
PROSPECTIVE_WINDOW = dt.timedelta(minutes=15)

#: The engine attaches this caveat for datasets whose timestamps are date-shifted
#: wall-clock times. It is untrue of a live account, so it is replaced with a
#: statement of what was actually done.
_DATASET_CLOCK_CAVEAT = describe_time_basis(history.ENGINE_TIME_BASIS, None)
LIVE_CLOCK_NOTE = (
    "Timestamps were expressed on this account's local clock, using its UTC offset at "
    "the meal time, so that time-of-day inputs match how the model was trained."
)
UNCONFIRMED_NOTE = (
    "This forecast used an AI nutrition estimate that was not reviewed or confirmed. "
    "The estimate may be substantially wrong, and so may this forecast."
)


def _artifact(ctx: V2Context):
    integrity = ctx.model_integrity
    if integrity.get("enforced") and not integrity.get("verified"):
        raise core.V2Error(
            503, "model_integrity_failure",
            "The loaded model artifact does not match the approved checksum; "
            "forecasting is disabled.",
        )
    try:
        return ctx.prediction_service.artifact()
    except v1_errors.ApiError as error:
        raise core.V2Error(503, "model_unavailable", "No model artifact is loaded.") from error


def _run_engine(artifact, inputs: history.EngineInputs):
    return frozen_forecast(
        artifact, inputs.history, inputs.meal_timestamp, inputs.composition,
        participant=inputs.participant, override=inputs.override,
        time_basis=history.ENGINE_TIME_BASIS,
    )


def _contract_forecast(result, true_meal_time: dt.datetime, extra_notes: list[str]) -> dict:
    """The frozen `ForecastResponse` shape, with the two fields that would be untrue
    for a live account corrected: the projected timestamp and the dataset clock caveat."""
    payload = result.to_contract_dict()
    payload["meal_timestamp"] = core.rfc3339(true_meal_time)
    payload["limitations"] = [
        note for note in payload["limitations"] if note != _DATASET_CLOCK_CAVEAT
    ] + [LIVE_CLOCK_NOTE] + extra_notes
    # Validated against the frozen v1 response model: a shape regression fails here.
    ForecastResponse.model_validate(payload)
    return payload


def _load_meal(conn: Connection, user_id: str, meal_id: str) -> dict:
    row = conn.execute(
        select(t.meals).where(and_(t.meals.c.meal_id == meal_id, t.meals.c.user_id == user_id))
    ).mappings().first()
    if row is None:
        raise core.not_found("meal")
    return dict(row)


def _existing(conn: Connection, meal_id: str, confirmation_id: str) -> dict | None:
    row = conn.execute(
        select(t.predictions).where(and_(
            t.predictions.c.meal_id == meal_id,
            t.predictions.c.confirmation_id == confirmation_id,
        ))
    ).mappings().first()
    return dict(row) if row else None


def predict_meal(conn: Connection, ctx: V2Context, user: dict, meal_id: str, *,
                 allow_unconfirmed_estimate: bool = False, request_id: str | None = None) -> dict:
    user_id = user["user_id"]
    meal = _load_meal(conn, user_id, meal_id)
    if meal["status"] == "voided":
        raise core.conflict("meal_voided", "This meal was deleted and cannot be forecast.")

    inputs_confirmed = True
    if meal["current_confirmation_id"] is None:
        if not allow_unconfirmed_estimate:
            raise core.conflict(
                "meal_not_confirmed",
                "Confirm the meal's nutrition values before requesting a forecast.",
                {"meal_status": meal["status"]},
            )
        from backend.app.v2.services import meals as meal_service

        meal = meal_service.confirm_from_estimate_unreviewed(conn, ctx, user_id, meal)
        inputs_confirmed = False

    confirmation = dict(conn.execute(
        select(t.meal_confirmations).where(
            t.meal_confirmations.c.confirmation_id == meal["current_confirmation_id"])
    ).mappings().one())
    if confirmation["source"] == "ai_unconfirmed":
        inputs_confirmed = False

    stored = _existing(conn, meal_id, confirmation["confirmation_id"])
    if stored is not None:
        return prediction_payload(conn, stored, replayed=True)

    artifact = _artifact(ctx)
    now = ctx.now()
    participant_id = participant_id_for(conn, user_id)
    consent = has_consent(conn, user_id, CONSENT_PERSONALIZATION)

    # Bring this user's finished episodes up to date first, so a meal completed a few
    # hours ago is available as history for the forecast being made now.
    outcomes.score_due(conn, ctx, user_id, include_in_grace=True)

    tolerance = float(artifact.config.features.baseline_tolerance_minutes)
    inputs = history.build_engine_inputs(
        conn, user=user, participant_id=participant_id, meal=meal,
        confirmation=confirmation, baseline_tolerance_minutes=tolerance,
        personalization_consent=consent,
    )

    policy = ctx.settings.personalization_policy
    applied = {"policy": policy, "calibration_applied": False, "calibration": None,
               "calibration_episode_ids": [], "consent": consent}
    engine_artifact = artifact
    if policy == personalization.POLICY_FROZEN_CALIBRATION and consent:
        calibration, episode_ids = personalization.fit_user_calibration(
            conn, artifact, user_id, participant_id, meal["occurred_at"])
        engine_artifact = personalization.artifact_view(artifact, calibration)
        applied.update(calibration=personalization.calibration_record(calibration),
                       calibration_episode_ids=episode_ids,
                       calibration_applied=calibration.personalized)

    try:
        result = _run_engine(engine_artifact, inputs)
        features = build_features(
            history=inputs.history, meal_timestamp=inputs.meal_timestamp,
            composition=inputs.composition, config=artifact.config.features,
            timezone_name=None, time_basis=history.ENGINE_TIME_BASIS, override=inputs.override,
        )
    except InsufficientPremealContext as error:
        raise core.V2Error(
            422, "insufficient_premeal_context",
            "No CGM reading close enough before the meal to anchor a forecast.",
            {"baseline_tolerance_minutes": tolerance,
             "latest_cgm_age_minutes": inputs.context["cgm"]["latest_age_minutes"],
             "cgm_readings_in_lookback": inputs.context["cgm"]["readings_in_lookback"]},
        ) from error
    except FeatureError as error:
        raise core.V2Error(422, "prediction_inputs_invalid",
                           "The forecast inputs could not be assembled.") from error

    offsets = {
        str(h): (float(engine_artifact.personalization.calibration_for(participant_id).offset_for(h))
                 if result.prediction_status == "personalized" else 0.0)
        for h in HORIZONS
    }
    cohort = {str(h): float(result.horizon_deltas[h]) - offsets[str(h)] for h in HORIZONS}
    forecast = _contract_forecast(
        result, meal["occurred_at"], [] if inputs_confirmed else [UNCONFIRMED_NOTE])

    snapshot_id, prediction_id = core.new_id("snp"), core.new_id("prd")
    kind = "prospective" if now <= meal["occurred_at"] + PROSPECTIVE_WINDOW else "retrospective"
    integrity = ctx.model_integrity
    retrieval = {name: features.values[name] for name in ("r_hist_n", "q_retr_n")}
    applied["admissible_prior_meals"] = int(retrieval["r_hist_n"])
    applied["retrieval_active"] = not math.isnan(features.values["q_retr_delta_60"])

    conn.execute(t.feature_snapshots.insert().values(
        snapshot_id=snapshot_id, user_id=user_id, meal_id=meal_id, as_of=meal["occurred_at"],
        feature_set_version=features.feature_set_version, feature_names=list(FEATURE_NAMES),
        feature_values=[None if math.isnan(features.values[n]) else float(features.values[n])
                        for n in FEATURE_NAMES],
        missing_features=list(features.missing_features), history_blob=inputs.blob,
        history_sha256=inputs.sha256, context=inputs.context, created_at=now,
    ))
    values = dict(
        prediction_id=prediction_id, user_id=user_id, meal_id=meal_id,
        confirmation_id=confirmation["confirmation_id"], snapshot_id=snapshot_id,
        participant_id=participant_id, model_id=MODEL_ID, model_version=result.model_version,
        feature_set_version=features.feature_set_version,
        artifact_sha256=integrity.get("artifact_sha256") or "",
        source_commit=integrity.get("source_commit"),
        prediction_status=result.prediction_status, prediction_kind=kind,
        inputs_confirmed=inputs_confirmed, personalization_policy=policy,
        personalization=applied, baseline_glucose_mg_dl=float(result.baseline_glucose_mg_dl),
        cohort_deltas=cohort, applied_offsets=offsets, forecast=forecast,
        request_id=request_id, created_at=now,
    )
    try:
        with conn.begin_nested():
            conn.execute(t.predictions.insert().values(**values))
    except IntegrityError:
        # A concurrent request stored the forecast first; return that one.
        stored = _existing(conn, meal_id, confirmation["confirmation_id"])
        if stored is None:
            raise
        return prediction_payload(conn, stored, replayed=True)

    conn.execute(t.response_episodes.insert().values(
        **outcomes.new_episode_values(prediction_id, meal, user_id,
                                      ctx.settings.outcome_grace_minutes)))

    alert = alerts.evaluate_forecast(conn, ctx, user, values,
                                     inputs.context["cgm"]["latest_age_minutes"])
    # Identifiers and policy only. No glucose, no macros.
    logger.info(
        "prediction_created prediction=%s status=%s kind=%s policy=%s calibration=%s "
        "priors=%d retrieval=%s confirmed=%s",
        prediction_id, result.prediction_status, kind, policy, applied["calibration_applied"],
        applied["admissible_prior_meals"], applied["retrieval_active"], inputs_confirmed,
    )
    return prediction_payload(conn, values, replayed=False, alert=alert)


def prediction_payload(conn: Connection, row: dict, *, replayed: bool = False,
                       alert: dict | None = None) -> dict:
    snapshot = conn.execute(
        select(t.feature_snapshots.c.context, t.feature_snapshots.c.missing_features,
               t.feature_snapshots.c.history_sha256)
        .where(t.feature_snapshots.c.snapshot_id == row["snapshot_id"])
    ).mappings().one()
    episode = conn.execute(
        select(t.response_episodes.c.status, t.response_episodes.c.eligible_after)
        .where(t.response_episodes.c.prediction_id == row["prediction_id"])
    ).mappings().first()
    if alert is None:
        stored_alert = conn.execute(
            select(t.alert_events).where(t.alert_events.c.prediction_id == row["prediction_id"])
        ).mappings().first()
        alert = alerts.alert_payload(dict(stored_alert)) if stored_alert else None
    personal = row["personalization"]
    cgm = snapshot["context"].get("cgm", {})
    return {
        "prediction_id": row["prediction_id"],
        "meal_id": row["meal_id"],
        "confirmation_id": row["confirmation_id"],
        "created_at": core.rfc3339(row["created_at"]),
        "replayed": replayed,
        "prediction_kind": row["prediction_kind"],
        "inputs_confirmed": row["inputs_confirmed"],
        "model": {
            "model_id": row["model_id"], "model_version": row["model_version"],
            "feature_set_version": row["feature_set_version"],
            "artifact_sha256": row["artifact_sha256"],
        },
        "personalization": {
            "policy": personal["policy"],
            "consent": personal["consent"],
            "admissible_prior_meals": personal["admissible_prior_meals"],
            "retrieval_active": personal["retrieval_active"],
            "retrieval_min_history": 5,
            "calibration_applied": personal["calibration_applied"],
        },
        "context_quality": {
            "latest_cgm_age_minutes": cgm.get("latest_age_minutes"),
            "cgm_readings_last_60_min": cgm.get("readings_last_60_min"),
            "cgm_device": cgm.get("device"),
            "multi_device_overlap": cgm.get("multi_device_overlap"),
            "missing_features": snapshot["missing_features"],
        },
        "snapshot": {"snapshot_id": row["snapshot_id"], "history_sha256": snapshot["history_sha256"]},
        "outcome_status": None if episode is None else episode["status"],
        "outcome_not_before": None if episode is None else core.rfc3339(episode["eligible_after"]),
        "alert": alert,
        # Exactly the frozen contract v1.0 `ForecastResponse`.
        "forecast": row["forecast"],
    }


def reproduce(conn: Connection, ctx: V2Context, user_id: str, prediction_id: str) -> dict:
    """Re-run the engine on a stored snapshot and compare with the stored forecast."""
    row = conn.execute(
        select(t.predictions).where(and_(
            t.predictions.c.prediction_id == prediction_id, t.predictions.c.user_id == user_id))
    ).mappings().first()
    if row is None:
        raise core.not_found("prediction")
    artifact = _artifact(ctx)
    current_sha = ctx.model_integrity.get("artifact_sha256")
    if current_sha != row["artifact_sha256"]:
        return {"prediction_id": prediction_id, "reproduced": False,
                "reason": "artifact_differs", "stored_artifact_sha256": row["artifact_sha256"],
                "loaded_artifact_sha256": current_sha}

    snapshot = conn.execute(
        select(t.feature_snapshots).where(t.feature_snapshots.c.snapshot_id == row["snapshot_id"])
    ).mappings().one()
    inputs = history.inputs_from_snapshot(snapshot["history_blob"])
    engine_artifact = artifact
    record = row["personalization"].get("calibration")
    if record is not None:
        engine_artifact = personalization.artifact_view(
            artifact, personalization.calibration_from_record(row["participant_id"], record))
    result = _run_engine(engine_artifact, inputs)
    features = build_features(
        history=inputs.history, meal_timestamp=inputs.meal_timestamp,
        composition=inputs.composition, config=artifact.config.features,
        timezone_name=None, time_basis=history.ENGINE_TIME_BASIS, override=inputs.override,
    )
    recomputed = [None if math.isnan(features.values[n]) else float(features.values[n])
                  for n in FEATURE_NAMES]
    stored_forecast = row["forecast"]
    fresh = result.to_contract_dict()
    same_forecast = all(
        fresh[key] == stored_forecast[key]
        for key in ("points", "glucose_at_minutes", "metrics", "drivers", "prediction_status",
                    "model_version", "uncertainty_status")
    )
    same_features = recomputed == list(snapshot["feature_values"])
    return {
        "prediction_id": prediction_id,
        "reproduced": same_forecast and same_features,
        "forecast_identical": same_forecast,
        "features_identical": same_features,
        "feature_count": len(recomputed),
        "artifact_sha256": current_sha,
        "history_sha256": snapshot["history_sha256"],
        "history_sha256_verified": inputs.sha256 == snapshot["history_sha256"],
    }

