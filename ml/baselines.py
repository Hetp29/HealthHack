"""Benchmark models.

**Model 0 -- persistence.** Predicts that glucose does not move: delta = 0 at every
horizon, so predicted absolute glucose equals the measured pre-meal baseline. This is
the benchmark the master specification requires. It is not a strawman: over a
two-hour window with real sensor noise it is a genuinely hard floor for small meals,
and any model that cannot beat it has learned nothing about meal response.

**Cohort mean delta.** Predicts each horizon's training-fold mean delta, ignoring the
meal entirely. Included because it separates two very different claims: "the model
knows a meal raises glucose" (beats persistence) from "the model knows THIS meal and
THIS person" (beats the cohort mean). Reporting only against persistence would
flatter the model.

Both expose the same `predict_deltas` interface as the trained models, so
`ml/evaluate.py` scores them through one code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ml.config import HORIZONS


@dataclass
class PersistenceBaseline:
    """Model 0: predicted delta is identically zero at every horizon."""

    name: str = "persistence"

    def fit(self, features: pd.DataFrame, deltas: pd.DataFrame) -> PersistenceBaseline:
        # Nothing to learn; present so the training loop can treat all models alike.
        return self

    def predict_deltas(self, features: pd.DataFrame) -> dict[int, np.ndarray]:
        n = len(features)
        return {horizon: np.zeros(n, dtype="float64") for horizon in HORIZONS}


@dataclass
class CohortMeanDeltaBaseline:
    """Predicts the training-fold mean delta at each horizon, ignoring all features."""

    name: str = "cohort_mean_delta"
    means: dict[int, float] = field(default_factory=dict)

    def fit(self, features: pd.DataFrame, deltas: pd.DataFrame) -> CohortMeanDeltaBaseline:
        # Means come from the TRAINING fold only; nothing here sees held-out data.
        self.means = {
            horizon: float(deltas[horizon].dropna().mean())
            if deltas[horizon].notna().any()
            else 0.0
            for horizon in HORIZONS
        }
        return self

    def predict_deltas(self, features: pd.DataFrame) -> dict[int, np.ndarray]:
        n = len(features)
        return {
            horizon: np.full(n, self.means.get(horizon, 0.0), dtype="float64")
            for horizon in HORIZONS
        }
