"""Canonical typed records for the GlucoTwin data layer (contract v1.0).

Source of authority: ``contracts/canonical_schema_v1.md`` and the record schemas in
``contracts/openapi.yaml``. Field names, units and nullability here must match those
files exactly. Do not rename or add API-facing fields without a contract bump approved
by Ruhaan.

Two families of types live here:

* **API-shaped** (`ParticipantSummary`, `CgmPoint`, `HeartRatePoint`, `MealRecord`,
  `ParticipantHistory`): ``model_dump(mode="json")`` validates against the frozen JSON
  Schemas in ``contracts/schemas/``. No extra fields, because those schemas set
  ``additionalProperties: false``.
* **Canonical storage records** (`Participant`, `CgmRecord`, `HeartRateRecord`,
  `MedicationRecord`, `ActivityRecord`, `IngestionRun`): the full canonical rows,
  including ``data_source`` provenance.

Every datetime is offset-aware and normalized to UTC. Naive datetimes are rejected.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

SCHEMA_VERSION = "1.0"

DataSource = Literal["synthetic_dev", "official", "mixed", "unknown"]
DATA_SOURCES: tuple[str, ...] = ("synthetic_dev", "official", "mixed", "unknown")

# Canonical units. The data layer stores exactly these; conversions happen only in the
# adapter and only when a mapping config declares the source unit explicitly.
UNITS = {
    "glucose_mg_dl": "mg/dL",
    "heart_rate_bpm": "bpm",
    "resting_heart_rate_bpm": "bpm",
    "carbs_g": "g",
    "protein_g": "g",
    "fat_g": "g",
    "fiber_g": "g",
    "calories_kcal": "kcal",
}

MACRO_FIELDS: tuple[str, ...] = ("carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal")


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def _utc_and_finite(cls, value):
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite numeric value")
        return value


# ---------------------------------------------------------------- API-shaped records


class ParticipantSummary(_Record):
    """Row of ``GET /api/participants``."""

    participant_id: str
    display_name: str
    data_source: DataSource
    days_available: float


class CgmPoint(_Record):
    timestamp: AwareDatetime
    glucose_mg_dl: float


class HeartRatePoint(_Record):
    timestamp: AwareDatetime
    heart_rate_bpm: float
    resting_heart_rate_bpm: Optional[float] = None


class MealRecord(_Record):
    meal_id: str
    participant_id: str
    timestamp: AwareDatetime
    carbs_g: Optional[float] = None
    protein_g: Optional[float] = None
    fat_g: Optional[float] = None
    fiber_g: Optional[float] = None
    calories_kcal: Optional[float] = None
    image_ref: Optional[str] = None

    @property
    def has_complete_macros(self) -> bool:
        return all(getattr(self, f) is not None for f in MACRO_FIELDS)


class ParticipantHistory(_Record):
    """Body of ``GET /api/participants/{id}/history``. Arrays ascend by timestamp."""

    participant_id: str
    cgm: list[CgmPoint]
    meals: list[MealRecord]
    heart_rate: list[HeartRatePoint]


# ---------------------------------------------------------- canonical storage records


class Participant(_Record):
    participant_id: str
    display_name: str
    timezone: Optional[str] = None
    data_source: DataSource


class CgmRecord(_Record):
    participant_id: str
    timestamp: AwareDatetime
    glucose_mg_dl: float
    data_source: DataSource

    def to_point(self) -> CgmPoint:
        return CgmPoint(timestamp=self.timestamp, glucose_mg_dl=self.glucose_mg_dl)


class HeartRateRecord(_Record):
    participant_id: str
    timestamp: AwareDatetime
    heart_rate_bpm: float
    resting_heart_rate_bpm: Optional[float] = None
    data_source: DataSource

    def to_point(self) -> HeartRatePoint:
        return HeartRatePoint(
            timestamp=self.timestamp,
            heart_rate_bpm=self.heart_rate_bpm,
            resting_heart_rate_bpm=self.resting_heart_rate_bpm,
        )


class MedicationRecord(_Record):
    id: str
    participant_id: str
    timestamp: AwareDatetime
    medication_class: str
    dose: Optional[float] = None
    dose_unit: Optional[str] = None
    data_source: DataSource


class ActivityRecord(_Record):
    participant_id: str
    timestamp: AwareDatetime
    activity_value: Optional[float] = None
    activity_unit: Optional[str] = None
    data_source: DataSource


class PredictionContext(_Record):
    """Everything observed for one participant at or before ``as_of``.

    Built for prediction-time feature construction: no record in here has a timestamp
    later than ``as_of``. ``lookback_start`` is the inclusive lower bound used, or
    ``None`` when the full history was requested.
    """

    participant_id: str
    as_of: AwareDatetime
    lookback_start: Optional[AwareDatetime] = None
    cgm: list[CgmPoint]
    heart_rate: list[HeartRatePoint]
    meals: list[MealRecord]
    medications: list[MedicationRecord]
    activity: list[ActivityRecord]


class IngestionRun(_Record):
    run_id: str
    source: str
    data_source: DataSource
    schema_version: str
    created_at: AwareDatetime
    records: int
    rejected: int
    warnings: int
    error_summary: Optional[str] = None
    sha256: str
    status: str
    mapping_name: Optional[str] = None
    report_path: Optional[str] = None
