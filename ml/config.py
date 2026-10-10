"""Frozen modelling constants for GlucoTwin.

Anything in here that is also visible across the team boundary is fixed by
contracts/openapi.yaml and contracts/canonical_schema_v1.md. Changing a value marked
FROZEN is a contract change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Contract-level constants (FROZEN -- mirrored in contracts/openapi.yaml)
# --------------------------------------------------------------------------- #

CONTRACT_VERSION = "1.0.0"

#: The four horizons the API must report, in minutes after the meal.
HORIZONS: tuple[int, ...] = (30, 60, 90, 120)

#: The forecast grid: exactly 25 points at 0, 5, 10, ..., 120 minutes inclusive.
FORECAST_MINUTES: tuple[int, ...] = tuple(range(0, 121, 5))

#: Total forecast window length in minutes.
HORIZON_WINDOW_MINUTES = 120

#: Version of the feature contract. Inference refuses an artifact whose feature set
#: version does not match the running code, rather than silently mis-ordering columns.
FEATURE_SET_VERSION = "fs-1.0"

#: Model artifact version. Bumped when the trained model's semantics change.
MODEL_VERSION = "v0.1.0"

#: Provenance labels permitted on records and forecasts.
DATA_SOURCES = ("synthetic_dev", "official", "mixed", "unknown")

# --------------------------------------------------------------------------- #
# Standard disclaimer strings (surfaced through the API's `limitations`)
# --------------------------------------------------------------------------- #

RESEARCH_DISCLAIMER = (
    "Research prototype; not medical advice and not for clinical decision-making."
)

INTERPOLATION_CAVEAT = (
    "The model learns glucose change at 30/60/90/120 minutes and the curve between "
    "those knots is linearly interpolated, so the reported peak time is resolved only "
    "to those knots and true peak timing may fall between them."
)

NO_UNCERTAINTY_CAVEAT = (
    "Prediction intervals are not calibrated, so no interval bounds are reported."
)

ATTRIBUTION_CAVEAT = (
    "Drivers are model attributions -- how this model's fitted coefficients combine "
    "this meal's features -- not measured causal physiological effects."
)


# --------------------------------------------------------------------------- #
# Modelling configuration (our implementation choice; tunable, documented)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FeatureConfig:
    """Windows and tolerances used to build prediction-time features.

    Every window looks strictly backwards from the meal timestamp. No field here can
    reference data after the meal; that is enforced in features.py and tested
    adversarially in tests/test_leakage.py.
    """

    #: A baseline CGM reading must exist within this many minutes BEFORE the meal.
    #: Without one there is no measured minute-0 value, and prediction is refused 422.
    baseline_tolerance_minutes: float = 15.0

    #: Window for the pre-meal CGM trend (least-squares slope), in minutes.
    slope_window_minutes: float = 30.0

    #: Window for pre-meal CGM level and variability, in minutes.
    variability_window_minutes: float = 60.0

    #: Window for mean pre-meal heart rate, in minutes.
    hr_window_minutes: float = 30.0

    #: Look-back for "was there a medication event recently", in hours.
    medication_lookback_hours: float = 12.0

    #: Cap on minutes-since-prior-meal, so an overnight fast does not dominate scaling.
    max_minutes_since_prior_meal: float = 720.0

    #: Minimum CGM samples required in a window before a statistic is considered
    #: observed rather than missing.
    min_samples_for_window: int = 2


@dataclass(frozen=True)
class TargetConfig:
    """How supervised targets are sampled from post-meal CGM."""

    #: A target at horizon h uses the CGM observation nearest to t+h within this
    #: tolerance. Beyond it, the target is missing and the reason is recorded.
    tolerance_minutes: float = 10.0

    #: Drop a meal episode unless it has a target at at least this many horizons.
    min_horizons_required: int = 4

    #: Tag episodes with another meal inside (t, t+120]. Whether they are excluded is
    #: an evaluation-time switch, so the effect can be sensitivity-tested.
    tag_overlapping_meals: bool = True


@dataclass(frozen=True)
class ModelConfig:
    """Model family hyperparameters."""

    #: Ridge penalty grid; selected by chronological inner CV on the training fold only.
    ridge_alphas: tuple[float, ...] = (0.1, 1.0, 10.0, 100.0, 1000.0)

    #: Folds for the inner chronological CV used to pick alpha.
    inner_cv_splits: int = 3

    #: Deterministic seed for every stochastic step.
    random_seed: int = 20261011

    #: Clip predicted absolute glucose into a plausible physiological domain.
    #: Clipping is disclosed in `limitations` whenever it actually engages.
    clip_glucose_min_mg_dl: float = 40.0
    clip_glucose_max_mg_dl: float = 400.0


@dataclass(frozen=True)
class PersonalizationConfig:
    """Shrunk per-participant residual calibration (Model 2)."""

    #: Minimum training meal episodes before any personal calibration is applied.
    #: Below this the response is `generalized_fallback`.
    min_episodes: int = 4

    #: Shrinkage constant k in the factor n / (n + k). Larger k means stronger pull
    #: toward the cohort model when personal history is sparse.
    shrinkage_k: float = 6.0

    #: Hard cap on the magnitude of a per-participant offset, in mg/dL, so a single
    #: outlying training meal cannot produce an implausible personal correction.
    max_offset_mg_dl: float = 60.0


@dataclass(frozen=True)
class EvaluationConfig:
    """Chronological, meal-episode-level evaluation protocol."""

    #: Fraction of each participant's LATEST meal episodes held out.
    test_fraction: float = 0.25

    #: Minimum episodes a participant needs to contribute a held-out episode.
    min_episodes_for_split: int = 4

    #: Fraction of participants held out whole, for the cohort_holdout protocol.
    cohort_test_fraction: float = 0.34


@dataclass(frozen=True)
class Config:
    """Top-level configuration bundle."""

    features: FeatureConfig = field(default_factory=FeatureConfig)
    targets: TargetConfig = field(default_factory=TargetConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    personalization: PersonalizationConfig = field(default_factory=PersonalizationConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)


#: Default configuration used by the CLIs and the backend unless overridden.
DEFAULT_CONFIG = Config()
