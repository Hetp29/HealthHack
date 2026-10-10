"""Canonical typed domain records.

This is the Python mirror of `contracts/canonical_schema_v1.md`. The data layer
produces these; the ML pipeline and the backend consume them. Keeping one typed
definition means the adapter, the feature builder and the API can never disagree about
field names or units.

Every datetime on these records is offset-aware and normalized to UTC. The
constructors enforce that, because an offset-naive timestamp silently compared against
a meal time is exactly how leakage bugs happen.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

DataSource = Literal["synthetic_dev", "official", "mixed", "unknown"]


class NaiveDatetimeError(ValueError):
    """Raised when a datetime arrives without an explicit UTC offset."""


def require_utc(value: dt.datetime, field_name: str = "timestamp") -> dt.datetime:
    """Return `value` normalized to UTC, rejecting offset-naive input.

    Offset-naive datetimes are rejected rather than assumed to be UTC: an assumption
    here would shift every window boundary by the local offset and quietly corrupt the
    leakage guarantees.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise NaiveDatetimeError(
            f"{field_name} must be offset-aware RFC3339; got naive {value.isoformat()!r}"
        )
    return value.astimezone(dt.timezone.utc)


@dataclass(frozen=True, slots=True)
class CgmPoint:
    """One continuous-glucose-monitor observation."""

    timestamp: dt.datetime
    glucose_mg_dl: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", require_utc(self.timestamp, "cgm.timestamp"))


@dataclass(frozen=True, slots=True)
class HeartRatePoint:
    """One heart-rate observation. Optional modality."""

    timestamp: dt.datetime
    heart_rate_bpm: float
    resting_heart_rate_bpm: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", require_utc(self.timestamp, "heart_rate.timestamp"))


@dataclass(frozen=True, slots=True)
class MealRecord:
    """One meal event.

    Macronutrients are optional at ingestion (a photo-only meal is a valid record) but
    all five are required to request a forecast -- see `has_complete_macros`.
    """

    meal_id: str
    participant_id: str
    timestamp: dt.datetime
    carbs_g: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    calories_kcal: float | None = None
    image_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", require_utc(self.timestamp, "meal.timestamp"))

    @property
    def has_complete_macros(self) -> bool:
        return all(
            value is not None
            for value in (
                self.carbs_g,
                self.protein_g,
                self.fat_g,
                self.fiber_g,
                self.calories_kcal,
            )
        )


@dataclass(frozen=True, slots=True)
class MedicationEvent:
    """One medication administration event. Optional modality.

    A medication class is never mapped to a pharmacodynamic effect. The model uses
    exposure recency only.
    """

    participant_id: str
    timestamp: dt.datetime
    medication_class: str
    dose: float | None = None
    dose_unit: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", require_utc(self.timestamp, "medication.timestamp"))


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """One activity observation. Optional, not required for the MVP."""

    participant_id: str
    timestamp: dt.datetime
    activity_value: float | None = None
    activity_unit: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", require_utc(self.timestamp, "activity.timestamp"))


@dataclass(frozen=True, slots=True)
class ParticipantSummary:
    """Roster entry for one participant."""

    participant_id: str
    display_name: str
    data_source: DataSource
    days_available: float
    timezone: str | None = None


@dataclass(frozen=True, slots=True)
class ParticipantHistory:
    """All observed records for one participant, each list ascending by timestamp.

    An absent optional modality is an empty list, never `None`.
    """

    participant_id: str
    data_source: DataSource
    cgm: list[CgmPoint] = field(default_factory=list)
    meals: list[MealRecord] = field(default_factory=list)
    heart_rate: list[HeartRatePoint] = field(default_factory=list)
    medications: list[MedicationEvent] = field(default_factory=list)
    activity: list[ActivityEvent] = field(default_factory=list)

    def truncated_at(self, cutoff: dt.datetime) -> ParticipantHistory:
        """Return a copy containing only observations at or before `cutoff`.

        This is the single chokepoint that makes prediction-time feature extraction
        leakage-safe: features are built from a truncated history object, so a future
        observation is not merely unused, it is absent.
        """
        cutoff = require_utc(cutoff, "cutoff")
        return ParticipantHistory(
            participant_id=self.participant_id,
            data_source=self.data_source,
            cgm=[point for point in self.cgm if point.timestamp <= cutoff],
            meals=[meal for meal in self.meals if meal.timestamp <= cutoff],
            heart_rate=[point for point in self.heart_rate if point.timestamp <= cutoff],
            medications=[event for event in self.medications if event.timestamp <= cutoff],
            activity=[event for event in self.activity if event.timestamp <= cutoff],
        )

    def cgm_after(self, start: dt.datetime, end: dt.datetime) -> list[CgmPoint]:
        """CGM observations in the half-open-then-closed interval (start, end].

        Used ONLY for target construction, never for features.
        """
        start = require_utc(start, "start")
        end = require_utc(end, "end")
        return [point for point in self.cgm if start < point.timestamp <= end]
