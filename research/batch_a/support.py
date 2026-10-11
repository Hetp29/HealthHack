"""Support sets: each episode's admissible historical meal-response examples.

A "support set" for a target meal is the participant's OWN earlier meals whose full
120-minute response had already elapsed at the target's timestamp. Every Batch A
candidate consumes this same object, so differences between candidates are differences
in how the set is ENCODED, never in which history they were allowed to see.

CAUSAL FILTER (identical to the promoted fs-1.3 retrieval path):
  * same participant only;
  * strict `meal_timestamp <` -- a meal is never in its own support set;
  * `meal_timestamp + 120 min < target` -- the whole response was observable;
  * >= 20 native grid points, else the curve is not characterized well enough.

Built once for the whole development pool and indexed by (participant_id, meal_id), so
folds slice it rather than recomputing. Fold membership cannot influence a support set.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from ml.config import FORECAST_MINUTES, HORIZON_WINDOW_MINUTES, HORIZONS

#: Per-item similarity space. Fixed order so metrics and distances are reproducible.
ITEM_KEYS = (
    "carbs_g",
    "fat_g",
    "protein_g",
    "fiber_g",
    "premeal_glucose_mg_dl",
    "tod_sin",
    "tod_cos",
)

MIN_CURVE_POINTS = 20
GRID = list(FORECAST_MINUTES)


@dataclass
class SupportSet:
    """Admissible history for one target episode."""

    #: (n_items, 7) similarity vectors of the earlier meals
    vectors: np.ndarray
    #: (n_items, 4) observed deltas at 30/60/90/120 for those meals
    deltas: np.ndarray
    #: (n_items, 25) observed delta curves
    curves: np.ndarray
    #: (n_items,) days elapsed between that meal and the target
    age_days: np.ndarray
    #: the target's own similarity vector
    query: np.ndarray

    @property
    def n(self) -> int:
        return self.vectors.shape[0]


def _query_vector(episode) -> np.ndarray:
    values = episode.features.values
    return np.asarray(
        [float(values.get(key, 0.0)) for key in ITEM_KEYS], dtype="float64"
    )


def _observed(episode) -> tuple[np.ndarray, np.ndarray] | None:
    """(delta curve on the 25-grid, horizon deltas) or None if coverage is too thin."""
    observed = episode.target.observed_curve
    if len(observed) < MIN_CURVE_POINTS:
        return None
    baseline = episode.target.baseline_glucose_mg_dl
    minutes = np.asarray(sorted(observed), dtype="float64")
    values = np.asarray([observed[int(m)] - baseline for m in minutes], dtype="float64")
    curve = np.interp(np.asarray(GRID, dtype="float64"), minutes, values)
    curve[0] = 0.0
    horizons = np.asarray([curve[GRID.index(h)] for h in HORIZONS], dtype="float64")
    return curve, horizons


def build_support_sets(episodes) -> dict[tuple[str, str], SupportSet]:
    """One support set per episode, built strictly causally."""
    by_participant: dict[str, list[int]] = {}
    for index, episode in enumerate(episodes):
        by_participant.setdefault(episode.participant_id, []).append(index)

    out: dict[tuple[str, str], SupportSet] = {}
    empty = (
        np.zeros((0, len(ITEM_KEYS))),
        np.zeros((0, len(HORIZONS))),
        np.zeros((0, len(GRID))),
        np.zeros(0),
    )

    for indices in by_participant.values():
        indices.sort(key=lambda i: episodes[i].meal_timestamp)
        pool_vectors: list[np.ndarray] = []
        pool_deltas: list[np.ndarray] = []
        pool_curves: list[np.ndarray] = []
        pool_ready: list[dt.datetime] = []
        pool_times: list[dt.datetime] = []

        for index in indices:
            target = episodes[index]
            t = target.meal_timestamp
            usable = [j for j, ready in enumerate(pool_ready) if ready <= t]
            key = (target.participant_id, target.meal_id)
            if usable:
                out[key] = SupportSet(
                    vectors=np.vstack([pool_vectors[j] for j in usable]),
                    deltas=np.vstack([pool_deltas[j] for j in usable]),
                    curves=np.vstack([pool_curves[j] for j in usable]),
                    age_days=np.asarray(
                        [(t - pool_times[j]).total_seconds() / 86400.0 for j in usable],
                        dtype="float64",
                    ),
                    query=_query_vector(target),
                )
            else:
                out[key] = SupportSet(*empty, query=_query_vector(target))

            observed = _observed(target)
            if observed is not None:
                curve, horizons = observed
                pool_vectors.append(_query_vector(target))
                pool_deltas.append(horizons)
                pool_curves.append(curve)
                pool_times.append(t)
                pool_ready.append(t + dt.timedelta(minutes=HORIZON_WINDOW_MINUTES))

    return out
