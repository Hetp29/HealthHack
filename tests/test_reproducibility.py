"""Determinism and train/inference transform consistency.

The master specification's acceptance gate requires that artifacts "train
deterministically from a fresh checkout". These tests check the three places
non-determinism actually creeps in: the fixture seed, the fitted coefficients, and
the fitted transforms being reused rather than recomputed at serve time.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
import sys

import numpy as np
import pytest

from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devfixture import SyntheticDevRepository, _stable_offset
from ml.features import MealComposition, build_features, rows_to_frame
from ml.inference import forecast
from ml.train import episodes_to_matrices, train
from ml.targets import build_episodes


def _fixture_digest(repository) -> str:
    parts = []
    for summary in repository.list_participants():
        history = repository.get_history(summary.participant_id)
        parts.append(
            f"{summary.participant_id}:{len(history.cgm)}:"
            f"{sum(point.glucose_mg_dl for point in history.cgm):.6f}:"
            f"{len(history.meals)}:{len(history.heart_rate)}"
        )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _model_digest(artifact) -> str:
    parts = []
    for horizon in HORIZONS:
        _, coefficients, intercept = artifact.generalized.coefficients(horizon)
        parts.append(
            f"{horizon}:{artifact.generalized.chosen_alpha[horizon]}:{intercept:.12f}:"
            + ",".join(f"{c:.12f}" for c in coefficients)
        )
    for participant_id in sorted(artifact.personalization.calibrations):
        calibration = artifact.personalization.calibrations[participant_id]
        parts.append(
            f"{participant_id}:{calibration.personalized}:"
            + ",".join(f"{calibration.offsets[h]:.12f}" for h in HORIZONS)
        )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


class TestFixtureDeterminism:
    def test_two_instances_agree(self):
        assert _fixture_digest(SyntheticDevRepository()) == _fixture_digest(
            SyntheticDevRepository()
        )

    def test_stable_offset_does_not_use_salted_hashing(self):
        """Builtin `hash()` is salted per process; a digest is not."""
        expected = _stable_offset("synthetic_p001")
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from ml.devfixture import _stable_offset;"
                "print(_stable_offset('synthetic_p001'))",
            ],
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": "12345", "PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 0, result.stderr
        assert int(result.stdout.strip()) == expected

    def test_a_different_seed_produces_different_data(self):
        assert _fixture_digest(SyntheticDevRepository(seed=1)) != _fixture_digest(
            SyntheticDevRepository(seed=2)
        )

    def test_participants_are_independently_seeded(self):
        """Changing one participant must not perturb the others' data."""
        repository = SyntheticDevRepository()
        other = SyntheticDevRepository()
        for participant_id in ("synthetic_p001", "synthetic_p003"):
            a = repository.get_history(participant_id)
            b = other.get_history(participant_id)
            assert [p.glucose_mg_dl for p in a.cgm] == [p.glucose_mg_dl for p in b.cgm]


class TestModelDeterminism:
    def test_two_training_runs_produce_identical_models(self):
        first = train(SyntheticDevRepository(), DEFAULT_CONFIG)
        second = train(SyntheticDevRepository(), DEFAULT_CONFIG)
        assert _model_digest(first) == _model_digest(second)

    def test_predictions_are_identical_across_runs(self):
        repository = SyntheticDevRepository()
        episodes, _ = build_episodes(repository, DEFAULT_CONFIG)
        design = episodes_to_matrices(episodes[:10]).features

        first = train(SyntheticDevRepository(), DEFAULT_CONFIG).generalized.predict_deltas(design)
        second = train(SyntheticDevRepository(), DEFAULT_CONFIG).generalized.predict_deltas(design)
        for horizon in HORIZONS:
            np.testing.assert_allclose(first[horizon], second[horizon], rtol=0, atol=0)


class TestTransformConsistency:
    """Train-time and serve-time transforms must be the same fitted object."""

    @pytest.fixture(scope="class")
    def artifact(self):
        return train(SyntheticDevRepository(), DEFAULT_CONFIG)

    def test_imputer_statistics_are_reused_not_recomputed(self, artifact):
        """Predicting one row alone must match predicting it inside a batch.

        If any transform were refitted at inference, a single-row frame would be
        standardized against itself and the prediction would differ.
        """
        repository = SyntheticDevRepository()
        episodes, _ = build_episodes(repository, DEFAULT_CONFIG)
        batch = episodes_to_matrices(episodes[:20]).features

        batched = artifact.generalized.predict_deltas(batch)
        for index in (0, 7, 19):
            single = artifact.generalized.predict_deltas(
                batch.iloc[[index]].reset_index(drop=True)
            )
            for horizon in HORIZONS:
                assert single[horizon][0] == pytest.approx(
                    batched[horizon][index], abs=1e-9
                )

    def test_a_missing_feature_at_serve_time_is_imputed_with_training_medians(
        self, artifact
    ):
        repository = SyntheticDevRepository()
        episodes, _ = build_episodes(repository, DEFAULT_CONFIG)
        design = episodes_to_matrices(episodes[:1]).features.copy()
        design.loc[0, "hr_mean_30min_bpm"] = np.nan

        # Must not raise, and must produce finite output.
        predictions = artifact.generalized.predict_deltas(design)
        for horizon in HORIZONS:
            assert np.isfinite(predictions[horizon][0])

    def test_scaled_values_are_centred_on_the_training_mean(self, artifact):
        """`explain` relies on this: a scaled value is a deviation from training mean."""
        repository = SyntheticDevRepository()
        episodes, _ = build_episodes(repository, DEFAULT_CONFIG)
        design = episodes_to_matrices(episodes).features
        scaled = artifact.generalized.transform_to_scaled(60, design)
        # The training fold IS this full set here, so column means must be ~0.
        np.testing.assert_allclose(scaled.mean(axis=0), 0.0, atol=1e-9)

    def test_forecast_is_deterministic(self, artifact):
        repository = SyntheticDevRepository()
        history = repository.get_history("synthetic_p001")
        summary = repository.get_participant("synthetic_p001")
        meal = history.meals[-1]
        composition = MealComposition.from_meal_record(meal)

        first = forecast(artifact, history, meal.timestamp, composition, participant=summary)
        second = forecast(artifact, history, meal.timestamp, composition, participant=summary)
        assert first.to_contract_dict() == second.to_contract_dict()


class TestArtifactRoundTrip:
    def test_feature_set_mismatch_is_refused(self, tmp_path):
        """A stale artifact must raise, not serve confident nonsense."""
        import joblib

        from ml.train import load_artifact

        artifact = train(SyntheticDevRepository(), DEFAULT_CONFIG)
        artifact.feature_set_version = "fs-does-not-exist"
        path = tmp_path / "stale.joblib"
        joblib.dump(artifact, path)

        with pytest.raises(ValueError, match="feature_set_version"):
            load_artifact(path)

    def test_feature_name_mismatch_is_refused(self, tmp_path):
        import joblib

        from ml.train import load_artifact

        artifact = train(SyntheticDevRepository(), DEFAULT_CONFIG)
        artifact.feature_names = ("carbs_g",)
        path = tmp_path / "reordered.joblib"
        joblib.dump(artifact, path)

        with pytest.raises(ValueError, match="feature names"):
            load_artifact(path)

    def test_manifest_records_provenance(self):
        artifact = train(SyntheticDevRepository(), DEFAULT_CONFIG)
        manifest = artifact.manifest()
        assert manifest["feature_names"] == list(artifact.feature_names)
        assert manifest["horizons"] == list(HORIZONS)
        assert manifest["data_source"] == "synthetic_dev"
        assert manifest["trained_at"].endswith("Z")
        assert dt.datetime.fromisoformat(
            manifest["trained_at"].replace("Z", "+00:00")
        ).tzinfo is not None
        assert "config" in manifest
        assert manifest["training_summary"]["episodes"] > 0
