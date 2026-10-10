"""Trajectory reconstruction methods (Batch C).

THE PROBLEM
-----------
fs-1.3 predicts four horizon deltas (30/60/90/120) and joins them with straight lines
through minute 0. A piecewise-linear curve attains its maximum AT a knot, so predicted
time-to-peak can only be 0/30/60/90/120. Measured reference-set time-to-peak MAE is
28.02 minutes -- the model's weakest metric.

An earlier unconstrained FPCA overlay improved timing but produced >3 direction changes
in 17.8% of curves, which no postprandial glucose response does. Batch C asks whether a
SHAPE-CONSTRAINED reconstruction can buy the timing without the oscillation.

WHAT EACH METHOD MAY CHANGE
---------------------------
  horizon-preserving (C1, C2, C5): the curve passes exactly through the base model's
    four horizon predictions. Horizon MAE is therefore identical to the base by
    construction, and these can only be judged on curve quality.
  shape-predicting (C3, C6): fit a parametric response whose peak time is continuous.
    These MAY move the horizon values, so horizon MAE is reported and must not degrade.

Every method receives only the base model's horizon predictions and the measured
baseline. None of them sees the observed post-meal trajectory.
"""

from __future__ import annotations

import numpy as np

from ml.config import FORECAST_MINUTES, HORIZONS

GRID = np.asarray(FORECAST_MINUTES, dtype="float64")
KNOTS = np.asarray([0, *HORIZONS], dtype="float64")
HORIZON_INDEX = [list(FORECAST_MINUTES).index(h) for h in HORIZONS]


def _knot_values(deltas: np.ndarray) -> np.ndarray:
    """(n, 5) knot deltas: 0 at minute 0, then the four horizon predictions."""
    return np.column_stack([np.zeros(deltas.shape[0]), deltas])


# --------------------------------------------------------------------------- #
# Baseline and C1/C2: horizon-preserving interpolation
# --------------------------------------------------------------------------- #


def linear(deltas: np.ndarray) -> np.ndarray:
    """The production reconstruction: piecewise linear through the knots."""
    values = _knot_values(deltas)
    return np.vstack([np.interp(GRID, KNOTS, row) for row in values])


def pchip(deltas: np.ndarray) -> np.ndarray:
    """C1 -- shape-preserving piecewise cubic Hermite (PCHIP).

    PCHIP is monotone-preserving: between two adjacent knots it introduces NO new
    extremum. So where the knot sequence is monotone it cannot create an off-knot peak
    -- it only smooths the corners. That limitation is the point of testing it: it
    isolates how much of the curve error is cornering versus genuine timing.
    """
    from scipy.interpolate import PchipInterpolator

    values = _knot_values(deltas)
    return np.vstack([PchipInterpolator(KNOTS, row, extrapolate=False)(GRID) for row in values])


def curvature_spline(deltas: np.ndarray, penalty: float = 1.0) -> np.ndarray:
    """C2 -- cubic reconstruction that penalises curvature but pins the knots exactly.

    Minimises `||D2 y||^2` (discrete second difference, i.e. roughness) subject to
    `y(knot) = predicted`. Solved as an equality-constrained least-squares problem via
    its KKT system, so the horizon anchors are satisfied to machine precision rather
    than approximately.
    """
    n = GRID.size
    second = np.zeros((n - 2, n))
    for i in range(n - 2):
        second[i, i : i + 3] = [1.0, -2.0, 1.0]
    quad = penalty * (second.T @ second) + 1e-8 * np.eye(n)

    anchor_rows = [0] + HORIZON_INDEX
    constraint = np.zeros((len(anchor_rows), n))
    for r, c in enumerate(anchor_rows):
        constraint[r, c] = 1.0

    kkt = np.block([
        [quad, constraint.T],
        [constraint, np.zeros((constraint.shape[0], constraint.shape[0]))],
    ])
    values = _knot_values(deltas)
    out = np.empty((deltas.shape[0], n))
    for i, row in enumerate(values):
        rhs = np.concatenate([np.zeros(n), row])
        out[i] = np.linalg.solve(kkt, rhs)[:n]
    return out


# --------------------------------------------------------------------------- #
# C3: unimodal rise-and-fall family
# --------------------------------------------------------------------------- #


def _surge(t: np.ndarray, amplitude: float, peak_time: float, shape: float) -> np.ndarray:
    """Scaled gamma-like surge: exactly 0 at t=0, `amplitude` at `peak_time`.

    `((t/tp) * exp(1 - t/tp)) ** s`. Unimodal by construction for s > 0, with a
    CONTINUOUS peak time -- the property the piecewise-linear curve cannot have.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        unit = (t / peak_time) * np.exp(1.0 - t / peak_time)
    unit = np.nan_to_num(unit, nan=0.0, posinf=0.0, neginf=0.0)
    return amplitude * np.power(np.clip(unit, 0.0, None), shape)


def unimodal_fit(deltas: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """C3 -- fit a unimodal surge to each row's four horizon predictions.

    Returns (curves, peak_times). Amplitude is allowed to be NEGATIVE so a predicted
    net decline is representable: the mission is explicit that a positive excursion must
    not be forced.
    """
    from scipy.optimize import least_squares

    targets = np.asarray(HORIZONS, dtype="float64")
    curves = np.empty((deltas.shape[0], GRID.size))
    peak_times = np.empty(deltas.shape[0])

    for i, row in enumerate(deltas):
        sign = 1.0 if np.max(np.abs(row)) == 0 or row[np.argmax(np.abs(row))] >= 0 else -1.0
        magnitude = np.abs(row)

        def residual(params, magnitude=magnitude):
            amplitude, peak_time, shape = params
            return _surge(targets, amplitude, peak_time, shape) - magnitude

        best, best_cost = None, np.inf
        for start_peak in (35.0, 60.0, 90.0):
            try:
                result = least_squares(
                    residual,
                    x0=[max(magnitude.max(), 1.0), start_peak, 1.5],
                    bounds=([0.0, 10.0, 0.5], [400.0, 120.0, 4.0]),
                    max_nfev=200,
                )
            except Exception:
                continue
            if result.cost < best_cost:
                best, best_cost = result.x, result.cost
        if best is None:
            curves[i] = np.interp(GRID, KNOTS, np.concatenate([[0.0], row]))
            peak_times[i] = GRID[int(np.argmax(curves[i]))]
            continue
        amplitude, peak_time, shape = best
        curves[i] = sign * _surge(GRID, amplitude, peak_time, shape)
        peak_times[i] = peak_time
    return curves, peak_times


# --------------------------------------------------------------------------- #
# C4: gated unimodal / flexible
# --------------------------------------------------------------------------- #


def gated(deltas: np.ndarray, flexible: np.ndarray, unimodal: np.ndarray) -> np.ndarray:
    """C4 -- choose unimodal unless the PREDICTED knots themselves are multimodal.

    The gate reads only the four predicted horizon deltas: if they already imply more
    than one turning point (a rise, a fall and a second rise), the unimodal family
    cannot represent them and the flexible reconstruction is used. The gate never sees
    the observed trajectory.
    """
    out = np.empty_like(flexible)
    knots = _knot_values(deltas)
    for i in range(deltas.shape[0]):
        diffs = np.diff(knots[i])
        signs = np.sign(diffs)
        nonzero = signs[signs != 0]
        turns = int((np.diff(nonzero) != 0).sum()) if nonzero.size > 1 else 0
        out[i] = flexible[i] if turns > 1 else unimodal[i]
    return out


# --------------------------------------------------------------------------- #
# C5 / C6 helpers
# --------------------------------------------------------------------------- #


def anchored_correction(base_curves: np.ndarray, correction: np.ndarray) -> np.ndarray:
    """C5 -- add a correction forced to zero at minute 0 and every horizon anchor.

    Implemented by subtracting the correction's own piecewise-linear interpolation
    through the anchors, which guarantees exact zero there regardless of what the
    correction model predicted.
    """
    anchor_rows = [0] + HORIZON_INDEX
    out = np.empty_like(base_curves)
    for i in range(base_curves.shape[0]):
        at_anchors = correction[i][anchor_rows]
        out[i] = base_curves[i] + correction[i] - np.interp(GRID, KNOTS, at_anchors)
    return out


def retimed_unimodal(deltas: np.ndarray, peak_times: np.ndarray) -> np.ndarray:
    """C6 -- unimodal curve whose peak time is SUPPLIED (from a direct predictor).

    Amplitude and shape are then fitted to the horizon predictions with the peak time
    held fixed, so a better timing estimate translates directly into a better curve.
    """
    from scipy.optimize import least_squares

    targets = np.asarray(HORIZONS, dtype="float64")
    curves = np.empty((deltas.shape[0], GRID.size))
    for i, row in enumerate(deltas):
        sign = 1.0 if np.max(np.abs(row)) == 0 or row[np.argmax(np.abs(row))] >= 0 else -1.0
        magnitude = np.abs(row)
        peak_time = float(np.clip(peak_times[i], 10.0, 120.0))

        def residual(params, magnitude=magnitude, peak_time=peak_time):
            amplitude, shape = params
            return _surge(targets, amplitude, peak_time, shape) - magnitude

        try:
            result = least_squares(
                residual, x0=[max(magnitude.max(), 1.0), 1.5],
                bounds=([0.0, 0.5], [400.0, 4.0]), max_nfev=150,
            )
            curves[i] = sign * _surge(GRID, result.x[0], peak_time, result.x[1])
        except Exception:
            curves[i] = np.interp(GRID, KNOTS, np.concatenate([[0.0], row]))
    return curves


# --------------------------------------------------------------------------- #
# Curve-quality diagnostics
# --------------------------------------------------------------------------- #


def turning_points(curves: np.ndarray) -> np.ndarray:
    """Direction changes per curve. A clean rise-and-fall has exactly 1."""
    out = np.zeros(curves.shape[0])
    for i, row in enumerate(curves):
        signs = np.sign(np.diff(row))
        nonzero = signs[signs != 0]
        out[i] = int((np.diff(nonzero) != 0).sum()) if nonzero.size > 1 else 0
    return out


def overshoot(curves: np.ndarray, deltas: np.ndarray) -> np.ndarray:
    """How far a curve exceeds the envelope of its own knot predictions, in mg/dL."""
    knots = _knot_values(deltas)
    lo, hi = knots.min(axis=1), knots.max(axis=1)
    above = np.clip(curves.max(axis=1) - hi, 0.0, None)
    below = np.clip(lo - curves.min(axis=1), 0.0, None)
    return np.maximum(above, below)


def horizon_deviation(curves: np.ndarray, deltas: np.ndarray) -> np.ndarray:
    """Max |curve(horizon) - predicted horizon|. Zero for horizon-preserving methods."""
    return np.abs(curves[:, HORIZON_INDEX] - deltas).max(axis=1)
