"""Prediction orchestration and model/metrics lifecycle.

Holds the loaded artifact and the measured evaluation report for the process, and
translates ML-layer exceptions into the API's error envelope.

Design decisions worth stating:

**The artifact loads lazily and failure is not fatal.** A missing or stale artifact
leaves the service serving `/health` and `/api/model/info` so an operator can see
*why* prediction is unavailable, while `/api/predict` returns `503 model_not_trained`.
Crashing at startup would turn a recoverable "you haven't run `ml.train` yet" into a
dead deployment.

**Metrics are read from disk, never synthesized.** When `metrics.json` is absent,
`GET /api/model/metrics` returns an explicitly empty report whose `limitations` say so.
There is no code path that estimates, interpolates or defaults a metric value.

**History is fetched once and truncation happens in the ML layer.** The service hands
`ml.inference.forecast` the full history object; `build_features` truncates at the meal
timestamp. Truncating here as well would duplicate the invariant in two places, and the
one that matters is the one next to the feature code.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import pathlib
import uuid

from backend.app import errors
from backend.app.config import Settings
from backend.app.services.data_access import (
    DataLayerError,
    DataRepository,
    build_repository,
)
from ml.config import (
    FEATURE_SET_VERSION,
    HORIZONS,
    NO_UNCERTAINTY_CAVEAT,
    RESEARCH_DISCLAIMER,
)
from ml.features import (
    ContextOverride,
    FeatureError,
    InsufficientPremealContext,
    MealComposition,
)
from ml.inference import ForecastResult, forecast

logger = logging.getLogger("glucotwin.prediction")

COMPARE_CAVEAT = (
    "These two forecasts show how the same meal is modelled differently for two "
    "people, using each person's own pre-meal context and calibration. They are "
    "model outputs on research data, not a dietary recommendation, not a ranking of "
    "participants, and not a statement about either person's health."
)


class PredictionService:
    """Everything `/api/predict`, `/api/compare` and `/api/model/*` need."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._artifact = None
        self._artifact_error: str | None = None
        self._repository: DataRepository | None = None
        self._repository_error: str | None = None
        self._metrics: dict | None = None
        self._metrics_error: str | None = None

    # -- lifecycle ---------------------------------------------------------- #

    def load(self) -> None:
        """Attempt to bring up every dependency, recording failures rather than raising."""
        self._load_repository()
        self._load_artifact()
        self._load_metrics()

    def _load_repository(self) -> None:
        try:
            self._repository = build_repository(self.settings)
            self._repository_error = None
            logger.info("data_layer_ready backend=%s", self.settings.data_backend)
        except DataLayerError as error:
            self._repository = None
            self._repository_error = str(error)
            logger.error("data_layer_unavailable backend=%s", self.settings.data_backend)

    def _load_artifact(self) -> None:
        path = self.settings.model_artifact
        if not path.exists():
            self._artifact = None
            self._artifact_error = f"artifact not found at {path}"
            logger.warning("model_artifact_missing path=%s", path)
            return
        try:
            from ml.train import load_artifact

            self._artifact = load_artifact(path)
            self._artifact_error = None
            logger.info(
                "model_loaded version=%s feature_set=%s data_source=%s",
                self._artifact.model_version,
                self._artifact.feature_set_version,
                self._artifact.data_source,
            )
        except Exception as error:
            # A stale or corrupt artifact must not be served: its coefficients may
            # correspond to a different column layout.
            self._artifact = None
            self._artifact_error = f"{type(error).__name__}: {error}"
            logger.error("model_load_failed path=%s error=%s", path, error)

    def _load_metrics(self) -> None:
        path = self.settings.metrics_path
        if not path.exists():
            self._metrics = None
            self._metrics_error = f"metrics report not found at {path}"
            logger.info("metrics_missing path=%s", path)
            return
        try:
            self._metrics = json.loads(path.read_text())
            self._metrics_error = None
            logger.info("metrics_loaded path=%s", path)
        except Exception as error:
            self._metrics = None
            self._metrics_error = f"{type(error).__name__}: {error}"
            logger.error("metrics_load_failed path=%s error=%s", path, error)

    # -- accessors ---------------------------------------------------------- #

    @property
    def model_ready(self) -> bool:
        return self._artifact is not None

    @property
    def metrics_available(self) -> bool:
        return bool(self._metrics and self._metrics.get("by_model"))

    def repository(self) -> DataRepository:
        if self._repository is None:
            raise errors.data_layer_unavailable(
                self.settings.data_backend, self._repository_error or "not initialized"
            )
        return self._repository

    def artifact(self):
        if self._artifact is None:
            raise errors.model_not_trained(
                str(self.settings.model_artifact), self._artifact_error
            )
        return self._artifact

    def require_participant(self, participant_id: str):
        """Resolve a participant or raise `404 participant_not_found`."""
        summary = self.repository().get_participant(participant_id)
        if summary is None:
            raise errors.participant_not_found(participant_id)
        return summary

    # -- prediction --------------------------------------------------------- #

    def predict(
        self,
        participant_id: str,
        meal_timestamp: dt.datetime,
        meal: dict,
        override: dict | None = None,
    ) -> ForecastResult:
        """Produce one forecast, mapping ML failures onto contract error codes.

        Order matters: the participant is resolved before the model is required, so an
        unknown id returns 404 rather than being masked by a 503 on an untrained model.
        """
        summary = self.require_participant(participant_id)
        artifact = self.artifact()
        history = self.repository().get_history(participant_id)

        composition = MealComposition(
            carbs_g=float(meal["carbs_g"]),
            protein_g=float(meal["protein_g"]),
            fat_g=float(meal["fat_g"]),
            fiber_g=float(meal["fiber_g"]),
            calories_kcal=float(meal["calories_kcal"]),
        )
        context = None
        if override:
            context = ContextOverride(
                premeal_glucose_mg_dl=override.get("premeal_glucose_mg_dl"),
                recent_heart_rate_bpm=override.get("recent_heart_rate_bpm"),
                minutes_since_prior_meal=override.get("minutes_since_prior_meal"),
                as_of=override.get("as_of"),
            )

        try:
            result = forecast(
                artifact,
                history,
                meal_timestamp,
                composition,
                participant=summary,
                override=context,
            )
        except InsufficientPremealContext as error:
            raise errors.ApiError(
                422,
                errors.INSUFFICIENT_PREMEAL_CONTEXT,
                "No CGM observation close enough before the meal to establish a "
                "measured baseline, so minute 0 of the forecast cannot be anchored.",
                {
                    "participant_id": participant_id,
                    "meal_timestamp": meal_timestamp.isoformat(),
                    "baseline_tolerance_minutes": (
                        artifact.config.features.baseline_tolerance_minutes
                    ),
                    "reason": str(error),
                },
            ) from error
        except FeatureError as error:
            code = (
                errors.FUTURE_CONTEXT_OVERRIDE
                if "later than meal_timestamp" in str(error)
                else errors.INVALID_REQUEST
            )
            raise errors.ApiError(
                422, code, str(error), {"participant_id": participant_id}
            ) from error

        logger.info(
            "prediction participant=%s status=%s model=%s peak=%.1f ttp=%d",
            participant_id,
            result.prediction_status,
            artifact.model_version,
            result.summary.peak_mg_dl,
            result.summary.time_to_peak_minutes,
        )
        return result

    def compare(
        self,
        participant_ids: list[str],
        meal_timestamp: dt.datetime,
        meal: dict,
        override: dict | None = None,
    ) -> dict:
        """Two forecasts for the same meal, plus the mandatory caveat."""
        forecasts = [
            self.predict(participant_id, meal_timestamp, meal, override).to_contract_dict()
            for participant_id in participant_ids
        ]
        return {
            "comparison_id": f"cmp_{uuid.uuid4().hex[:12]}",
            "forecasts": forecasts,
            "caveat": COMPARE_CAVEAT,
        }

    # -- model metadata ----------------------------------------------------- #

    def model_info(self) -> dict:
        """Provenance and readiness. Returns a 200-shaped payload even with no artifact."""
        if self._artifact is None:
            limitations = [
                "No trained model artifact is loaded, so prediction is unavailable. "
                "Run `python -m ml.train --source synthetic_dev`.",
                RESEARCH_DISCLAIMER,
            ]
            if self._artifact_error:
                limitations.append(f"Artifact load status: {self._artifact_error}.")
            return {
                "model_version": "none",
                "trained_at": None,
                "data_source": "unknown",
                "feature_set_version": FEATURE_SET_VERSION,
                "metrics_available": False,
                "limitations": limitations,
            }

        artifact = self._artifact
        limitations = [RESEARCH_DISCLAIMER, NO_UNCERTAINTY_CAVEAT]
        if artifact.data_source == "synthetic_dev":
            limitations.append(
                "Trained on synthetic development fixtures, not on organizer data. "
                "No external validity is claimed."
            )
        if not self.metrics_available:
            limitations.append(
                "No evaluation report is loaded, so no validated performance metrics "
                "are available. Run `python -m ml.evaluate --protocol all`."
            )
        personalized = sum(
            1
            for calibration in artifact.personalization.calibrations.values()
            if calibration.personalized
        )
        total = len(artifact.personalization.calibrations)
        if total:
            limitations.append(
                f"{personalized} of {total} training participants met the minimum "
                f"{artifact.config.personalization.min_episodes}-episode threshold for "
                "personal calibration; the rest receive the generalized cohort model."
            )

        return {
            "model_version": artifact.model_version,
            "trained_at": artifact.trained_at,
            "data_source": artifact.data_source,
            "feature_set_version": artifact.feature_set_version,
            "metrics_available": self.metrics_available,
            "limitations": limitations,
        }

    def metrics(self) -> dict:
        """The measured evaluation report, or an explicitly empty one."""
        if self._metrics is not None:
            return self._metrics

        limitations = [
            "No evaluation has been run against this artifact, so no validated "
            "metrics exist.",
            "Run `python -m ml.evaluate --protocol all` to populate this report.",
            RESEARCH_DISCLAIMER,
        ]
        if self._metrics_error:
            limitations.append(f"Metrics load status: {self._metrics_error}.")

        return {
            "dataset_source": (
                self._artifact.data_source if self._artifact else "unknown"
            ),
            "evaluation_protocol": {
                "name": "within_person_chronological",
                "description": (
                    "No evaluation has been run. This block describes the protocol that "
                    "WOULD be used: per participant, meal episodes are ordered by "
                    "timestamp, the earliest train and the latest are held out, split by "
                    "whole meal episode."
                ),
                "split_unit": "meal_episode",
                "test_fraction": 0.25,
                "evaluated_at": dt.datetime.now(dt.timezone.utc),
                "excluded_overlapping_meals": None,
            },
            "by_model": [],
            "by_horizon": [],
            "sample_counts": {
                "participants": 0,
                "train_episodes": 0,
                "test_episodes": 0,
                "excluded_episodes": None,
                "by_participant": [],
            },
            "limitations": limitations,
        }

    def history_payload(
        self,
        participant_id: str,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
    ) -> dict:
        """Observed history for one participant, in contract shape."""
        self.require_participant(participant_id)
        history = self.repository().get_history(participant_id, start, end)
        return {
            "participant_id": participant_id,
            "cgm": [
                {"timestamp": point.timestamp, "glucose_mg_dl": point.glucose_mg_dl}
                for point in history.cgm
            ],
            "meals": [
                {
                    "meal_id": meal.meal_id,
                    "participant_id": meal.participant_id,
                    "timestamp": meal.timestamp,
                    "carbs_g": meal.carbs_g,
                    "protein_g": meal.protein_g,
                    "fat_g": meal.fat_g,
                    "fiber_g": meal.fiber_g,
                    "calories_kcal": meal.calories_kcal,
                    "image_ref": meal.image_ref,
                }
                for meal in history.meals
            ],
            "heart_rate": [
                {
                    "timestamp": point.timestamp,
                    "heart_rate_bpm": point.heart_rate_bpm,
                    "resting_heart_rate_bpm": point.resting_heart_rate_bpm,
                }
                for point in history.heart_rate
            ],
        }

    def participants_payload(self) -> dict:
        return {
            "participants": [
                {
                    "participant_id": summary.participant_id,
                    "display_name": summary.display_name,
                    "data_source": summary.data_source,
                    "days_available": summary.days_available,
                }
                for summary in self.repository().list_participants()
            ]
        }
