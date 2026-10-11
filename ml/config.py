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
FEATURE_SET_VERSION = "fs-1.3"

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

#: Historical note: a constant asserting that a UTC fallback degraded the circadian
#: features used to live here. It was removed under ruling D3 -- for the organizer
#: datasets the stored clock IS the participant's local wall clock, so time of day is
#: exact. `ml/provenance.describe_time_basis` now produces the accurate caveat.
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

    #: Shrinkage constant for the historical-response fingerprint (fs-1.2), matching
    #: the participant-calibration k so both personal corrections are damped alike.
    fingerprint_shrinkage_k: float = 3.0

    #: fs-1.3 retrieval: how many of the participant's most similar earlier meals to
    #: retrieve. k=5 was selected on development validation (research/advanced/), never
    #: on the reserved reference set.
    retrieval_k: int = 5

    #: Minimum native CGM grid points an earlier meal's response needs before it may be
    #: retrieved. Below this the curve is not characterized well enough to average.
    retrieval_min_curve_points: int = 20

    #: INSUFFICIENT-HISTORY FALLBACK. Minimum retrievable earlier meals before the
    #: retrieval features are emitted at all. Below it they are left missing, so the
    #: model falls back to the generalized + fingerprint prediction.
    #:
    #: This exists because the few-shot breakdown showed retrieval HURTS when history
    #: is very thin: averaging 1-2 "most similar" meals is noisier than the plain
    #: fingerprint mean over everything. Threshold selected on development folds.
    retrieval_min_history: int = 5


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
    #:
    #: Selected as 3.0 on rolling purged DEVELOPMENT validation (docs/experiments/
    #: personalization.json), never on the held-out test set. The sweep was monotone
    #: -- k=3: 24.96, k=6: 25.12, k=12: 25.44, k=20: 25.81 mg/dL MAE -- which is what
    #: you expect when participants already have ~20 training episodes, so the raw
    #: mean residual is reasonably well estimated and heavy shrinkage only discards
    #: real signal. The k=3 gain over k=6 is small (+0.16 mg/dL, participant-clustered
    #: 95% CI [+0.06, +0.26]) but it harmed no participant by more than 0.37 mg/dL
    #: while helping 6 by more than 0.5.
    shrinkage_k: float = 3.0

    #: Hard cap on the magnitude of a per-participant offset, in mg/dL, so a single
    #: outlying training meal cannot produce an implausible personal correction.
    max_offset_mg_dl: float = 60.0


@dataclass(frozen=True)
class Day8Config:
    """The Day-8 held-out inclusion rule (ruling D1).

    The challenge frames the task as "seven days of history, then forecast a novel
    Day-8 meal". The primary result must therefore be measured on meals that genuinely
    have that much prior context -- not on any meal that merely happens to have a
    baseline reading.

    Thresholds mirror `data/eligibility.py` so our counts are directly comparable with
    the data owner's audit, but the inclusion rule itself is owned here.
    """

    #: Days of prior CGM history a meal needs to qualify as a Day-8 episode.
    history_days: int = 7

    #: Required fraction of the nominal 5-minute readings present in that window.
    #: CGMacros has real sensor gaps, so demanding 100% would discard good episodes.
    min_history_coverage: float = 0.70

    #: Nominal CGM cadence used to compute the expected reading count.
    nominal_cadence_minutes: float = 5.0

    #: PURGE/EMBARGO. A training episode at t' owns the response window (t', t'+120].
    #: If t' + 120 > first test meal time, the two windows share CGM observations and
    #: the training target partially contains the held-out answer. Training episodes
    #: within this many minutes of the test cutoff are therefore dropped entirely.
    embargo_minutes: float = 120.0

    #: Share of each participant's training pool held back, as its LATEST slice, for
    #: regularization selection. Participant-aware and chronological within person,
    #: and never the final test set (ruling: "do not tune on the final test set").
    validation_fraction: float = 0.20

    #: Exclude episodes with another meal inside the 120-minute window from the TEST
    #: set. Their observed response blends two meals, so an error measured against it
    #: is not attributable to the meal being forecast. They are kept in training,
    #: tagged, and both counts are reported.
    exclude_overlap_from_test: bool = True


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
    day8: Day8Config = field(default_factory=Day8Config)


#: Default configuration used by the CLIs and the backend unless overridden.
DEFAULT_CONFIG = Config()
