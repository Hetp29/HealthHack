"""Typed request and response models for contract v1.0.

One-to-one with `contracts/openapi.yaml`. Every model sets `extra="forbid"`, mirroring
`additionalProperties: false`, so a client that sends an unexpected field gets a 422
rather than having it silently dropped -- which is how a contract drifts in practice.

Datetimes use `AwareDatetime`, so an offset-naive value fails validation and is
translated to `422 naive_datetime` by `backend/app/errors.py`.

Response models exist even though `ml/inference.py` already emits contract-shaped
dicts. Validating those dicts through these models on the way out means a
contract-shape regression in the ML layer surfaces as a server-side 500 in tests
rather than as malformed JSON in Neil's frontend, and it gives FastAPI the schema it
needs for its own generated docs.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

DataSource = Literal["synthetic_dev", "official", "mixed", "unknown"]
PredictionStatus = Literal["personalized", "generalized_fallback"]
UncertaintyStatus = Literal["not_calibrated", "empirical_holdout"]

#: Macronutrient grams: non-negative and bounded, so a typo cannot drive the model
#: far outside its training distribution without being rejected.
Grams = Annotated[float, Field(ge=0, le=1000)]
FiberGrams = Annotated[float, Field(ge=0, le=200)]
Kcal = Annotated[float, Field(ge=0, le=10000)]
ParticipantId = Annotated[str, Field(min_length=1, max_length=128)]


class Strict(BaseModel):
    """Base: forbid unknown fields, matching `additionalProperties: false`.

    `protected_namespaces=()` is required because the frozen contract uses
    `model_version` and `model_name`, which collide with Pydantic's reserved
    `model_` prefix. The contract field names win; Pydantic's warning is the
    thing that gives way.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())


# --------------------------------------------------------------------------- #
# system
# --------------------------------------------------------------------------- #


class HealthResponse(Strict):
    status: Literal["ok"] = "ok"
    version: str


class ErrorResponse(Strict):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# participants
# --------------------------------------------------------------------------- #


class ParticipantSummaryModel(Strict):
    participant_id: str
    display_name: str
    data_source: DataSource
    days_available: float = Field(ge=0)


class ParticipantsResponse(Strict):
    participants: list[ParticipantSummaryModel]


class CgmPointModel(Strict):
    timestamp: AwareDatetime
    glucose_mg_dl: float = Field(ge=0)


class HeartRatePointModel(Strict):
    timestamp: AwareDatetime
    heart_rate_bpm: float = Field(ge=0)
    resting_heart_rate_bpm: float | None = Field(default=None, ge=0)


class MealRecordModel(Strict):
    meal_id: str = Field(min_length=1)
    participant_id: str = Field(min_length=1)
    timestamp: AwareDatetime
    carbs_g: float | None = Field(default=None, ge=0)
    protein_g: float | None = Field(default=None, ge=0)
    fat_g: float | None = Field(default=None, ge=0)
    fiber_g: float | None = Field(default=None, ge=0)
    calories_kcal: float | None = Field(default=None, ge=0)
    image_ref: str | None = None


class HistoryResponse(Strict):
    participant_id: str
    cgm: list[CgmPointModel]
    meals: list[MealRecordModel]
    heart_rate: list[HeartRatePointModel]


# --------------------------------------------------------------------------- #
# prediction
# --------------------------------------------------------------------------- #


class MealInput(Strict):
    """All five macros are required: the model consumes them directly."""

    carbs_g: Grams
    protein_g: Grams
    fat_g: Grams
    fiber_g: FiberGrams
    calories_kcal: Kcal


class ContextOverrideModel(Strict):
    """Caller-supplied pre-meal context. Every member optional, defaulting to null.

    `as_of` must be at or before `meal_timestamp`; the cross-field check lives on
    `PredictRequest` because it needs both values.
    """

    premeal_glucose_mg_dl: float | None = Field(default=None, ge=20, le=600)
    recent_heart_rate_bpm: float | None = Field(default=None, ge=20, le=250)
    minutes_since_prior_meal: float | None = Field(default=None, ge=0, le=1440)
    as_of: AwareDatetime | None = None


class _PredictBase(Strict):
    meal_timestamp: AwareDatetime
    meal: MealInput
    context_override: ContextOverrideModel | None = None

    def model_post_init(self, _context: Any) -> None:
        override = self.context_override
        if override is None or override.as_of is None:
            return
        as_of = override.as_of.astimezone(dt.timezone.utc)
        meal_time = self.meal_timestamp.astimezone(dt.timezone.utc)
        if as_of > meal_time:
            # Raised as a plain ValueError so Pydantic reports it as a validation
            # failure; the handler maps the message to `future_context_override`.
            raise ValueError(
                "context_override.as_of must be at or before meal_timestamp; a "
                "forecast may never consume post-meal information"
            )


class PredictRequest(_PredictBase):
    participant_id: ParticipantId


class CompareRequest(_PredictBase):
    participant_ids: list[ParticipantId] = Field(min_length=2, max_length=2)


class ForecastPointModel(Strict):
    minute: int = Field(ge=0, le=120)
    glucose_mg_dl: float
    lower_mg_dl: float | None = None
    upper_mg_dl: float | None = None


class GlucoseAtMinutes(Strict):
    """Derived read-out of the four required horizons. Keys are strings by contract."""

    model_config = ConfigDict(
        extra="forbid", protected_namespaces=(), populate_by_name=True
    )

    h30: float = Field(alias="30")
    h60: float = Field(alias="60")
    h90: float = Field(alias="90")
    h120: float = Field(alias="120")


class ForecastMetricsModel(Strict):
    peak_mg_dl: float
    peak_delta_mg_dl: float
    time_to_peak_minutes: int = Field(ge=0, le=120)
    incremental_auc_mg_dl_min: float = Field(ge=0)


class DriverModel(Strict):
    feature: str
    contribution_mg_dl: float
    explanation: str


class ForecastResponse(Strict):
    participant_id: str
    meal_timestamp: AwareDatetime
    model_version: str
    data_source: DataSource
    prediction_status: PredictionStatus
    points: list[ForecastPointModel] = Field(min_length=25, max_length=25)
    glucose_at_minutes: GlucoseAtMinutes
    metrics: ForecastMetricsModel
    drivers: list[DriverModel]
    uncertainty_status: UncertaintyStatus
    limitations: list[str] = Field(min_length=1)

    @field_validator("points")
    @classmethod
    def _points_are_the_frozen_grid(
        cls, points: list[ForecastPointModel]
    ) -> list[ForecastPointModel]:
        minutes = [point.minute for point in points]
        expected = list(range(0, 121, 5))
        if minutes != expected:
            raise ValueError(
                "points must be exactly minutes 0,5,...,120 in ascending order"
            )
        return points

    @field_validator("limitations")
    @classmethod
    def _limitations_non_empty(cls, limitations: list[str]) -> list[str]:
        if not any(entry.strip() for entry in limitations):
            raise ValueError("limitations must carry at least one non-empty caveat")
        return limitations


class CompareResponse(Strict):
    comparison_id: str
    forecasts: list[ForecastResponse] = Field(min_length=2, max_length=2)
    caveat: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# model metadata
# --------------------------------------------------------------------------- #


class HorizonMetricModel(Strict):
    horizon_minutes: Literal[30, 60, 90, 120]
    n: int = Field(ge=0)
    mae_mg_dl: float | None = Field(default=None, ge=0)
    rmse_mg_dl: float | None = Field(default=None, ge=0)
    interval_coverage: float | None = Field(default=None, ge=0, le=1)


class CurveMetricModel(Strict):
    n: int = Field(ge=0)
    peak_mae_mg_dl: float | None = Field(default=None, ge=0)
    peak_delta_mae_mg_dl: float | None = Field(default=None, ge=0)
    time_to_peak_mae_minutes: float | None = Field(default=None, ge=0)
    iauc_mae_mg_dl_min: float | None = Field(default=None, ge=0)


class ModelMetricBlock(Strict):
    model_name: str
    overall_mae_mg_dl: float | None = Field(default=None, ge=0)
    overall_rmse_mg_dl: float | None = Field(default=None, ge=0)
    by_horizon: list[HorizonMetricModel]
    curve: CurveMetricModel


class ParticipantSampleCount(Strict):
    participant_id: str
    train_episodes: int = Field(ge=0)
    test_episodes: int = Field(ge=0)
    personalized: bool


class SampleCounts(Strict):
    participants: int = Field(ge=0)
    train_episodes: int = Field(ge=0)
    test_episodes: int = Field(ge=0)
    excluded_episodes: int | None = Field(default=None, ge=0)
    by_participant: list[ParticipantSampleCount]


class ParticipantComparisonRow(Strict):
    participant_id: str
    n: int = Field(ge=0)
    generalized_mae_mg_dl: float | None = None
    personalized_mae_mg_dl: float | None = None
    delta_mae_mg_dl: float | None = None


class PersonalizationComparison(Strict):
    participants_improved: int = Field(ge=0)
    participants_worsened: int = Field(ge=0)
    participants_unchanged: int = Field(ge=0)
    by_participant: list[ParticipantComparisonRow]


class EvaluationProtocol(Strict):
    name: Literal["within_person_chronological", "cohort_holdout"]
    description: str
    split_unit: Literal["meal_episode"]
    test_fraction: float = Field(ge=0, le=1)
    evaluated_at: AwareDatetime
    excluded_overlapping_meals: bool | None = None


class MetricsResponse(Strict):
    dataset_source: DataSource
    evaluation_protocol: EvaluationProtocol
    by_model: list[ModelMetricBlock]
    by_horizon: list[HorizonMetricModel]
    sample_counts: SampleCounts
    personalization: PersonalizationComparison | None = None
    limitations: list[str] = Field(min_length=1)


class ModelInfoResponse(Strict):
    model_version: str
    trained_at: AwareDatetime | None = None
    data_source: DataSource
    feature_set_version: str
    metrics_available: bool
    limitations: list[str] = Field(min_length=1)
