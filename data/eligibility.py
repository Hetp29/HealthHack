"""Descriptive "Day 8" meal-episode eligibility, for data audits only.

This counts how many meals COULD serve as a Day-8 style episode; it is not the ML
owner's inclusion rule (``ml/targets.py`` decides that). The thresholds mirror
``ml/config.py`` so the counts are comparable:

* history: at least 7 days of CGM before the meal, with >= 70% of the nominal 5-min
  readings present in that 7-day window;
* baseline: a CGM reading within 15 min before (or at) the meal;
* horizons: a CGM reading within +/-10 min of t+30, 60, 90 and 120 min;
* overlap: another meal within 120 min after the meal (tagged, still eligible).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HISTORY_DAYS = 7
MIN_HISTORY_COVERAGE = 0.70
NOMINAL_CADENCE_MIN = 5
BASELINE_TOLERANCE_MIN = 15
HORIZON_TOLERANCE_MIN = 10
HORIZONS_MIN = (30, 60, 90, 120)
OVERLAP_WINDOW_MIN = 120

_MIN = np.timedelta64(60, "s")


def _as_ns(values) -> np.ndarray:
    return np.sort(pd.to_datetime(pd.Series(values)).to_numpy(dtype="datetime64[ns]"))


def _has_reading_within(cgm: np.ndarray, targets: np.ndarray, before: float, after: float) -> np.ndarray:
    lo = np.searchsorted(cgm, targets - np.timedelta64(int(before * 60), "s"), side="left")
    hi = np.searchsorted(cgm, targets + np.timedelta64(int(after * 60), "s"), side="right")
    return hi > lo


def meal_episodes(cgm_times, meal_times) -> pd.DataFrame:
    """One row per meal with each criterion as a boolean column."""
    cgm = _as_ns(cgm_times)
    meals = _as_ns(meal_times)
    out = pd.DataFrame({"meal_time": meals})
    if len(meals) == 0:
        for col in ("has_7d_history", "history_coverage_ok", "baseline_ok", "horizons_ok", "eligible", "overlap"):
            out[col] = pd.Series(dtype=bool)
        out["history_coverage"] = pd.Series(dtype=float)
        return out
    if len(cgm) == 0:
        first = None
    else:
        first = cgm[0]
    hist = np.timedelta64(HISTORY_DAYS * 24 * 60, "m")
    out["has_7d_history"] = (meals - first >= hist) if first is not None else False
    n_hist = np.searchsorted(cgm, meals, side="left") - np.searchsorted(cgm, meals - hist, side="left")
    expected = HISTORY_DAYS * 24 * 60 / NOMINAL_CADENCE_MIN
    out["history_coverage"] = np.round(n_hist / expected, 4)
    out["history_coverage_ok"] = out["has_7d_history"] & (out["history_coverage"] >= MIN_HISTORY_COVERAGE)
    out["baseline_ok"] = _has_reading_within(cgm, meals, BASELINE_TOLERANCE_MIN, 0)
    horizons_ok = np.ones(len(meals), dtype=bool)
    for h in HORIZONS_MIN:
        horizons_ok &= _has_reading_within(cgm, meals + h * _MIN, HORIZON_TOLERANCE_MIN, HORIZON_TOLERANCE_MIN)
    out["horizons_ok"] = horizons_ok
    nxt = np.append(meals[1:], np.datetime64("NaT"))
    gap = (nxt - meals) / _MIN
    out["overlap"] = np.nan_to_num(gap, nan=np.inf) <= OVERLAP_WINDOW_MIN
    out["eligible"] = out["history_coverage_ok"] & out["baseline_ok"] & out["horizons_ok"]
    return out


def summarize(episodes: pd.DataFrame, complete_macros: np.ndarray | None = None) -> dict:
    n = len(episodes)
    eligible = episodes["eligible"] if n else pd.Series(dtype=bool)
    summary = {
        "candidate_meals": n,
        "with_7d_history": int(episodes["has_7d_history"].sum()) if n else 0,
        "history_coverage_ok": int(episodes["history_coverage_ok"].sum()) if n else 0,
        "baseline_ok": int(episodes["baseline_ok"].sum()) if n else 0,
        "horizons_ok": int(episodes["horizons_ok"].sum()) if n else 0,
        "eligible_episodes": int(eligible.sum()) if n else 0,
        "eligible_without_overlap": int((eligible & ~episodes["overlap"]).sum()) if n else 0,
        "first_eligible_meal_time": str(episodes.loc[eligible, "meal_time"].min()) if n and eligible.any() else "",
    }
    if complete_macros is not None:
        summary["eligible_with_complete_macros"] = int((eligible & complete_macros).sum()) if n else 0
    return summary
