"""Attribution must exactly describe the number shown beside it."""

from __future__ import annotations

import pathlib

import pytest

from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devfixture import SyntheticDevRepository
from ml.explain import (
    INDICATOR_PREFIX,
    MIN_ABS_CONTRIBUTION_MG_DL,
    explain_horizon,
    peak_horizon,
    verify_decomposition,
)
from ml.features import MealComposition, build_features, rows_to_frame
from ml.train import load_artifact, train

ARTIFACT_PATH = pathlib.Path("ml/artifacts/model.joblib")


@pytest.fixture(scope="module")
def artifact():
    """Prefer the committed-workflow artifact; train one if the repo is fresh."""
    if ARTIFACT_PATH.exists():
        try:
            return load_artifact(ARTIFACT_PATH)
        except Exception:
            pass
    return train(SyntheticDevRepository(), DEFAULT_CONFIG)


@pytest.fixture(scope="module")
def repository():
    return SyntheticDevRepository()


def _design(repository, artifact, participant_id: str, meal_index: int = 15):
    history = repository.get_history(participant_id)
    summary = repository.get_participant(participant_id)
    meal = history.meals[meal_index]
    row = build_features(
        history=history,
        meal_timestamp=meal.timestamp,
        composition=MealComposition.from_meal_record(meal),
        config=artifact.config.features,
        timezone_name=summary.timezone,
    )
    return rows_to_frame([row])


class TestDecompositionIsExact:
    @pytest.mark.parametrize("horizon", HORIZONS)
    def test_intercept_plus_contributions_equals_the_prediction(
        self, artifact, repository, horizon
    ):
        design = _design(repository, artifact, "synthetic_p002")
        exact, reconstructed, predicted = verify_decomposition(
            artifact.generalized, design, horizon
        )
        assert exact, f"reconstructed {reconstructed} != predicted {predicted}"

    @pytest.mark.parametrize(
        "participant_id",
        ["synthetic_p001", "synthetic_p002", "synthetic_p003", "synthetic_p005"],
    )
    def test_holds_across_participants_including_missing_modalities(
        self, artifact, repository, participant_id
    ):
        design = _design(repository, artifact, participant_id)
        exact, _, _ = verify_decomposition(artifact.generalized, design, 60)
        assert exact

    def test_explain_rejects_a_multi_row_frame(self, artifact, repository):
        design = _design(repository, artifact, "synthetic_p001")
        doubled = design.iloc[[0, 0]].reset_index(drop=True)
        with pytest.raises(ValueError, match="exactly one row"):
            explain_horizon(artifact.generalized, doubled, 60)


class TestDriverContent:
    def test_drivers_are_ranked_by_absolute_contribution(self, artifact, repository):
        design = _design(repository, artifact, "synthetic_p002")
        drivers, _, _ = explain_horizon(artifact.generalized, design, 60)
        magnitudes = [abs(driver.contribution_mg_dl) for driver in drivers]
        assert magnitudes == sorted(magnitudes, reverse=True)

    def test_negligible_contributions_are_omitted(self, artifact, repository):
        design = _design(repository, artifact, "synthetic_p002")
        drivers, _, _ = explain_horizon(artifact.generalized, design, 60)
        for driver in drivers:
            assert abs(driver.contribution_mg_dl) >= MIN_ABS_CONTRIBUTION_MG_DL

    def test_driver_count_is_capped(self, artifact, repository):
        design = _design(repository, artifact, "synthetic_p002")
        drivers, _, _ = explain_horizon(
            artifact.generalized, design, 60, max_drivers=3
        )
        # The cap applies to model features; a calibration term may be appended.
        assert len(drivers) <= 4

    def test_every_driver_has_a_non_empty_explanation(self, artifact, repository):
        design = _design(repository, artifact, "synthetic_p002")
        drivers, _, _ = explain_horizon(artifact.generalized, design, 60)
        for driver in drivers:
            assert driver.explanation.strip()

    def test_explanations_disclaim_causality(self, artifact, repository):
        design = _design(repository, artifact, "synthetic_p002")
        drivers, _, _ = explain_horizon(artifact.generalized, design, 60)
        feature_drivers = [
            driver
            for driver in drivers
            if driver.feature != "participant_calibration"
            and not driver.feature.endswith("_unavailable")
        ]
        assert feature_drivers
        for driver in feature_drivers:
            assert "not" in driver.explanation.lower()

    def test_no_raw_sklearn_column_names_leak_out(self, artifact, repository):
        """`missingindicator_x` must be rendered as `x_unavailable`."""
        design = _design(repository, artifact, "synthetic_p005")
        drivers, _, _ = explain_horizon(artifact.generalized, design, 60)
        for driver in drivers:
            assert not driver.feature.startswith(INDICATOR_PREFIX)

    def test_missing_modality_surfaces_as_an_unavailability_driver(
        self, artifact, repository
    ):
        """p005 has no heart-rate data at all."""
        design = _design(repository, artifact, "synthetic_p005")
        drivers, _, _ = explain_horizon(artifact.generalized, design, 60)
        names = [driver.feature for driver in drivers]
        assert any(name.endswith("_unavailable") for name in names)

    def test_calibration_offset_appears_as_its_own_driver(self, artifact, repository):
        design = _design(repository, artifact, "synthetic_p002")
        drivers, _, _ = explain_horizon(
            artifact.generalized, design, 60, calibration_offset_mg_dl=9.0
        )
        matching = [d for d in drivers if d.feature == "participant_calibration"]
        assert len(matching) == 1
        assert matching[0].contribution_mg_dl == pytest.approx(9.0)

    def test_a_negligible_calibration_offset_is_not_listed(self, artifact, repository):
        design = _design(repository, artifact, "synthetic_p002")
        drivers, _, _ = explain_horizon(
            artifact.generalized, design, 60, calibration_offset_mg_dl=0.01
        )
        assert not any(d.feature == "participant_calibration" for d in drivers)


class TestPeakHorizonSelection:
    def test_picks_the_largest_positive_delta(self):
        assert peak_horizon({30: 5.0, 60: 20.0, 90: 12.0, 120: 2.0}) == 60

    def test_falls_back_when_nothing_rises(self):
        assert peak_horizon({30: -5.0, 60: -2.0, 90: -8.0, 120: -1.0}) == 60

    def test_respects_an_explicit_default(self):
        assert peak_horizon({h: -1.0 for h in HORIZONS}, default=90) == 90

    def test_ties_resolve_to_a_real_horizon(self):
        assert peak_horizon({30: 10.0, 60: 10.0, 90: 1.0, 120: 1.0}) in HORIZONS
