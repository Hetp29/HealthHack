"""Batch A encoders: ways to turn a support set into predictive information.

HONEST SCOPE NOTE
-----------------
No deep-learning framework is installed (no torch / jax / tensorflow), and installing
one hours before a submission deadline to fit ~900 training episodes is not a trade I
am willing to make. So the "DeepSets" and "attention" candidates below are implemented
with scikit-learn, and the difference matters:

  * `DeepSetsEncoder` has a **fixed item embedding** (phi) -- a hand-specified
    permutation-invariant pooling of per-item statistics -- and a **learned aggregator**
    (rho, an MLP). A full DeepSets would also learn phi by backpropagation. This is
    therefore a DeepSets *architecture* with an unlearned phi, and is labelled as such
    everywhere it is reported.
  * `AttentionEncoder` is single-head attention whose scores come from a **learned
    diagonal metric and learned temperature**, fitted by bounded numerical optimisation
    on the training fold. That is mathematically a learned-kernel weighted average --
    equivalent to attention with a diagonal-metric dot-product score -- not a
    multi-head transformer block.

Both are legitimate members of their families and are the strongest versions runnable
here. Neither is described as something it is not.

Every encoder is fitted on TRAINING-fold episodes only and reads support sets that were
already filtered causally (see `support.py`).
"""

from __future__ import annotations

import numpy as np

from ml.config import HORIZONS
from research.batch_a.support import ITEM_KEYS, SupportSet

N_HORIZONS = len(HORIZONS)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _standardize(vectors: np.ndarray, query: np.ndarray, weights: np.ndarray | None = None):
    """Scale differences by the SUPPORT SET's own spread, optionally reweighted.

    Using the support set's spread (not a training-fold statistic) keeps the distance
    purely a function of that participant's own history.
    """
    scale = vectors.std(axis=0, ddof=0)
    scale[scale <= 1e-9] = 1.0
    diff = (vectors - query) / scale
    if weights is not None:
        diff = diff * weights
    return diff


def kernel_weights(
    support: SupportSet, k: int, metric: np.ndarray | None = None, temperature: float = 1.0
) -> tuple[np.ndarray, np.ndarray]:
    """(indices, weights) of the k nearest items under a Gaussian kernel."""
    diff = _standardize(support.vectors, support.query, metric)
    distances = np.sqrt(np.sum(diff**2, axis=1))
    order = np.argsort(distances)[:k]
    chosen = distances[order]
    bandwidth = float(np.median(chosen))
    if bandwidth <= 1e-9:
        bandwidth = 1.0
    weights = np.exp(-((chosen / (bandwidth * temperature)) ** 2))
    total = weights.sum()
    if total <= 0:
        weights = np.ones_like(weights) / max(len(weights), 1)
    else:
        weights = weights / total
    return order, weights


# --------------------------------------------------------------------------- #
# 1. Kernel-weighted retrieval (the mechanism already promoted in fs-1.3)
# --------------------------------------------------------------------------- #


class KernelRetrieval:
    """Kernel-weighted mean of the k nearest neighbours' observed horizon deltas."""

    name = "kernel_retrieval"
    n_parameters = 0

    def __init__(self, k: int = 5):
        self.k = k

    def fit(self, support_sets, episodes, targets):
        return self

    def encode(self, support: SupportSet) -> np.ndarray:
        if support.n == 0:
            return np.full(N_HORIZONS, np.nan)
        order, weights = kernel_weights(support, self.k)
        return weights @ support.deltas[order]


# --------------------------------------------------------------------------- #
# 2. Learned-metric retrieval
# --------------------------------------------------------------------------- #


class LearnedMetricRetrieval:
    """Kernel retrieval with a diagonal metric fitted on the training fold.

    Optimises the training-fold retrieval MAE over log-scale feature weights with
    L-BFGS-B and a tight bound (0.2x to 5x), so the metric cannot collapse onto a
    single feature. Seven parameters.
    """

    name = "learned_metric_retrieval"

    def __init__(self, k: int = 5):
        self.k = k
        self.metric = np.ones(len(ITEM_KEYS))

    @property
    def n_parameters(self) -> int:
        return len(ITEM_KEYS)

    def fit(self, support_sets, episodes, targets):
        from scipy.optimize import minimize

        usable = [
            (support_sets[(e.participant_id, e.meal_id)], targets[i])
            for i, e in enumerate(episodes)
            if support_sets[(e.participant_id, e.meal_id)].n >= 2
            and np.isfinite(targets[i]).all()
        ]
        if len(usable) < 40:
            return self

        def objective(log_weights: np.ndarray) -> float:
            metric = np.exp(log_weights)
            total = 0.0
            for support, target in usable:
                order, weights = kernel_weights(support, self.k, metric)
                prediction = weights @ support.deltas[order]
                total += float(np.mean(np.abs(prediction - target)))
            return total / len(usable)

        result = minimize(
            objective,
            np.zeros(len(ITEM_KEYS)),
            method="L-BFGS-B",
            bounds=[(np.log(0.2), np.log(5.0))] * len(ITEM_KEYS),
            options={"maxiter": 40},
        )
        self.metric = np.exp(result.x)
        return self

    def encode(self, support: SupportSet) -> np.ndarray:
        if support.n == 0:
            return np.full(N_HORIZONS, np.nan)
        order, weights = kernel_weights(support, self.k, self.metric)
        return weights @ support.deltas[order]


# --------------------------------------------------------------------------- #
# 3. Attention encoder (learned diagonal metric + learned temperature)
# --------------------------------------------------------------------------- #


class AttentionEncoder:
    """Single-head attention over the WHOLE support set, not just k neighbours.

    Scores are `-||(x_j - q) / s * w||^2 / tau`, softmaxed over all items. `w` (diagonal
    metric) and `tau` (temperature) are fitted together on the training fold. Unlike
    k-nearest retrieval this attends to every admissible item, letting a broad soft
    weighting emerge if that fits better.
    """

    name = "attention_encoder"

    def __init__(self):
        self.metric = np.ones(len(ITEM_KEYS))
        self.log_tau = 0.0

    @property
    def n_parameters(self) -> int:
        return len(ITEM_KEYS) + 1

    def _attend(self, support: SupportSet, metric, log_tau) -> np.ndarray:
        diff = _standardize(support.vectors, support.query, metric)
        scores = -np.sum(diff**2, axis=1) / np.exp(log_tau)
        scores = scores - scores.max()
        weights = np.exp(scores)
        weights = weights / max(weights.sum(), 1e-12)
        return weights @ support.deltas

    def fit(self, support_sets, episodes, targets):
        from scipy.optimize import minimize

        usable = [
            (support_sets[(e.participant_id, e.meal_id)], targets[i])
            for i, e in enumerate(episodes)
            if support_sets[(e.participant_id, e.meal_id)].n >= 2
            and np.isfinite(targets[i]).all()
        ]
        if len(usable) < 40:
            return self

        def objective(params: np.ndarray) -> float:
            metric, log_tau = np.exp(params[:-1]), params[-1]
            total = 0.0
            for support, target in usable:
                total += float(
                    np.mean(np.abs(self._attend(support, metric, log_tau) - target))
                )
            return total / len(usable)

        bounds = [(np.log(0.2), np.log(5.0))] * len(ITEM_KEYS) + [(np.log(0.1), np.log(10.0))]
        result = minimize(
            objective,
            np.zeros(len(ITEM_KEYS) + 1),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 40},
        )
        self.metric = np.exp(result.x[:-1])
        self.log_tau = float(result.x[-1])
        return self

    def encode(self, support: SupportSet) -> np.ndarray:
        if support.n == 0:
            return np.full(N_HORIZONS, np.nan)
        return self._attend(support, self.metric, self.log_tau)


# --------------------------------------------------------------------------- #
# 4. DeepSets-style encoder (fixed phi, learned rho)
# --------------------------------------------------------------------------- #


class DeepSetsEncoder:
    """Permutation-invariant pooling of per-item statistics, then a learned MLP.

    phi (FIXED): each item contributes its similarity difference from the query, its
    observed horizon deltas, and its age. Pooled by mean, max, min and inverse-distance
    weighted mean -- all permutation invariant.
    rho (LEARNED): a small MLP mapping the pooled vector to four horizon deltas.
    """

    name = "deepsets_encoder"

    def __init__(self, hidden: tuple[int, ...] = (32, 16), seed: int = 20261011):
        self.hidden = hidden
        self.seed = seed
        self.model = None
        self.scaler = None

    @property
    def n_parameters(self) -> int:
        if self.model is None:
            return 0
        return int(sum(c.size for c in self.model.coefs_) + sum(b.size for b in self.model.intercepts_))

    @staticmethod
    def pool(support: SupportSet) -> np.ndarray:
        """Fixed permutation-invariant item embedding and pooling (phi)."""
        if support.n == 0:
            return np.full(4 * (len(ITEM_KEYS) + N_HORIZONS + 1) + 1, np.nan)
        diff = _standardize(support.vectors, support.query)
        distances = np.sqrt(np.sum(diff**2, axis=1))
        items = np.column_stack([diff, support.deltas, support.age_days[:, None]])
        inverse = 1.0 / (1.0 + distances)
        inverse = inverse / inverse.sum()
        pooled = np.concatenate(
            [
                items.mean(axis=0),
                items.max(axis=0),
                items.min(axis=0),
                inverse @ items,
                [float(support.n)],
            ]
        )
        return pooled

    def fit(self, support_sets, episodes, targets):
        from sklearn.neural_network import MLPRegressor
        from sklearn.preprocessing import StandardScaler

        rows, labels = [], []
        for i, episode in enumerate(episodes):
            support = support_sets[(episode.participant_id, episode.meal_id)]
            if support.n == 0 or not np.isfinite(targets[i]).all():
                continue
            pooled = self.pool(support)
            if not np.isfinite(pooled).all():
                continue
            rows.append(pooled)
            labels.append(targets[i])
        if len(rows) < 60:
            return self

        X = np.vstack(rows)
        y = np.vstack(labels)
        self.scaler = StandardScaler().fit(X)
        self.model = MLPRegressor(
            hidden_layer_sizes=self.hidden,
            alpha=1.0,                 # strong L2: ~900 episodes
            learning_rate_init=0.01,
            max_iter=600,
            early_stopping=True,
            n_iter_no_change=20,
            validation_fraction=0.15,
            random_state=self.seed,
        ).fit(self.scaler.transform(X), y)
        return self

    def encode(self, support: SupportSet) -> np.ndarray:
        if self.model is None or support.n == 0:
            return np.full(N_HORIZONS, np.nan)
        pooled = self.pool(support)
        if not np.isfinite(pooled).all():
            return np.full(N_HORIZONS, np.nan)
        return np.asarray(
            self.model.predict(self.scaler.transform(pooled[None, :]))[0], dtype="float64"
        )


# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #


class ShuffledNeighbourControl:
    """Kernel retrieval over ANOTHER participant's support set.

    If the retrieval features carry genuine participant-specific signal, replacing the
    neighbours with someone else's history must destroy the gain. If performance holds
    up, the features were only encoding cohort-level information and the
    "personalization" claim would be false.
    """

    name = "control_shuffled_neighbours"
    n_parameters = 0

    def __init__(self, k: int = 5, seed: int = 20261011):
        self.k = k
        self.rng = np.random.default_rng(seed)
        self._pool: list[SupportSet] = []

    def fit(self, support_sets, episodes, targets):
        self._pool = [s for s in support_sets.values() if s.n >= self.k]
        return self

    def encode(self, support: SupportSet) -> np.ndarray:
        if not self._pool:
            return np.full(N_HORIZONS, np.nan)
        donor = self._pool[int(self.rng.integers(len(self._pool)))]
        swapped = SupportSet(
            vectors=donor.vectors, deltas=donor.deltas, curves=donor.curves,
            age_days=donor.age_days, query=support.query,
        )
        order, weights = kernel_weights(swapped, self.k)
        return weights @ swapped.deltas[order]
