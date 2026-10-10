"""Curve math verified against hand-calculated arrays.

These definitions are frozen by the master specification and consumed by both the API
response and the evaluation report, so they are checked against values computed by
hand rather than against the implementation's own output.
"""

from __future__ import annotations

import pytest

from ml.config import FORECAST_MINUTES, HORIZONS
from ml.metrics import (
    build_curve,
    incremental_auc,
    interpolate_deltas,
    summarize_curve,
    summarize_observed,
)


def test_grid_is_exactly_25_points_at_five_minute_spacing():
    assert len(FORECAST_MINUTES) == 25
    assert FORECAST_MINUTES[0] == 0
    assert FORECAST_MINUTES[-1] == 120
    assert all(b - a == 5 for a, b in zip(FORECAST_MINUTES, FORECAST_MINUTES[1:]))


def test_interpolation_passes_exactly_through_the_knots():
    deltas = {30: 20.0, 60: 40.0, 90: 20.0, 120: 0.0}
    curve = interpolate_deltas(deltas)
    by_minute = dict(zip(FORECAST_MINUTES, curve))
    assert by_minute[0] == 0.0, "minute 0 delta is zero because minute 0 is measured"
    for horizon, value in deltas.items():
        assert by_minute[horizon] == pytest.approx(value)


def test_interpolation_is_linear_between_knots():
    # 0 -> 0 and 30 -> 30 means minute 15 must land on 15 exactly.
    curve = interpolate_deltas({30: 30.0, 60: 30.0, 90: 30.0, 120: 30.0})
    by_minute = dict(zip(FORECAST_MINUTES, curve))
    assert by_minute[15] == pytest.approx(15.0)
    assert by_minute[45] == pytest.approx(30.0)


def test_interpolation_requires_all_four_horizons():
    with pytest.raises(ValueError, match="missing predicted deltas"):
        interpolate_deltas({30: 1.0, 60: 2.0})


def test_minute_zero_is_the_measured_baseline_even_under_clipping():
    # A baseline below the clip floor must survive: it is a measurement, not a prediction.
    minutes, values, clipped = build_curve(
        30.0, {30: 0.0, 60: 0.0, 90: 0.0, 120: 0.0}, clip_min=40.0, clip_max=400.0
    )
    assert values[0] == 30.0
    assert clipped is True


def test_clipping_flag_is_false_when_nothing_is_clipped():
    _, _, clipped = build_curve(
        100.0, {30: 10.0, 60: 20.0, 90: 10.0, 120: 0.0}, clip_min=40.0, clip_max=400.0
    )
    assert clipped is False


class TestIncrementalAuc:
    """iAUC = trapezoidal integration of max(glucose - baseline, 0)."""

    def test_triangle_matches_hand_calculation(self):
        # Rises linearly 0 -> 40 over 60 min, falls 40 -> 0 over 60 min.
        # Area of the triangle = 0.5 * base * height = 0.5 * 120 * 40 = 2400.
        minutes, values, _ = build_curve(100.0, {30: 20.0, 60: 40.0, 90: 20.0, 120: 0.0})
        assert incremental_auc(minutes, values, 100.0) == pytest.approx(2400.0)

    def test_rectangle_matches_hand_calculation(self):
        # Flat +30 from minute 0 would be 30 * 120 = 3600, but minute 0 is the
        # baseline so the first 30-minute segment is a ramp: 0.5*30*30 = 450,
        # plus 90 min at +30 = 2700. Total 3150.
        minutes, values, _ = build_curve(100.0, {30: 30.0, 60: 30.0, 90: 30.0, 120: 30.0})
        assert incremental_auc(minutes, values, 100.0) == pytest.approx(3150.0)

    def test_flat_curve_has_zero_area(self):
        minutes, values, _ = build_curve(100.0, {h: 0.0 for h in HORIZONS})
        assert incremental_auc(minutes, values, 100.0) == 0.0

    def test_dip_below_baseline_contributes_zero_not_negative(self):
        """This is what separates positive iAUC from signed net excursion."""
        minutes, values, _ = build_curve(100.0, {30: -20.0, 60: -40.0, 90: -20.0, 120: 0.0})
        assert incremental_auc(minutes, values, 100.0) == 0.0

    def test_mixed_excursion_counts_only_the_positive_part(self):
        # +40 peak at 30 min then down to -40 at 90 and back to 0 at 120.
        # Positive region: 0->30 ramp up (0.5*30*40=600), 30->60 from 40 to 0 (600).
        # The 60->90 and 90->120 segments are at or below baseline: 0.
        minutes, values, _ = build_curve(100.0, {30: 40.0, 60: 0.0, 90: -40.0, 120: 0.0})
        assert incremental_auc(minutes, values, 100.0) == pytest.approx(1200.0)

    def test_is_never_negative_for_any_curve(self):
        for deltas in (
            {30: -5.0, 60: -10.0, 90: -3.0, 120: -1.0},
            {30: 0.0, 60: -50.0, 90: 0.0, 120: 0.0},
        ):
            minutes, values, _ = build_curve(100.0, deltas)
            assert incremental_auc(minutes, values, 100.0) >= 0.0


class TestCurveSummary:
    def test_peak_and_delta(self):
        minutes, values, _ = build_curve(105.0, {30: 19.0, 60: 41.0, 90: 34.0, 120: 13.0})
        summary = summarize_curve(minutes, values, 105.0)
        assert summary.peak_mg_dl == pytest.approx(146.0)
        assert summary.peak_delta_mg_dl == pytest.approx(41.0)
        assert summary.time_to_peak_minutes == 60

    def test_earliest_tie_wins(self):
        """Two equal maxima must resolve to the earlier minute."""
        minutes, values, _ = build_curve(100.0, {30: 30.0, 60: 30.0, 90: 10.0, 120: 0.0})
        # The plateau spans 30..60, so the peak is attained first at minute 30.
        assert summarize_curve(minutes, values, 100.0).time_to_peak_minutes == 30

    def test_no_excursion_peaks_at_minute_zero(self):
        minutes, values, _ = build_curve(100.0, {h: -5.0 for h in HORIZONS})
        summary = summarize_curve(minutes, values, 100.0)
        assert summary.time_to_peak_minutes == 0
        assert summary.peak_delta_mg_dl == pytest.approx(0.0)

    def test_empty_curve_is_an_error(self):
        with pytest.raises(ValueError, match="empty curve"):
            summarize_curve([], [], 100.0)


class TestObservedSummary:
    def test_dense_observed_curve_is_summarized(self):
        observed = {minute: 100.0 + minute * 0.2 for minute in FORECAST_MINUTES}
        summary = summarize_observed(observed, 100.0)
        assert summary is not None
        assert summary.time_to_peak_minutes == 120

    def test_sparse_coverage_is_refused(self):
        """Too few points to characterize a peak must return None, not a guess."""
        assert summarize_observed({0: 100.0, 5: 102.0, 10: 103.0}, 100.0) is None

    def test_truncated_window_is_refused(self):
        """A sensor that dropped out early cannot yield a trustworthy peak time."""
        observed = {minute: 100.0 + minute for minute in range(0, 70, 5)}
        assert summarize_observed(observed, 100.0) is None

    def test_empty_observed_curve_is_refused(self):
        assert summarize_observed({}, 100.0) is None
