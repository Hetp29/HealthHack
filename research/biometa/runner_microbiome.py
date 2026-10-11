"""Phase 3: microbiome and gut-health features on top of fs-1.3.

    python -m research.biometa.runner_microbiome --db-url sqlite:///data/local/cgmacros_v2.db

WHAT THE MEASUREMENT SCALE ALLOWS, AND WHAT IT FORBIDS
------------------------------------------------------
`microbes.csv` is binary presence/absence (verified in Phase 1; see
`research.biometa.metadata`). The mission asked for several microbiome analyses
"only where the measurement scale supports their calculation" and "if
appropriate". It does not support all of them, so two are declined on the data
rather than attempted and quietly fudged:

  M1 diversity    PARTIAL. Observed richness (count of taxa present) is well
                  defined on incidence data and is implemented. Shannon, Simpson
                  and evenness are NOT: every one of them is a function of
                  abundance proportions p_i, and incidence data fixes every
                  present taxon at the same value. Computing them here would
                  produce log(richness) in a costume. DECLINED.
  M3 log-ratio    DECLINED. Centred/additive log-ratios assume a composition on
                  the simplex. A 0/1 incidence vector is not a composition, there
                  is no total to close against, and every zero is structural
                  rather than a rounded-down small abundance -- so the usual
                  pseudo-count repair is not a repair of anything. The honest
                  substitute for "how much of group X" on this scale is a
                  prevalence fraction, which M2/M9 provide under that name.

The 45x1979 matrix also means ~44 training participants and 1979 columns, so no
model is fitted directly on the raw taxa: every microbiome representation is
reduced to a handful of columns INSIDE the training fold.

IDENTITY-PROXY RISK IS THE CENTRAL THREAT
-----------------------------------------
Off-diagonal Jaccard similarity averages 0.201, i.e. microbiomes are close to
unique per person. A constant-per-participant near-unique vector is an excellent
participant fingerprint, so within-person gains are expected and nearly
meaningless. Two defences: cold-start folds (validation participants never seen),
and a training-fold-safe shuffled control that destroys the
microbiome->participant mapping while preserving the marginals.
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
    genus_of,
    gut_health,
    jaccard_matrix,
    microbiome_incidence,
    prevalence_fractions,
    richness,
)

RESULTS = pathlib.Path("research/biometa/results")
SHUFFLE_SEED = 20261011


# --------------------------------------------------------------------------- #
# M4 / M5: representations reduced inside the training fold
# --------------------------------------------------------------------------- #
class SvdAttachment:
    """M4: truncated SVD of the binary incidence matrix, fitted on training rows."""

    def __init__(self, incidence: pd.DataFrame, k: int):
        self.inc, self.k, self.name = incidence, k, f"svd{k}"
        self._mu = self._vt = None
        self._rows: pd.Index | None = None

    def fit(self, train_participants):
        rows = [p for p in train_participants
                if p in self.inc.index and self.inc.loc[p].notna().all()]
        X = self.inc.loc[rows].to_numpy("float64")
        self._mu = X.mean(0)
        _, _, vt = np.linalg.svd(X - self._mu, full_matrices=False)
        self._vt = vt[: self.k]
        return self

    def columns(self, participant_ids):
        out = np.zeros((len(participant_ids), self.k))
        for i, p in enumerate(participant_ids):
            if p in self.inc.index and self.inc.loc[p].notna().all():
                out[i] = (self.inc.loc[p].to_numpy("float64") - self._mu) @ self._vt.T
        return pd.DataFrame(out, columns=[f"mb_svd{j}" for j in range(self.k)])


class SelectedTaxaAttachment:
    """M5: top-k taxa chosen INSIDE the training fold by |point-biserial| against
    the participant's mean 60-minute response. Selection never sees validation rows."""

    def __init__(self, incidence: pd.DataFrame, response: pd.Series, k: int):
        self.inc, self.resp, self.k = incidence, response, k
        self.name = f"select{k}"
        self._taxa: list[str] = []

    def fit(self, train_participants):
        rows = [p for p in train_participants
                if p in self.inc.index and self.inc.loc[p].notna().all()
                and p in self.resp.index and np.isfinite(self.resp[p])]
        if len(rows) < 8:
            self._taxa = []
            return self
        X = self.inc.loc[rows].to_numpy("float64")
        y = self.resp.loc[rows].to_numpy("float64")
        y = (y - y.mean()) / (y.std() or 1.0)
        keep = (X.sum(0) > 2) & (X.sum(0) < len(rows) - 2)
        corr = np.zeros(X.shape[1])
        Xc = X[:, keep] - X[:, keep].mean(0)
        sd = Xc.std(0)
        sd[sd == 0] = 1.0
        corr[keep] = np.abs((Xc / sd).T @ y) / len(rows)
        self._taxa = [self.inc.columns[i] for i in np.argsort(-corr)[: self.k]]
        return self

    def columns(self, participant_ids):
        if not self._taxa:
            return pd.DataFrame(index=range(len(participant_ids)))
        block = self.inc.reindex(participant_ids)[self._taxa]
        block.index = range(len(block))
        block.columns = [f"mb_sel{j}" for j in range(len(self._taxa))]
        return block.fillna(0.5)


class JaccardNeighbourAttachment:
    """M6/M8: borrow responses from microbiome-similar TRAINING participants.

    This is the one mechanism by which participant-level biology could plausibly
    help a COLD-START participant: their own history is unavailable by definition,
    but their microbiome is measured at study start, so it can index other people
    whose responses we have observed. Weights are Jaccard^power, restricted to
    training participants, normalised, and the query participant is excluded.
    """

    def __init__(self, incidence: pd.DataFrame, responses: pd.DataFrame,
                 power: float = 4.0, top: int = 5, label: str = "jac"):
        self.sim = jaccard_matrix(incidence)
        self.resp = responses
        self.power, self.top, self.name = power, top, label
        self._w: pd.DataFrame | None = None
        self._global: pd.Series | None = None

    def fit(self, train_participants):
        rows = [p for p in train_participants
                if p in self.sim.index and p in self.resp.index]
        self._pool = rows
        self._global = self.resp.loc[rows].mean()
        return self

    def columns(self, participant_ids):
        cols = [f"mb_nb_{h}" for h in HORIZONS] + ["mb_nb_sim"]
        out = np.zeros((len(participant_ids), len(cols)))
        pool_resp = self.resp.loc[self._pool].to_numpy("float64")
        for i, p in enumerate(participant_ids):
            if p not in self.sim.index:
                out[i, :-1] = self._global.to_numpy()
                out[i, -1] = np.nan
                continue
            s = np.array([0.0 if q == p else float(self.sim.loc[p, q])
                          if q in self.sim.columns else 0.0 for q in self._pool])
            order = np.argsort(-s)[: self.top]
            w = s[order] ** self.power
            if w.sum() <= 0:
                out[i, :-1] = self._global.to_numpy()
                out[i, -1] = 0.0
                continue
            w = w / w.sum()
            out[i, :-1] = w @ pool_resp[order]
            out[i, -1] = float(s[order].mean())
        return pd.DataFrame(out, columns=cols)


def participant_mean_responses(matrices) -> pd.DataFrame:
    """Mean observed delta per participant per horizon, from a fold's TRAINING set.

    Computed from the fold's own training matrices, so it never sees a validation
    episode. This is what the Jaccard neighbour features average over.
    """
    ids = pd.Index(matrices.participant_ids, name="participant_id")
    out = {}
    for h in HORIZONS:
        s = pd.Series(matrices.deltas[h].to_numpy("float64"), index=ids)
        out[h] = s.groupby(level=0).mean()
    return pd.DataFrame(out)


def microbiome_correction(incidence: pd.DataFrame) -> Candidate:
    """M7: participant-level residual correction predicted from microbiome SVD."""
    from sklearn.linear_model import Ridge

    svd = SvdAttachment(incidence, 4)

    def run(train, validate, config):
        blend, pers = fit_blend(train.features, train, config)
        base_tr = pers.apply(train.participant_ids, blend.predict_deltas(train.features))
        base_va = pers.apply(validate.participant_ids, blend.predict_deltas(validate.features))
        svd.fit(np.unique(train.participant_ids))
        Z_tr = svd.columns(train.participant_ids).to_numpy("float64")
        Z_va = svd.columns(validate.participant_ids).to_numpy("float64")
        out = {}
        for h in HORIZONS:
            obs = train.deltas[h].to_numpy("float64")
            keep = np.isfinite(obs) & np.isfinite(base_tr[h])
            if keep.sum() < 30:
                out[h] = base_va[h]
                continue
            r = Ridge(alpha=500.0).fit(Z_tr[keep], obs[keep] - base_tr[h][keep])
            out[h] = base_va[h] + r.predict(Z_va)
        return out

    return Candidate("M7 microbiome residual correction", run, "SVD4 -> residual, ridge a=500")


def selected_taxa_candidate(incidence: pd.DataFrame, k: int) -> Candidate:
    """M5: taxon selection performed inside each training fold, never across it.

    The selection target is each TRAINING participant's mean 60-minute response,
    computed from that fold's training episodes only. Validation participants
    contribute nothing to which taxa are chosen.
    """

    def run(train, validate, config):
        resp = participant_mean_responses(train)[60]
        a = SelectedTaxaAttachment(incidence, resp, k).fit(np.unique(train.participant_ids))
        X_tr = pd.concat([train.features.reset_index(drop=True),
                          a.columns(train.participant_ids)], axis=1)
        X_va = pd.concat([validate.features.reset_index(drop=True),
                          a.columns(validate.participant_ids)], axis=1)
        blend, pers = fit_blend(X_tr, train, config)
        return pers.apply(validate.participant_ids, blend.predict_deltas(X_va))

    return Candidate(f"M5 selected taxa (k={k})", run,
                     "|point-biserial| vs mean 60-min response, inside training fold")


def neighbour_candidate(name: str, incidence: pd.DataFrame, power: float, top: int,
                        shuffle: bool = False) -> Candidate:
    inc = incidence
    if shuffle:
        rng = np.random.default_rng(SHUFFLE_SEED)
        idx = list(incidence.index)
        rng.shuffle(idx)
        inc = incidence.copy()
        inc.index = idx
        inc = inc.loc[incidence.index]

    def run(train, validate, config):
        resp = participant_mean_responses(train)
        a = JaccardNeighbourAttachment(inc, resp, power, top).fit(np.unique(train.participant_ids))
        X_tr = pd.concat([train.features.reset_index(drop=True),
                          a.columns(train.participant_ids)], axis=1)
        X_va = pd.concat([validate.features.reset_index(drop=True),
                          a.columns(validate.participant_ids)], axis=1)
        blend, pers = fit_blend(X_tr, train, config)
        return pers.apply(validate.participant_ids, blend.predict_deltas(X_va))

    return Candidate(name, run, f"jaccard^{power:g}, top {top}" + (" SHUFFLED" if shuffle else ""))


def build_candidates(inc: pd.DataFrame, gh: pd.DataFrame) -> list[Candidate]:
    rich = pd.DataFrame({"mb_richness": richness(inc)})
    prev = prevalence_fractions(inc, FUNCTIONAL_GROUPS)
    # M2: the most common genera by taxonomy, grouped -- distinct from M9's
    # literature-prespecified functional groups.
    genera = pd.Series([genus_of(c) for c in inc.columns])
    common = genera.value_counts().head(12).index.tolist()
    common_groups = {g: (g,) for g in common}
    prev_common = prevalence_fractions(inc, common_groups)

    cands = [metadata_candidate("A fs-1.3 (reference)", None, "no metadata")]
    cands.append(metadata_candidate("M1 richness", TableAttachment(rich, ["mb_richness"], "rich"),
                                    "observed richness; Shannon/Simpson DECLINED (binary scale)"))
    cands.append(metadata_candidate(
        "M2 prevalence, 12 commonest genera",
        TableAttachment(prev_common, [c for c in prev_common.columns if c.startswith("prev_")],
                        "common12"), "taxonomy-driven groups"))
    cands.append(metadata_candidate(
        "M9 prevalence, functional groups",
        TableAttachment(prev, [c for c in prev.columns if c.startswith("prev_")], "func"),
        "literature-prespecified: SCFA/propionate/lactate/bile/proteobacteria/mucin"))
    for k in (2, 4, 8):
        cands.append(metadata_candidate(f"M4 microbiome SVD{k}", SvdAttachment(inc, k),
                                        "fitted on training rows only"))
    cands.append(selected_taxa_candidate(inc, 10))
    cands.append(neighbour_candidate("M6 jaccard neighbour response", inc, 4.0, 5))
    cands.append(neighbour_candidate("M8 jaccard fingerprint (top 10)", inc, 2.0, 10))
    cands.append(microbiome_correction(inc))
    ghc = list(gh.columns)
    cands.append(metadata_candidate("M10 gut-health ordinal scores",
                                    TableAttachment(gh, ghc, "gut22"),
                                    "22 vendor ordinal 1/2/3 scores"))
    # negative controls
    cands.append(metadata_candidate(
        "NC shuffled microbiome SVD4",
        _ShuffledSvd(inc, 4), "training-fold-safe permutation"))
    cands.append(neighbour_candidate("NC shuffled jaccard neighbour", inc, 4.0, 5, shuffle=True))
    cands.append(metadata_candidate(
        "NC shuffled gut-health",
        TableAttachment(gh, ghc, "gut22", shuffle_seed=SHUFFLE_SEED), "permuted mapping"))
    return cands


class _ShuffledSvd(SvdAttachment):
    """SVD control: the incidence->participant mapping is permuted among TRAINING
    participants only, so validation participants keep their true unseen rows."""

    def fit(self, train_participants):
        rows = [p for p in train_participants
                if p in self.inc.index and self.inc.loc[p].notna().all()]
        rng = np.random.default_rng(SHUFFLE_SEED)
        perm = list(rows)
        rng.shuffle(perm)
        self._remap = dict(zip(rows, perm))
        super().fit(train_participants)
        return self

    def columns(self, participant_ids):
        mapped = [getattr(self, "_remap", {}).get(p, p) for p in participant_ids]
        return super().columns(mapped)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-url", required=True)
    args = ap.parse_args()

    from ml.experiments import load_dev_pool

    dev, _ = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    inc, gh = microbiome_incidence(), gut_health()
    cands = build_candidates(inc, gh)
    print(f"Phase 3: {len(cands)} configurations x 2 regimes on {len(dev)} episodes")
    print("  DECLINED on measurement scale: Shannon/Simpson/evenness (M1), "
          "CLR/ALR log-ratios (M3)")

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
    df.to_csv(RESULTS / "microbiome_leaderboard.csv", index=False)
    print("\n=== PHASE 3 MICROBIOME LEADERBOARD (delta>0 = better than fs-1.3) ===")
    for label in ("within_person", "cold_start"):
        sub = df[df.regime == label].sort_values("mae")
        print(f"\n{label}:")
        print(f"  {'model':46s}{'MAE':>8s}{'delta':>8s}{'95% CI':>20s}{'excl0':>7s}{'worst ppt':>11s}")
        for _, r in sub.iterrows():
            d = "  ref" if pd.isna(r.get("delta")) else f"{r['delta']:+.3f}"
            ci = ("" if pd.isna(r.get("ci_low"))
                  else f"[{r['ci_low']:+.3f},{r['ci_high']:+.3f}]")
            ex = "" if pd.isna(r.get("delta")) else ("YES" if r["excludes_zero"] else "no")
            w = ("" if pd.isna(r.get("worst_participant_delta"))
                 else f"{r['worst_participant_delta']:+.2f}")
            print(f"  {r['model']:46s}{r['mae']:8.3f}{d:>8s}{ci:>20s}{ex:>7s}{w:>11s}")
    print(f"\nwrote {RESULTS}/microbiome_leaderboard.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
