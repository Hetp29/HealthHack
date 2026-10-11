"""Curve reconstruction and metric definitions.

Two groups of functions:

1. **Curve math** -- how the four learned horizon predictions become the 25-point
   five-minute trajectory, and how peak / time-to-peak / iAUC are derived from it.
   These definitions are FROZEN by the master specification and are mirrored in
   `contracts/openapi.yaml`. They live here, in one place, so that the API response,
   the evaluation report and the tests cannot drift apart. Verified against
   hand-calculated arrays in `tests/test_metrics.py`.

2. **Error metrics** -- MAE / RMSE at each horizon, plus errors on the curve-derived
   summary statistics. Every one reports its own `n`, because a metric without a
   sample count is not interpretable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ml.config import FORECAST_MINUTES, HORIZONS, HORIZON_WINDOW_MINUTES

#: Knot minutes of the interpolated curve: minute 0 (measured) plus the four horizons.
KNOT_MINUTES: tuple[int, ...] = (0, *HORIZONS)


# --------------------------------------------------------------------------- #
# Curve reconstruction
# --------------------------------------------------------------------------- #


def interpolate_deltas(
    horizon_deltas: dict[int, float], minutes: tuple[int, ...] = FORECAST_MINUTES
) -> list[float]:
    """Linearly interpolate predicted deltas onto the five-minute grid.

    Knots are minute 0 (delta exactly 0, since minute 0 is the measured baseline) and
    the four learned horizons. Between knots the delta is linear.

    LIMITATION, disclosed in every response: a piecewise-linear curve attains its
    extremum at a knot, so the reported `time_to_peak_minutes` can only ever be one of
    0/30/60/90/120. True peak timing may fall between knots. This is honest about what
    a four-horizon model can resolve; manufacturing a smooth overshoot between knots
    would claim timing precision the model does not have.
    """
    missing = [h for h in HORIZONS if h not in horizon_deltas]
    if missing:
        raise ValueError(f"missing predicted deltas for horizons {missing}")

    knot_values = [0.0, *(float(horizon_deltas[h]) for h in HORIZONS)]
    return [
        float(np.interp(minute, KNOT_MINUTES, knot_values)) for minute in minutes
    ]


def build_curve(
    baseline_mg_dl: float,
    horizon_deltas: dict[int, float],
    *,
    clip_min: float | None = None,
    clip_max: float | None = None,
) -> tuple[list[int], list[float], bool]:
    """Return (minutes, absolute glucose, clipped) for the 25-point curve.

    `clipped` reports whether clipping actually engaged, so the response can disclose
    it rather than silently presenting a bounded curve as a raw model output.
    """
    deltas = interpolate_deltas(horizon_deltas)
    values = [baseline_mg_dl + delta for delta in deltas]

    clipped = False
    if clip_min is not None or clip_max is not None:
        lo = -math.inf if clip_min is None else clip_min
        hi = math.inf if clip_max is None else clip_max
        bounded = [min(max(value, lo), hi) for value in values]
        clipped = any(
            abs(b - v) > 1e-9 for b, v in zip(bounded, values)
        )
        values = bounded

    # Minute 0 is the measured baseline by contract and is never clipped away.
    values[0] = baseline_mg_dl

    return list(FORECAST_MINUTES), values, clipped


# --------------------------------------------------------------------------- #
# Curve-derived summary metrics (FROZEN definitions)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CurveSummary:
    """Peak / time-to-peak / iAUC derived from a sampled curve."""

    peak_mg_dl: float
    peak_delta_mg_dl: float
    time_to_peak_minutes: int
    incremental_auc_mg_dl_min: float


def incremental_auc(
    minutes: list[int] | tuple[int, ...],
    values: list[float],
    baseline_mg_dl: float,
) -> float:
    """Positive incremental AUC in mg/dL*min.

    Trapezoidal integration of `max(glucose - baseline, 0)` over the sampled grid.
    Clamping each endpoint to zero BEFORE integrating is what makes this strictly
    non-negative and distinct from signed net excursion: a dip below baseline
    contributes nothing rather than subtracting area.
    """
    if len(minutes) != len(values):
        raise ValueError("minutes and values must be the same length")
    if len(minutes) < 2:
        return 0.0

    total = 0.0
    for i in range(len(minutes) - 1):
        left = max(values[i] - baseline_mg_dl, 0.0)
        right = max(values[i + 1] - baseline_mg_dl, 0.0)
        total += 0.5 * (left + right) * (minutes[i + 1] - minutes[i])
    return float(total)


def summarize_curve(
    minutes: list[int] | tuple[int, ...],
    values: list[float],
    baseline_mg_dl: float | None = None,
) -> CurveSummary:
    """Derive the frozen summary metrics from a sampled curve.

    `baseline_mg_dl` defaults to the value at minute 0. Peak ties resolve to the
    EARLIEST minute, as the contract specifies.
    """
    if not values:
        raise ValueError("cannot summarize an empty curve")
    baseline = values[0] if baseline_mg_dl is None else baseline_mg_dl

    peak = max(values)
    # Earliest-tie peak: `next` over the ordered sequence stops at the first match.
    time_to_peak = next(m for m, v in zip(minutes, values) if v == peak)

    return CurveSummary(
        peak_mg_dl=float(peak),
        peak_delta_mg_dl=float(peak - baseline),
        time_to_peak_minutes=int(time_to_peak),
        incremental_auc_mg_dl_min=incremental_auc(minutes, values, baseline),
    )


def summarize_observed(
    observed_curve: dict[int, float], baseline_mg_dl: float
) -> CurveSummary | None:
    """Summarize an OBSERVED (sparse) curve for error measurement.

    Returns `None` when coverage is too thin to characterize a peak. Requires at least
    half the grid and an observation in the final third of the window, so an episode
    whose sensor dropped out mid-response cannot contribute a spuriously early peak.
    """
    if not observed_curve:
        return None
    minutes = sorted(observed_curve)
    if len(minutes) < len(FORECAST_MINUTES) // 2:
        return None
    if max(minutes) < (2 * HORIZON_WINDOW_MINUTES) // 3:
        return None
    values = [observed_curve[m] for m in minutes]
    return summarize_curve(minutes, values, baseline_mg_dl)


# --------------------------------------------------------------------------- #
# Error metrics
# --------------------------------------------------------------------------- #


def mae(errors: list[float] | np.ndarray) -> float | None:
    """Mean absolute error, or `None` when there is nothing to average."""
    array = np.asarray(errors, dtype="float64")
    if array.size == 0:
        return None
    return float(np.mean(np.abs(array)))


def rmse(errors: list[float] | np.ndarray) -> float | None:
    """Root mean squared error, or `None` when there is nothing to average."""
    array = np.asarray(errors, dtype="float64")
    if array.size == 0:
        return None
    return float(np.sqrt(np.mean(np.square(array))))


@dataclass
class HorizonErrors:
    """Accumulated signed errors (predicted - observed) at one horizon."""

    horizon_minutes: int
    errors: list[float]

    @property
    def n(self) -> int:
        return len(self.errors)

    def as_dict(self) -> dict:
        return {
            "horizon_minutes": self.horizon_minutes,
            "n": self.n,
            "mae_mg_dl": _round(mae(self.errors)),
            "rmse_mg_dl": _round(rmse(self.errors)),
            "interval_coverage": None,
        }


@dataclass
class CurveErrors:
    """Accumulated absolute errors on the curve-derived summary statistics."""

    peak: list[float]
    peak_delta: list[float]
    time_to_peak: list[float]
    iauc: list[float]

    @classmethod
    def empty(cls) -> CurveErrors:
        return cls(peak=[], peak_delta=[], time_to_peak=[], iauc=[])

    @property
    def n(self) -> int:
        return len(self.peak)

    def add(self, predicted: CurveSummary, observed: CurveSummary) -> None:
        self.peak.append(predicted.peak_mg_dl - observed.peak_mg_dl)
        self.peak_delta.append(predicted.peak_delta_mg_dl - observed.peak_delta_mg_dl)
        self.time_to_peak.append(
            float(predicted.time_to_peak_minutes - observed.time_to_peak_minutes)
        )
        self.iauc.append(
            predicted.incremental_auc_mg_dl_min - observed.incremental_auc_mg_dl_min
        )

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "peak_mae_mg_dl": _round(mae(self.peak)),
            "peak_delta_mae_mg_dl": _round(mae(self.peak_delta)),
            "time_to_peak_mae_minutes": _round(mae(self.time_to_peak)),
            "iauc_mae_mg_dl_min": _round(mae(self.iauc)),
        }


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)
