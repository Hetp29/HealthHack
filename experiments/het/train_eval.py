"""Ablation study: does each feature group (and the meal photo) improve accuracy?

Two honest protocols (whole meal episodes are the split unit; nothing fitted on test):
  * new_person : 9-fold leave-participants-out (5 whole people per fold). The model has never seen this person.
                 Their own earlier meals still feed the "personal response" features,
                 exactly as the 7-day Day-8 history would at prediction time.
  * day8       : per participant, the latest 25% of meals are held out; training uses
                 everyone else's meals plus this person's earlier 75% (mirrors
                 ml/config.py EvaluationConfig, so it is comparable with ml/artifacts).

Every fitted step (median imputation, scaling, photo PCA, the model itself) is fitted on
the training fold only.

Usage:  python -m experiments.het.train_eval [--suite ablation|photo_only] [--protocol new_person day8]
Writes: data/local/het_experiments/results_<suite>.json and prints markdown tables.
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore", category=UserWarning)

EP = Path("data/local/het_experiments/episodes.parquet")
EMB = Path("data/local/het_experiments/photo_embeddings.npz")
OUT = Path("data/local/het_experiments/results.json")
HORIZONS = (30, 60, 90, 120)
SEED = 20261011
N_PCA = 16

# Feature groups ----------------------------------------------------------------------
CURRENT = [  # same information as ml/features.py FEATURE_NAMES (fs-1.0), no medications in CGMacros
    "baseline", "cgm_slope_30m", "cgm_mean_60m", "cgm_std_60m", "minutes_since_last_cgm",
    "carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal", "tod_sin", "tod_cos",
    "minutes_since_prior_meal", "prior_meal_carbs_g", "hr_mean_30m",
]
CONTEXT = ["cgm_slope_60m", "cgm_mean_3h", "cgm_std_3h", "cgm_mean_24h", "cgm_std_24h",
           "cgm_mean_7d", "cgm_std_7d", "cgm_p90_7d", "cgm_tar140_7d", "carb_kcal_frac",
           "net_carbs_g", "carbs_prior_6h", "met_mean_60m", "met_mean_24h"]
PERSONAL = ["n_prior_episodes", "pers_mean_y30", "pers_mean_y60", "pers_mean_y90",
            "pers_mean_y120", "pers_rise_per_carb", "pers_peak_mean"]
BIO = ["age", "is_male", "bmi", "a1c", "fasting_glucose", "fasting_insulin",
       "triglycerides", "hdl", "homa_ir"]

LGB_PARAMS = dict(n_estimators=400, learning_rate=0.03, num_leaves=15, min_child_samples=20,
                  subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
                  objective="l1", random_state=SEED, verbose=-1, n_jobs=4)

# name -> (model family, tabular feature list, use photo)
CONFIGS = {
    "persistence (glucose stays flat)": ("zero", [], False),
    "cohort mean rise": ("mean", [], False),
    "current approach (Ridge, fs-1.0)": ("ridge", CURRENT, False),
    "LightGBM, fs-1.0 features": ("lgb", CURRENT, False),
    "+ longer context & activity": ("lgb", CURRENT + CONTEXT, False),
    "+ personal response history": ("lgb", CURRENT + CONTEXT + PERSONAL, False),
    "+ labs (A1c, BMI, HOMA-IR...)": ("lgb", CURRENT + CONTEXT + PERSONAL + BIO, False),
    "+ meal photo (CLIP)": ("lgb", CURRENT + CONTEXT + PERSONAL + BIO, True),
    "photo without labs": ("lgb", CURRENT + CONTEXT + PERSONAL, True),
}

# "Snap a photo instead of logging": every feature derived from logged macros is removed,
# for the current meal AND earlier meals (which would not have been logged either). Meal
# time is still known (the photo is timestamped).
MACRO_DERIVED = {"carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal", "prior_meal_carbs_g",
                 "carb_kcal_frac", "net_carbs_g", "carbs_prior_6h", "pers_rise_per_carb"}
NO_LOG = [f for f in CURRENT + CONTEXT + PERSONAL if f not in MACRO_DERIVED]
PHOTO_ONLY_CONFIGS = {
    "persistence (glucose stays flat)": ("zero", [], False),
    "cohort mean rise": ("mean", [], False),
    "no meal info (glucose + time only)": ("lgb", NO_LOG, False),
    "photo only (no logging)": ("lgb", NO_LOG, True),
    "logged macros (no photo)": ("lgb", CURRENT + CONTEXT + PERSONAL, False),
    "logged macros + photo": ("lgb", CURRENT + CONTEXT + PERSONAL, True),
}
SUITES = {"ablation": CONFIGS, "photo_only": PHOTO_ONLY_CONFIGS}


def load() -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    ep = pd.read_parquet(EP).sort_values(["participant_id", "t"]).reset_index(drop=True)
    z = np.load(EMB, allow_pickle=False)
    idx = {k: i for i, k in enumerate(z["keys"])}
    emb = np.full((len(ep), z["emb"].shape[1]), np.nan, dtype="float32")
    zs_names = [f"photo_is_{p.replace(' ', '_').replace(',', '')}" for p in z["prompts"]]
    zs = np.full((len(ep), len(zs_names)), np.nan, dtype="float32")
    for r, key in enumerate(ep.image_key):
        if isinstance(key, str) and key in idx:
            emb[r], zs[r] = z["emb"][idx[key]], z["zs"][idx[key]]
    ep[zs_names] = zs
    return ep, emb, zs_names


def photo_block(emb: np.ndarray, tr: np.ndarray, te: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """PCA of CLIP embeddings fitted on the training fold only."""
    ok_tr = ~np.isnan(emb[tr, 0])
    pca = PCA(n_components=N_PCA, random_state=SEED).fit(emb[tr][ok_tr])

    def tf(rows):
        out = np.full((len(rows), N_PCA), np.nan)
        ok = ~np.isnan(emb[rows, 0])
        if ok.any():
            out[ok] = pca.transform(emb[rows][ok])
        return out

    return tf(tr), tf(te)


def fit_predict(kind, feats, use_photo, ep, emb, zs_names, tr, te) -> np.ndarray:
    """Return predicted deltas [len(te), 4]."""
    y = ep[[f"y{h}" for h in HORIZONS]].to_numpy()
    if kind == "zero":
        return np.zeros((len(te), 4))
    if kind == "mean":
        return np.tile(y[tr].mean(axis=0), (len(te), 1))
    Xtr, Xte = ep.loc[tr, feats].to_numpy(float), ep.loc[te, feats].to_numpy(float)
    if use_photo:
        ptr, pte = photo_block(emb, tr, te)
        Xtr = np.hstack([Xtr, ep.loc[tr, zs_names].to_numpy(float), ptr])
        Xte = np.hstack([Xte, ep.loc[te, zs_names].to_numpy(float), pte])
    pred = np.zeros((len(te), 4))
    for j in range(4):
        if kind == "ridge":
            m = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                              RidgeCV(alphas=(0.1, 1, 10, 100, 1000)))
        else:
            m = lgb.LGBMRegressor(**LGB_PARAMS)
        m.fit(Xtr, y[tr, j])
        pred[:, j] = m.predict(Xte)
    return pred


def splits(ep: pd.DataFrame, protocol: str):
    if protocol == "new_person":
        # 9 folds of 5 whole participants: every person is predicted by a model that never saw them.
        pids = np.array(sorted(ep.participant_id.unique()))
        np.random.default_rng(SEED).shuffle(pids)
        for fold in np.array_split(pids, 9):
            mask = ep.participant_id.isin(fold).to_numpy()
            yield np.flatnonzero(~mask), np.flatnonzero(mask)
    elif protocol == "day8":
        test = np.zeros(len(ep), bool)
        for _, g in ep.groupby("participant_id"):
            n_test = max(1, int(round(0.25 * len(g))))
            test[g.index[-n_test:]] = True  # ep sorted by (participant, t)
        yield np.flatnonzero(~test), np.flatnonzero(test)
    else:
        raise ValueError(protocol)


def metrics(ep: pd.DataFrame, rows: np.ndarray, pred: np.ndarray) -> dict:
    y = ep.loc[rows, [f"y{h}" for h in HORIZONS]].to_numpy()
    err = pred - y
    out = {"n": int(len(rows)),
           "mae": float(np.abs(err).mean()),
           "rmse": float(np.sqrt((err ** 2).mean())),
           "within_20": float((np.abs(err) <= 20).mean()),
           "peak_mae": float(np.abs(pred.max(1) - y.max(1)).mean())}
    for j, h in enumerate(HORIZONS):
        out[f"mae_{h}"] = float(np.abs(err[:, j]).mean())
        ss = ((y[:, j] - y[:, j].mean()) ** 2).sum()
        out[f"r2_{h}"] = float(1 - (err[:, j] ** 2).sum() / ss)
    # 95% CI on MAE by resampling PARTICIPANTS (episodes within a person are correlated)
    pids = ep.loc[rows, "participant_id"].to_numpy()
    uniq = np.unique(pids)
    per = {p: np.abs(err[pids == p]) for p in uniq}
    rng = np.random.default_rng(SEED)
    boots = []
    for _ in range(1000):
        pick = rng.choice(uniq, len(uniq), replace=True)
        boots.append(np.concatenate([per[p] for p in pick]).mean())
    out["mae_ci95"] = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]
    return out


def run(protocol: str, ep, emb, zs_names, configs: dict) -> dict:
    preds = {name: np.zeros((len(ep), 4)) for name in configs}
    covered = np.zeros(len(ep), bool)
    for i, (tr, te) in enumerate(splits(ep, protocol)):
        print(f"  {protocol} fold {i + 1}", flush=True)
        covered[te] = True
        for name, (kind, feats, photo) in configs.items():
            preds[name][te] = fit_predict(kind, feats, photo, ep, emb, zs_names, tr, te)
    rows = np.flatnonzero(covered)
    res = {}
    for name in configs:
        res[name] = {"all": metrics(ep, rows, preds[name][rows])}
        clean = rows[~ep.loc[rows, "has_overlap"].to_numpy()]
        res[name]["no_overlap"] = metrics(ep, clean, preds[name][clean])
    return res


def table(res: dict, subset: str = "all") -> str:
    lines = ["| model | MAE 30 | MAE 60 | MAE 90 | MAE 120 | MAE all (95% CI) | RMSE | within 20 mg/dL | peak err | R² 60 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in res.items():
        m = r[subset]
        lo, hi = m["mae_ci95"]
        lines.append(f"| {name} | {m['mae_30']:.1f} | {m['mae_60']:.1f} | {m['mae_90']:.1f} | {m['mae_120']:.1f} | "
                     f"**{m['mae']:.1f}** ({lo:.1f}–{hi:.1f}) | {m['rmse']:.1f} | {m['within_20']:.0%} | "
                     f"{m['peak_mae']:.1f} | {m['r2_60']:.2f} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", nargs="+", default=["new_person", "day8"])
    ap.add_argument("--suite", choices=sorted(SUITES), default="ablation")
    args = ap.parse_args()
    ep, emb, zs_names = load()
    print(f"{len(ep)} episodes, {ep.participant_id.nunique()} participants, "
          f"photo for {(~np.isnan(emb[:, 0])).mean():.0%}")
    results = {}
    for p in args.protocol:
        results[p] = run(p, ep, emb, zs_names, SUITES[args.suite])
        n = results[p]["persistence (glucose stays flat)"]["all"]["n"]
        print(f"\n### {p}  (n={n} held-out meals; errors in mg/dL of the post-meal rise)\n")
        print(table(results[p]))
    out = OUT.with_name(f"results_{args.suite}.json")
    out.write_text(json.dumps(results, indent=1))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
