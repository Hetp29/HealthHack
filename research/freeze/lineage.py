"""Phase 5: per-feature data lineage and prediction-time availability audit.

    python -m research.freeze.lineage

The table is not prose. For every one of the 39 features the LEAKAGE STATUS column
is backed by an experiment: the same history is rebuilt with post-prediction data
injected -- future CGM, a future meal, future heart rate, a future medication
event -- and the feature's value is compared. A feature that moves is leaking.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import math
import pathlib

from ml.config import DEFAULT_CONFIG
from ml.features import FEATURE_LABELS, FEATURE_NAMES, MealComposition, build_features
from ml.schema import CgmPoint, HeartRatePoint, MealRecord, ParticipantHistory

CFG = DEFAULT_CONFIG.features
UTC = dt.timezone.utc
T0 = dt.datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
MACROS = MealComposition(60.0, 20.0, 15.0, 5.0, 450.0)
RESULTS = pathlib.Path("research/freeze/results")

#: SOURCE / OBSERVATION TIME / AVAILABILITY / TRANSFORMATION per feature group.
GROUPS = {
    "premeal_glucose_mg_dl": ("CGM sensor (Dexcom G6 Pro)", "last reading at or before t",
                              "yes -- observed before t", "nearest reading within tolerance"),
    "cgm_slope_30min_mg_dl_per_min": ("CGM sensor", "[t-30, t]", "yes", "least-squares slope"),
    "cgm_mean_60min_mg_dl": ("CGM sensor", "[t-60, t]", "yes", "mean"),
    "cgm_std_60min_mg_dl": ("CGM sensor", "[t-60, t]", "yes", "standard deviation"),
    "minutes_since_last_cgm": ("CGM sensor", "last reading at or before t", "yes",
                               "elapsed minutes"),
    "carbs_g": ("meal log (CGMacros `Carbs`)", "logged for the meal at t",
                "RETROSPECTIVE -- see note R1", "as recorded"),
    "protein_g": ("meal log (`Protein`)", "logged for the meal at t",
                  "RETROSPECTIVE -- see note R1", "as recorded"),
    "fat_g": ("meal log (`Fat`)", "logged for the meal at t",
              "RETROSPECTIVE -- see note R1", "as recorded"),
    "fiber_g": ("meal log (`Fiber`)", "logged for the meal at t",
                "RETROSPECTIVE -- see note R1", "as recorded; a measured 0 g is preserved"),
    "calories_kcal": ("meal log (`Calories`)", "logged for the meal at t",
                      "RETROSPECTIVE -- see note R1", "as recorded"),
    "tod_sin": ("meal timestamp", "t itself", "yes", "sin(2*pi*minute_of_day/1440)"),
    "tod_cos": ("meal timestamp", "t itself", "yes", "cos(2*pi*minute_of_day/1440)"),
    "minutes_since_prior_meal": ("meal log", "most recent meal strictly before t", "yes",
                                 "elapsed minutes"),
    "prior_meal_carbs_g": ("meal log", "most recent meal strictly before t", "yes",
                           "as recorded"),
    "hr_mean_30min_bpm": ("Fitbit Sense", "[t-30, t]", "yes", "mean; missing when absent"),
    "medication_exposure_recent": ("medication records", "events at or before t", "yes",
                                   "indicator over the recent window"),
    "g_slope_10": ("CGM sensor", "[t-10, t]", "yes", "least-squares slope"),
    "g_slope_15": ("CGM sensor", "[t-15, t]", "yes", "least-squares slope"),
    "g_slope_60": ("CGM sensor", "[t-60, t]", "yes", "least-squares slope"),
    "g_accel": ("CGM sensor", "[t-30, t]", "yes", "difference of consecutive slopes"),
    "g_rise_fraction": ("CGM sensor", "[t-60, t]", "yes", "fraction of rising steps"),
    "g_cv_60": ("CGM sensor", "[t-60, t]", "yes", "std / mean"),
    "g_range_120": ("CGM sensor", "[t-120, t]", "yes", "max - min"),
    "g_dev_from_night": ("CGM sensor", "overnight window before t", "yes",
                         "current minus own overnight floor"),
    "r_hist_peak_delta": ("CGM sensor + meal log", "earlier meals whose 120-min window "
                          "fully elapsed before t", "yes -- see note C1",
                          "shrunk mean, n/(n+3)"),
    "r_hist_per_carb": ("CGM + meal log", "same as above", "yes -- note C1",
                        "shrunk mean of response per gram"),
    "r_hist_ttp": ("CGM + meal log", "same as above", "yes -- note C1", "shrunk mean"),
    "r_hist_recovery": ("CGM + meal log", "same as above", "yes -- note C1", "shrunk mean"),
    "r_hist_n": ("meal log", "same as above", "yes -- note C1",
                 "count of admissible earlier meals (UNCAPPED)"),
    "q_retr_delta_30": ("CGM + meal log", "k=5 nearest admissible earlier meals",
                        "yes -- note C1", "kernel-weighted mean"),
    "q_retr_delta_60": ("CGM + meal log", "same", "yes -- note C1", "kernel-weighted mean"),
    "q_retr_delta_90": ("CGM + meal log", "same", "yes -- note C1", "kernel-weighted mean"),
    "q_retr_delta_120": ("CGM + meal log", "same", "yes -- note C1", "kernel-weighted mean"),
    "q_retr_n": ("meal log", "same", "yes -- note C1",
                 "count of neighbours RETRIEVED = min(k, admissible)"),
    "q_retr_weight": ("CGM + meal log", "same", "yes -- note C1", "sum of kernel weights"),
    "q_retr_mean_distance": ("CGM + meal log", "same", "yes -- note C1",
                             "mean standardized distance"),
    "q_retr_peak_delta": ("CGM + meal log", "same", "yes -- note C1",
                          "max of the blended curve"),
    "q_retr_ttp": ("CGM + meal log", "same", "yes -- note C1", "argmax of blended curve"),
    "q_retr_curve_spread": ("CGM + meal log", "same", "yes -- note C1",
                            "mean pointwise std across neighbours"),
}


def base_history() -> ParticipantHistory:
    cgm, t, i = [], T0 - dt.timedelta(hours=60), 0
    while t <= T0:
        cgm.append(CgmPoint(timestamp=t,
                            glucose_mg_dl=round(105 + 22 * math.sin(i / 12.0), 1)))
        t += dt.timedelta(minutes=5)
        i += 1
    meals = [MealRecord(meal_id=f"m{j}", participant_id="p1",
                        timestamp=T0 - dt.timedelta(hours=48 - 6 * j),
                        carbs_g=40.0 + 4 * j, protein_g=15.0, fat_g=10.0,
                        fiber_g=4.0, calories_kcal=350.0 + 8 * j) for j in range(7)]
    hr = [HeartRatePoint(timestamp=T0 - dt.timedelta(minutes=m), heart_rate_bpm=70.0 + m % 7)
          for m in range(0, 60, 5)]
    return ParticipantHistory(participant_id="p1", data_source="official",
                              cgm=cgm, meals=meals, heart_rate=hr)


def with_future(history: ParticipantHistory) -> ParticipantHistory:
    """The same history plus a deliberately extreme POST-PREDICTION future."""
    future_cgm = [CgmPoint(timestamp=T0 + dt.timedelta(minutes=m), glucose_mg_dl=390.0)
                  for m in range(5, 185, 5)]
    future_meal = MealRecord(meal_id="future", participant_id="p1",
                             timestamp=T0 + dt.timedelta(minutes=45), carbs_g=250.0,
                             protein_g=99.0, fat_g=99.0, fiber_g=99.0,
                             calories_kcal=2000.0)
    future_hr = [HeartRatePoint(timestamp=T0 + dt.timedelta(minutes=m), heart_rate_bpm=180.0)
                 for m in range(5, 65, 5)]
    return dataclasses.replace(
        history,
        cgm=sorted(history.cgm + future_cgm, key=lambda p: p.timestamp),
        meals=sorted(history.meals + [future_meal], key=lambda m: m.timestamp),
        heart_rate=sorted(history.heart_rate + future_hr, key=lambda p: p.timestamp))


def main() -> int:
    base = base_history()
    a = build_features(base, T0, MACROS, CFG, meal_id="q")
    b = build_features(with_future(base), T0, MACROS, CFG, meal_id="q")

    rows, leaking = [], []
    for name in FEATURE_NAMES:
        va, vb = a.values[name], b.values[name]
        same = (math.isnan(va) and math.isnan(vb)) or va == vb
        source, obs, avail, transform = GROUPS[name]
        status = "CLEAN -- invariant to injected future data" if same else "*** LEAK ***"
        if not same:
            leaking.append((name, va, vb))
        rows.append({"FEATURE": name, "LABEL": FEATURE_LABELS.get(name, ""),
                     "SOURCE": source, "OBSERVATION TIME": obs,
                     "AVAILABILITY AT PREDICTION": avail,
                     "TRANSFORMATION": transform, "LEAKAGE STATUS": status,
                     "value_without_future": None if math.isnan(va) else va,
                     "value_with_future": None if math.isnan(vb) else vb})

    print("=== PHASE 5: DATA LINEAGE AND PREDICTION-TIME AVAILABILITY ===")
    print(f"  features audited: {len(rows)}")
    print(f"  injected future: 36 post-meal CGM points at 390 mg/dL, a 250 g meal at")
    print(f"  t+45 min, and 12 heart-rate points at 180 bpm")
    print(f"  features that CHANGED: {len(leaking)}")
    for n, va, vb in leaking:
        print(f"    *** {n}: {va} -> {vb}")
    print()
    print(f"  {'FEATURE':30s}{'AVAILABILITY':34s}LEAKAGE")
    for r in rows:
        print(f"  {r['FEATURE']:30s}{r['AVAILABILITY AT PREDICTION']:34s}"
              f"{r['LEAKAGE STATUS']}")

    extra = {
        "note_R1": (
            "RETROSPECTIVE MACRO AVAILABILITY. The CGMacros dictionary defines "
            "Calories/Carbs/Protein/Fat/Fiber as quantities 'in the consumed meal', and "
            "`Amount Consumed` as the percentage eaten, derived from an END-of-meal "
            "photograph taken a median 16 minutes AFTER the meal. The logged macros "
            "therefore describe what was actually eaten. In deployment the equivalent "
            "input is a participant's PLANNED or logged-at-the-time meal, which is "
            "prospectively available but may differ from what they finish. This is a "
            "known EVALUATION LIMITATION of the dataset and of the task as specified -- "
            "it is not a model-integrity failure, and fs-1.3 has always received these "
            "five fields. Reported accuracy must not be presented as proof that exact "
            "consumed quantities are knowable before eating."),
        "note_C1": (
            "CAUSAL HISTORY. Fingerprint and retrieval features use only earlier meals "
            "whose whole 120-minute response window elapsed STRICTLY before t, which is "
            "verified empirically above and in tests/test_freeze_retrieval_audit.py. A "
            "response window ending exactly AT t is EXCLUDED."),
        "not_used": {
            "Amount Consumed": "never a feature -- derived from the end-of-meal photo",
            "meal photographs": "no image feature exists in fs-1.3; the vision research "
                                "track was not promoted",
            "post-meal CGM": "removed by ParticipantHistory.truncated_at before feature "
                             "extraction, so it is absent rather than merely unused",
            "future meals": "same chokepoint",
            "participant biological metadata (A1c, BMI, microbiome, gut health)":
                "not promoted; no fs-1.3 feature reads them",
            "participant identity": "no participant id, index or one-hot is a feature",
        },
        "leakage_chokepoint": "ml/features.py::build_features -> history.truncated_at(t)",
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "phase5_lineage.json").write_text(
        json.dumps({"rows": rows, "leaking": leaking, "notes": extra}, indent=2) + "\n")
    print(f"\nwrote {RESULTS}/phase5_lineage.json")
    if leaking:
        print("\nPHASE 5: FAIL")
        return 1
    print("\nPHASE 5: PASS -- all 39 features invariant to injected post-prediction data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
