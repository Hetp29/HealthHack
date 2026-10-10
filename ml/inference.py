"""Forecast generation: artifact + history + meal -> contract-shaped response.

This is the only place a `ForecastResponse` is constructed, so every guarantee the
contract makes is enforced once, here:

  * exactly 25 points at minutes 0,5,...,120, with minute 0 the MEASURED baseline;
  * `glucose_at_minutes` read out of `points` by exact minute lookup, never recomputed;
  * summary metrics derived from the sampled curve via `ml/metrics.py`;
  * interval bounds `None` while `uncertainty_status` is `not_calibrated`;
  * `limitations` non-empty, with the fallback reason first when personalization did
    not apply;
  * drivers taken from the model's own exact decomposition, or `[]` with an honest note.

Nothing here invents a number. Every value is a measurement, a model output, or an
arithmetic consequence of the two.

CLI, used to hand Neil a fixture produced by a real trained model rather than by hand:

    python -m ml.inference --participant synthetic_p001 --emit-fixture
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from dataclasses import asdict, dataclass, field
from typing import Literal

from ml.config import (
    FORECAST_MINUTES,
    HORIZONS,
    INTERPOLATION_CAVEAT,
    NO_UNCERTAINTY_CAVEAT,
    RESEARCH_DISCLAIMER,
)
from ml.explain import DriverContribution, explain_horizon, peak_horizon
from ml.features import (
    ContextOverride,
    FeatureRow,
    MealComposition,
    build_features,
    rows_to_frame,
)
from ml.metrics import CurveSummary, build_curve, summarize_curve
from ml.schema import ParticipantHistory, ParticipantSummary, require_utc

PredictionStatus = Literal["personalized", "generalized_fallback"]
UncertaintyStatus = Literal["not_calibrated", "empirical_holdout"]

ModelChoice = Literal["personalized", "generalized", "persistence", "cohort_mean_delta"]


@dataclass(frozen=True)
class ForecastPoint:
    minute: int
    glucose_mg_dl: float
    lower_mg_dl: float | None = None
    upper_mg_dl: float | None = None


@dataclass
class ForecastResult:
    """A complete forecast, one-to-one with the API's `ForecastResponse`."""

    participant_id: str
    meal_timestamp: dt.datetime
    model_version: str
    data_source: str
    prediction_status: PredictionStatus
    points: list[ForecastPoint]
    glucose_at_minutes: dict[int, float]
    summary: CurveSummary
    drivers: list[DriverContribution]
    uncertainty_status: UncertaintyStatus
    limitations: list[str]
    #: Not part of the API contract; retained for evaluation and debugging.
    horizon_deltas: dict[int, float] = field(default_factory=dict)
    baseline_glucose_mg_dl: float = 0.0
    driver_horizon_minutes: int | None = None

    def to_contract_dict(self) -> dict:
        """Serialize to exactly the frozen `ForecastResponse` JSON shape."""
        return {
            "participant_id": self.participant_id,
            "meal_timestamp": _rfc3339(self.meal_timestamp),
            "model_version": self.model_version,
            "data_source": self.data_source,
            "prediction_status": self.prediction_status,
            "points": [
                {
                    "minute": point.minute,
                    "glucose_mg_dl": round(point.glucose_mg_dl, 1),
                    "lower_mg_dl": (
                        None if point.lower_mg_dl is None else round(point.lower_mg_dl, 1)
                    ),
                    "upper_mg_dl": (
                        None if point.upper_mg_dl is None else round(point.upper_mg_dl, 1)
                    ),
                }
                for point in self.points
            ],
            "glucose_at_minutes": {
                str(horizon): round(self.glucose_at_minutes[horizon], 1)
                for horizon in HORIZONS
            },
            "metrics": {
                "peak_mg_dl": round(self.summary.peak_mg_dl, 1),
                "peak_delta_mg_dl": round(self.summary.peak_delta_mg_dl, 1),
                "time_to_peak_minutes": int(self.summary.time_to_peak_minutes),
                "incremental_auc_mg_dl_min": round(
                    self.summary.incremental_auc_mg_dl_min, 1
                ),
            },
            "drivers": [asdict(driver) for driver in self.drivers],
            "uncertainty_status": self.uncertainty_status,
            "limitations": list(self.limitations),
        }


def _rfc3339(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def forecast(
    artifact,
    history: ParticipantHistory,
    meal_timestamp: dt.datetime,
    composition: MealComposition,
    *,
    participant: ParticipantSummary | None = None,
    override: ContextOverride | None = None,
    model: ModelChoice = "personalized",
) -> ForecastResult:
    """Produce one forecast.

    Raises `InsufficientPremealContext` when no measured baseline exists, and
    `FeatureError` when the override tries to reach past the meal timestamp. The
    backend maps both to 422.
    """
    meal_timestamp = require_utc(meal_timestamp, "meal_timestamp")
    config = artifact.config

    features: FeatureRow = build_features(
        history=history,
        meal_timestamp=meal_timestamp,
        composition=composition,
        config=config.features,
        timezone_name=participant.timezone if participant else None,
        override=override,
    )
    design = rows_to_frame([features])
    baseline = features.baseline_glucose_mg_dl

    # -- predict horizon deltas --------------------------------------------- #
    if model == "persistence":
        cohort_deltas = {horizon: 0.0 for horizon in HORIZONS}
    elif model == "cohort_mean_delta":
        cohort_deltas = {
            horizon: float(values[0])
            for horizon, values in artifact.cohort_mean.predict_deltas(design).items()
        }
    else:
        cohort_deltas = {
            horizon: float(values[0])
            for horizon, values in artifact.generalized.predict_deltas(design).items()
        }

    calibration = artifact.personalization.calibration_for(history.participant_id)
    apply_personalization = model == "personalized" and calibration.personalized

    horizon_deltas = dict(cohort_deltas)
    if apply_personalization:
        for horizon in HORIZONS:
            horizon_deltas[horizon] += calibration.offset_for(horizon)

    status: PredictionStatus = (
        "personalized" if apply_personalization else "generalized_fallback"
    )

    # -- reconstruct the curve ---------------------------------------------- #
    minutes, values, clipped = build_curve(
        baseline,
        horizon_deltas,
        clip_min=config.model.clip_glucose_min_mg_dl,
        clip_max=config.model.clip_glucose_max_mg_dl,
    )
    points = [
        ForecastPoint(minute=minute, glucose_mg_dl=value, lower_mg_dl=None, upper_mg_dl=None)
        for minute, value in zip(minutes, values)
    ]

    by_minute = {point.minute: point.glucose_mg_dl for point in points}
    glucose_at_minutes = {horizon: by_minute[horizon] for horizon in HORIZONS}
    summary = summarize_curve(minutes, values, baseline)

    # -- drivers ------------------------------------------------------------ #
    drivers: list[DriverContribution] = []
    driver_horizon: int | None = None
    attribution_note: str | None = None

    if model in {"personalized", "generalized"}:
        driver_horizon = peak_horizon(horizon_deltas)
        drivers, _, _ = explain_horizon(
            artifact.generalized,
            design,
            driver_horizon,
            calibration_offset_mg_dl=(
                calibration.offset_for(driver_horizon) if apply_personalization else 0.0
            ),
        )
    else:
        attribution_note = (
            f"The {model} benchmark has no feature attribution, so no drivers are "
            "reported."
        )

    # -- limitations -------------------------------------------------------- #
    limitations: list[str] = []
    if status == "generalized_fallback" and calibration.fallback_reason:
        # First, so a client can surface it prominently.
        limitations.append(calibration.fallback_reason)
    limitations.append(RESEARCH_DISCLAIMER)
    limitations.append(INTERPOLATION_CAVEAT)
    limitations.append(NO_UNCERTAINTY_CAVEAT)

    if driver_horizon is not None:
        limitations.append(
            f"Drivers are reported at the {driver_horizon}-minute horizon, where this "
            "forecast peaks, and describe that horizon only."
        )
    if attribution_note:
        limitations.append(attribution_note)

    if features.missing_features:
        readable = ", ".join(sorted(features.missing_features))
        limitations.append(
            f"These inputs were unavailable for this meal and were filled with "
            f"training-set medians plus an explicit missing-value flag: {readable}."
        )
    if clipped:
        limitations.append(
            "The predicted curve was clipped to the plausible range "
            f"{config.model.clip_glucose_min_mg_dl:g}-"
            f"{config.model.clip_glucose_max_mg_dl:g} mg/dL; the raw model output went "
            "outside it, which indicates an input far from the training distribution."
        )
    if participant is None or not participant.timezone:
        limitations.append(
            "No participant timezone was available, so time-of-day features were "
            "computed in UTC and may be shifted relative to the participant's local day."
        )
    if artifact.data_source == "synthetic_dev":
        limitations.append(
            "Produced by a model trained on synthetic development fixtures, not on "
            "organizer data. No external validity is claimed."
        )

    return ForecastResult(
        participant_id=history.participant_id,
        meal_timestamp=meal_timestamp,
        model_version=artifact.model_version,
        data_source=artifact.data_source,
        prediction_status=status,
        points=points,
        glucose_at_minutes=glucose_at_minutes,
        summary=summary,
        drivers=drivers,
        uncertainty_status="not_calibrated",
        limitations=limitations,
        horizon_deltas=horizon_deltas,
        baseline_glucose_mg_dl=baseline,
        driver_horizon_minutes=driver_horizon,
    )


def forecast_curve_only(
    artifact, baseline_mg_dl: float, horizon_deltas: dict[int, float]
) -> tuple[list[int], list[float], CurveSummary]:
    """Curve + summary from pre-computed deltas, used by the evaluation loop."""
    minutes, values, _ = build_curve(
        baseline_mg_dl,
        horizon_deltas,
        clip_min=artifact.config.model.clip_glucose_min_mg_dl,
        clip_max=artifact.config.model.clip_glucose_max_mg_dl,
    )
    return minutes, values, summarize_curve(minutes, values, baseline_mg_dl)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    from ml.train import build_repository, load_artifact

    parser = argparse.ArgumentParser(
        description="Generate one forecast from a trained GlucoTwin artifact."
    )
    parser.add_argument("--artifact", default="ml/artifacts/model.joblib")
    parser.add_argument("--source", default="synthetic_dev")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--seed", type=int, default=20261011)
    parser.add_argument(
        "--participant", default="synthetic_p001", help="participant_id to forecast for"
    )
    parser.add_argument(
        "--meal-index",
        type=int,
        default=-1,
        help="index into the participant's logged meals; -1 means the last one",
    )
    parser.add_argument(
        "--emit-fixture",
        action="store_true",
        help=(
            "write contracts/examples/predict_response.model_generated.json -- a "
            "fixture produced by this actual model, for frontend binding"
        ),
    )
    args = parser.parse_args(argv)

    artifact = load_artifact(pathlib.Path(args.artifact))
    repository = build_repository(args.source, args.days, args.seed)

    summary = repository.get_participant(args.participant)
    if summary is None:
        raise SystemExit(f"unknown participant {args.participant!r}")
    history = repository.get_history(args.participant)
    if not history.meals:
        raise SystemExit(f"participant {args.participant!r} has no logged meals")

    meal = history.meals[args.meal_index]
    result = forecast(
        artifact,
        history,
        meal.timestamp,
        MealComposition.from_meal_record(meal),
        participant=summary,
    )
    payload = result.to_contract_dict()
    print(json.dumps(payload, indent=2))

    if args.emit_fixture:
        target = pathlib.Path("contracts/examples/predict_response.model_generated.json")
        target.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nwrote {target}", flush=True)

    return 0


if __name__ == "__main__":
    from ml.inference import main as _main

    raise SystemExit(_main())
