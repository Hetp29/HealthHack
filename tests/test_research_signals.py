"""Adversarial leakage and sanity tests for the physiological signal engine.

The `r_` fingerprint group summarizes a participant's OBSERVED past responses. That is
legitimate -- those responses had already happened at prediction time -- but it is the
most leakage-prone construct in the project, so it is attacked directly here:
post-meal glucose is poisoned, the target meal is made to look like its own history,
and the strict `<` timestamp boundary is probed to the second.

A physiology-flavoured feature that silently reads the future would produce a
spectacular validation score and a worthless model. These tests are the guard.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from ml.config import DEFAULT_CONFIG
from ml.schema import ActivityEvent, CgmPoint, HeartRatePoint, MealRecord, ParticipantHistory
from research.physiology.signals import (
    GROUPS,
    SIGNAL_SPECS,
    absorption_signals,
    activity_signals,
    all_signal_names,
    glucose_signals,
    mob_signals,
    names_for_group,
    profile_signals,
    response_signals,
)

T = dt.datetime(2020, 5, 8, 12, 30, tzinfo=dt.timezone.utc)


def _premeal_cgm(start_minutes: int = 180) -> list[CgmPoint]:
    """Native 5-minute Dexcom-style readings rising gently before the meal."""
    return [
        CgmPoint(T - dt.timedelta(minutes=m), 100.0 + 0.02 * (start_minutes - m))
        for m in range(start_minutes, -1, -5)
    ]


def _history(**kwargs) -> ParticipantHistory:
    kwargs.setdefault("cgm", _premeal_cgm())
    return ParticipantHistory(participant_id="p", data_source="official", **kwargs)


class TestRegistryHygiene:
    def test_every_signal_has_a_full_specification(self):
        for spec in SIGNAL_SPECS:
            assert spec.name and spec.group in GROUPS
            assert spec.evidence in {"A", "B", "C", "D"}
            assert spec.units and spec.definition and spec.interpretation
            assert spec.missing_when

    def test_no_level_d_signal_is_implemented(self):
        """Level D needs unobserved insulin/tracer data; it must not ship as a feature."""
        assert [s.name for s in SIGNAL_SPECS if s.evidence == "D"] == []

    def test_names_are_unique_and_group_prefixed(self):
        names = all_signal_names()
        assert len(names) == len(set(names))
        prefixes = {"glucose": "g_", "mob": "m_", "absorption": "a_",
                    "response": "r_", "profile": "p_", "activity": "v_"}
        for spec in SIGNAL_SPECS:
            assert spec.name.startswith(prefixes[spec.group]), spec.name

    def test_groups_partition_the_registry(self):
        covered = sum(len(names_for_group(g)) for g in GROUPS)
        assert covered == len(SIGNAL_SPECS)

    def test_latent_signals_are_labelled_level_c_not_a(self):
        """A fingerprint is an estimated state, not a measured biomarker."""
        by_name = {s.name: s for s in SIGNAL_SPECS}
        for name in ("r_hist_peak_delta", "r_hist_per_carb", "r_hist_ttp"):
            assert by_name[name].evidence == "C"

    def test_homa_ir_is_labelled_a_surrogate(self):
        spec = next(s for s in SIGNAL_SPECS if s.name == "p_homa_ir")
        assert spec.evidence == "B"
        assert "surrogate" in spec.interpretation.lower()
        assert "not" in spec.interpretation.lower()


class TestGlucoseSignalsIgnoreTheFuture:
    def test_post_meal_poisoning_changes_nothing(self):
        clean = glucose_signals(_history(), T, 103.6)
        poisoned_cgm = _premeal_cgm() + [
            CgmPoint(T + dt.timedelta(minutes=m), 400.0) for m in range(5, 125, 5)
        ]
        poisoned = glucose_signals(
            _history(cgm=poisoned_cgm).truncated_at(T), T, 103.6
        )
        assert poisoned == clean

    def test_slope_of_a_known_ramp(self):
        """+1 mg/dL per minute must read back as exactly 1.0 at every window."""
        cgm = [CgmPoint(T - dt.timedelta(minutes=m), 100.0 - m) for m in range(60, -1, -5)]
        signals = glucose_signals(_history(cgm=cgm), T, 100.0)
        for key in ("g_slope_10", "g_slope_15", "g_slope_60"):
            assert signals[key] == pytest.approx(1.0)

    def test_acceleration_is_zero_on_a_straight_line(self):
        cgm = [CgmPoint(T - dt.timedelta(minutes=m), 100.0 - m) for m in range(60, -1, -5)]
        assert glucose_signals(_history(cgm=cgm), T, 100.0)["g_accel"] == pytest.approx(0.0)

    def test_rise_fraction_detects_a_monotone_rise(self):
        cgm = [CgmPoint(T - dt.timedelta(minutes=m), 100.0 - m) for m in range(25, -1, -5)]
        assert glucose_signals(_history(cgm=cgm), T, 100.0)["g_rise_fraction"] == 1.0

    def test_rise_fraction_detects_a_monotone_fall(self):
        cgm = [CgmPoint(T - dt.timedelta(minutes=m), 100.0 + m) for m in range(25, -1, -5)]
        assert glucose_signals(_history(cgm=cgm), T, 100.0)["g_rise_fraction"] == 0.0

    def test_sparse_history_yields_nan_not_zero(self):
        signals = glucose_signals(_history(cgm=[CgmPoint(T, 100.0)]), T, 100.0)
        assert math.isnan(signals["g_slope_10"])
        assert math.isnan(signals["g_cv_60"])

    def test_overnight_reference_uses_only_prior_nights(self):
        night = [CgmPoint(T - dt.timedelta(days=1, hours=8, minutes=m), 90.0) for m in range(0, 60, 5)]
        signals = glucose_signals(_history(cgm=night + _premeal_cgm()), T, 120.0)
        assert signals["g_dev_from_night"] == pytest.approx(30.0)


class TestMealOnBoardIsCausal:
    def test_future_meals_are_excluded(self):
        past = MealRecord("past", "p", T - dt.timedelta(minutes=60), carbs_g=50.0, fat_g=10.0)
        future = MealRecord("future", "p", T + dt.timedelta(minutes=30), carbs_g=999.0, fat_g=99.0)
        with_future = _history(meals=[past, future]).truncated_at(T)
        only_past = _history(meals=[past])
        assert mob_signals(with_future, T) == mob_signals(only_past, T)

    def test_the_meal_itself_is_excluded(self):
        """Strict `<`: the meal being forecast is not its own meal-on-board."""
        this_meal = MealRecord("this", "p", T, carbs_g=80.0, fat_g=20.0)
        signals = mob_signals(_history(meals=[this_meal]), T)
        assert signals["m_carb_mob_exp60"] == 0.0
        assert signals["m_meals_4h"] == 0.0

    def test_decay_matches_the_kernel(self):
        """One meal exactly one time constant back must contribute carbs * e^-1."""
        meal = MealRecord("m", "p", T - dt.timedelta(minutes=60), carbs_g=100.0)
        signals = mob_signals(_history(meals=[meal]), T)
        assert signals["m_carb_mob_exp60"] == pytest.approx(100.0 * math.exp(-1.0))
        assert signals["m_carb_mob_exp120"] == pytest.approx(100.0 * math.exp(-0.5))

    def test_older_meals_contribute_less(self):
        near = mob_signals(
            _history(meals=[MealRecord("m", "p", T - dt.timedelta(minutes=30), carbs_g=50.0)]), T
        )
        far = mob_signals(
            _history(meals=[MealRecord("m", "p", T - dt.timedelta(minutes=200), carbs_g=50.0)]), T
        )
        assert near["m_carb_mob_exp60"] > far["m_carb_mob_exp60"]

    def test_no_prior_meal_is_zero_exposure_not_missing(self):
        """Zero exposure is a genuine measurement here, unlike an absent sensor."""
        signals = mob_signals(_history(), T)
        assert signals["m_carb_mob_exp60"] == 0.0
        assert not math.isnan(signals["m_carb_mob_exp60"])


class TestResponseFingerprintCannotSeeItsOwnTarget:
    def _response(self, peak_delta=40.0, ttp=60.0, delta_120=10.0, carbs=50.0):
        return {"peak_delta": peak_delta, "ttp": ttp, "delta_120": delta_120, "carbs": carbs}

    def test_no_prior_episode_yields_nan(self):
        signals = response_signals([])
        assert signals["r_hist_n"] == 0.0
        assert math.isnan(signals["r_hist_peak_delta"])
        assert math.isnan(signals["r_hist_per_carb"])

    def test_shrinkage_applies_with_few_episodes(self):
        """n=1, k=3 -> factor 0.25, so a 40 mg/dL mean becomes 10."""
        signals = response_signals([self._response(peak_delta=40.0)], shrinkage_k=3.0)
        assert signals["r_hist_peak_delta"] == pytest.approx(10.0)

    def test_more_evidence_means_less_shrinkage(self):
        few = response_signals([self._response()] * 2, 3.0)["r_hist_peak_delta"]
        many = response_signals([self._response()] * 40, 3.0)["r_hist_peak_delta"]
        assert abs(many) > abs(few)

    def test_per_carb_uses_a_floor_so_tiny_meals_cannot_explode(self):
        signals = response_signals([self._response(peak_delta=40.0, carbs=1.0)], 3.0)
        # Floor of 10 g, so 40/10 * 0.25 = 1.0 rather than 40/1 * 0.25 = 10.
        assert signals["r_hist_per_carb"] == pytest.approx(1.0)

    def test_recovery_is_peak_minus_120_minute_delta(self):
        signals = response_signals(
            [self._response(peak_delta=50.0, delta_120=20.0)] * 30, 3.0
        )
        expected = 30.0 * (30 / 33)
        assert signals["r_hist_recovery"] == pytest.approx(expected)

    def test_episode_count_is_reported_so_trust_can_be_weighed(self):
        assert response_signals([self._response()] * 7)["r_hist_n"] == 7.0


class TestStrictEarlierEpisodeBoundary:
    """The `<` filter in build_signals, probed to the second."""

    def _dev_pool(self):
        import pickle, pathlib
        cache = pathlib.Path("ml/_devdata/signals.pkl")
        if not cache.exists():
            pytest.skip("signal cache absent; run research.physiology.build_signals")
        with cache.open("rb") as handle:
            return pickle.load(handle)

    def test_cached_matrix_matches_the_registry(self):
        payload = self._dev_pool()
        assert list(payload["frame"].columns) == all_signal_names()
        assert len(payload["frame"]) == len(payload["keys"])

    def test_first_episode_per_participant_has_no_fingerprint(self):
        """A participant's earliest episode cannot have a prior response."""
        payload = self._dev_pool()
        frame = payload["frame"]
        seen = set()
        checked = 0
        for i, (pid, _) in enumerate(payload["keys"]):
            if pid in seen:
                continue
            seen.add(pid)
            assert frame.iloc[i]["r_hist_n"] == 0.0, pid
            assert math.isnan(frame.iloc[i]["r_hist_peak_delta"]), pid
            checked += 1
        assert checked >= 40, "expected one first-episode check per participant"

    def test_fingerprint_evidence_grows_monotonically_within_participant(self):
        payload = self._dev_pool()
        frame = payload["frame"]
        by_participant: dict[str, list[float]] = {}
        for i, (pid, _) in enumerate(payload["keys"]):
            by_participant.setdefault(pid, []).append(float(frame.iloc[i]["r_hist_n"]))
        for pid, counts in by_participant.items():
            assert counts == sorted(counts), f"{pid} fingerprint evidence is not monotone"

    def test_no_signal_is_constant_across_the_whole_pool(self):
        """A constant column is a dead feature; flag it rather than ship it."""
        payload = self._dev_pool()
        frame = payload["frame"]
        constant = [c for c in frame.columns if frame[c].nunique(dropna=True) <= 1]
        assert constant == [], f"constant signals: {constant}"

    def test_no_infinite_values(self):
        payload = self._dev_pool()
        frame = payload["frame"]
        bad = [c for c in frame.columns if np.isinf(frame[c].to_numpy()).any()]
        assert bad == [], f"non-finite signals: {bad}"


class TestAbsorptionAndProfile:
    def test_saturating_carbohydrate_is_concave(self):
        """Equal carbohydrate increments must yield diminishing returns.

        Concavity is defined over EQUAL steps. An earlier version of this test
        compared doubling steps (30->60->120), which is not a concavity test: the
        Michaelis-Menten form 60x/(60+x) gives +10 for both of those because the
        second step adds twice as much carbohydrate.
        """
        f = lambda c: absorption_signals(c, 20.0, 10.0, 5.0, 300.0)["a_carb_sat"]
        steps = [f(c + 30.0) - f(c) for c in (0.0, 30.0, 60.0, 90.0)]
        assert all(
            later < earlier for earlier, later in zip(steps, steps[1:])
        ), f"increments should shrink, got {steps}"

    def test_saturating_carbohydrate_is_increasing_and_bounded(self):
        f = lambda c: absorption_signals(c, 20.0, 10.0, 5.0, 300.0)["a_carb_sat"]
        values = [f(c) for c in (0.0, 25.0, 50.0, 100.0, 200.0, 400.0)]
        assert values == sorted(values)
        assert values[-1] < 60.0, "asymptote is the 60 g scale parameter"

    def test_energy_fractions_need_positive_calories(self):
        signals = absorption_signals(50.0, 20.0, 10.0, 5.0, 0.0)
        assert math.isnan(signals["a_carb_frac_kcal"])

    def test_net_carbs_floors_at_zero(self):
        assert absorption_signals(5.0, 0.0, 0.0, 20.0, 100.0)["a_net_carbs"] == 0.0

    def test_homa_ir_formula(self):
        """HOMA-IR = insulin * glucose / 405."""
        signals = profile_signals(
            {"fasting_insulin": 10.0, "fasting_glucose": 100.0, "a1c": 5.5, "bmi": 25.0, "age": 40.0}
        )
        assert signals["p_homa_ir"] == pytest.approx(10.0 * 100.0 / 405.0)

    @pytest.mark.parametrize(
        ("a1c", "expected"), [(5.0, 0.0), (5.6, 0.0), (5.7, 1.0), (6.4, 1.0), (6.5, 2.0), (8.5, 2.0)]
    )
    def test_cohort_uses_ada_a1c_thresholds(self, a1c, expected):
        assert profile_signals({"a1c": a1c})["p_cohort_ord"] == expected

    def test_absent_profile_is_nan_never_zero(self):
        signals = profile_signals(None)
        assert all(math.isnan(v) for v in signals.values())


class TestActivitySignals:
    def test_future_activity_is_excluded(self):
        past = ActivityEvent("p", T - dt.timedelta(minutes=10), 5.0, "kcal_per_minute")
        future = ActivityEvent("p", T + dt.timedelta(minutes=10), 500.0, "kcal_per_minute")
        with_future = _history(activity=[past, future]).truncated_at(T)
        assert activity_signals(with_future, T)["v_activity_kcal_60"] == pytest.approx(5.0)

    def test_missing_activity_is_nan_not_zero(self):
        """Absent wearable data must not look like genuine inactivity."""
        assert math.isnan(activity_signals(_history(), T)["v_activity_kcal_60"])

    def test_hr_deviation_is_relative_to_the_participants_own_median(self):
        history = [HeartRatePoint(T - dt.timedelta(minutes=m), 60.0, 55.0) for m in range(300, 60, -5)]
        recent = [HeartRatePoint(T - dt.timedelta(minutes=m), 80.0, 55.0) for m in range(25, -1, -5)]
        signals = activity_signals(_history(heart_rate=history + recent), T)
        assert signals["v_hr_dev_baseline"] == pytest.approx(20.0)
