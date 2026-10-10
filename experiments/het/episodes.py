"""Build one row per CGMacros meal: pre-meal features + post-meal glucose targets.

Target definitions mirror ml/config.py + ml/targets.py so numbers are comparable with
Ruhaan's pipeline: baseline = last Dexcom reading within 15 min before the meal,
target(h) = Dexcom reading nearest t+h within +-10 min, modelled as delta from baseline,
episode kept only if all four horizons exist. Overlap (another meal in (t, t+120]) is
tagged, not dropped.

Leakage rules:
  * every CGM / HR / activity feature reads only rows with timestamp <= t;
  * "personal response" features use only EARLIER meals whose full 120-min response
    had finished by t (prior_t + 120 <= t), so no target value of an ongoing meal leaks;
  * the meal photo is the one taken at the meal time (the "after" photos ~16 min later
    are not used: they would not exist when a forecast is requested).

Timestamps are source-local wall clock (time_basis = source_local_unknown_tz), so the
clock reading of the stored timestamp IS local time of day (data.timeutil.local_minutes_of_day).

Usage:  python -m experiments.het.episodes   ->  data/local/het_experiments/episodes.parquet
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DB = Path("data/glucotwin.db")
BIO = Path("data/official_raw/cgmacros_extracted/CGMacros/bio.csv")
OUT = Path("data/local/het_experiments/episodes.parquet")

HORIZONS = (30, 60, 90, 120)
BASELINE_TOL, TARGET_TOL = 15.0, 10.0


def _ts(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.str[:19])  # wall clock; see module docstring


def _load(conn, sql) -> pd.DataFrame:
    df = pd.read_sql(sql, conn)
    df["t"] = _ts(df["timestamp"])
    return df.drop(columns="timestamp")


def _bio() -> pd.DataFrame:
    b = pd.read_csv(BIO)
    b.columns = [c.strip() for c in b.columns]
    out = pd.DataFrame({
        "participant_id": b["subject"].map(lambda s: f"cgmacros_{int(s):03d}"),
        "age": b["Age"],
        "is_male": (b["Gender"].str.strip().str.upper() == "M").astype(float),
        "bmi": b["BMI"],
        "a1c": b["A1c PDL (Lab)"],
        "fasting_glucose": b["Fasting GLU - PDL (Lab)"],
        "fasting_insulin": b["Insulin"],
        "triglycerides": b["Triglycerides"],
        "hdl": b["HDL"],
    })
    for c in out.columns[1:]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out["homa_ir"] = out.fasting_glucose * out.fasting_insulin / 405.0
    return out


def _window(times: np.ndarray, vals: np.ndarray, t: np.datetime64, minutes: float):
    lo = np.searchsorted(times, t - np.timedelta64(int(minutes * 60), "s"), "left")
    hi = np.searchsorted(times, t, "right")
    return times[lo:hi], vals[lo:hi]


def _nearest(times: np.ndarray, vals: np.ndarray, t: np.datetime64, tol: float) -> float:
    i = np.searchsorted(times, t)
    best, best_d = np.nan, tol * 60 + 1e-9
    for j in (i - 1, i):
        if 0 <= j < len(times):
            d = abs((times[j] - t) / np.timedelta64(1, "s"))
            if d <= best_d:
                best, best_d = vals[j], d
    return best


def build() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    meals = _load(conn, "select * from meals").sort_values(["participant_id", "t"])
    cgm = _load(conn, "select participant_id, timestamp, glucose_mg_dl g from cgm")
    hr = _load(conn, "select participant_id, timestamp, heart_rate_bpm v from heart_rate")
    act = _load(conn, "select participant_id, timestamp, activity_value v from activity where activity_unit='MET'")
    consumed = pd.read_sql(
        "select entity_id meal_id, value_num amount_consumed_pct from source_attributes "
        "where attribute='amount_consumed_pct'", conn)
    if act.empty:  # unit label differs by build; fall back to any activity row
        act = _load(conn, "select participant_id, timestamp, activity_value v from activity")
    conn.close()

    rows = []
    for pid, pm in meals.groupby("participant_id", sort=True):
        c = cgm[cgm.participant_id == pid].sort_values("t")
        ct, cg = c.t.values, c.g.values.astype(float)
        h = hr[hr.participant_id == pid].sort_values("t")
        ht, hv = h.t.values, h.v.values.astype(float)
        a = act[act.participant_id == pid].sort_values("t")
        at, av = a.t.values, a.v.values.astype(float)
        pm = pm.reset_index(drop=True)
        done: list[dict] = []  # finished earlier episodes (for personal-response features)

        for k, meal in pm.iterrows():
            t = np.datetime64(meal.t)
            r = {"participant_id": pid, "meal_id": meal.meal_id, "t": meal.t,
                 "image_ref": meal.image_ref}

            # -- baseline + targets -------------------------------------------------
            bt, bv = _window(ct, cg, t, BASELINE_TOL)
            if len(bv) == 0:
                continue
            base = bv[-1]
            tg = {hh: _nearest(ct, cg, t + np.timedelta64(hh, "m"), TARGET_TOL) for hh in HORIZONS}
            if any(np.isnan(v) for v in tg.values()):
                continue
            r["baseline"] = base
            for hh, v in tg.items():
                r[f"y{hh}"] = v - base
            r["minutes_since_last_cgm"] = (t - bt[-1]) / np.timedelta64(1, "m")

            # -- pre-meal glucose context -----------------------------------------
            for mins, tag in ((30, "30m"), (60, "60m"), (180, "3h"), (1440, "24h"), (7 * 1440, "7d")):
                wt, wv = _window(ct, cg, t, mins)
                ok = len(wv) >= 2
                r[f"cgm_mean_{tag}"] = wv.mean() if ok else np.nan
                r[f"cgm_std_{tag}"] = wv.std(ddof=1) if ok else np.nan
                if tag in ("30m", "60m") and ok:
                    x = (wt - t) / np.timedelta64(1, "m")
                    r[f"cgm_slope_{tag}"] = np.polyfit(x, wv, 1)[0]
                elif tag in ("30m", "60m"):
                    r[f"cgm_slope_{tag}"] = np.nan
            _, w7 = _window(ct, cg, t, 7 * 1440)
            r["cgm_p90_7d"] = np.percentile(w7, 90) if len(w7) > 10 else np.nan
            r["cgm_tar140_7d"] = (w7 > 140).mean() if len(w7) > 10 else np.nan

            # -- meal composition ---------------------------------------------------
            for col in ("carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal"):
                r[col] = meal[col]
            kcal = meal.calories_kcal if meal.calories_kcal and meal.calories_kcal > 0 else np.nan
            r["carb_kcal_frac"] = 4 * meal.carbs_g / kcal if pd.notna(meal.carbs_g) else np.nan
            r["net_carbs_g"] = (meal.carbs_g or 0) - (meal.fiber_g or 0)

            # -- timing (local wall clock) -------------------------------------------
            mod = meal.t.hour * 60 + meal.t.minute
            r["tod_sin"], r["tod_cos"] = np.sin(2 * np.pi * mod / 1440), np.cos(2 * np.pi * mod / 1440)
            prev = pm.iloc[:k]
            if len(prev):
                gap = (meal.t - prev.t.iloc[-1]).total_seconds() / 60
                r["minutes_since_prior_meal"] = min(gap, 720)
                r["prior_meal_carbs_g"] = prev.carbs_g.iloc[-1]
                r["carbs_prior_6h"] = prev[prev.t >= meal.t - pd.Timedelta(hours=6)].carbs_g.sum()
            else:
                r["minutes_since_prior_meal"] = r["prior_meal_carbs_g"] = np.nan
                r["carbs_prior_6h"] = 0.0
            later = pm.iloc[k + 1:]
            r["has_overlap"] = bool(((later.t > meal.t) & (later.t <= meal.t + pd.Timedelta(minutes=120))).any())

            # -- wearables -------------------------------------------------------------
            _, w = _window(ht, hv, t, 30)
            r["hr_mean_30m"] = w.mean() if len(w) else np.nan
            _, w = _window(at, av, t, 60)
            r["met_mean_60m"] = w.mean() if len(w) else np.nan
            _, w = _window(at, av, t, 1440)
            r["met_mean_24h"] = w.mean() if len(w) else np.nan

            # -- personal response from FINISHED earlier meals ---------------------------
            fin = [d for d in done if d["t"] + pd.Timedelta(minutes=120) <= meal.t]
            r["n_prior_episodes"] = len(fin)
            if fin:
                f = pd.DataFrame(fin)
                for hh in HORIZONS:
                    r[f"pers_mean_y{hh}"] = f[f"y{hh}"].mean()
                f = f[f.carbs_g >= 10]
                r["pers_rise_per_carb"] = (f.peak / f.carbs_g).mean() if len(f) else np.nan
                r["pers_peak_mean"] = f.peak.mean() if len(f) else np.nan
            else:
                for hh in HORIZONS:
                    r[f"pers_mean_y{hh}"] = np.nan
                r["pers_rise_per_carb"] = r["pers_peak_mean"] = np.nan

            rows.append(r)
            done.append({"t": meal.t, "carbs_g": meal.carbs_g or 0.0,
                         "peak": max(r[f"y{hh}"] for hh in HORIZONS),
                         **{f"y{hh}": r[f"y{hh}"] for hh in HORIZONS}})

    ep = pd.DataFrame(rows)
    ep = ep.merge(_bio(), on="participant_id", how="left").merge(consumed, on="meal_id", how="left")
    ep["image_key"] = ep.image_ref.str.replace("cgmacros://", "", regex=False)
    return ep


def main() -> None:
    ep = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ep.to_parquet(OUT, index=False)
    print(f"wrote {OUT}: {len(ep)} episodes, {ep.participant_id.nunique()} participants, "
          f"{ep.has_overlap.mean():.0%} overlapping, {ep.image_key.notna().mean():.0%} with photo")


if __name__ == "__main__":
    main()
