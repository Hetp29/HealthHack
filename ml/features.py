"""Prediction-time feature extraction.

The one rule this module exists to enforce: **a feature for a meal at time `t` may
only depend on observations at or before `t`.**

It is enforced structurally rather than by convention. `build_features` begins by
calling `ParticipantHistory.truncated_at(meal_timestamp)`, so post-meal observations
are not merely unused -- they are absent from the object the extractors see. A bug in
a window calculation therefore cannot reach forward in time; the worst it can do is
use too little history. `tests/test_leakage.py` attacks this adversarially by
appending extreme post-meal CGM values and asserting the feature vector is unchanged.

Missing optional features are returned as `NaN`. Imputation is deliberately NOT done
here: it is a fitted transformation and belongs inside the training pipeline so that
the fold boundary is respected (`ml/train.py`). A feature that is missing here stays
missing until a pipeline fitted on training data fills it.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml.config import FEATURE_SET_VERSION, FeatureConfig
from ml.schema import MealRecord, ParticipantHistory, require_utc

# --------------------------------------------------------------------------- #
# The feature contract
# --------------------------------------------------------------------------- #

#: Ordered, frozen feature list. The order is part of FEATURE_SET_VERSION; inference
#: refuses an artifact whose recorded version differs, rather than risking a silent
#: column permutation.
FEATURE_NAMES: tuple[str, ...] = (
    # -- pre-meal glucose context (CGM at or before t) --
    "premeal_glucose_mg_dl",
    "cgm_slope_30min_mg_dl_per_min",
    "cgm_mean_60min_mg_dl",
    "cgm_std_60min_mg_dl",
    "minutes_since_last_cgm",
    # -- meal composition (known at t by definition) --
    "carbs_g",
    "protein_g",
    "fat_g",
    "fiber_g",
    "calories_kcal",
    # NOTE: deliberately NO derived combinations such as `carbs - fiber` or
    # `fat + protein`. For a linear model those are exact linear combinations of
    # columns already present: they add zero information, and they make the
    # coefficient attribution in ml/explain.py arbitrary, because Ridge splits the
    # shared signal across collinear columns and the split can carry a
    # counterintuitive sign. If a tree-based family is ever justified by held-out
    # performance, interaction terms belong there, not here.
    # -- timing --
    "tod_sin",
    "tod_cos",
    "minutes_since_prior_meal",
    "prior_meal_carbs_g",
    # -- wearable context (optional modality) --
    # Mean pre-meal heart rate only. `hr - resting_hr` is NOT included: resting heart
    # rate is a near-constant participant trait, so the difference is mostly a
    # participant-identity proxy. Letting the cohort model read participant identity
    # would blur the generalized-vs-personalized comparison that the whole evaluation
    # rests on, and would flatter the cohort_holdout protocol. Stable per-person
    # offsets are the personalization layer's job (ml/personalization.py).
    "hr_mean_30min_bpm",
    # -- medication exposure (presence/recency only, never an efficacy assumption) --
    "medication_exposure_recent",
)

#: Features that may legitimately be absent. These are median-imputed by the fitted
#: pipeline, which also adds a binary missing-indicator column for each.
OPTIONAL_FEATURES: frozenset[str] = frozenset(
    {
        "cgm_slope_30min_mg_dl_per_min",
        "cgm_mean_60min_mg_dl",
        "cgm_std_60min_mg_dl",
        "minutes_since_prior_meal",
        "prior_meal_carbs_g",
        "hr_mean_30min_bpm",
        # Time of day is unknowable for a real-UTC dataset with no recorded timezone.
        # It is left missing in that case rather than guessed -- see ruling D3 and
        # `ml/provenance.py`.
        "tod_sin",
        "tod_cos",
    }
)

#: Human-readable labels used in driver explanations.
FEATURE_LABELS: dict[str, str] = {
    "premeal_glucose_mg_dl": "pre-meal glucose",
    "cgm_slope_30min_mg_dl_per_min": "pre-meal glucose trend (30 min)",
    "cgm_mean_60min_mg_dl": "mean glucose (prior 60 min)",
    "cgm_std_60min_mg_dl": "glucose variability (prior 60 min)",
    "minutes_since_last_cgm": "CGM reading age",
    "carbs_g": "carbohydrate",
    "protein_g": "protein",
    "fat_g": "fat",
    "fiber_g": "fiber",
    "calories_kcal": "calories",
    "tod_sin": "time of day (cyclic)",
    "tod_cos": "time of day (cyclic)",
    "minutes_since_prior_meal": "time since previous meal",
    "prior_meal_carbs_g": "previous meal carbohydrate",
    "hr_mean_30min_bpm": "mean heart rate (prior 30 min)",
    "medication_exposure_recent": "recent medication exposure",
}


class FeatureError(ValueError):
    """Base class for feature-construction failures."""


class InsufficientPremealContext(FeatureError):
    """No CGM observation close enough before the meal to establish a baseline.

    Surfaces as `422 insufficient_premeal_context`. Deliberately an error rather than
    an imputed value: minute 0 of the forecast is a *measured* reading by contract, so
    inventing it would silently fabricate the curve's anchor.
    """


@dataclass(frozen=True)
class MealComposition:
    """The macronutrient content of the meal being forecast.

    Separate from `MealRecord` because a forecast request supplies composition without
    a stored meal: `POST /api/predict` can score a hypothetical Day-8 meal that was
    never logged.
    """

    carbs_g: float
    protein_g: float
    fat_g: float
    fiber_g: float
    calories_kcal: float

    @classmethod
    def from_meal_record(cls, meal: MealRecord) -> MealComposition:
        if not meal.has_complete_macros:
            raise FeatureError(
                f"meal {meal.meal_id!r} is missing macronutrients; a forecast requires "
                "all of carbs/protein/fat/fiber/calories"
            )
        return cls(
            carbs_g=float(meal.carbs_g),          # type: ignore[arg-type]
            protein_g=float(meal.protein_g),      # type: ignore[arg-type]
            fat_g=float(meal.fat_g),              # type: ignore[arg-type]
            fiber_g=float(meal.fiber_g),          # type: ignore[arg-type]
            calories_kcal=float(meal.calories_kcal),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class ContextOverride:
    """Caller-supplied pre-meal context, mirroring the API's `context_override`.

    `as_of` must be at or before the meal timestamp; the caller-facing guard lives in
    the backend, and `build_features` asserts it again so the ML layer is safe when
    driven directly from a script.
    """

    premeal_glucose_mg_dl: float | None = None
    recent_heart_rate_bpm: float | None = None
    minutes_since_prior_meal: float | None = None
    as_of: dt.datetime | None = None


@dataclass(frozen=True)
class FeatureRow:
    """One extracted feature vector plus the provenance needed to explain it."""

    participant_id: str
    meal_timestamp: dt.datetime
    meal_id: str | None
    values: dict[str, float]
    #: Measured pre-meal glucose. Minute 0 of the forecast curve, by contract.
    baseline_glucose_mg_dl: float
    #: Features that were unavailable and left as NaN for the pipeline to impute.
    missing_features: tuple[str, ...]
    feature_set_version: str = FEATURE_SET_VERSION

    def to_series(self) -> pd.Series:
        return pd.Series(
            {name: self.values[name] for name in FEATURE_NAMES}, dtype="float64"
        )


# --------------------------------------------------------------------------- #
# Window helpers -- every one looks strictly backwards
# --------------------------------------------------------------------------- #


def _window_values(
    points: list,
    cutoff: dt.datetime,
    minutes: float,
    attribute: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (elapsed_minutes_before_cutoff, values) for points in [cutoff-minutes, cutoff].

    `elapsed` is negative-going time measured in minutes relative to `cutoff`, so a
    least-squares slope over it is in units per minute of real time.
    """
    start = cutoff - dt.timedelta(minutes=minutes)
    times: list[float] = []
    values: list[float] = []
    for point in points:
        if start <= point.timestamp <= cutoff:
            times.append((point.timestamp - cutoff).total_seconds() / 60.0)
            values.append(float(getattr(point, attribute)))
    return np.asarray(times, dtype="float64"), np.asarray(values, dtype="float64")


def _least_squares_slope(times: np.ndarray, values: np.ndarray) -> float:
    """Slope of values against time, in value-units per minute."""
    if times.size < 2:
        return math.nan
    spread = times - times.mean()
    denominator = float(np.sum(spread**2))
    if denominator <= 0.0:
        # All observations share a timestamp; a slope is undefined, not zero.
        return math.nan
    return float(np.sum(spread * (values - values.mean())) / denominator)


def _minutes_of_day(
    timestamp: dt.datetime, time_basis: str, tz_name: str | None
) -> float | None:
    """Minutes since the participant's LOCAL midnight, or `None` when unknowable.

    Delegates to the data layer's `local_minutes_of_day`, which is the single
    authority on what a canonical timestamp means (ruling D3):

    * `source_local_unknown_tz` -- the stored value encodes the source's local wall
      clock under a `+00:00` label, so its clock reading IS the local time of day.
      Read it directly; converting would corrupt it.
    * `utc` with a known IANA timezone -- convert, then read the clock.
    * `utc` with no timezone -- the local time of day is genuinely unknown. Return
      `None` so the caller records the feature as missing. Guessing UTC here would
      silently shift every circadian feature by the participant's true offset.

    The previous implementation did the last case wrong in a way that mattered: it
    assumed UTC and described that as a mild limitation. For the organizer datasets
    the assumption happened to be harmless (their stored clock already IS local), but
    the reasoning was inverted, so the code was right by accident rather than by
    construction.
    """
    if timestamp.tzinfo is None:
        raise FeatureError("meal_timestamp must be offset-aware")
    try:
        from data.timeutil import local_minutes_of_day
    except Exception:  # pragma: no cover - data layer always present in this repo
        # Degrade to the documented `utc` semantics rather than failing a forecast.
        if time_basis == "utc" and not tz_name:
            return None
        local = timestamp
        if tz_name:
            try:
                from zoneinfo import ZoneInfo

                local = timestamp.astimezone(ZoneInfo(tz_name))
            except Exception:
                return None
        return local.hour * 60.0 + local.minute + local.second / 60.0

    try:
        return local_minutes_of_day(timestamp, time_basis, tz_name)
    except Exception as error:
        raise FeatureError(f"cannot establish local time of day: {error}") from error


# --------------------------------------------------------------------------- #
# Feature construction
# --------------------------------------------------------------------------- #


def build_features(
    history: ParticipantHistory,
    meal_timestamp: dt.datetime,
    composition: MealComposition,
    config: FeatureConfig,
    *,
    meal_id: str | None = None,
    timezone_name: str | None = None,
    time_basis: str = "utc",
    override: ContextOverride | None = None,
) -> FeatureRow:
    """Extract the prediction-time feature vector for one meal.

    Raises `InsufficientPremealContext` when no CGM reading exists within
    `config.baseline_tolerance_minutes` before the meal.
    """
    meal_timestamp = require_utc(meal_timestamp, "meal_timestamp")

    if override is not None and override.as_of is not None:
        as_of = require_utc(override.as_of, "context_override.as_of")
        if as_of > meal_timestamp:
            raise FeatureError(
                "context_override.as_of is later than meal_timestamp; a forecast may "
                "never consume post-meal information"
            )

    # THE leakage chokepoint. Everything below sees only observations <= meal_timestamp.
    past = history.truncated_at(meal_timestamp)

    values: dict[str, float] = {}

    # -- baseline glucose (required) ------------------------------------------ #
    baseline: float | None = None
    minutes_since_last_cgm = math.nan
    if past.cgm:
        last = past.cgm[-1]
        age_minutes = (meal_timestamp - last.timestamp).total_seconds() / 60.0
        if age_minutes <= config.baseline_tolerance_minutes:
            baseline = float(last.glucose_mg_dl)
            minutes_since_last_cgm = age_minutes

    if override is not None and override.premeal_glucose_mg_dl is not None:
        baseline = float(override.premeal_glucose_mg_dl)
        # An overridden baseline is current by construction.
        minutes_since_last_cgm = 0.0

    if baseline is None:
        raise InsufficientPremealContext(
            "no CGM observation within "
            f"{config.baseline_tolerance_minutes:g} minutes before {meal_timestamp.isoformat()}"
        )

    values["premeal_glucose_mg_dl"] = baseline
    values["minutes_since_last_cgm"] = minutes_since_last_cgm

    # -- pre-meal CGM trend and variability ----------------------------------- #
    slope_times, slope_values = _window_values(
        past.cgm, meal_timestamp, config.slope_window_minutes, "glucose_mg_dl"
    )
    values["cgm_slope_30min_mg_dl_per_min"] = (
        _least_squares_slope(slope_times, slope_values)
        if slope_times.size >= config.min_samples_for_window
        else math.nan
    )

    _, var_values = _window_values(
        past.cgm, meal_timestamp, config.variability_window_minutes, "glucose_mg_dl"
    )
    if var_values.size >= config.min_samples_for_window:
        values["cgm_mean_60min_mg_dl"] = float(var_values.mean())
        values["cgm_std_60min_mg_dl"] = float(var_values.std(ddof=1))
    else:
        values["cgm_mean_60min_mg_dl"] = math.nan
        values["cgm_std_60min_mg_dl"] = math.nan

    # -- meal composition ----------------------------------------------------- #
    values["carbs_g"] = composition.carbs_g
    values["protein_g"] = composition.protein_g
    values["fat_g"] = composition.fat_g
    values["fiber_g"] = composition.fiber_g
    values["calories_kcal"] = composition.calories_kcal

    # -- timing --------------------------------------------------------------- #
    minutes_of_day = _minutes_of_day(meal_timestamp, time_basis, timezone_name)
    if minutes_of_day is None:
        # Unknowable local clock: record missing, let the fitted imputer handle it.
        values["tod_sin"] = math.nan
        values["tod_cos"] = math.nan
    else:
        angle = 2.0 * math.pi * minutes_of_day / 1440.0
        values["tod_sin"] = math.sin(angle)
        values["tod_cos"] = math.cos(angle)

    # Strictly earlier meals only: `<` excludes the meal being forecast, which appears
    # in `past` when scoring a logged meal.
    prior_meals = [meal for meal in past.meals if meal.timestamp < meal_timestamp]
    if prior_meals:
        prior = prior_meals[-1]
        gap = (meal_timestamp - prior.timestamp).total_seconds() / 60.0
        values["minutes_since_prior_meal"] = min(gap, config.max_minutes_since_prior_meal)
        values["prior_meal_carbs_g"] = (
            float(prior.carbs_g) if prior.carbs_g is not None else math.nan
        )
    else:
        values["minutes_since_prior_meal"] = math.nan
        values["prior_meal_carbs_g"] = math.nan

    if override is not None and override.minutes_since_prior_meal is not None:
        values["minutes_since_prior_meal"] = min(
            float(override.minutes_since_prior_meal), config.max_minutes_since_prior_meal
        )

    # -- heart rate (optional modality) --------------------------------------- #
    _, hr_values = _window_values(
        past.heart_rate, meal_timestamp, config.hr_window_minutes, "heart_rate_bpm"
    )
    hr_mean = float(hr_values.mean()) if hr_values.size >= 1 else math.nan
    if override is not None and override.recent_heart_rate_bpm is not None:
        hr_mean = float(override.recent_heart_rate_bpm)
    values["hr_mean_30min_bpm"] = hr_mean


    # -- medication exposure -------------------------------------------------- #
    # Presence and recency only. A medication class is never mapped to an effect.
    lookback_start = meal_timestamp - dt.timedelta(hours=config.medication_lookback_hours)
    recent_medication = any(
        lookback_start <= event.timestamp <= meal_timestamp for event in past.medications
    )
    values["medication_exposure_recent"] = 1.0 if recent_medication else 0.0

    missing = tuple(
        name for name in FEATURE_NAMES if math.isnan(values[name])
    )
    unexpected = [name for name in missing if name not in OPTIONAL_FEATURES]
    if unexpected:
        raise FeatureError(
            f"required features are NaN and cannot be imputed: {unexpected}"
        )

    return FeatureRow(
        participant_id=history.participant_id,
        meal_timestamp=meal_timestamp,
        meal_id=meal_id,
        values={name: values[name] for name in FEATURE_NAMES},
        baseline_glucose_mg_dl=baseline,
        missing_features=missing,
    )


def rows_to_frame(rows: list[FeatureRow]) -> pd.DataFrame:
    """Stack feature rows into a DataFrame with the frozen column order.

    A DataFrame (rather than a bare array) is used throughout so that scikit-learn
    carries feature names into the fitted pipeline, which is what makes the driver
    attribution in `ml/explain.py` able to name columns correctly even after the
    imputer appends missing-indicator columns.
    """
    if not rows:
        return pd.DataFrame(columns=list(FEATURE_NAMES), dtype="float64")
    frame = pd.DataFrame(
        [[row.values[name] for name in FEATURE_NAMES] for row in rows],
        columns=list(FEATURE_NAMES),
        dtype="float64",
    )
    return frame
