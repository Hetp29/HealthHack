"""Target construction, overlap tagging and episode assembly."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devfixture import SyntheticDevRepository
from ml.schema import CgmPoint, MealRecord, ParticipantHistory
from ml.targets import build_episodes, build_targets

MEAL_TIME = dt.datetime(2026, 10, 4, 12, 30, tzinfo=dt.timezone.utc)


def _meal(offset_minutes: float, meal_id: str = "m") -> MealRecord:
    return MealRecord(
        meal_id=meal_id,
        participant_id="p_test",
        timestamp=MEAL_TIME + dt.timedelta(minutes=offset_minutes),
        carbs_g=50.0,
        protein_g=20.0,
        fat_g=15.0,
        fiber_g=5.0,
        calories_kcal=400.0,
    )


def _dense_cgm(baseline: float = 100.0, rise_per_minute: float = 0.25) -> list[CgmPoint]:
    """A clean 5-minute series spanning one hour before to two hours after the meal."""
    points = []
    for minute in range(-60, 125, 5):
        value = baseline + max(0.0, minute) * rise_per_minute
        points.append(CgmPoint(MEAL_TIME + dt.timedelta(minutes=minute), value))
    return points


def _history(cgm, meals=None) -> ParticipantHistory:
    return ParticipantHistory(
        participant_id="p_test",
        data_source="synthetic_dev",
        cgm=cgm,
        meals=sorted(meals or [], key=lambda meal: meal.timestamp),
    )


class TestHorizonTargets:
    def test_targets_are_sampled_at_each_horizon(self):
        target = build_targets(_history(_dense_cgm()), MEAL_TIME, 100.0, DEFAULT_CONFIG)
        assert target.available_horizons == HORIZONS
        # +0.25 mg/dL per minute means the delta at h minutes is 0.25*h.
        for horizon in HORIZONS:
            assert target.delta[horizon] == pytest.approx(0.25 * horizon)

    def test_delta_is_measured_against_the_supplied_baseline(self):
        target = build_targets(_history(_dense_cgm()), MEAL_TIME, 90.0, DEFAULT_CONFIG)
        assert target.absolute[60] == pytest.approx(115.0)
        assert target.delta[60] == pytest.approx(25.0)

    def test_a_gap_at_one_horizon_records_a_reason(self):
        cgm = [
            point
            for point in _dense_cgm()
            # Remove everything near t+60 so that horizon cannot be sampled.
            if not (
                MEAL_TIME + dt.timedelta(minutes=50)
                <= point.timestamp
                <= MEAL_TIME + dt.timedelta(minutes=70)
            )
        ]
        target = build_targets(_history(cgm), MEAL_TIME, 100.0, DEFAULT_CONFIG)
        assert 60 not in target.delta
        assert 60 in target.missing_reasons
        assert "no CGM observation within" in target.missing_reasons[60]
        assert target.available_horizons == (30, 90, 120)

    def test_readings_beyond_tolerance_are_not_used(self):
        tight = dataclasses.replace(
            DEFAULT_CONFIG,
            targets=dataclasses.replace(DEFAULT_CONFIG.targets, tolerance_minutes=1.0),
        )
        sparse = [
            CgmPoint(MEAL_TIME, 100.0),
            CgmPoint(MEAL_TIME + dt.timedelta(minutes=35), 120.0),
        ]
        target = build_targets(_history(sparse), MEAL_TIME, 100.0, tight)
        assert target.delta == {}

    def test_nearest_reading_wins(self):
        cgm = [
            CgmPoint(MEAL_TIME, 100.0),
            CgmPoint(MEAL_TIME + dt.timedelta(minutes=27), 150.0),
            CgmPoint(MEAL_TIME + dt.timedelta(minutes=31), 120.0),
        ]
        target = build_targets(_history(cgm), MEAL_TIME, 100.0, DEFAULT_CONFIG)
        assert target.absolute[30] == 120.0


class TestObservedCurve:
    def test_minute_zero_is_the_baseline(self):
        target = build_targets(_history(_dense_cgm()), MEAL_TIME, 97.5, DEFAULT_CONFIG)
        assert target.observed_curve[0] == 97.5

    def test_dense_coverage_fills_most_of_the_grid(self):
        target = build_targets(_history(_dense_cgm()), MEAL_TIME, 100.0, DEFAULT_CONFIG)
        assert len(target.observed_curve) >= 24


class TestOverlapTagging:
    def test_a_meal_inside_the_window_is_tagged(self):
        target = build_targets(
            _history(_dense_cgm(), meals=[_meal(0, "this"), _meal(45, "next")]),
            MEAL_TIME,
            100.0,
            DEFAULT_CONFIG,
        )
        assert target.has_overlapping_meal is True
        assert target.overlapping_meal_ids == ("next",)

    def test_a_meal_after_the_window_is_not_tagged(self):
        target = build_targets(
            _history(_dense_cgm(), meals=[_meal(0, "this"), _meal(121, "later")]),
            MEAL_TIME,
            100.0,
            DEFAULT_CONFIG,
        )
        assert target.has_overlapping_meal is False

    def test_a_meal_exactly_at_the_window_edge_is_tagged(self):
        """The window is (t, t+120], so t+120 counts."""
        target = build_targets(
            _history(_dense_cgm(), meals=[_meal(0, "this"), _meal(120, "edge")]),
            MEAL_TIME,
            100.0,
            DEFAULT_CONFIG,
        )
        assert target.has_overlapping_meal is True

    def test_the_meal_itself_does_not_count_as_an_overlap(self):
        target = build_targets(
            _history(_dense_cgm(), meals=[_meal(0, "this")]),
            MEAL_TIME,
            100.0,
            DEFAULT_CONFIG,
        )
        assert target.has_overlapping_meal is False


class TestEpisodeAssembly:
    @pytest.fixture(scope="class")
    def built(self):
        return build_episodes(SyntheticDevRepository(), DEFAULT_CONFIG)

    def test_episodes_are_produced(self, built):
        episodes, report = built
        assert len(episodes) > 100
        assert report.accepted == len(episodes)

    def test_episodes_are_chronologically_sorted(self, built):
        episodes, _ = built
        timestamps = [episode.meal_timestamp for episode in episodes]
        assert timestamps == sorted(timestamps)

    def test_exclusions_are_counted_with_reasons(self, built):
        _, report = built
        assert report.candidate_meals >= report.accepted
        excluded = report.candidate_meals - report.accepted
        assert sum(report.reasons.values()) == excluded, (
            "every excluded meal must have a recorded reason -- silent drops make the "
            "sample count untraceable"
        )

    def test_report_serializes_for_the_metrics_endpoint(self, built):
        _, report = built
        payload = report.as_dict()
        assert set(payload) == {
            "candidate_meals",
            "accepted",
            "excluded",
            "reasons",
            "overlapping_tagged",
        }

    def test_excluding_overlap_reduces_the_episode_count(self):
        with_overlap, report = build_episodes(SyntheticDevRepository(), DEFAULT_CONFIG)
        without, _ = build_episodes(
            SyntheticDevRepository(), DEFAULT_CONFIG, exclude_overlapping=True
        )
        assert len(without) == len(with_overlap) - report.overlapping_tagged

    def test_every_episode_meets_the_horizon_requirement(self, built):
        episodes, _ = built
        for episode in episodes:
            assert (
                len(episode.target.available_horizons)
                >= DEFAULT_CONFIG.targets.min_horizons_required
            )

    def test_participant_filter_isolates_one_participant(self):
        episodes, _ = build_episodes(
            SyntheticDevRepository(),
            DEFAULT_CONFIG,
            participant_ids=["synthetic_p003"],
        )
        assert episodes
        assert {e.participant_id for e in episodes} == {"synthetic_p003"}

    def test_features_and_targets_share_the_same_baseline(self, built):
        """The curve anchor and the target reference must be the same measurement."""
        episodes, _ = built
        for episode in episodes[:25]:
            assert (
                episode.features.baseline_glucose_mg_dl
                == episode.target.baseline_glucose_mg_dl
            )
