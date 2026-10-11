"""Family 1: functional trajectory heads.

THE PROBLEM THIS ATTACKS
------------------------
The production model predicts four horizon deltas (30/60/90/120) and reconstructs the
curve by linear interpolation through those knots plus minute 0. A piecewise-linear
function attains its maximum AT a knot, so predicted time-to-peak can only ever be
0/30/60/90/120 minutes. That is a structural ceiling, not a fitting problem, and it is
why time-to-peak has been the model's weakest metric all along.

A functional head removes the ceiling: predict coefficients on a smooth basis spanning
the whole two-hour window, then evaluate the basis on the five-minute grid. The peak can
land anywhere.

TWO BASES
---------
**FPCA** -- principal components of the TRAINING-fold observed delta trajectories. Data
driven: the components are whatever shapes actually occur in this cohort. Few
coefficients capture most variance, which matters at ~900 episodes.

**B-spline** -- a fixed cubic basis with interior knots. Model driven: no dependence on
the training trajectories' covariance, so it cannot overfit the basis itself, but it
does not concentrate variance the way FPCA does.

LEAKAGE CONTROL
---------------
The basis is fitted on TRAINING-fold trajectories only, inside each fold. A basis fitted
on all episodes would leak the shape of validation responses. Episodes whose native CGM
coverage is too sparse are dropped from basis fitting (not imputed) so the components
describe measured curves.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.config import FORECAST_MINUTES, HORIZONS, Config
from ml.train import EpisodeMatrices, GeneralizedModel

#: Minimum native grid points an episode needs to contribute a trajectory target.
MIN_CURVE_POINTS = 20

GRID = np.asarray(FORECAST_MINUTES, dtype="float64")
HORIZON_INDEX = {h: list(FORECAST_MINUTES).index(h) for h in HORIZONS}


def episode_delta_curves(episodes) -> tuple[np.ndarray, np.ndarray]:
    """(curves, usable_mask) of observed deltas on the 25-point grid.

    Missing grid points are filled by linear interpolation BETWEEN NATIVE READINGS of
    the same episode, purely so a basis can be least-squares fitted. That is
    interpolation of already-observed past data within one completed response window --
    it is never used as a prediction target or counted as an independent measurement.
    """
    curves = np.full((len(episodes), GRID.size), np.nan)
    usable = np.zeros(len(episodes), dtype=bool)
    for i, episode in enumerate(episodes):
        observed = episode.target.observed_curve
        if len(observed) < MIN_CURVE_POINTS:
            continue
        baseline = episode.target.baseline_glucose_mg_dl
        minutes = np.asarray(sorted(observed), dtype="float64")
        values = np.asarray([observed[int(m)] - baseline for m in minutes], dtype="float64")
        curves[i] = np.interp(GRID, minutes, values)
        curves[i, 0] = 0.0  # minute 0 is the measured baseline by contract
        usable[i] = True
    return curves, usable


# --------------------------------------------------------------------------- #
# Bases
# --------------------------------------------------------------------------- #


class FpcaBasis:
    """Mean curve plus the leading principal components of training trajectories."""

    def __init__(self, n_components: int = 4):
        self.n_components = n_components
        self.mean_: np.ndarray | None = None
        self.components_: np.ndarray | None = None
        self.explained_: np.ndarray | None = None

    def fit(self, curves: np.ndarray) -> FpcaBasis:
        from sklearn.decomposition import PCA

        self.mean_ = curves.mean(axis=0)
        n = min(self.n_components, curves.shape[0] - 1, curves.shape[1])
        pca = PCA(n_components=n, random_state=20261011).fit(curves - self.mean_)
        self.components_ = pca.components_
        self.explained_ = pca.explained_variance_ratio_
        return self

    def project(self, curves: np.ndarray) -> np.ndarray:
        return (curves - self.mean_) @ self.components_.T

    def reconstruct(self, coefficients: np.ndarray) -> np.ndarray:
        reconstructed = self.mean_ + coefficients @ self.components_
        reconstructed[:, 0] = 0.0
        return reconstructed

    @property
    def size(self) -> int:
        return 0 if self.components_ is None else self.components_.shape[0]


class SplineBasis:
    """Fixed cubic B-spline basis, forced through zero at minute 0."""

    def __init__(self, n_interior: int = 4):
        self.n_interior = n_interior
        self.design_: np.ndarray | None = None

    def fit(self, curves: np.ndarray) -> SplineBasis:
        from scipy.interpolate import BSpline  # noqa: F401  (availability check)

        knots = np.linspace(GRID[0], GRID[-1], self.n_interior + 2)[1:-1]
        full = np.concatenate([[GRID[0]] * 4, knots, [GRID[-1]] * 4])
        degree = 3
        n_basis = len(full) - degree - 1
        design = np.zeros((GRID.size, n_basis))
        for j in range(n_basis):
            coefficients = np.zeros(n_basis)
            coefficients[j] = 1.0
            design[:, j] = BSpline(full, coefficients, degree, extrapolate=False)(GRID)
        design = np.nan_to_num(design)
        # Subtract each basis function's value at minute 0 so every curve starts at 0.
        design = design - design[0:1, :]
        self.design_ = design
        return self

    def project(self, curves: np.ndarray) -> np.ndarray:
        # Ridge-regularized least squares, so a near-singular design cannot explode.
        design = self.design_
        gram = design.T @ design + 1e-6 * np.eye(design.shape[1])
        return np.linalg.solve(gram, design.T @ curves.T).T

    def reconstruct(self, coefficients: np.ndarray) -> np.ndarray:
        reconstructed = coefficients @ self.design_.T
        reconstructed[:, 0] = 0.0
        return reconstructed

    @property
    def size(self) -> int:
        return 0 if self.design_ is None else self.design_.shape[1]


# --------------------------------------------------------------------------- #
# Heads
# --------------------------------------------------------------------------- #


class TrajectoryHead:
    """Predict basis coefficients from features, then evaluate the basis on the grid.

    `coefficient_model` is one regressor per basis coefficient. Ridge keeps the
    coefficient predictions smooth and is appropriate given how few independent
    episodes exist; the booster variant is available for comparison.
    """

    def __init__(self, basis, estimator: str = "ridge"):
        self.basis = basis
        self.estimator = estimator
        self.models: list = []

    def fit(self, train: EpisodeMatrices, episodes, config: Config) -> TrajectoryHead:
        curves, usable = episode_delta_curves(episodes)
        if usable.sum() < 30:
            raise ValueError("too few usable trajectories to fit a basis")
        self.basis.fit(curves[usable])
        coefficients = self.basis.project(curves[usable])

        features = train.features.loc[usable].reset_index(drop=True)
        self.models = []
        for j in range(coefficients.shape[1]):
            target = pd.DataFrame({30: coefficients[:, j]})
            # Reuse the production pipeline (median impute + indicators + scale + Ridge)
            # by treating one coefficient as a single-horizon target.
            model = _CoefficientRegressor(self.estimator, config)
            model.fit(features, coefficients[:, j])
            self.models.append(model)
        return self

    def predict_curves(self, features: pd.DataFrame) -> np.ndarray:
        coefficients = np.column_stack([m.predict(features) for m in self.models])
        return self.basis.reconstruct(coefficients)

    def predict_deltas(self, features: pd.DataFrame) -> dict[int, np.ndarray]:
        curves = self.predict_curves(features)
        return {h: curves[:, HORIZON_INDEX[h]] for h in HORIZONS}


class _CoefficientRegressor:
    """Ridge (with the production preprocessing) or a conservative booster."""

    def __init__(self, kind: str, config: Config):
        self.kind = kind
        self.config = config
        self.model = None

    def fit(self, features: pd.DataFrame, target: np.ndarray):
        if self.kind == "ridge":
            from ml.train import make_pipeline

            alphas = list(self.config.model.ridge_alphas)
            self.model = make_pipeline(
                alphas[len(alphas) // 2], self.config.model.random_seed
            ).fit(features, target)
        else:
            from ml.train import BOOSTER_PARAMS
            from sklearn.ensemble import HistGradientBoostingRegressor

            self.model = HistGradientBoostingRegressor(**BOOSTER_PARAMS).fit(
                features, target
            )
        return self

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.model.predict(features), dtype="float64")
