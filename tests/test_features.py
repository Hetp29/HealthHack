"""Feature calculation correctness, on hand-constructed histories."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from ml.config import DEFAULT_CONFIG
from ml.features import (
    FEATURE_LABELS,
    FEATURE_NAMES,
    OPTIONAL_FEATURES,
    ContextOverride,
    FeatureError,
    MealComposition,
    build_features,
    rows_to_frame,
)
from ml.schema import CgmPoint, HeartRatePoint, MealRecord, MedicationEvent, ParticipantHistory

MEAL_TIME = dt.datetime(2026, 10, 4, 12, 30, tzinfo=dt.timezone.utc)
COMPOSITION = MealComposition(
    carbs_g=55.0, protein_g=22.0, fat_g=16.0, fiber_g=7.0, calories_kcal=460.0
)


def _build(history: ParticipantHistory, **kwargs):
    return build_features(
        history=history,
        meal_timestamp=MEAL_TIME,
        composition=COMPOSITION,
        config=DEFAULT_CONFIG.features,
        timezone_name=kwargs.pop("timezone_name", "UTC"),
        time_basis=kwargs.pop("time_basis", "utc"),
        **kwargs,
    )


def _history(**kwargs) -> ParticipantHistory:
    return ParticipantHistory(
        participant_id="p_test", data_source="synthetic_dev", **kwargs
    )


class TestFeatureContract:
    def test_feature_set_has_no_duplicates(self):
        assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))

    def test_every_feature_has_a_human_label(self):
        """Labels drive the driver explanations; a missing one would surface raw."""
        missing = [name for name in FEATURE_NAMES if name not in FEATURE_LABELS]
        assert missing == []

    def test_optional_features_are_all_real_features(self):
        assert OPTIONAL_FEATURES <= set(FEATURE_NAMES)

    def test_no_derived_linear_combinations_of_other_features(self):
        """Exact linear combinations add nothing to a linear model and wreck attribution."""
        for banned in ("net_carbs_g", "fat_protein_g", "hr_above_resting_bpm"):
            assert banned not in FEATURE_NAMES

    def test_every_row_carries_every_feature_in_frozen_order(self):
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)]))
        assert tuple(row.values) == FEATURE_NAMES

    def test_frame_columns_match_the_frozen_order(self):
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)]))
        frame = rows_to_frame([row, row])
        assert tuple(frame.columns) == FEATURE_NAMES
        assert len(frame) == 2

    def test_empty_frame_still_has_the_columns(self):
        frame = rows_to_frame([])
        assert tuple(frame.columns) == FEATURE_NAMES
        assert len(frame) == 0


class TestGlucoseFeatures:
    def test_baseline_is_the_most_recent_reading(self):
        cgm = [
            CgmPoint(MEAL_TIME - dt.timedelta(minutes=10), 95.0),
            CgmPoint(MEAL_TIME - dt.timedelta(minutes=5), 101.0),
        ]
        row = _build(_history(cgm=cgm))
        assert row.values["premeal_glucose_mg_dl"] == 101.0
        assert row.values["minutes_since_last_cgm"] == pytest.approx(5.0)

    def test_slope_of_a_known_ramp(self):
        """+1 mg/dL per minute over 30 minutes must read back as exactly 1.0."""
        cgm = [
            CgmPoint(MEAL_TIME - dt.timedelta(minutes=m), 100.0 - m)
            for m in range(30, -1, -5)
        ]
        row = _build(_history(cgm=cgm))
        assert row.values["cgm_slope_30min_mg_dl_per_min"] == pytest.approx(1.0)

    def test_slope_of_a_flat_series_is_zero(self):
        cgm = [
            CgmPoint(MEAL_TIME - dt.timedelta(minutes=m), 100.0) for m in range(30, -1, -5)
        ]
        assert _build(_history(cgm=cgm)).values["cgm_slope_30min_mg_dl_per_min"] == pytest.approx(0.0)

    def test_slope_of_a_descending_ramp_is_negative(self):
        cgm = [
            CgmPoint(MEAL_TIME - dt.timedelta(minutes=m), 100.0 + m)
            for m in range(30, -1, -5)
        ]
        assert _build(_history(cgm=cgm)).values["cgm_slope_30min_mg_dl_per_min"] == pytest.approx(-1.0)

    def test_single_reading_leaves_slope_and_variability_missing(self):
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)]))
        assert math.isnan(row.values["cgm_slope_30min_mg_dl_per_min"])
        assert math.isnan(row.values["cgm_mean_60min_mg_dl"])
        assert math.isnan(row.values["cgm_std_60min_mg_dl"])
        assert "cgm_slope_30min_mg_dl_per_min" in row.missing_features

    def test_mean_and_std_over_a_known_window(self):
        # Four readings 90, 100, 110, 120 inside the 60-minute window.
        cgm = [
            CgmPoint(MEAL_TIME - dt.timedelta(minutes=45), 90.0),
            CgmPoint(MEAL_TIME - dt.timedelta(minutes=30), 100.0),
            CgmPoint(MEAL_TIME - dt.timedelta(minutes=15), 110.0),
            CgmPoint(MEAL_TIME, 120.0),
        ]
        row = _build(_history(cgm=cgm))
        assert row.values["cgm_mean_60min_mg_dl"] == pytest.approx(105.0)
        # Sample standard deviation (ddof=1) of 90,100,110,120 is 12.909944...
        assert row.values["cgm_std_60min_mg_dl"] == pytest.approx(12.909944, abs=1e-5)

    def test_readings_outside_the_window_are_excluded(self):
        cgm = [
            CgmPoint(MEAL_TIME - dt.timedelta(minutes=120), 400.0),  # outside 60 min
            CgmPoint(MEAL_TIME - dt.timedelta(minutes=30), 100.0),
            CgmPoint(MEAL_TIME, 100.0),
        ]
        row = _build(_history(cgm=cgm))
        assert row.values["cgm_mean_60min_mg_dl"] == pytest.approx(100.0)


class TestMealFeatures:
    def test_macros_pass_through_unchanged(self):
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)]))
        assert row.values["carbs_g"] == 55.0
        assert row.values["protein_g"] == 22.0
        assert row.values["fat_g"] == 16.0
        assert row.values["fiber_g"] == 7.0
        assert row.values["calories_kcal"] == 460.0

    def test_incomplete_macros_are_refused(self):
        meal = MealRecord(
            meal_id="m", participant_id="p_test", timestamp=MEAL_TIME, carbs_g=50.0
        )
        with pytest.raises(FeatureError, match="missing macronutrients"):
            MealComposition.from_meal_record(meal)

    def test_complete_macros_convert(self):
        meal = MealRecord(
            meal_id="m",
            participant_id="p_test",
            timestamp=MEAL_TIME,
            carbs_g=50.0,
            protein_g=20.0,
            fat_g=10.0,
            fiber_g=5.0,
            calories_kcal=370.0,
        )
        composition = MealComposition.from_meal_record(meal)
        assert composition.carbs_g == 50.0
        assert composition.calories_kcal == 370.0


class TestTimingFeatures:
    def test_time_of_day_is_cyclic_and_unit_norm(self):
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)]))
        s, c = row.values["tod_sin"], row.values["tod_cos"]
        assert s**2 + c**2 == pytest.approx(1.0)

    def test_midnight_maps_to_sin_zero_cos_one(self):
        midnight = dt.datetime(2026, 10, 4, 0, 0, tzinfo=dt.timezone.utc)
        row = build_features(
            history=ParticipantHistory(
                participant_id="p",
                data_source="synthetic_dev",
                cgm=[CgmPoint(midnight, 100.0)],
            ),
            meal_timestamp=midnight,
            composition=COMPOSITION,
            config=DEFAULT_CONFIG.features,
            timezone_name="UTC",
        )
        assert row.values["tod_sin"] == pytest.approx(0.0)
        assert row.values["tod_cos"] == pytest.approx(1.0)

    def test_timezone_shifts_time_of_day(self):
        """A participant timezone must actually change the circadian features."""
        history = _history(cgm=[CgmPoint(MEAL_TIME, 100.0)])
        utc = _build(history, timezone_name="UTC")
        ny = _build(history, timezone_name="America/New_York")
        assert utc.values["tod_sin"] != pytest.approx(ny.values["tod_sin"])

    def test_an_unparseable_timezone_is_refused_not_quietly_replaced_by_utc(self):
        """Ruling D3: never silently substitute UTC for an unusable local clock.

        This test previously asserted the OPPOSITE -- that a bogus timezone fell back
        to UTC and matched it. That fallback manufactured a time of day the data does
        not support.

        A malformed timezone is a data-layer defect, not a legitimate unknown: the
        data layer validates timezone names at ingestion (`data.timeutil
        .is_valid_timezone`), so one cannot legitimately reach the model. It therefore
        fails loudly, surfacing the upstream bug, instead of being imputed around.
        Contrast `test_absent_timezone_on_utc_data_yields_missing_time_of_day`, where
        the clock is genuinely unknown and the feature IS recorded missing.
        """
        history = _history(cgm=[CgmPoint(MEAL_TIME, 100.0)])
        with pytest.raises(FeatureError, match="local time of day"):
            _build(history, timezone_name="Not/AZone")

    def test_absent_timezone_on_utc_data_yields_missing_time_of_day(self):
        """A genuinely unknown local clock is recorded missing, never guessed."""
        history = _history(cgm=[CgmPoint(MEAL_TIME, 100.0)])
        row = _build(history, timezone_name=None)
        assert math.isnan(row.values["tod_sin"])
        assert math.isnan(row.values["tod_cos"])
        assert "tod_sin" in row.missing_features
        assert "tod_cos" in row.missing_features

    def test_source_local_basis_tolerates_a_stray_timezone(self):
        """Floating local time needs no conversion, so a stray zone cannot corrupt it."""
        history = _history(cgm=[CgmPoint(MEAL_TIME, 100.0)])
        row = _build(
            history, timezone_name="Not/AZone", time_basis="source_local_unknown_tz"
        )
        assert row.values["tod_sin"] == pytest.approx(
            _build(history, time_basis="source_local_unknown_tz").values["tod_sin"]
        )

    def test_prior_meal_gap_is_capped(self):
        old_meal = MealRecord(
            meal_id="old",
            participant_id="p_test",
            timestamp=MEAL_TIME - dt.timedelta(days=3),
            carbs_g=10.0,
            protein_g=1.0,
            fat_g=1.0,
            fiber_g=1.0,
            calories_kcal=60.0,
        )
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)], meals=[old_meal]))
        assert row.values["minutes_since_prior_meal"] == pytest.approx(
            DEFAULT_CONFIG.features.max_minutes_since_prior_meal
        )

    def test_prior_meal_with_unknown_carbs_leaves_that_feature_missing(self):
        prior = MealRecord(
            meal_id="prior",
            participant_id="p_test",
            timestamp=MEAL_TIME - dt.timedelta(hours=4),
        )
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)], meals=[prior]))
        assert math.isnan(row.values["prior_meal_carbs_g"])
        assert not math.isnan(row.values["minutes_since_prior_meal"])


class TestOptionalModalities:
    def test_absent_heart_rate_is_missing_not_zero(self):
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)]))
        assert math.isnan(row.values["hr_mean_30min_bpm"])
        assert "hr_mean_30min_bpm" in row.missing_features

    def test_heart_rate_mean_over_the_window(self):
        hr = [
            HeartRatePoint(MEAL_TIME - dt.timedelta(minutes=20), 60.0, 55.0),
            HeartRatePoint(MEAL_TIME - dt.timedelta(minutes=10), 80.0, 55.0),
        ]
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)], heart_rate=hr))
        assert row.values["hr_mean_30min_bpm"] == pytest.approx(70.0)

    def test_medication_exposure_is_a_presence_flag(self):
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)]))
        assert row.values["medication_exposure_recent"] == 0.0

        recent = MedicationEvent(
            participant_id="p_test",
            timestamp=MEAL_TIME - dt.timedelta(hours=2),
            medication_class="biguanide",
            dose=500.0,
            dose_unit="mg",
        )
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)], medications=[recent]))
        assert row.values["medication_exposure_recent"] == 1.0

    def test_medication_outside_the_lookback_does_not_count(self):
        stale = MedicationEvent(
            participant_id="p_test",
            timestamp=MEAL_TIME - dt.timedelta(hours=48),
            medication_class="biguanide",
        )
        row = _build(_history(cgm=[CgmPoint(MEAL_TIME, 100.0)], medications=[stale]))
        assert row.values["medication_exposure_recent"] == 0.0


class TestContextOverride:
    def test_each_member_overrides_its_feature(self):
        history = _history(cgm=[CgmPoint(MEAL_TIME, 100.0)])
        row = _build(
            history,
            override=ContextOverride(
                premeal_glucose_mg_dl=130.0,
                recent_heart_rate_bpm=88.0,
                minutes_since_prior_meal=240.0,
            ),
        )
        assert row.baseline_glucose_mg_dl == 130.0
        assert row.values["premeal_glucose_mg_dl"] == 130.0
        assert row.values["hr_mean_30min_bpm"] == 88.0
        assert row.values["minutes_since_prior_meal"] == 240.0

    def test_overridden_baseline_is_treated_as_current(self):
        stale = [CgmPoint(MEAL_TIME - dt.timedelta(minutes=60), 100.0)]
        row = _build(
            _history(cgm=stale),
            override=ContextOverride(premeal_glucose_mg_dl=111.0),
        )
        assert row.values["minutes_since_last_cgm"] == 0.0

    def test_an_empty_override_changes_nothing(self):
        history = _history(cgm=[CgmPoint(MEAL_TIME, 100.0)])
        assert _build(history, override=ContextOverride()).values == _build(history).values
