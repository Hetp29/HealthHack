"""Phase 5: combinations, restricted to components their own development results justify.

    python -m research.biometa.runner_combination --db-url sqlite:///... [--with-transformer]

WHAT IS AND IS NOT ADMITTED HERE
--------------------------------
The mission's rule is explicit: do not combine failed components hoping complexity
rescues them. Applying it to the Phase 2 and Phase 3 results:

  ADMITTED
    M7  microbiome residual correction -- the only biological candidate whose
        cold-start interval excluded zero (+0.029 [+0.009, +0.048]). Tiny, and
        heavily discounted for multiplicity, but it is the one component with any
        inferential claim at all, and its worst-participant deterioration is the
        mildest measured anywhere (-0.13).
    F   hierarchical cohort shrinkage -- +0.010 [-0.004, +0.024] cold-start. Not
        significant, but it is the only metabolic mechanism that is reliably
        HARMLESS, and a combination needs a safe member to be worth testing.
    M1+M9 richness + functional-group prevalence -- the two nominally positive
        cold-start microbiome features (+0.066, +0.090), neither significant.
        Admitted once, as a pair, to test whether two weak biological signals are
        complementary rather than redundant.

  REFUSED, and why
    Every lipid feature. `C group:lipid` cost -10.787 mg/dL cold-start and
    -561 mg/dL on its worst participant. Combining that with anything is not a
    hypothesis, it is a way to lose by a smaller margin.
    `C group:all13`, `B m_chol_hdl_ratio`, `B m_ldl`, `M2`, `M5`, `M10` -- all
    significantly worse cold-start on their own.
    Every direct Transformer. All lost in Phase 4.
"""

from __future__ import annotations

import argparse
import pathlib

import numpy as np
import pandas as pd

from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devvalidation import Candidate, participant_holdout_folds, rolling_folds
from research.biometa.common import TableAttachment, fit_blend, metadata_candidate, score_all
from research.biometa.metadata import (
    FUNCTIONAL_GROUPS,
    gut_health,
    metabolic_table,
    microbiome_incidence,
    prevalence_fractions,
    richness,
)
from research.biometa.runner_microbiome import SvdAttachment, microbiome_correction
from research.biometa.runner_metabolic import hierarchical_shrinkage

RESULTS = pathlib.Path("research/biometa/results")


def stacked_correction(incidence: pd.DataFrame, table: pd.DataFrame,
                       k: float = 3.0) -> Candidate:
    """M7 then F: the microbiome residual correction, then the cohort shrinkage.

    Applied in sequence on the residual, which is the only composition that keeps
    each component doing exactly what it did alone.
    """
    from sklearn.linear_model import Ridge

    svd = SvdAttachment(incidence, 4)

    def run(train, validate, config):
        blend, pers = fit_blend(train.features, train, config)
        base_tr = pers.apply(train.participant_ids, blend.predict_deltas(train.features))
        base_va = pers.apply(validate.participant_ids, blend.predict_deltas(validate.features))
        svd.fit(np.unique(train.participant_ids))
        Z_tr = svd.columns(train.participant_ids).to_numpy("float64")
        Z_va = svd.columns(validate.participant_ids).to_numpy("float64")
        present = [p for p in np.unique(train.participant_ids) if p in table.index]
        med = float(table.loc[present, "m_cohort_ord"].median())
        coh_tr = table.reindex(train.participant_ids)["m_cohort_ord"].fillna(med).to_numpy()
        coh_va = table.reindex(validate.participant_ids)["m_cohort_ord"].fillna(med).to_numpy()

        out = {}
        for h in HORIZONS:
            obs = train.deltas[h].to_numpy("float64")
            keep = np.isfinite(obs) & np.isfinite(base_tr[h])
            if keep.sum() < 30:
                out[h] = base_va[h]
                continue
            resid = obs - base_tr[h]
            m7 = Ridge(alpha=500.0).fit(Z_tr[keep], resid[keep])
            after_tr = resid - m7.predict(Z_tr)
            after_va = base_va[h] + m7.predict(Z_va)
            grand = float(np.mean(after_tr[keep]))
            offset = {}
            for c in np.unique(coh_tr[keep]):
                sel = keep & (coh_tr == c)
                n = int(sel.sum())
                offset[c] = (n / (n + k)) * float(np.mean(after_tr[sel])) + (k / (n + k)) * grand
            out[h] = after_va + np.array([offset.get(c, grand) for c in coh_va])
        return out

    return Candidate("P5 M7 + F stacked", run, "microbiome residual then cohort shrinkage")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-url", required=True)
    args = ap.parse_args()

    from ml.experiments import load_dev_pool

    dev, _ = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    inc, table = microbiome_incidence(), metabolic_table()
    rich = pd.DataFrame({"mb_richness": richness(inc)})
    prev = prevalence_fractions(inc, FUNCTIONAL_GROUPS)
    bio_pair = pd.concat(
        [rich, prev[[c for c in prev.columns if c.startswith("prev_")]]], axis=1)

    cands = [
        metadata_candidate("A fs-1.3 (reference)", None, "no metadata"),
        microbiome_correction(inc),
        hierarchical_shrinkage(table),
        metadata_candidate("P5 M1 + M9 biological pair",
                           TableAttachment(bio_pair, list(bio_pair.columns), "pair"),
                           "richness + functional-group prevalence"),
        stacked_correction(inc, table),
    ]
    print(f"Phase 5: {len(cands)} justified combinations x 2 regimes")

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
    df.to_csv(RESULTS / "combination_leaderboard.csv", index=False)
    print("\n=== PHASE 5 COMBINATIONS ===")
    for label in ("within_person", "cold_start"):
        sub = df[df.regime == label].sort_values("mae")
        print(f"\n{label}:")
        for _, r in sub.iterrows():
            d = "  ref" if pd.isna(r.get("delta")) else f"{r['delta']:+.3f}"
            ci = "" if pd.isna(r.get("ci_low")) else f"[{r['ci_low']:+.3f},{r['ci_high']:+.3f}]"
            ex = "" if pd.isna(r.get("delta")) else ("YES" if r["excludes_zero"] else "no")
            w = ("" if pd.isna(r.get("worst_participant_delta"))
                 else f"{r['worst_participant_delta']:+.2f}")
            print(f"  {r['model']:34s}{r['mae']:8.3f}{d:>8s}{ci:>20s}{ex:>7s}{w:>9s}")
    print(f"\nwrote {RESULTS}/combination_leaderboard.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
