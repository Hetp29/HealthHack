"""Adversarial leakage tests.

The central scientific claim of this project is that a forecast for a meal at time `t`
uses only information available at `t`. These tests attack that claim directly: they
inject extreme post-meal observations and assert the feature vector does not move.

A passing suite here is what makes the reported metrics meaningful. If any of these
fail, every number in the validation report is suspect.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from ml.config import DEFAULT_CONFIG
from ml.devfixture import SyntheticDevRepository
from ml.evaluate import (
    LeakageError,
    _assert_no_leakage,
    split_cohort_holdout,
    split_within_person,
)
from ml.features import (
    ContextOverride,
    FeatureError,
    MealComposition,
    build_features,
)
from ml.schema import CgmPoint, HeartRatePoint, MealRecord, ParticipantHistory
from ml.targets import build_episodes

MEAL_TIME = dt.datetime(2026, 10, 4, 12, 30, tzinfo=dt.timezone.utc)
COMPOSITION = MealComposition(
    carbs_g=55.0, protein_g=22.0, fat_g=16.0, fiber_g=7.0, calories_kcal=460.0
)


def _history(
    cgm: list[CgmPoint],
    meals: list[MealRecord] | None = None,
    heart_rate: list[HeartRatePoint] | None = None,
) -> ParticipantHistory:
    return ParticipantHistory(
        participant_id="p_test",
        data_source="synthetic_dev",
        cgm=sorted(cgm, key=lambda point: point.timestamp),
        meals=sorted(meals or [], key=lambda meal: meal.timestamp),
        heart_rate=sorted(heart_rate or [], key=lambda point: point.timestamp),
    )


def _premeal_cgm() -> list[CgmPoint]:
    """Twelve readings over the hour before the meal, rising gently."""
    return [
        CgmPoint(MEAL_TIME - dt.timedelta(minutes=minute), 100.0 + 0.05 * (60 - minute))
        for minute in range(60, -1, -5)
    ]


def _build(history: ParticipantHistory, **kwargs):
    return build_features(
        history=history,
        meal_timestamp=MEAL_TIME,
        composition=COMPOSITION,
        config=DEFAULT_CONFIG.features,
        timezone_name="UTC",
        **kwargs,
    )


class TestFeaturesIgnoreTheFuture:
    def test_extreme_post_meal_cgm_does_not_change_any_feature(self):
        """The canonical attack: bury the answer in the history and check it is unused."""
        clean = _build(_history(_premeal_cgm()))

        poisoned_cgm = _premeal_cgm() + [
            CgmPoint(MEAL_TIME + dt.timedelta(minutes=minute), 400.0)
            for minute in range(5, 125, 5)
        ]
        poisoned = _build(_history(poisoned_cgm))

        assert poisoned.values == clean.values
        assert poisoned.baseline_glucose_mg_dl == clean.baseline_glucose_mg_dl

    def test_a_reading_exactly_at_the_meal_timestamp_is_allowed(self):
        """`<= t` is the rule, so the reading at t itself is legitimate context."""
        with_at_t = _build(_history(_premeal_cgm() + [CgmPoint(MEAL_TIME, 123.0)]))
        assert with_at_t.baseline_glucose_mg_dl == 123.0

    def test_a_reading_one_second_after_the_meal_is_excluded(self):
        boundary = _build(
            _history(
                _premeal_cgm()
                + [CgmPoint(MEAL_TIME + dt.timedelta(seconds=1), 400.0)]
            )
        )
        assert boundary.baseline_glucose_mg_dl != 400.0

    def test_future_meals_do_not_affect_prior_meal_features(self):
        future_meal = MealRecord(
            meal_id="future",
            participant_id="p_test",
            timestamp=MEAL_TIME + dt.timedelta(minutes=30),
            carbs_g=999.0,
            protein_g=1.0,
            fat_g=1.0,
            fiber_g=1.0,
            calories_kcal=4000.0,
        )
        past_meal = MealRecord(
            meal_id="past",
            participant_id="p_test",
            timestamp=MEAL_TIME - dt.timedelta(hours=5),
            carbs_g=40.0,
            protein_g=10.0,
            fat_g=10.0,
            fiber_g=4.0,
            calories_kcal=300.0,
        )
        row = _build(_history(_premeal_cgm(), meals=[past_meal, future_meal]))
        assert row.values["prior_meal_carbs_g"] == 40.0
        assert row.values["minutes_since_prior_meal"] == pytest.approx(300.0)

    def test_the_meal_being_forecast_is_not_its_own_prior_meal(self):
        """Scoring a logged meal must not treat that meal as the preceding one."""
        this_meal = MealRecord(
            meal_id="this",
            participant_id="p_test",
            timestamp=MEAL_TIME,
            carbs_g=55.0,
            protein_g=22.0,
            fat_g=16.0,
            fiber_g=7.0,
            calories_kcal=460.0,
        )
        row = _build(_history(_premeal_cgm(), meals=[this_meal]))
        # No strictly-earlier meal exists, so the prior-meal features must be missing.
        assert "minutes_since_prior_meal" in row.missing_features
        assert "prior_meal_carbs_g" in row.missing_features

    def test_future_heart_rate_does_not_change_the_hr_feature(self):
        premeal_hr = [
            HeartRatePoint(MEAL_TIME - dt.timedelta(minutes=m), 70.0, 60.0)
            for m in range(30, -1, -5)
        ]
        clean = _build(_history(_premeal_cgm(), heart_rate=premeal_hr))
        poisoned_hr = premeal_hr + [
            HeartRatePoint(MEAL_TIME + dt.timedelta(minutes=m), 180.0, 60.0)
            for m in range(5, 125, 5)
        ]
        poisoned = _build(_history(_premeal_cgm(), heart_rate=poisoned_hr))
        assert poisoned.values["hr_mean_30min_bpm"] == clean.values["hr_mean_30min_bpm"]

    def test_truncation_is_what_enforces_this(self):
        """The guarantee is structural: the truncated history has no future rows."""
        history = _history(
            _premeal_cgm()
            + [CgmPoint(MEAL_TIME + dt.timedelta(minutes=30), 400.0)]
        )
        truncated = history.truncated_at(MEAL_TIME)
        assert all(point.timestamp <= MEAL_TIME for point in truncated.cgm)
        assert len(truncated.cgm) == len(history.cgm) - 1


class TestContextOverrideCannotReachForward:
    def test_as_of_after_the_meal_is_rejected(self):
        with pytest.raises(FeatureError, match="later than meal_timestamp"):
            _build(
                _history(_premeal_cgm()),
                override=ContextOverride(
                    premeal_glucose_mg_dl=400.0,
                    as_of=MEAL_TIME + dt.timedelta(minutes=1),
                ),
            )

    def test_as_of_at_the_meal_timestamp_is_accepted(self):
        row = _build(
            _history(_premeal_cgm()),
            override=ContextOverride(premeal_glucose_mg_dl=112.0, as_of=MEAL_TIME),
        )
        assert row.baseline_glucose_mg_dl == 112.0

    def test_override_with_no_as_of_is_accepted(self):
        row = _build(
            _history(_premeal_cgm()),
            override=ContextOverride(premeal_glucose_mg_dl=90.0),
        )
        assert row.baseline_glucose_mg_dl == 90.0


class TestBaselineIsMeasuredNotInvented:
    def test_a_stale_cgm_reading_is_refused_rather_than_used(self):
        stale = [CgmPoint(MEAL_TIME - dt.timedelta(minutes=45), 100.0)]
        with pytest.raises(Exception) as excinfo:
            _build(_history(stale))
        assert "no CGM observation within" in str(excinfo.value)

    def test_no_cgm_at_all_is_refused(self):
        with pytest.raises(Exception) as excinfo:
            _build(_history([]))
        assert "no CGM observation within" in str(excinfo.value)

    def test_a_reading_inside_tolerance_is_used(self):
        fresh = [CgmPoint(MEAL_TIME - dt.timedelta(minutes=10), 108.0)]
        assert _build(_history(fresh)).baseline_glucose_mg_dl == 108.0


class TestNaiveDatetimesAreRejected:
    def test_naive_meal_timestamp_is_rejected(self):
        with pytest.raises(Exception) as excinfo:
            build_features(
                history=_history(_premeal_cgm()),
                meal_timestamp=dt.datetime(2026, 10, 4, 12, 30),
                composition=COMPOSITION,
                config=DEFAULT_CONFIG.features,
            )
        assert "offset-aware" in str(excinfo.value)

    def test_naive_cgm_timestamp_is_rejected_at_construction(self):
        with pytest.raises(Exception) as excinfo:
            CgmPoint(dt.datetime(2026, 10, 4, 12, 0), 100.0)
        assert "offset-aware" in str(excinfo.value)


class TestSplitGuarantees:
    @pytest.fixture(scope="class")
    def episodes(self):
        episodes, _ = build_episodes(SyntheticDevRepository(), DEFAULT_CONFIG)
        return episodes

    def test_within_person_split_is_strictly_chronological(self, episodes):
        train, test = split_within_person(episodes, DEFAULT_CONFIG)
        latest_train: dict[str, dt.datetime] = {}
        for episode in train:
            current = latest_train.get(episode.participant_id)
            if current is None or episode.meal_timestamp > current:
                latest_train[episode.participant_id] = episode.meal_timestamp
        for episode in test:
            assert episode.meal_timestamp > latest_train[episode.participant_id]

    def test_within_person_split_passes_its_own_assertion(self, episodes):
        train, test = split_within_person(episodes, DEFAULT_CONFIG)
        _assert_no_leakage("within_person_chronological", train, test)

    def test_cohort_split_shares_no_participants(self, episodes):
        train, test = split_cohort_holdout(episodes, DEFAULT_CONFIG)
        assert {e.participant_id for e in train} & {e.participant_id for e in test} == set()
        _assert_no_leakage("cohort_holdout", train, test)

    def test_the_leakage_assertion_actually_fires(self, episodes):
        """A guard that cannot fail is not a guard. Hand it a bad split."""
        train, test = split_within_person(episodes, DEFAULT_CONFIG)
        # Swap one episode across the boundary so a test meal precedes a train meal.
        bad_train = train + [test[-1]]
        with pytest.raises(LeakageError):
            _assert_no_leakage("within_person_chronological", bad_train, test)

    def test_cohort_assertion_fires_on_shared_participant(self, episodes):
        train, test = split_cohort_holdout(episodes, DEFAULT_CONFIG)
        with pytest.raises(LeakageError, match="both folds"):
            _assert_no_leakage("cohort_holdout", train + test[:1], test)

    def test_every_participant_keeps_at_least_one_training_episode(self, episodes):
        train, test = split_within_person(episodes, DEFAULT_CONFIG)
        train_ids = {e.participant_id for e in train}
        for episode in test:
            assert episode.participant_id in train_ids

    def test_sparse_participant_contributes_no_test_episodes(self, episodes):
        """Below `min_episodes_for_split` a participant cannot be scored held-out."""
        config = dataclasses.replace(
            DEFAULT_CONFIG,
            evaluation=dataclasses.replace(
                DEFAULT_CONFIG.evaluation, min_episodes_for_split=4
            ),
        )
        train, test = split_within_person(episodes, config)
        sparse = "synthetic_p006"
        assert any(e.participant_id == sparse for e in train)
        assert not any(e.participant_id == sparse for e in test)
