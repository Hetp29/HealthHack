"""Shrinkage behaviour and the generalized fallback."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.personalization import PersonalizationModel


def _residuals(values: list[float]) -> dict[int, np.ndarray]:
    return {horizon: np.array(values, dtype="float64") for horizon in HORIZONS}


def _fit(ids: list[str], values: list[float], config=None) -> PersonalizationModel:
    config = config or DEFAULT_CONFIG.personalization
    return PersonalizationModel(config).fit(
        np.array(ids, dtype=object), _residuals(values)
    )


class TestShrinkage:
    def test_offset_is_the_shrunk_mean_residual(self):
        """offset = mean residual * n/(n+k), with k read from config, not hardcoded.

        Derived from `DEFAULT_CONFIG` so that re-tuning `shrinkage_k` (it moved from
        6.0 to 3.0 after rolling development validation) cannot silently invalidate
        the arithmetic this test is actually checking.
        """
        n, residual = 10, 8.0
        k = DEFAULT_CONFIG.personalization.shrinkage_k
        model = _fit(["p"] * n, [residual] * n)
        calibration = model.calibration_for("p")
        assert calibration.raw_offsets[60] == pytest.approx(residual)
        assert calibration.shrinkage_factors[60] == pytest.approx(n / (n + k))
        assert calibration.offsets[60] == pytest.approx(residual * n / (n + k))

    def test_shrinkage_factor_formula_across_sample_sizes(self):
        """The n/(n+k) curve itself, independent of the configured k."""
        k = DEFAULT_CONFIG.personalization.shrinkage_k
        for n in (4, 10, 25, 100):
            calibration = _fit(["p"] * n, [10.0] * n).calibration_for("p")
            assert calibration.shrinkage_factors[60] == pytest.approx(n / (n + k))

    def test_more_evidence_means_less_shrinkage(self):
        few = _fit(["p"] * 5, [10.0] * 5).calibration_for("p")
        many = _fit(["p"] * 50, [10.0] * 50).calibration_for("p")
        assert few.shrinkage_factors[60] < many.shrinkage_factors[60]
        assert abs(few.offsets[60]) < abs(many.offsets[60])

    def test_shrinkage_never_reaches_one(self):
        """An offset is always pulled at least slightly toward the cohort model."""
        model = _fit(["p"] * 1000, [10.0] * 1000)
        assert model.calibration_for("p").shrinkage_factors[60] < 1.0

    def test_zero_mean_residual_gives_no_correction(self):
        model = _fit(["p"] * 20, [5.0, -5.0] * 10)
        assert model.calibration_for("p").offsets[60] == pytest.approx(0.0)

    def test_offsets_are_capped(self):
        config = dataclasses.replace(
            DEFAULT_CONFIG.personalization, max_offset_mg_dl=10.0
        )
        model = _fit(["p"] * 100, [500.0] * 100, config)
        assert model.calibration_for("p").offsets[60] == pytest.approx(10.0)

    def test_negative_offsets_are_capped_too(self):
        config = dataclasses.replace(
            DEFAULT_CONFIG.personalization, max_offset_mg_dl=10.0
        )
        model = _fit(["p"] * 100, [-500.0] * 100, config)
        assert model.calibration_for("p").offsets[60] == pytest.approx(-10.0)

    def test_sign_follows_the_residual(self):
        """Positive residual means the cohort model under-predicted, so correct upward."""
        assert _fit(["p"] * 20, [12.0] * 20).calibration_for("p").offsets[60] > 0
        assert _fit(["p"] * 20, [-12.0] * 20).calibration_for("p").offsets[60] < 0


class TestFallback:
    def test_below_the_threshold_no_offset_is_applied(self):
        model = _fit(["p"] * 2, [30.0, 30.0])
        calibration = model.calibration_for("p")
        assert calibration.personalized is False
        # A raw offset was computed but must NOT be applied.
        assert calibration.raw_offsets[60] == pytest.approx(30.0)
        assert calibration.offset_for(60) == 0.0

    def test_at_the_threshold_personalization_engages(self):
        n = DEFAULT_CONFIG.personalization.min_episodes
        assert _fit(["p"] * n, [10.0] * n).calibration_for("p").personalized is True

    def test_one_below_the_threshold_falls_back(self):
        n = DEFAULT_CONFIG.personalization.min_episodes - 1
        assert _fit(["p"] * n, [10.0] * n).calibration_for("p").personalized is False

    def test_fallback_reason_names_the_count_and_the_threshold(self):
        calibration = _fit(["p"] * 2, [1.0, 1.0]).calibration_for("p")
        assert calibration.fallback_reason is not None
        assert "2 usable training meal episode" in calibration.fallback_reason
        assert str(DEFAULT_CONFIG.personalization.min_episodes) in calibration.fallback_reason

    def test_unseen_participant_falls_back_without_raising(self):
        model = _fit(["p"] * 10, [5.0] * 10)
        calibration = model.calibration_for("never_seen")
        assert calibration.personalized is False
        assert calibration.offset_for(60) == 0.0
        assert "no meal episodes in the training data" in calibration.fallback_reason

    def test_personalized_participant_has_no_fallback_reason(self):
        assert _fit(["p"] * 10, [5.0] * 10).calibration_for("p").fallback_reason is None


class TestParticipantIsolation:
    def test_participants_do_not_contaminate_each_other(self):
        model = _fit(["a"] * 10 + ["b"] * 10, [20.0] * 10 + [-20.0] * 10)
        assert model.calibration_for("a").raw_offsets[60] == pytest.approx(20.0)
        assert model.calibration_for("b").raw_offsets[60] == pytest.approx(-20.0)

    def test_apply_uses_each_row_s_own_participant(self):
        model = _fit(["a"] * 10 + ["b"] * 10, [20.0] * 10 + [-20.0] * 10)
        ids = np.array(["a", "b", "a"], dtype=object)
        cohort = {horizon: np.array([0.0, 0.0, 0.0]) for horizon in HORIZONS}
        adjusted = model.apply(ids, cohort)
        assert adjusted[60][0] > 0
        assert adjusted[60][1] < 0
        assert adjusted[60][0] == pytest.approx(adjusted[60][2])

    def test_apply_does_not_mutate_the_input(self):
        model = _fit(["a"] * 10, [20.0] * 10)
        cohort = {horizon: np.array([0.0]) for horizon in HORIZONS}
        model.apply(np.array(["a"], dtype=object), cohort)
        assert cohort[60][0] == 0.0

    def test_apply_leaves_fallback_participants_unchanged(self):
        model = _fit(["a"] * 10 + ["b"] * 1, [20.0] * 10 + [50.0])
        ids = np.array(["b"], dtype=object)
        cohort = {horizon: np.array([7.0]) for horizon in HORIZONS}
        assert model.apply(ids, cohort)[60][0] == pytest.approx(7.0)


class TestValidMasks:
    def test_masked_out_episodes_do_not_contribute(self):
        ids = np.array(["p"] * 10, dtype=object)
        residuals = {h: np.array([100.0] * 5 + [0.0] * 5) for h in HORIZONS}
        masks = {h: np.array([False] * 5 + [True] * 5) for h in HORIZONS}
        model = PersonalizationModel(DEFAULT_CONFIG.personalization).fit(
            ids, residuals, valid_masks=masks
        )
        assert model.calibration_for("p").raw_offsets[60] == pytest.approx(0.0)
        assert model.calibration_for("p").episode_counts[60] == 5

    def test_non_finite_residuals_are_dropped(self):
        ids = np.array(["p"] * 6, dtype=object)
        residuals = {h: np.array([10.0] * 5 + [np.nan]) for h in HORIZONS}
        model = PersonalizationModel(DEFAULT_CONFIG.personalization).fit(ids, residuals)
        assert model.calibration_for("p").episode_counts[60] == 5
        assert model.calibration_for("p").raw_offsets[60] == pytest.approx(10.0)


def test_model_serializes_for_the_manifest():
    model = _fit(["a"] * 10 + ["b"] * 1, [5.0] * 10 + [9.0])
    payload = model.as_dict()
    assert payload["min_episodes"] == DEFAULT_CONFIG.personalization.min_episodes
    assert payload["shrinkage_k"] == DEFAULT_CONFIG.personalization.shrinkage_k
    assert [row["participant_id"] for row in payload["participants"]] == ["a", "b"]
    assert payload["participants"][1]["personalized"] is False
