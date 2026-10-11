"""Phase 2: does low-dimensional metabolic metadata add anything to fs-1.3?

    python -m research.biometa.runner_metabolic --db-url sqlite:///data/local/cgmacros_v2.db

WHAT MAKES THIS DIFFERENT FROM THE EARLIER PROFILE EXPERIMENT
-------------------------------------------------------------
The earlier `+ profile` run was scored against the physiology signal-engine
baseline and gave +1.392 [1.059, 1.778] within-person but +0.767 [-0.302, 1.762]
cold-start -- promising within people, not robust across them. That baseline had
no personalisation. fs-1.3 does: it carries a historical-response fingerprint
(`r_hist_*`) and retrieval over the participant's own past meals (`q_retr_*`),
which are DIRECT observations of how this person actually responds to food. The
sharper question, and the one asked here, is whether a static lab panel still adds
information once the model can already see the participant's own measured
responses. It is a genuinely different experiment, not a rerun.

VARIANTS (A-F as specified)
---------------------------
A  fs-1.3, unchanged -- the reference.
B  each metabolic variable alone (13 of them).
C  five prespecified metabolic groups, fixed before any scoring.
D  strongly regularised linear correction: a heavily penalised ridge maps metadata
   to the fs-1.3 residual, fitted on training folds, and its output is ADDED to
   the fs-1.3 prediction. Capacity is one linear map, so it cannot memorise.
E  low-capacity interaction: ADA cohort x (carbs, premeal glucose, slope) only.
F  hierarchical shrinkage: per-cohort residual offsets shrunk to the global mean
   by n/(n+k), the same estimator fs-1.3 already uses per participant.

NEGATIVE CONTROLS
-----------------
*  no-metadata baseline (= A).
*  training-fold-safe shuffled metadata: the metadata->participant mapping is
   permuted among TRAINING participants only. Validation participants keep their
   true unseen rows, and the marginal distribution is untouched. A gain that
   survives this is not biology.
"""

from __future__ import annotations

import argparse
import pathlib

import numpy as np
import pandas as pd

from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devvalidation import Candidate, participant_holdout_folds, rolling_folds
from research.biometa.common import (
    TableAttachment,
    fit_blend,
    metadata_candidate,
    score_all,
)
from research.biometa.metadata import METABOLIC_GROUPS, metabolic_table

RESULTS = pathlib.Path("research/biometa/results")
SHUFFLE_SEED = 20261011

METABOLIC_FIELDS = [
    "m_a1c", "m_fasting_glucose", "m_fasting_insulin", "m_homa_ir", "m_bmi",
    "m_age", "m_cohort_ord", "m_sex_female", "m_triglycerides", "m_hdl",
    "m_cholesterol", "m_ldl", "m_chol_hdl_ratio",
]


def _resolved_groups() -> dict[str, list[str]]:
    return {k: list(v) for k, v in METABOLIC_GROUPS.items()}


def regularized_correction(table: pd.DataFrame, alpha: float = 1000.0) -> Candidate:
    """Variant D: one strongly penalised linear map from metadata to the residual."""
    from sklearn.linear_model import Ridge

    def run(train, validate, config):
        blend, pers = fit_blend(train.features, train, config)
        base_tr = pers.apply(train.participant_ids, blend.predict_deltas(train.features))
        base_va = pers.apply(validate.participant_ids, blend.predict_deltas(validate.features))

        present = [p for p in np.unique(train.participant_ids) if p in table.index]
        med = table.loc[present, METABOLIC_FIELDS].median()
        M_tr = table.reindex(train.participant_ids)[METABOLIC_FIELDS].fillna(med).to_numpy()
        M_va = table.reindex(validate.participant_ids)[METABOLIC_FIELDS].fillna(med).to_numpy()
        mu, sd = M_tr.mean(0), M_tr.std(0)
        sd[sd == 0] = 1.0
        Z_tr, Z_va = (M_tr - mu) / sd, (M_va - mu) / sd

        out = {}
        for h in HORIZONS:
            obs = train.deltas[h].to_numpy("float64")
            keep = np.isfinite(obs) & np.isfinite(base_tr[h])
            if keep.sum() < 30:
                out[h] = base_va[h]
                continue
            r = Ridge(alpha=alpha).fit(Z_tr[keep], obs[keep] - base_tr[h][keep])
            out[h] = base_va[h] + r.predict(Z_va)
        return out

    return Candidate(f"D regularised linear correction (alpha={alpha:g})", run,
                     "metadata -> fs-1.3 residual, one linear map")


def cohort_interaction(table: pd.DataFrame) -> Candidate:
    """Variant E: cohort x a few prespecified features. Three extra columns only."""
    base = ["carbs_g", "premeal_glucose_mg_dl", "g_slope_15"]

    def run(train, validate, config):
        present = [p for p in np.unique(train.participant_ids) if p in table.index]
        med = float(table.loc[present, "m_cohort_ord"].median())

        def build(src, ids):
            coh = table.reindex(ids)["m_cohort_ord"].fillna(med).to_numpy()
            f = src.features.reset_index(drop=True).copy()
            for b in base:
                if b in f.columns:
                    f[f"x_cohort_{b}"] = coh * f[b].to_numpy("float64")
            f["m_cohort_ord"] = coh
            return f

        X_tr, X_va = build(train, train.participant_ids), build(validate, validate.participant_ids)
        blend, pers = fit_blend(X_tr, train, config)
        return pers.apply(validate.participant_ids, blend.predict_deltas(X_va))

    return Candidate("E cohort interaction (low capacity)", run,
                     "ADA cohort x {carbs, premeal glucose, slope}")


def hierarchical_shrinkage(table: pd.DataFrame, k: float = 3.0) -> Candidate:
    """Variant F: per-cohort residual offsets shrunk to the global mean by n/(n+k)."""

    def run(train, validate, config):
        blend, pers = fit_blend(train.features, train, config)
        base_tr = pers.apply(train.participant_ids, blend.predict_deltas(train.features))
        base_va = pers.apply(validate.participant_ids, blend.predict_deltas(validate.features))

        present = [p for p in np.unique(train.participant_ids) if p in table.index]
        med = float(table.loc[present, "m_cohort_ord"].median())
        coh_tr = table.reindex(train.participant_ids)["m_cohort_ord"].fillna(med).to_numpy()
        coh_va = table.reindex(validate.participant_ids)["m_cohort_ord"].fillna(med).to_numpy()

        out = {}
        for h in HORIZONS:
            obs = train.deltas[h].to_numpy("float64")
            resid = obs - base_tr[h]
            keep = np.isfinite(resid)
            grand = float(np.mean(resid[keep])) if keep.any() else 0.0
            offset = {}
            for c in np.unique(coh_tr[keep]):
                sel = keep & (coh_tr == c)
                n = int(sel.sum())
                # shrink the cohort mean toward the grand mean: n/(n+k)
                offset[c] = (n / (n + k)) * float(np.mean(resid[sel])) + (k / (n + k)) * grand
            out[h] = base_va[h] + np.array([offset.get(c, grand) for c in coh_va])
        return out

    return Candidate(f"F hierarchical cohort shrinkage (k={k:g})", run,
                     "per-ADA-cohort residual offset, shrunk")


def build_candidates(table: pd.DataFrame) -> list[Candidate]:
    cands = [metadata_candidate("A fs-1.3 (reference)", None, "no metadata")]
    for f in METABOLIC_FIELDS:
        cands.append(metadata_candidate(f"B {f}", TableAttachment(table, [f], f), "single variable"))
    for name, fields in _resolved_groups().items():
        cands.append(metadata_candidate(f"C group:{name}", TableAttachment(table, fields, name),
                                        "+".join(fields)))
    cands.append(metadata_candidate("C group:all13",
                                    TableAttachment(table, METABOLIC_FIELDS, "all13"),
                                    "every metabolic field at once"))
    cands.append(regularized_correction(table))
    cands.append(cohort_interaction(table))
    cands.append(hierarchical_shrinkage(table))
    # negative controls, matched to the two most informative-looking groups
    for name in ("core4", "all13"):
        fields = METABOLIC_FIELDS if name == "all13" else _resolved_groups()["core4"]
        cands.append(metadata_candidate(
            f"NC shuffled metadata:{name}",
            TableAttachment(table, fields, name, shuffle_seed=SHUFFLE_SEED),
            "training-fold-safe permutation of metadata->participant"))
    return cands


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-url", required=True)
    args = ap.parse_args()

    from ml.experiments import load_dev_pool

    dev, _ = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    table = metabolic_table()
    cands = build_candidates(table)
    print(f"Phase 2: {len(cands)} configurations x 2 regimes on {len(dev)} episodes")

    rows: list[dict] = []
    for label, folds in (("within_person", rolling_folds(dev, DEFAULT_CONFIG, 3)),
                         ("cold_start", participant_holdout_folds(dev, DEFAULT_CONFIG, 4))):
        print(f"\n--- {label} ({len(folds)} folds) ---")
        scored, _ = score_all(folds, cands, DEFAULT_CONFIG, "A fs-1.3 (reference)", label)
        for r in scored:
            r["regime"] = label
        rows.extend(scored)

    RESULTS.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "metabolic_leaderboard.csv", index=False)

    print("\n=== PHASE 2 METABOLIC LEADERBOARD (delta>0 = better than fs-1.3) ===")
    for label in ("within_person", "cold_start"):
        sub = df[df.regime == label].sort_values("mae")
        print(f"\n{label}:")
        print(f"  {'model':46s}{'MAE':>8s}{'delta':>8s}{'95% CI':>20s}{'excl0':>7s}{'worst ppt':>11s}")
        for _, r in sub.iterrows():
            d = "  ref" if pd.isna(r.get("delta")) else f"{r['delta']:+.3f}"
            ci = ("" if pd.isna(r.get("ci_low"))
                  else f"[{r['ci_low']:+.3f},{r['ci_high']:+.3f}]")
            ex = "" if pd.isna(r.get("delta")) else ("YES" if r["excludes_zero"] else "no")
            w = "" if pd.isna(r.get("worst_participant_delta")) else f"{r['worst_participant_delta']:+.2f}"
            print(f"  {r['model']:46s}{r['mae']:8.3f}{d:>8s}{ci:>20s}{ex:>7s}{w:>11s}")
    print(f"\nwrote {RESULTS}/metabolic_leaderboard.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
