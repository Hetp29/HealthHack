"""Image- and food-derived feature blocks, every transform fitted on training folds.

FOOD-COMPOSITION REALITY CHECK (Phase 2.3 / Phase Four)
-------------------------------------------------------
CGMacros ships NO food identifier of any kind. The per-participant schema is
exactly: Timestamp, Libre GL, Dexcom GL, HR, Calories (Activity), METs, Meal Type,
Calories, Carbs, Protein, Fat, Fiber, Amount Consumed, Image path. There is no
food name, no description, no ingredient list, no brand, no restaurant, no UPC and
no annotation column, and the data dictionary confirms the schema is complete.

Consequences, stated once and honoured everywhere below:

  * USDA FoodData Central matching is NOT POSSIBLE. A match needs a name, an
    ingredient description or a product identifier. Inventing one from a
    photograph would be fabricating the very ground truth the enrichment is
    supposed to supply, so this branch is reported as unsupported rather than
    attempted. No USDA feature appears in any candidate.
  * Glycaemic index cannot be assigned. There is no identified food to look up,
    and assigning a generic GI to an unidentified mixed meal would be inventing a
    measurement.
  * The only SOURCE-OBSERVED food categorisation is `Meal Type`
    (breakfast/lunch/dinner/snack). That is a meal-occasion label, not a food
    category -- it says when, not what -- and it is reported as such.
  * "Enriched composition" therefore means ALGEBRAIC TRANSFORMS OF THE SOURCE
    MACROS ONLY (fractions, ratios, energy density). Nothing is imported, nothing
    is estimated from an external table, nothing is fabricated.

`Amount Consumed` is deliberately NOT used as a feature. The dictionary defines it
as the "% of meal consumed", and the inventory shows it is derived from the
END-of-meal photograph taken a median 16 minutes after the meal. It is not
available at prediction time.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ml.config import FORECAST_MINUTES, HORIZONS

HORIZON_WINDOW = 120
MIN_CURVE_POINTS = 20


class Pca:
    """Training-fold-only PCA. Centred on training rows, components from them."""

    def __init__(self, k: int):
        self.k = k
        self.mu = None
        self.vt = None

    def fit(self, X: np.ndarray) -> "Pca":
        self.mu = X.mean(axis=0)
        _, _, vt = np.linalg.svd(X - self.mu, full_matrices=False)
        self.vt = vt[: self.k]
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mu) @ self.vt.T


def composition_features(carbs, protein, fat, fiber, calories) -> pd.DataFrame:
    """Algebraic transforms of the SOURCE-OBSERVED macros. Nothing external."""
    c = np.asarray(carbs, dtype="float64")
    p = np.asarray(protein, dtype="float64")
    f = np.asarray(fat, dtype="float64")
    fb = np.asarray(fiber, dtype="float64")
    kcal = np.asarray(calories, dtype="float64")
    mass = np.where((c + p + f) > 0, c + p + f, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = {
            # macronutrient composition of the meal, independent of its size
            "fc_carb_frac": c / mass,
            "fc_protein_frac": p / mass,
            "fc_fat_frac": f / mass,
            # available carbohydrate: total carbs less fibre. This follows the
            # standard definition and uses only source-measured quantities.
            "fc_available_carb_g": np.maximum(c - fb, 0.0),
            "fc_fiber_frac_of_carb": np.where(c > 0, fb / c, np.nan),
            # fat and protein slow gastric emptying, so their ratio to carbohydrate
            # is a plausible modifier -- a HYPOTHESIS, not a measured rate
            "fc_fat_to_carb": np.where(c > 0, f / c, np.nan),
            "fc_protein_to_carb": np.where(c > 0, p / c, np.nan),
            "fc_energy_density": np.where(mass > 0, kcal / mass, np.nan),
            "fc_carb_energy_frac": np.where(kcal > 0, 4.0 * c / kcal, np.nan),
        }
    return pd.DataFrame(out)


def meal_type_features(meal_types: list[str]) -> pd.DataFrame:
    """One-hot of the ONLY source-observed meal categorisation that exists.

    This is a meal-occasion label (when), not a food category (what). fs-1.3
    already encodes time of day through `tod_sin`/`tod_cos`, so much of this is
    expected to be redundant.
    """
    cats = ["breakfast", "lunch", "dinner", "snack"]
    out = {f"mt_{c}": np.array([1.0 if m == c else 0.0 for m in meal_types])
           for c in cats}
    out["mt_unknown"] = np.array([1.0 if m not in cats else 0.0 for m in meal_types])
    return pd.DataFrame(out)


def prior_response_index(stamps, values, meals_by_participant):
    """Per participant, the causally admissible earlier meals and their curves.

    Admission is exactly fs-1.3's filter: the earlier meal's whole 120-minute
    window must have elapsed strictly before the query meal.
    """
    import bisect

    grid = np.asarray(FORECAST_MINUTES, dtype="float64")
    index: dict[str, list[dict]] = {}
    for pid, meals in meals_by_participant.items():
        s, v = stamps.get(pid), values.get(pid)
        if s is None or not len(s):
            continue
        s_list = list(s)
        out = []
        for m in meals:
            t0 = m["timestamp"]
            lo = bisect.bisect_left(s_list, t0)
            hi = bisect.bisect_right(s_list, t0 + dt.timedelta(minutes=HORIZON_WINDOW))
            if lo == 0 or hi - lo < MIN_CURVE_POINTS:
                continue
            base = float(v[lo - 1])
            offs = np.array([(s_list[k] - t0).total_seconds() / 60.0 for k in range(lo, hi)])
            dels = np.array([float(v[k]) - base for k in range(lo, hi)])
            curve = np.interp(grid, offs, dels)
            curve[0] = 0.0
            out.append({"timestamp": t0, "meal_id": m.get("meal_id"), "curve": curve,
                        "deltas": {h: float(np.interp(h, grid, curve)) for h in HORIZONS}})
        index[pid] = sorted(out, key=lambda r: r["timestamp"])
    return index


def image_retrieval_features(episodes, embeddings: dict, prior_index: dict,
                             top: int = 5, power: float = 8.0) -> pd.DataFrame:
    """C6/E5: retrieve the participant's OWN past meals by image similarity.

    For each episode, the admissible earlier meals (whole response elapsed before
    this meal) that also have a prediction-time photograph are ranked by cosine
    similarity between the two meal photographs, and their observed responses are
    averaged with weight similarity^power. This asks whether "this looks like that
    meal I ate on Tuesday" carries information beyond the macros.

    Strictly causal: the candidate set is the same filter fs-1.3 uses, and the
    query meal is never in it.
    """
    cols = [f"iq_delta_{h}" for h in HORIZONS] + ["iq_sim_mean", "iq_n"]
    rows = np.full((len(episodes), len(cols)), np.nan)
    for i, ep in enumerate(episodes):
        q = embeddings.get((ep.participant_id, ep.meal_id))
        if q is None:
            continue
        meal_time = ep.meal_timestamp
        cands = []
        for prior in prior_index.get(ep.participant_id, []):
            if prior["timestamp"] + dt.timedelta(minutes=HORIZON_WINDOW) >= meal_time:
                continue
            key = (ep.participant_id, prior["meal_id"])
            e = embeddings.get(key)
            if e is None or key == (ep.participant_id, ep.meal_id):
                continue
            cands.append((float(np.dot(q, e) / ((np.linalg.norm(q) * np.linalg.norm(e)) or 1.0)),
                          prior))
        if not cands:
            continue
        cands.sort(key=lambda t: -t[0])
        chosen = cands[:top]
        sims = np.array([c[0] for c in chosen])
        w = np.clip(sims, 0.0, None) ** power
        if w.sum() <= 0:
            w = np.ones_like(w)
        w = w / w.sum()
        for j, h in enumerate(HORIZONS):
            rows[i, j] = float(np.dot(w, [c[1]["deltas"][h] for c in chosen]))
        rows[i, len(HORIZONS)] = float(sims.mean())
        rows[i, len(HORIZONS) + 1] = float(len(chosen))
    return pd.DataFrame(rows, columns=cols)


#: Conservative food-form text prompts for CLIP zero-shot (E3/E4/C5). Deliberately
#: coarse: these describe APPEARANCE ONLY. A high "liquid" score is not evidence of
#: a known glycaemic index, and nothing downstream treats it as such.
FOOD_PROMPTS = {
    "liquid": ["a glass of a drink", "a cup of coffee or tea", "a bottle of soda",
               "a smoothie or juice in a glass"],
    "solid_plate": ["a plate of solid food", "a sandwich on a plate",
                    "a bowl of cooked food"],
    "refined_starch": ["a plate of white bread", "a bowl of white rice",
                       "a plate of pasta", "french fries"],
    "whole_food": ["a bowl of fresh salad", "a plate of raw vegetables",
                   "a bowl of whole fruit"],
    "sweet": ["a slice of cake", "a bowl of ice cream", "a plate of cookies",
              "a doughnut"],
    "protein_dish": ["a grilled piece of meat on a plate", "a cooked fish fillet",
                     "scrambled eggs on a plate"],
    "mixed_meal": ["a plate with several different foods",
                   "a full dinner plate with meat and vegetables"],
}
