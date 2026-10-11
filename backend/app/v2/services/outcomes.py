"""Score a forecast against what the sensor then measured.

An episode is the 120 minutes after one forecast meal. It is scored once that window
has closed AND a grace period for late sensor uploads has passed. Scoring does three
things and only three:

  1. measures how complete the observed response is;
  2. computes forecast-versus-observed error;
  3. decides whether the episode may serve as personal history for FUTURE forecasts.

It never edits a stored prediction. A forecast is a record of what was said at the
time; an outcome is a separate record about it.

The observed targets are built with `ml.targets.build_targets` -- the same routine
that defined "observed response" when the model was evaluated -- so a live outcome
and a reported evaluation metric mean the same thing.
"""

from __future__ import annotations

import datetime as dt
import math

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import CONSENT_PERSONALIZATION, V2Context, has_consent
from backend.app.v2.services import glucose, meal_provenance
from ml.config import DEFAULT_CONFIG, HORIZONS, HORIZON_WINDOW_MINUTES
from ml.metrics import summarize_observed
from ml.schema import CgmPoint, MealRecord, ParticipantHistory
from ml.targets import build_targets

WINDOW = dt.timedelta(minutes=HORIZON_WINDOW_MINUTES)
#: Keep retrying an episode with missing data for this long after its window closes.
LATE_DATA_HORIZON = dt.timedelta(hours=72)
#: The frozen retrieval rule: a response needs this many native readings to be usable.
MIN_CURVE_POINTS = DEFAULT_CONFIG.features.retrieval_min_curve_points
TARGET_TOLERANCE = dt.timedelta(minutes=DEFAULT_CONFIG.targets.tolerance_minutes)

#: Reasons that stop an episode being used as personal history.
DISQUALIFYING = frozenset({
    "missing_horizon_targets", "insufficient_curve_points", "intervening_meal",
    "sensor_correction_in_window", "quality_flagged_readings", "meal_not_eaten",
    "meal_partially_eaten", "inputs_unconfirmed", "personalization_consent_absent",
    "meal_inputs_revised", "meal_voided", meal_provenance.REASON_HYPOTHETICAL,
})


def new_episode_values(prediction_id: str, meal: dict, user_id: str, grace_minutes: float) -> dict:
    window_end = meal["occurred_at"] + WINDOW
    return {
        "episode_id": core.new_id("epi"), "prediction_id": prediction_id,
        "meal_id": meal["meal_id"], "user_id": user_id, "window_end_at": window_end,
        "eligible_after": window_end + dt.timedelta(minutes=grace_minutes),
        "status": "pending", "eligible_for_personalization": False,
        "ineligibility_reasons": [], "coverage": {}, "observed": {}, "errors": {},
        "score_revision": 0,
    }


def _score(conn: Connection, ctx: V2Context, episode: dict, now: dt.datetime, *, final: bool) -> dict | None:
    """Return the column values for a scored episode, or `None` to leave it pending.

    `final` is False for the early, opportunistic pass made just before a new forecast:
    a complete response is scored immediately, an incomplete one is left pending so
    that late-arriving readings are not prematurely counted as missing.
    """
    user_id = episode["user_id"]
    prediction = conn.execute(
        select(t.predictions).where(t.predictions.c.prediction_id == episode["prediction_id"])
    ).mappings().one()
    meal = conn.execute(
        select(t.meals).where(t.meals.c.meal_id == episode["meal_id"])
    ).mappings().one()
    snapshot_context = conn.execute(
        select(t.feature_snapshots.c.context)
        .where(t.feature_snapshots.c.snapshot_id == prediction["snapshot_id"])
    ).scalar() or {}

    reasons: list[str] = []
    if meal["status"] == "voided":
        return {"status": "voided", "eligible_for_personalization": False,
                "ineligibility_reasons": ["meal_voided"], "scored_at": now,
                "score_revision": episode["score_revision"] + 1}

    if meal_provenance.is_hypothetical(conn, user_id, dict(meal)):
        # A meal entered into a demonstration was never eaten by the recorded
        # participant: there is no observed response to score it against.
        return {"status": "voided", "eligible_for_personalization": False,
                "ineligibility_reasons": [meal_provenance.REASON_HYPOTHETICAL], "scored_at": now,
                "coverage": {}, "observed": {}, "errors": {},
                "score_revision": episode["score_revision"] + 1}

    start = meal["occurred_at"]
    end = start + WINDOW
    device = (snapshot_context.get("cgm") or {}).get("device") or {}
    # Compare against the same sensor the forecast was anchored on.
    rows = [
        r for r in glucose.fetch(conn, user_id, start - TARGET_TOLERANCE, end + TARGET_TOLERANCE,
                                 record_kinds=("cgm",))
        if (r["provider"], r["device_id"]) == (device.get("provider"), device.get("device_id"))
    ]
    in_window = [r for r in rows if start < r["observed_at"] <= end]

    intervening = and_(
        t.meals.c.user_id == user_id, t.meals.c.meal_id != meal["meal_id"],
        t.meals.c.status == "confirmed", t.meals.c.consumption_status != "not_eaten",
        t.meals.c.occurred_at > start, t.meals.c.occurred_at <= end,
    )
    # A hypothetical demonstration meal was not eaten, so it cannot have disturbed
    # this response and must not disqualify it.
    authentic = meal_provenance.authentic_meals_only(conn, user_id)
    if authentic is not None:
        intervening = and_(intervening, authentic)
    others = conn.execute(
        select(t.meals.c.meal_id, t.meals.c.occurred_at).where(intervening)
    ).mappings().all()

    history = ParticipantHistory(
        participant_id=prediction["participant_id"], data_source="unknown",
        cgm=[CgmPoint(timestamp=r["observed_at"], glucose_mg_dl=r["glucose_mg_dl"]) for r in rows],
        meals=[MealRecord(meal_id=o["meal_id"], participant_id=prediction["participant_id"],
                          timestamp=o["occurred_at"]) for o in others],
    )
    baseline = float(prediction["baseline_glucose_mg_dl"])
    targets = build_targets(history, start, baseline, DEFAULT_CONFIG, meal_id=meal["meal_id"])

    missing = [h for h in HORIZONS if h not in targets.absolute]
    if missing:
        if not final:
            return None
        reasons.append("missing_horizon_targets")
    if len(in_window) < MIN_CURVE_POINTS:
        if not final and not missing:
            # Complete at the horizons but thin in between; more may still arrive.
            return None
        reasons.append("insufficient_curve_points")
    if targets.has_overlapping_meal:
        reasons.append("intervening_meal")
    if any(r["correction_count"] > 0 for r in in_window):
        reasons.append("sensor_correction_in_window")
    if any(set(r["quality_flags"]) - {"corrected"} for r in in_window):
        reasons.append("quality_flagged_readings")
    if meal["consumption_status"] == "not_eaten":
        reasons.append("meal_not_eaten")
    elif meal["consumption_status"] == "partial":
        reasons.append("meal_partially_eaten")
    if not prediction["inputs_confirmed"]:
        reasons.append("inputs_unconfirmed")
    if meal["current_confirmation_id"] != prediction["confirmation_id"]:
        reasons.append("meal_inputs_revised")
    if not has_consent(conn, user_id, CONSENT_PERSONALIZATION):
        reasons.append("personalization_consent_absent")

    stamps = [start] + [r["observed_at"] for r in in_window] + [end]
    max_gap = max((b - a).total_seconds() / 60.0 for a, b in zip(stamps, stamps[1:]))
    coverage = {
        "readings_in_window": len(in_window),
        "required_readings": MIN_CURVE_POINTS,
        "horizons_observed": [h for h in HORIZONS if h in targets.absolute],
        "horizons_missing": missing,
        "max_gap_minutes": round(max_gap, 1),
        "device": device or None,
        "intervening_meal_ids": list(targets.overlapping_meal_ids),
    }

    forecast = prediction["forecast"]
    cohort = prediction["cohort_deltas"]
    per_horizon, signed, residuals = {}, [], {}
    for h in HORIZONS:
        if h not in targets.absolute:
            continue
        predicted = float(forecast["glucose_at_minutes"][str(h)])
        observed = targets.absolute[h]
        error = predicted - observed
        signed.append(error)
        per_horizon[str(h)] = {"predicted_mg_dl": predicted, "observed_mg_dl": round(observed, 1),
                               "error_mg_dl": round(error, 1)}
        # observed - cohort prediction, the quantity the frozen calibration averages.
        residuals[str(h)] = targets.delta[h] - float(cohort[str(h)])

    observed_summary = summarize_observed(targets.observed_curve, baseline)
    errors: dict = {"by_horizon": per_horizon, "residual_vs_cohort": residuals,
                    "sign_convention": "error = predicted - observed"}
    if signed:
        errors["mae_mg_dl"] = round(sum(abs(e) for e in signed) / len(signed), 2)
        errors["rmse_mg_dl"] = round(math.sqrt(sum(e * e for e in signed) / len(signed)), 2)
        errors["bias_mg_dl"] = round(sum(signed) / len(signed), 2)
    observed: dict = {
        "baseline_mg_dl": baseline,
        "curve": [{"minute": m, "glucose_mg_dl": round(v, 1)}
                  for m, v in sorted(targets.observed_curve.items())],
    }
    if observed_summary is not None:
        predicted_metrics = forecast["metrics"]
        observed["peak_mg_dl"] = round(observed_summary.peak_mg_dl, 1)
        observed["peak_delta_mg_dl"] = round(observed_summary.peak_delta_mg_dl, 1)
        observed["time_to_peak_minutes"] = observed_summary.time_to_peak_minutes
        errors["peak_error_mg_dl"] = round(predicted_metrics["peak_mg_dl"] - observed_summary.peak_mg_dl, 1)
        errors["time_to_peak_error_minutes"] = (
            predicted_metrics["time_to_peak_minutes"] - observed_summary.time_to_peak_minutes
        )

    status = "insufficient_data" if "missing_horizon_targets" in reasons else "scored"
    return {
        "status": status,
        "eligible_for_personalization": status == "scored" and not (set(reasons) & DISQUALIFYING),
        "ineligibility_reasons": reasons, "coverage": coverage, "observed": observed,
        "errors": errors, "scored_at": now, "score_revision": episode["score_revision"] + 1,
    }


def score_due(conn: Connection, ctx: V2Context, user_id: str | None = None, *,
              include_in_grace: bool = False) -> dict:
    """Score every episode that is ready. Safe to run repeatedly and concurrently
    with ingestion: each run recomputes from the observations stored at that moment.

    `include_in_grace` additionally attempts episodes whose window has closed but
    whose grace period has not; those are scored only if already complete.
    """
    now = ctx.now()
    e = t.response_episodes
    ready = or_(
        and_(e.c.status == "pending", e.c.eligible_after <= now),
        and_(e.c.status == "insufficient_data", e.c.window_end_at >= now - LATE_DATA_HORIZON),
    )
    if include_in_grace:
        ready = or_(ready, and_(e.c.status == "pending", e.c.window_end_at < now))
    query = select(e).where(ready).order_by(e.c.window_end_at)
    if user_id is not None:
        query = query.where(e.c.user_id == user_id)
    else:
        # A global pass runs on the wall clock. A demo account's episodes live on its
        # replay timeline and are scored only by requests made as that account.
        query = query.where(e.c.user_id.not_in(select(t.demo_replay_state.c.user_id)))

    tally = {"examined": 0, "scored": 0, "insufficient_data": 0, "voided": 0, "left_pending": 0}
    for episode in conn.execute(query).mappings().all():
        tally["examined"] += 1
        final = episode["eligible_after"] <= now
        values = _score(conn, ctx, dict(episode), now, final=final)
        if values is None:
            tally["left_pending"] += 1
            continue
        conn.execute(update(e).where(e.c.episode_id == episode["episode_id"]).values(**values))
        tally[values["status"]] += 1
    return tally


def reopen_affected_episodes(conn: Connection, user_id: str, first: dt.datetime, last: dt.datetime) -> int:
    """Send already-judged episodes back for rescoring when their readings change.

    Called by ingestion after late or corrected readings land inside a window that
    was scored (or given up on) earlier.
    """
    e = t.response_episodes
    result = conn.execute(
        update(e).where(and_(
            e.c.user_id == user_id,
            e.c.status.in_(("scored", "insufficient_data")),
            e.c.window_end_at >= first - TARGET_TOLERANCE,
            e.c.window_end_at <= last + WINDOW + TARGET_TOLERANCE,
        )).values(status="pending", eligible_for_personalization=False)
    )
    return result.rowcount or 0


def not_applicable_payload(episode: dict | None, prediction: dict, meal_id: str | None = None) -> dict:
    """The outcome of a hypothetical demonstration meal: explicitly none.

    Distinct from `pending` (an outcome is coming) and from `scored`. No observed
    value, error or coverage is returned, whatever an older episode row may hold.
    """
    episode = episode or {}
    return {
        "episode_id": episode.get("episode_id"),
        "prediction_id": episode.get("prediction_id") or prediction.get("prediction_id"),
        "meal_id": episode.get("meal_id") or meal_id,
        "status": meal_provenance.OUTCOME_NOT_APPLICABLE,
        "reason": meal_provenance.REASON_HYPOTHETICAL,
        "message": ("This meal was entered into a historical demonstration. The recorded "
                    "participant did not eat it, so no observed glucose response exists for it."),
        "window_end_at": None, "scoring_not_before": None, "scored_at": None,
        "score_revision": episode.get("score_revision", 0),
        "eligible_for_personalization": False,
        "ineligibility_reasons": [meal_provenance.REASON_HYPOTHETICAL],
        "coverage": None, "observed": None, "errors": None,
        "predicted": {
            "glucose_at_minutes": prediction["forecast"]["glucose_at_minutes"],
            "metrics": prediction["forecast"]["metrics"],
        },
        "units": {"glucose": "mg/dL", "time": "minutes"},
    }


def episode_payload(episode: dict, prediction: dict) -> dict:
    scored = episode["status"] == "scored"
    return {
        "episode_id": episode["episode_id"],
        "prediction_id": episode["prediction_id"],
        "meal_id": episode["meal_id"],
        "status": episode["status"],
        "window_end_at": core.rfc3339(episode["window_end_at"]),
        "scoring_not_before": core.rfc3339(episode["eligible_after"]),
        "scored_at": core.rfc3339(episode["scored_at"]),
        "score_revision": episode["score_revision"],
        "eligible_for_personalization": episode["eligible_for_personalization"],
        "ineligibility_reasons": episode["ineligibility_reasons"],
        "coverage": episode["coverage"] or None,
        "observed": episode["observed"] if scored or episode["observed"] else None,
        "errors": (
            {k: v for k, v in episode["errors"].items() if k != "residual_vs_cohort"}
            if episode["errors"] else None
        ),
        "predicted": {
            "glucose_at_minutes": prediction["forecast"]["glucose_at_minutes"],
            "metrics": prediction["forecast"]["metrics"],
        },
        "units": {"glucose": "mg/dL", "time": "minutes"},
    }
