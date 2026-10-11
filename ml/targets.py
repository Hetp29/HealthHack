"""Supervised target construction and meal-episode assembly.

A *meal episode* is the unit of learning and the unit of evaluation: one meal, its
prediction-time features (strictly pre-meal) and its post-meal CGM targets. Splits are
always by whole episode. Five-minute CGM rows are never split individually, because
rows inside one 120-minute window are not independent -- putting some in train and
some in test leaks the answer.

Targets are modelled as **deltas from the measured pre-meal baseline**, not absolute
glucose. Three reasons:
  1. the persistence baseline (Model 0) becomes exactly "delta = 0", so the comparison
     against it is clean;
  2. the model spends its capacity on the excursion, which is the quantity of interest,
     instead of re-learning each participant's glucose level;
  3. minute 0 of the returned curve stays a measured value by contract, so the curve
     is anchored in observation rather than prediction.

Absolute predictions are recovered as `baseline + predicted delta`.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

from ml.config import HORIZONS, HORIZON_WINDOW_MINUTES, Config
from ml.features import (
    FeatureError,
    FeatureRow,
    MealComposition,
    build_features,
)
from ml.provenance import resolve_time_basis
from ml.schema import MealRecord, ParticipantHistory


@dataclass(frozen=True)
class TargetRow:
    """Observed post-meal response for one meal episode."""

    participant_id: str
    meal_timestamp: dt.datetime
    meal_id: str | None
    baseline_glucose_mg_dl: float
    #: horizon minutes -> observed glucose at that horizon (mg/dL)
    absolute: dict[int, float]
    #: horizon minutes -> observed glucose minus baseline (mg/dL)
    delta: dict[int, float]
    #: Horizons with no usable observation, and why.
    missing_reasons: dict[int, str]
    #: Another meal was logged inside (t, t+120], so the observed response is a blend.
    has_overlapping_meal: bool
    overlapping_meal_ids: tuple[str, ...]
    #: Observed curve on the 5-minute grid, used for peak/iAUC error. Sparse: only
    #: minutes with an observation within tolerance are present.
    observed_curve: dict[int, float] = field(default_factory=dict)

    @property
    def available_horizons(self) -> tuple[int, ...]:
        return tuple(h for h in HORIZONS if h in self.delta)


@dataclass(frozen=True)
class EpisodeContext:
    """How much usable prior history this meal has, and whether it is Day-8 eligible.

    Computed from observations at or before the meal only, so it is itself a
    prediction-time property and may be used to decide inclusion without leaking.
    """

    #: Days between the participant's first CGM reading and this meal.
    prior_history_days: float
    #: Fraction of the nominal 5-min readings present in the required history window.
    prior_history_coverage: float
    #: Meets the full Day-8 rule: enough history, enough coverage, baseline present and
    #: all four horizon targets observed.
    day8_eligible: bool

    def as_dict(self) -> dict:
        return {
            "prior_history_days": round(self.prior_history_days, 3),
            "prior_history_coverage": round(self.prior_history_coverage, 4),
            "day8_eligible": self.day8_eligible,
        }


@dataclass(frozen=True)
class Episode:
    """One meal episode: pre-meal features paired with post-meal targets."""

    features: FeatureRow
    target: TargetRow
    context: EpisodeContext | None = None

    @property
    def participant_id(self) -> str:
        return self.features.participant_id

    @property
    def meal_timestamp(self) -> dt.datetime:
        return self.features.meal_timestamp

    @property
    def meal_id(self) -> str | None:
        return self.features.meal_id


@dataclass
class ExclusionReport:
    """Why candidate meals did not become usable episodes.

    Reported through `GET /api/model/metrics` so that a sample count can always be
    traced back to the meals behind it. Silent dropping of episodes is how an
    evaluation ends up measuring an easier problem than it claims.
    """

    candidate_meals: int = 0
    accepted: int = 0
    reasons: Counter = field(default_factory=Counter)
    overlapping_tagged: int = 0
    #: Accepted episodes that additionally satisfy the Day-8 prior-context rule.
    day8_eligible: int = 0

    def record(self, reason: str) -> None:
        self.reasons[reason] += 1

    def as_dict(self) -> dict:
        return {
            "candidate_meals": self.candidate_meals,
            "accepted": self.accepted,
            "excluded": self.candidate_meals - self.accepted,
            "reasons": dict(sorted(self.reasons.items())),
            "overlapping_tagged": self.overlapping_tagged,
            "day8_eligible": self.day8_eligible,
        }


def _nearest_observation(
    history: ParticipantHistory,
    target_time: dt.datetime,
    tolerance_minutes: float,
) -> tuple[float | None, float]:
    """Return (glucose, offset_minutes) for the CGM reading nearest `target_time`.

    Returns `(None, inf)` when nothing falls inside the tolerance. The nearest reading
    is used rather than an interpolated one so that an observed target is always a real
    measurement.
    """
    best_value: float | None = None
    best_offset = float("inf")
    for point in history.cgm:
        offset = abs((point.timestamp - target_time).total_seconds() / 60.0)
        if offset <= tolerance_minutes and offset < best_offset:
            best_value = float(point.glucose_mg_dl)
            best_offset = offset
    return best_value, best_offset


def build_targets(
    history: ParticipantHistory,
    meal_timestamp: dt.datetime,
    baseline_glucose_mg_dl: float,
    config: Config,
    *,
    meal_id: str | None = None,
) -> TargetRow:
    """Sample post-meal CGM into horizon targets and a sparse observed curve.

    Reads CGM strictly after `meal_timestamp`. This function is for TRAINING and
    EVALUATION only -- never call it on the inference path.
    """
    tolerance = config.targets.tolerance_minutes
    window_end = meal_timestamp + dt.timedelta(minutes=HORIZON_WINDOW_MINUTES)

    absolute: dict[int, float] = {}
    delta: dict[int, float] = {}
    missing_reasons: dict[int, str] = {}

    for horizon in HORIZONS:
        value, _ = _nearest_observation(
            history, meal_timestamp + dt.timedelta(minutes=horizon), tolerance
        )
        if value is None:
            missing_reasons[horizon] = (
                f"no CGM observation within {tolerance:g} min of t+{horizon} min"
            )
            continue
        absolute[horizon] = value
        delta[horizon] = value - baseline_glucose_mg_dl

    # Sparse observed curve on the 5-minute grid, for peak / time-to-peak / iAUC error.
    observed_curve: dict[int, float] = {0: baseline_glucose_mg_dl}
    for minute in range(5, HORIZON_WINDOW_MINUTES + 1, 5):
        value, _ = _nearest_observation(
            history, meal_timestamp + dt.timedelta(minutes=minute), tolerance_minutes=2.5
        )
        if value is not None:
            observed_curve[minute] = value

    # Overlap tagging: another logged meal inside the response window blends two
    # responses, so the "truth" for this episode is partly another meal's.
    overlapping = tuple(
        meal.meal_id
        for meal in history.meals
        if meal_timestamp < meal.timestamp <= window_end
    )

    return TargetRow(
        participant_id=history.participant_id,
        meal_timestamp=meal_timestamp,
        meal_id=meal_id,
        baseline_glucose_mg_dl=baseline_glucose_mg_dl,
        absolute=absolute,
        delta=delta,
        missing_reasons=missing_reasons,
        has_overlapping_meal=bool(overlapping),
        overlapping_meal_ids=overlapping,
        observed_curve=observed_curve,
    )


def _episode_context(
    history: ParticipantHistory,
    meal_timestamp: dt.datetime,
    available_horizons: int,
    config: Config,
) -> EpisodeContext:
    """Measure prior CGM history for one meal and apply the Day-8 rule.

    Reads only observations at or before the meal. `searchsorted` over the already
    sorted timestamps keeps this O(log n) rather than scanning the series.
    """
    day8 = config.day8
    window = dt.timedelta(days=day8.history_days)
    start = meal_timestamp - window

    timestamps = [point.timestamp for point in history.cgm]
    if not timestamps:
        return EpisodeContext(0.0, 0.0, False)

    import bisect

    lo = bisect.bisect_left(timestamps, start)
    hi = bisect.bisect_right(timestamps, meal_timestamp)
    in_window = max(0, hi - lo)

    expected = window.total_seconds() / 60.0 / day8.nominal_cadence_minutes
    coverage = in_window / expected if expected > 0 else 0.0
    prior_days = (meal_timestamp - timestamps[0]).total_seconds() / 86400.0

    eligible = (
        prior_days >= day8.history_days
        and coverage >= day8.min_history_coverage
        and available_horizons >= config.targets.min_horizons_required
    )
    return EpisodeContext(prior_days, coverage, eligible)


def build_episode(
    history: ParticipantHistory,
    meal: MealRecord,
    config: Config,
    *,
    timezone_name: str | None = None,
    time_basis: str = "utc",
) -> Episode:
    """Assemble one episode. Raises `FeatureError` when the meal is not usable."""
    features = build_features(
        history=history,
        meal_timestamp=meal.timestamp,
        composition=MealComposition.from_meal_record(meal),
        config=config.features,
        meal_id=meal.meal_id,
        timezone_name=timezone_name,
        time_basis=time_basis,
    )
    target = build_targets(
        history=history,
        meal_timestamp=meal.timestamp,
        baseline_glucose_mg_dl=features.baseline_glucose_mg_dl,
        config=config,
        meal_id=meal.meal_id,
    )
    context = _episode_context(
        history, meal.timestamp, len(target.available_horizons), config
    )
    return Episode(features=features, target=target, context=context)


def build_episodes(
    repository,
    config: Config,
    *,
    participant_ids: list[str] | None = None,
    exclude_overlapping: bool = False,
) -> tuple[list[Episode], ExclusionReport]:
    """Build every usable meal episode across participants, with an exclusion report.

    Episodes are returned sorted by (meal_timestamp, meal_id) so that downstream
    chronological splits are deterministic.
    """
    report = ExclusionReport()
    episodes: list[Episode] = []

    summaries = repository.list_participants()
    if participant_ids is not None:
        wanted = set(participant_ids)
        summaries = [s for s in summaries if s.participant_id in wanted]

    for summary in summaries:
        # What the participant's timestamps mean (ruling D3). Resolved once per
        # participant from the repository, not assumed.
        time_basis, timezone_name = resolve_time_basis(
            repository, summary.participant_id, summary.timezone
        )
        history = repository.get_history(summary.participant_id)
        for meal in history.meals:
            report.candidate_meals += 1

            if not meal.has_complete_macros:
                report.record("incomplete_macros")
                continue

            try:
                episode = build_episode(
                    history,
                    meal,
                    config,
                    timezone_name=timezone_name,
                    time_basis=time_basis,
                )
            except FeatureError as error:
                reason = type(error).__name__
                report.record(
                    "insufficient_premeal_context"
                    if reason == "InsufficientPremealContext"
                    else "feature_error"
                )
                continue

            if len(episode.target.available_horizons) < config.targets.min_horizons_required:
                report.record("insufficient_target_coverage")
                continue

            if episode.target.has_overlapping_meal:
                report.overlapping_tagged += 1
                if exclude_overlapping:
                    report.record("overlapping_meal_in_window")
                    continue

            episodes.append(episode)
            report.accepted += 1
            if episode.context is not None and episode.context.day8_eligible:
                report.day8_eligible += 1

    episodes.sort(key=lambda e: (e.meal_timestamp, e.meal_id or ""))
    return episodes, report
