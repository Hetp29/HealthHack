"""Candidate physiological signals, with explicit evidence levels.

EVIDENCE LEVELS (mission Part I §2)
  A  directly observed        -- measured CGM / meal log / HR / activity / lab value
  B  empirical proxy          -- derived from observed data, represents a biological
                                 process only indirectly
  C  latent predictive state  -- estimated from a model, no independent physiological
                                 ground truth
  D  not identifiable         -- needs unobserved insulin kinetics, gastric emptying,
                                 or tracer data. NOT implemented as measured physiology.

Hard rules enforced throughout:
  * every signal is a function of observations at or BEFORE the meal timestamp;
  * the "historical response" group reads earlier EPISODES' observed targets, which is
    legitimate (those responses had already happened) but is the single most
    leakage-prone group, so it is filtered on a strict `<` timestamp comparison and
    tested adversarially;
  * a signal that cannot be computed returns NaN. It is never zero-filled, because a
    zero would be read as a measurement.

NAMING: every signal name is prefixed by its group so ablations can select groups by
prefix: g_ glucose dynamics, m_ meal-on-board, a_ absorption/interaction,
r_ historical response, p_ participant profile, v_ activity/autonomic.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import numpy as np

from ml.schema import ParticipantHistory

# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SignalSpec:
    name: str
    group: str
    evidence: str
    units: str
    definition: str
    interpretation: str
    missing_when: str


SIGNAL_SPECS: list[SignalSpec] = []


def _spec(name, group, evidence, units, definition, interpretation, missing_when):
    SIGNAL_SPECS.append(
        SignalSpec(name, group, evidence, units, definition, interpretation, missing_when)
    )


# ---- group g: glucose dynamic state (Level A) ---- #
_spec("g_slope_10", "glucose", "A", "mg/dL/min",
      "OLS slope of native CGM over [t-10, t]",
      "very recent glucose momentum", "<2 readings in window")
_spec("g_slope_15", "glucose", "A", "mg/dL/min",
      "OLS slope over [t-15, t]", "short-horizon momentum", "<2 readings")
_spec("g_slope_60", "glucose", "A", "mg/dL/min",
      "OLS slope over [t-60, t]", "longer pre-meal drift", "<2 readings")
_spec("g_accel", "glucose", "A", "mg/dL/min^2",
      "(slope_15 - slope_30) / 15", "curvature: is the rise accelerating or easing",
      "either slope missing")
_spec("g_rise_fraction", "glucose", "A", "fraction",
      "share of consecutive positive differences among the last 6 readings <= t",
      "trend persistence, robust to a single noisy reading", "<3 readings")
_spec("g_cv_60", "glucose", "A", "fraction",
      "std/mean of native CGM over [t-60, t]",
      "short-term relative variability", "<2 readings or mean<=0")
_spec("g_range_120", "glucose", "A", "mg/dL",
      "max-min of native CGM over [t-120, t]", "recent excursion span", "<2 readings")
_spec("g_dev_from_night", "glucose", "B", "mg/dL",
      "premeal glucose minus the participant's median 02:00-06:00 local glucose over all "
      "observations strictly before t",
      "how elevated this person is versus their own overnight floor; a within-person "
      "reference rather than a cohort one", "no prior overnight readings")

# ---- group m: meal-on-board (Level B) ---- #
_spec("m_carb_mob_exp60", "mob", "B", "g (effective)",
      "sum over prior meals j of carbs_j * exp(-(t - t_j)/60min), 4 h lookback",
      "residual carbohydrate still plausibly being absorbed, exponential kernel",
      "never missing; 0.0 means no logged prior meal in the window")
_spec("m_carb_mob_exp120", "mob", "B", "g (effective)",
      "same with a 120-minute time constant",
      "slower absorption component", "never missing")
_spec("m_fat_mob_exp120", "mob", "B", "g (effective)",
      "same kernel applied to fat", "residual fat, a gastric-emptying delay proxy",
      "never missing")
_spec("m_meals_4h", "mob", "A", "count",
      "number of logged meals in (t-4h, t)", "recent eating density", "never missing")
_spec("m_carbs_24h", "mob", "A", "g",
      "total logged carbohydrate in (t-24h, t)", "daily carbohydrate burden",
      "never missing")

# ---- group a: absorption shape and macronutrient interaction (Level B) ---- #
_spec("a_carb_sat", "absorption", "B", "g",
      "carbs / (1 + carbs/60)", "saturating carbohydrate response: the 60th gram is "
      "expected to matter less than the 10th", "never missing")
_spec("a_carb_x_fat", "absorption", "B", "g^2/100",
      "carbs * fat / 100", "fat-delayed carbohydrate absorption", "never missing")
_spec("a_carb_x_fiber", "absorption", "B", "g^2/100",
      "carbs * fiber / 100", "fiber-attenuated absorption", "never missing")
_spec("a_carb_x_protein", "absorption", "B", "g^2/100",
      "carbs * protein / 100", "protein co-ingestion effect", "never missing")
_spec("a_carb_frac_kcal", "absorption", "B", "fraction",
      "4*carbs / calories", "carbohydrate share of energy", "calories <= 0")
_spec("a_fat_frac_kcal", "absorption", "B", "fraction",
      "9*fat / calories", "fat share of energy", "calories <= 0")
_spec("a_net_carbs", "absorption", "B", "g",
      "max(carbs - fiber, 0)",
      "available carbohydrate proxy. NOTE: linearly dependent on carbs and fiber, so "
      "it adds nothing to a linear model; included only to test whether the BOOSTER "
      "can exploit the ratio", "never missing")

# ---- group r: historical response fingerprint (Level C) ---- #
_spec("r_hist_peak_delta", "response", "C", "mg/dL",
      "shrunk mean observed peak delta over that participant's episodes strictly "
      "before t: raw_mean * n/(n+3)",
      "this person's typical excursion amplitude. A summary of OBSERVED past "
      "responses, NOT a measure of insulin secretion or glucose disposal",
      "no prior episode with an observed curve")
_spec("r_hist_per_carb", "response", "C", "mg/dL per g",
      "shrunk mean of (observed peak delta / max(carbs,10)) over strictly earlier episodes",
      "carbohydrate sensitivity phenotype, predictive proxy only",
      "no prior episode")
_spec("r_hist_ttp", "response", "C", "minutes",
      "shrunk mean observed time-to-peak over strictly earlier episodes",
      "typical peak delay phenotype", "no prior episode")
_spec("r_hist_recovery", "response", "B", "mg/dL",
      "shrunk mean of (observed peak - observed value at 120 min) over earlier episodes",
      "how much of the excursion this person clears within two hours",
      "no prior episode")
_spec("r_hist_n", "response", "A", "count",
      "number of that participant's episodes strictly before t",
      "how much personal evidence the fingerprint rests on", "never missing")

# ---- group p: participant biological profile (Level A lab / B derived) ---- #
_spec("p_a1c", "profile", "A", "% (NGSP)",
      "bio.csv 'A1c PDL (Lab)'",
      "chronic glycaemia. NOTE the data dictionary states mmol/mol but the observed "
      "range 4.6-8.5 is unambiguously NGSP %; treated as %",
      "absent from bio.csv")
_spec("p_bmi", "profile", "A", "kg/m^2", "bio.csv BMI", "adiposity", "absent")
_spec("p_age", "profile", "A", "years", "bio.csv Age", "age", "absent")
_spec("p_fasting_glucose", "profile", "A", "mg/dL", "bio.csv fasting lab glucose",
      "fasting glycaemia", "absent")
_spec("p_homa_ir", "profile", "B", "index",
      "fasting_insulin[mcU/mL] * fasting_glucose[mg/dL] / 405",
      "standard surrogate index for insulin resistance. A SURROGATE computed from two "
      "fasting lab values -- NOT a clamp-measured insulin sensitivity",
      "either fasting value absent")
_spec("p_cohort_ord", "profile", "B", "ordinal 0/1/2",
      "0 if A1c<5.7, 1 if 5.7<=A1c<6.5, 2 if A1c>=6.5 (ADA thresholds)",
      "metabolic-status cohort DERIVED from a measured lab A1c, not inferred from "
      "glucose trajectories. CGMacros ships no explicit status column",
      "A1c absent")

# ---- group v: activity and autonomic context (Level A/B) ---- #
_spec("v_activity_kcal_60", "activity", "A", "kcal",
      "sum of per-minute activity calories over [t-60, t]", "recent energy expenditure",
      "no activity rows in window")
_spec("v_mets_mean_60", "activity", "A", "MET",
      "mean METs over [t-60, t]", "recent intensity", "no activity rows")
_spec("v_activity_decay", "activity", "B", "kcal (effective)",
      "sum of activity kcal * exp(-(t - t_i)/120min) over [t-6h, t]",
      "decayed earlier activity load, a proxy for the prolonged post-exercise period "
      "of increased glucose uptake", "no activity rows")
_spec("v_hr_dev_baseline", "activity", "B", "bpm",
      "mean HR over [t-30, t] minus the participant's median HR over all observations "
      "strictly before t",
      "autonomic/arousal deviation from this person's own norm, which is more "
      "comparable across people than absolute HR", "no prior HR")

GROUPS: tuple[str, ...] = ("glucose", "mob", "absorption", "response", "profile", "activity")
PREFIX_BY_GROUP = {
    "glucose": "g_", "mob": "m_", "absorption": "a_",
    "response": "r_", "profile": "p_", "activity": "v_",
}


def names_for_group(group: str) -> list[str]:
    return [s.name for s in SIGNAL_SPECS if s.group == group]


def all_signal_names() -> list[str]:
    return [s.name for s in SIGNAL_SPECS]


# --------------------------------------------------------------------------- #
# Computation helpers -- all strictly backward-looking
# --------------------------------------------------------------------------- #

NAN = math.nan


def _window(points, cutoff: dt.datetime, minutes: float, attr: str):
    start = cutoff - dt.timedelta(minutes=minutes)
    times, values = [], []
    for p in points:
        if start <= p.timestamp <= cutoff:
            times.append((p.timestamp - cutoff).total_seconds() / 60.0)
            values.append(float(getattr(p, attr)))
    return np.asarray(times), np.asarray(values)


def _slope(times: np.ndarray, values: np.ndarray) -> float:
    if times.size < 2:
        return NAN
    centred = times - times.mean()
    denom = float(np.sum(centred**2))
    if denom <= 0:
        return NAN
    return float(np.sum(centred * (values - values.mean())) / denom)


def glucose_signals(past: ParticipantHistory, t: dt.datetime, baseline: float) -> dict:
    out: dict[str, float] = {}
    for label, minutes in (("g_slope_10", 10.0), ("g_slope_15", 15.0), ("g_slope_60", 60.0)):
        out[label] = _slope(*_window(past.cgm, t, minutes, "glucose_mg_dl"))
    slope30 = _slope(*_window(past.cgm, t, 30.0, "glucose_mg_dl"))
    out["g_accel"] = (
        (out["g_slope_15"] - slope30) / 15.0
        if not (math.isnan(out["g_slope_15"]) or math.isnan(slope30))
        else NAN
    )

    recent = [p.glucose_mg_dl for p in past.cgm[-6:]]
    if len(recent) >= 3:
        diffs = np.diff(np.asarray(recent, dtype="float64"))
        out["g_rise_fraction"] = float((diffs > 0).mean())
    else:
        out["g_rise_fraction"] = NAN

    _, v60 = _window(past.cgm, t, 60.0, "glucose_mg_dl")
    out["g_cv_60"] = (
        float(v60.std(ddof=1) / v60.mean()) if v60.size >= 2 and v60.mean() > 0 else NAN
    )
    _, v120 = _window(past.cgm, t, 120.0, "glucose_mg_dl")
    out["g_range_120"] = float(v120.max() - v120.min()) if v120.size >= 2 else NAN

    # Overnight floor from this participant's own earlier observations only.
    night = [p.glucose_mg_dl for p in past.cgm if 2 <= p.timestamp.hour < 6]
    out["g_dev_from_night"] = (
        float(baseline - float(np.median(night))) if night else NAN
    )
    return out


def mob_signals(past: ParticipantHistory, t: dt.datetime) -> dict:
    prior = [m for m in past.meals if m.timestamp < t]
    out = {
        "m_carb_mob_exp60": 0.0,
        "m_carb_mob_exp120": 0.0,
        "m_fat_mob_exp120": 0.0,
        "m_meals_4h": 0.0,
        "m_carbs_24h": 0.0,
    }
    for meal in prior:
        elapsed = (t - meal.timestamp).total_seconds() / 60.0
        carbs = float(meal.carbs_g or 0.0)
        fat = float(meal.fat_g or 0.0)
        if elapsed <= 240.0:
            out["m_carb_mob_exp60"] += carbs * math.exp(-elapsed / 60.0)
            out["m_carb_mob_exp120"] += carbs * math.exp(-elapsed / 120.0)
            out["m_fat_mob_exp120"] += fat * math.exp(-elapsed / 120.0)
            out["m_meals_4h"] += 1.0
        if elapsed <= 1440.0:
            out["m_carbs_24h"] += carbs
    return out


def absorption_signals(carbs, protein, fat, fiber, calories) -> dict:
    out = {
        "a_carb_sat": carbs / (1.0 + carbs / 60.0),
        "a_carb_x_fat": carbs * fat / 100.0,
        "a_carb_x_fiber": carbs * fiber / 100.0,
        "a_carb_x_protein": carbs * protein / 100.0,
        "a_net_carbs": max(carbs - fiber, 0.0),
    }
    out["a_carb_frac_kcal"] = 4.0 * carbs / calories if calories > 0 else NAN
    out["a_fat_frac_kcal"] = 9.0 * fat / calories if calories > 0 else NAN
    return out


def activity_signals(past: ParticipantHistory, t: dt.datetime) -> dict:
    out: dict[str, float] = {}
    kcal, mets, decayed = [], [], 0.0
    six_hours = t - dt.timedelta(hours=6)
    for event in past.activity:
        if event.timestamp > t:
            continue
        value = event.activity_value
        if value is None:
            continue
        unit = (event.activity_unit or "").lower()
        elapsed = (t - event.timestamp).total_seconds() / 60.0
        if "kcal" in unit:
            if elapsed <= 60.0:
                kcal.append(float(value))
            if event.timestamp >= six_hours:
                decayed += float(value) * math.exp(-elapsed / 120.0)
        elif "met" in unit and elapsed <= 60.0:
            mets.append(float(value))
    out["v_activity_kcal_60"] = float(np.sum(kcal)) if kcal else NAN
    out["v_mets_mean_60"] = float(np.mean(mets)) if mets else NAN
    out["v_activity_decay"] = float(decayed) if past.activity else NAN

    _, hr30 = _window(past.heart_rate, t, 30.0, "heart_rate_bpm")
    history = [p.heart_rate_bpm for p in past.heart_rate]
    out["v_hr_dev_baseline"] = (
        float(hr30.mean() - float(np.median(history)))
        if hr30.size >= 1 and history
        else NAN
    )
    return out


def profile_signals(profile: dict | None) -> dict:
    """Static participant lab/anthropometric covariates."""
    if not profile:
        return {name: NAN for name in names_for_group("profile")}
    a1c = profile.get("a1c")
    insulin = profile.get("fasting_insulin")
    glucose = profile.get("fasting_glucose")
    homa = (
        insulin * glucose / 405.0
        if insulin is not None and glucose is not None
        else NAN
    )
    cohort = NAN
    if a1c is not None:
        cohort = 0.0 if a1c < 5.7 else (1.0 if a1c < 6.5 else 2.0)
    return {
        "p_a1c": a1c if a1c is not None else NAN,
        "p_bmi": profile.get("bmi", NAN) if profile.get("bmi") is not None else NAN,
        "p_age": profile.get("age", NAN) if profile.get("age") is not None else NAN,
        "p_fasting_glucose": glucose if glucose is not None else NAN,
        "p_homa_ir": homa,
        "p_cohort_ord": cohort,
    }


def response_signals(prior_responses: list[dict], shrinkage_k: float = 3.0) -> dict:
    """Shrunk fingerprint from that participant's STRICTLY EARLIER observed responses.

    `prior_responses` must already be filtered to episodes with `meal_timestamp < t`.
    Each entry carries the OBSERVED peak delta, time to peak, 120-minute value and the
    meal's carbohydrate load -- all of which had already happened at prediction time.
    """
    n = len(prior_responses)
    out = {"r_hist_n": float(n)}
    if n == 0:
        for name in ("r_hist_peak_delta", "r_hist_per_carb", "r_hist_ttp", "r_hist_recovery"):
            out[name] = NAN
        return out

    factor = n / (n + shrinkage_k)
    peaks = np.array([r["peak_delta"] for r in prior_responses], dtype="float64")
    ttps = np.array([r["ttp"] for r in prior_responses], dtype="float64")
    per_carb = np.array(
        [r["peak_delta"] / max(r["carbs"], 10.0) for r in prior_responses], dtype="float64"
    )
    recovery = np.array(
        [r["peak_delta"] - r["delta_120"] for r in prior_responses], dtype="float64"
    )
    out["r_hist_peak_delta"] = float(np.nanmean(peaks) * factor)
    out["r_hist_per_carb"] = float(np.nanmean(per_carb) * factor)
    out["r_hist_ttp"] = float(np.nanmean(ttps) * factor)
    out["r_hist_recovery"] = float(np.nanmean(recovery) * factor)
    return out
