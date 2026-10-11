"""Families 2 and 3: retrieval-augmented prediction and few-shot set encoding.

IDEA
----
The production `r_hist_*` fingerprint averages ALL of a participant's earlier observed
responses. That throws away the most useful structure: a 90 g pasta meal tells you far
more about the next 90 g pasta meal than a 15 g snack does.

So instead of a plain mean, retrieve the participant's k most SIMILAR past meals and
take a kernel-weighted average of their observed response curves. This is a set encoder
over retrieved examples -- a non-parametric, per-participant predictor that needs no
fitted coefficients and therefore works for a participant who was never in training.

The retrieved prediction is exposed as FEATURES rather than blended at a fixed weight,
so the learner decides how much to trust it, and the trust can vary with how much
history exists and how close the matches are.

SIMILARITY
----------
Standardized Euclidean distance over a small, deliberately interpretable space:
carbohydrate, fat, protein, fibre, pre-meal glucose and cyclic time of day. Scales come
from the retrieval POOL (the participant's own past meals), so no training-fold
statistic leaks in.

Weights are Gaussian in distance, `exp(-(d/h)^2)`, with bandwidth `h` set to the median
retrieved distance -- adaptive, so a participant with a narrow dietary range still gets
sensible weighting.

LEAKAGE CONTROL
---------------
  * candidates are restricted to the SAME participant;
  * strict `meal_timestamp <` comparison, so a meal can never retrieve itself;
  * a candidate's response window must have fully elapsed before the target meal, so
    the retrieved curve was entirely observable at prediction time;
  * no training-fold labels are consulted -- retrieval reads only the participant's own
    past measurements, which is why it also works cold-start.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ml.config import FORECAST_MINUTES, HORIZON_WINDOW_MINUTES, HORIZONS

#: Keys of the similarity space. Order fixed so distances are reproducible.
SIMILARITY_KEYS = (
    "carbs_g",
    "fat_g",
    "protein_g",
    "fiber_g",
    "premeal_glucose_mg_dl",
    "tod_sin",
    "tod_cos",
)

GRID = list(FORECAST_MINUTES)

#: Minimum native grid points for a past response to be retrievable.
MIN_CURVE_POINTS = 20

RETRIEVAL_FEATURES = (
    *(f"q_retr_delta_{h}" for h in HORIZONS),
    "q_retr_n",
    "q_retr_weight",
    "q_retr_mean_distance",
    "q_retr_peak_delta",
    "q_retr_ttp",
    "q_retr_curve_spread",
)


def _observable_curve(episode) -> np.ndarray | None:
    """Observed delta curve on the 25-point grid, or None if coverage is too thin."""
    observed = episode.target.observed_curve
    if len(observed) < MIN_CURVE_POINTS:
        return None
    baseline = episode.target.baseline_glucose_mg_dl
    minutes = np.asarray(sorted(observed), dtype="float64")
    values = np.asarray([observed[int(m)] - baseline for m in minutes], dtype="float64")
    curve = np.interp(np.asarray(GRID, dtype="float64"), minutes, values)
    curve[0] = 0.0
    return curve


def build_retrieval_features(episodes, k: int = 5) -> pd.DataFrame:
    """Retrieval features for every episode, aligned to `episodes` order.

    One pass per participant. For episode *i*, the pool is that participant's earlier
    episodes whose response window had fully elapsed by `episodes[i].meal_timestamp`.
    """
    by_participant: dict[str, list[int]] = {}
    for index, episode in enumerate(episodes):
        by_participant.setdefault(episode.participant_id, []).append(index)

    rows = [dict.fromkeys(RETRIEVAL_FEATURES, np.nan) for _ in episodes]

    for indices in by_participant.values():
        indices.sort(key=lambda i: episodes[i].meal_timestamp)
        pool_vectors: list[np.ndarray] = []
        pool_curves: list[np.ndarray] = []
        pool_ready: list[dt.datetime] = []

        for position, index in enumerate(indices):
            target = episodes[index]
            t = target.meal_timestamp

            # Only pool entries whose response window had fully elapsed by `t`.
            available = [
                j for j, ready in enumerate(pool_ready) if ready <= t
            ]
            if available:
                matrix = np.vstack([pool_vectors[j] for j in available])
                query = _vector(target)
                scale = matrix.std(axis=0, ddof=0)
                scale[scale <= 1e-9] = 1.0
                distances = np.sqrt(
                    np.sum(((matrix - query) / scale) ** 2, axis=1)
                )
                order = np.argsort(distances)[:k]
                chosen = [available[j] for j in order]
                chosen_distances = distances[order]

                bandwidth = float(np.median(chosen_distances))
                if bandwidth <= 1e-9:
                    bandwidth = 1.0
                weights = np.exp(-((chosen_distances / bandwidth) ** 2))
                total = float(weights.sum())
                if total > 0:
                    curves = np.vstack([pool_curves[j] for j in chosen])
                    blended = (weights[:, None] * curves).sum(axis=0) / total
                    peak = float(blended.max())
                    row = rows[index]
                    for horizon in HORIZONS:
                        row[f"q_retr_delta_{horizon}"] = float(
                            blended[GRID.index(horizon)]
                        )
                    row["q_retr_n"] = float(len(chosen))
                    row["q_retr_weight"] = total
                    row["q_retr_mean_distance"] = float(chosen_distances.mean())
                    row["q_retr_peak_delta"] = peak
                    row["q_retr_ttp"] = float(GRID[int(np.argmax(blended))])
                    row["q_retr_curve_spread"] = float(curves.std(axis=0).mean())

            # Add THIS episode to the pool for later targets (never for itself).
            curve = _observable_curve(target)
            if curve is not None:
                pool_vectors.append(_vector(target))
                pool_curves.append(curve)
                pool_ready.append(
                    t + dt.timedelta(minutes=HORIZON_WINDOW_MINUTES)
                )

    frame = pd.DataFrame(rows, columns=list(RETRIEVAL_FEATURES), dtype="float64")
    # `q_retr_n` is a genuine count: zero retrieved is information, not a missing value.
    frame["q_retr_n"] = frame["q_retr_n"].fillna(0.0)
    frame["q_retr_weight"] = frame["q_retr_weight"].fillna(0.0)
    return frame


def _vector(episode) -> np.ndarray:
    values = episode.features.values
    return np.asarray(
        [float(values.get(key, 0.0)) for key in SIMILARITY_KEYS], dtype="float64"
    )
