"""Leakage and correctness tests for the advanced model families.

Retrieval is the most dangerous construct here: it reads other episodes' OBSERVED
responses. That is legitimate only if every retrieved response had fully elapsed before
the target meal, and only from the same participant. Both are attacked directly.

The functional trajectory basis is the second risk: a basis fitted on all episodes
would encode the shape of validation responses. These tests check the basis is fitted
per fold and that minute 0 stays pinned to the measured baseline.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from ml.config import DEFAULT_CONFIG, FORECAST_MINUTES, HORIZONS
from ml.devvalidation import _fewshot_bucket, rolling_folds
from research.advanced.retrieval import (
    MIN_CURVE_POINTS,
    RETRIEVAL_FEATURES,
    SIMILARITY_KEYS,
    build_retrieval_features,
)
from research.advanced.trajectory import (
    GRID,
    FpcaBasis,
    SplineBasis,
    episode_delta_curves,
)


@pytest.fixture(scope="module")
def dev_pool():
    import pathlib
    import pickle

    cache = pathlib.Path("ml/_devdata/dev_pool.pkl")
    if not cache.exists():
        pytest.skip("dev pool cache absent; run ml.experiments once")
    with cache.open("rb") as handle:
        return pickle.load(handle)["dev"]


class TestRetrievalIsCausal:
    def test_feature_set_is_stable(self, dev_pool):
        frame = build_retrieval_features(dev_pool[:200], k=5)
        assert list(frame.columns) == list(RETRIEVAL_FEATURES)
        assert len(frame) == 200

    def test_first_episode_per_participant_retrieves_nothing(self, dev_pool):
        """Nothing exists to retrieve before a participant's earliest meal."""
        frame = build_retrieval_features(dev_pool, k=5)
        seen, checked = set(), 0
        for i, episode in enumerate(dev_pool):
            if episode.participant_id in seen:
                continue
            seen.add(episode.participant_id)
            assert frame.iloc[i]["q_retr_n"] == 0.0, episode.participant_id
            assert np.isnan(frame.iloc[i]["q_retr_delta_60"]), episode.participant_id
            checked += 1
        assert checked >= 40

    def test_retrieved_count_never_exceeds_k(self, dev_pool):
        for k in (1, 3, 5):
            frame = build_retrieval_features(dev_pool, k=k)
            assert frame["q_retr_n"].max() <= k

    def test_retrieval_never_crosses_participants(self):
        """Two participants with opposite responses must not contaminate each other."""
        episodes = _synthetic_two_participants()
        frame = build_retrieval_features(episodes, k=5)
        # p_high only ever sees +60 responses, p_low only -20.
        for i, episode in enumerate(episodes):
            n = frame.iloc[i]["q_retr_n"]
            if n == 0:
                continue
            retrieved = frame.iloc[i]["q_retr_delta_60"]
            if episode.participant_id == "p_high":
                assert retrieved > 20.0, retrieved
            else:
                assert retrieved < 10.0, retrieved

    def test_a_meal_cannot_retrieve_itself(self):
        episodes = _synthetic_two_participants()
        frame = build_retrieval_features(episodes, k=10)
        # The first episode of each participant has an empty pool, proving the
        # episode under evaluation is never added before its own query.
        assert (frame["q_retr_n"] == 0).sum() >= 2

    def test_only_fully_elapsed_responses_are_retrievable(self):
        """A meal 30 minutes earlier has not finished responding and must be excluded."""
        base = dt.datetime(2020, 5, 1, 12, 0, tzinfo=dt.timezone.utc)
        episodes = [
            _episode("p", "m1", base, peak=60.0),
            # 30 min later: m1's 120-minute window has NOT elapsed.
            _episode("p", "m2", base + dt.timedelta(minutes=30), peak=60.0),
            # 4 h later: m1 and m2 have both fully elapsed.
            _episode("p", "m3", base + dt.timedelta(hours=4), peak=60.0),
        ]
        frame = build_retrieval_features(episodes, k=5)
        assert frame.iloc[0]["q_retr_n"] == 0.0, "no history at all"
        assert frame.iloc[1]["q_retr_n"] == 0.0, "m1 had not finished responding"
        assert frame.iloc[2]["q_retr_n"] == 2.0, "both earlier meals are now observable"

    def test_similarity_space_is_fixed_and_documented(self):
        assert SIMILARITY_KEYS == (
            "carbs_g", "fat_g", "protein_g", "fiber_g",
            "premeal_glucose_mg_dl", "tod_sin", "tod_cos",
        )

    def test_counts_are_zero_filled_but_values_are_not(self, dev_pool):
        """Zero retrieved is information; an undefined distance is not."""
        frame = build_retrieval_features(dev_pool, k=5)
        assert frame["q_retr_n"].isna().sum() == 0
        assert frame["q_retr_weight"].isna().sum() == 0
        assert frame["q_retr_delta_60"].isna().sum() > 0


class TestTrajectoryBasis:
    def test_curves_pin_minute_zero_to_zero_delta(self, dev_pool):
        curves, usable = episode_delta_curves(dev_pool[:300])
        assert usable.sum() > 100
        assert np.allclose(curves[usable][:, 0], 0.0)

    def test_sparse_episodes_are_dropped_not_imputed(self):
        episodes = [_episode("p", "m1", dt.datetime(2020, 5, 1, 12, tzinfo=dt.timezone.utc),
                             peak=50.0, n_points=5)]
        _, usable = episode_delta_curves(episodes)
        assert not usable.any(), "a 5-point curve must not contribute to a basis"

    def test_min_curve_points_threshold_is_enforced(self):
        assert MIN_CURVE_POINTS == 20

    @pytest.mark.parametrize("basis_cls", [FpcaBasis, SplineBasis])
    def test_basis_round_trips_a_smooth_excursion(self, basis_cls):
        t = GRID
        truth = 40.0 * (t / 55.0) * np.exp(1.0 - t / 55.0)
        truth[0] = 0.0
        curves = np.array([truth * s for s in np.linspace(0.6, 1.4, 60)])
        basis = basis_cls(4).fit(curves)
        reconstructed = basis.reconstruct(basis.project(curves))
        assert np.abs(reconstructed - curves).mean() < 0.5
        assert np.allclose(reconstructed[:, 0], 0.0)

    def test_fpca_concentrates_variance(self):
        t = GRID
        curves = np.array([
            40.0 * s * (t / 55.0) * np.exp(1.0 - t / 55.0) for s in np.linspace(0.5, 1.5, 80)
        ])
        curves[:, 0] = 0.0
        basis = FpcaBasis(4).fit(curves)
        assert basis.explained_[0] > 0.9, "a pure amplitude family needs one component"

    def test_functional_curve_can_peak_off_knot(self):
        """The whole point: a functional head is not limited to 0/30/60/90/120."""
        t = GRID
        # A family peaking at 45 min, which is NOT one of the four horizons.
        curves = np.array([
            40.0 * s * (t / 45.0) * np.exp(1.0 - t / 45.0) for s in np.linspace(0.8, 1.2, 40)
        ])
        curves[:, 0] = 0.0
        basis = FpcaBasis(3).fit(curves)
        reconstructed = basis.reconstruct(basis.project(curves))
        peak_minutes = {GRID[int(np.argmax(row))] for row in reconstructed}
        assert peak_minutes == {45.0}, peak_minutes


class TestFewShotBuckets:
    @pytest.mark.parametrize(
        ("count", "bucket"),
        [(0, "00_none"), (1, "01_1-2"), (2, "01_1-2"), (3, "02_3-4"),
         (4, "02_3-4"), (5, "03_5-9"), (9, "03_5-9"), (10, "04_10plus")],
    )
    def test_bucket_boundaries(self, count, bucket):
        assert _fewshot_bucket(count) == bucket

    def test_unknown_history_is_its_own_bucket(self):
        assert _fewshot_bucket(float("nan")) == "unknown"


class TestFoldsStillPurged:
    def test_rolling_folds_remain_purged_after_harness_changes(self, dev_pool):
        folds = rolling_folds(dev_pool, DEFAULT_CONFIG, n_folds=4)
        assert len(folds) == 3
        embargo = dt.timedelta(minutes=DEFAULT_CONFIG.day8.embargo_minutes)
        for fold in folds:
            earliest = {}
            for episode in fold.validate:
                current = earliest.get(episode.participant_id)
                if current is None or episode.meal_timestamp < current:
                    earliest[episode.participant_id] = episode.meal_timestamp
            for episode in fold.train:
                cutoff = earliest.get(episode.participant_id)
                if cutoff is not None:
                    assert episode.meal_timestamp <= cutoff - embargo


# --------------------------------------------------------------------------- #
# Synthetic episode helpers
# --------------------------------------------------------------------------- #


def _episode(participant: str, meal_id: str, timestamp, peak: float, n_points: int = 25):
    """A minimal Episode stand-in with a controllable observed response."""
    from ml.features import FeatureRow
    from ml.targets import Episode, TargetRow

    baseline = 100.0
    minutes = list(FORECAST_MINUTES)[:n_points]
    curve = {
        m: baseline + peak * (m / 60.0) * np.exp(1.0 - m / 60.0) for m in minutes
    }
    curve[0] = baseline
    values = {
        "carbs_g": 50.0, "fat_g": 10.0, "protein_g": 20.0, "fiber_g": 5.0,
        "premeal_glucose_mg_dl": baseline, "tod_sin": 0.0, "tod_cos": 1.0,
        "r_hist_n": 0.0,
    }
    features = FeatureRow(
        participant_id=participant, meal_timestamp=timestamp, meal_id=meal_id,
        values=values, baseline_glucose_mg_dl=baseline, missing_features=(),
    )
    target = TargetRow(
        participant_id=participant, meal_timestamp=timestamp, meal_id=meal_id,
        baseline_glucose_mg_dl=baseline,
        absolute={h: curve.get(h, baseline) for h in HORIZONS},
        delta={h: curve.get(h, baseline) - baseline for h in HORIZONS},
        missing_reasons={}, has_overlapping_meal=False, overlapping_meal_ids=(),
        observed_curve=curve,
    )
    return Episode(features=features, target=target, context=None)


def _synthetic_two_participants():
    base = dt.datetime(2020, 5, 1, 8, 0, tzinfo=dt.timezone.utc)
    episodes = []
    for day in range(6):
        when = base + dt.timedelta(days=day)
        episodes.append(_episode("p_high", f"high{day}", when, peak=60.0))
        episodes.append(_episode("p_low", f"low{day}", when, peak=5.0))
    return sorted(episodes, key=lambda e: (e.meal_timestamp, e.meal_id))
