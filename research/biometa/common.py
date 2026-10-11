"""Shared machinery for the biological-metadata experiments (Phases 2, 3, 5).

THE LEAKAGE RULE THAT GOVERNS THIS WHOLE MODULE
-----------------------------------------------
Biological metadata are PARTICIPANT-LEVEL and CONSTANT in time. That makes them
harmless with respect to temporal leakage -- they were collected at study start,
strictly before every episode -- and dangerous with respect to identity leakage:
a constant-per-participant column is a participant fingerprint, and in the
within-person regime (same people in train and validate) a flexible learner can
use it to memorise who it is looking at. That is exactly how a static profile can
look useful within-person and vanish cold-start.

So: every transformation that touches metadata -- median imputation, scaling,
PCA/SVD, feature selection, kernel construction, group shrinkage -- is fitted on
the fold's TRAINING participants only, and `attach` is the single chokepoint that
enforces it. Cold-start folds are the regime that decides.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml.candidates import GbmModel, _personalize
from ml.config import HORIZONS
from ml.train import GeneralizedModel


@dataclass
class _View:
    """An EpisodeMatrices-shaped view with substituted features."""
    features: pd.DataFrame
    _src: object

    def __getattr__(self, name):
        return getattr(self._src, name)


class Attachment:
    """A fold-local metadata transform: fitted on training participants, then applied.

    Subclasses implement `fit(train_participants)` and `columns(participant_ids)`.
    """

    name = "attachment"

    def fit(self, train_participants: np.ndarray) -> "Attachment":
        return self

    def columns(self, participant_ids: np.ndarray) -> pd.DataFrame:
        raise NotImplementedError


class TableAttachment(Attachment):
    """Append chosen participant-level columns, imputed with TRAINING-fold medians."""

    def __init__(self, table: pd.DataFrame, fields: list[str], name: str,
                 shuffle_seed: int | None = None):
        self.table = table
        self.fields = list(fields)
        self.name = name
        #: Negative control. Permutes the metadata->participant mapping across the
        #: fold's TRAINING participants only, so validation participants keep their
        #: true (unseen) rows and the marginal distribution is preserved. If a
        #: candidate still "wins" under this, it was not using biology.
        self.shuffle_seed = shuffle_seed
        self._median: pd.Series | None = None
        self._remap: dict[str, str] = {}

    def fit(self, train_participants):
        present = [p for p in train_participants if p in self.table.index]
        block = self.table.loc[present, self.fields]
        self._median = block.median(numeric_only=True)
        if self.shuffle_seed is not None:
            rng = np.random.default_rng(self.shuffle_seed)
            shuffled = list(present)
            rng.shuffle(shuffled)
            self._remap = dict(zip(present, shuffled))
        return self

    def columns(self, participant_ids):
        keys = [self._remap.get(p, p) for p in participant_ids]
        rows = self.table.reindex(keys)[self.fields]
        rows.index = range(len(rows))
        return rows.fillna(self._median)


def fit_blend(features: pd.DataFrame, source, config):
    """The production fs-1.3 family: 0.5*ridge + 0.5*GBM, then shrunk calibration."""
    ridge = GeneralizedModel().fit(features, source.deltas, config)
    booster = GbmModel().fit(_View(features, source), config)

    class _Blend:
        def predict_deltas(self, f):
            r, g = ridge.predict_deltas(f), booster.predict_deltas(f)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    blend = _Blend()
    return blend, _personalize(blend, _View(features, source), config)


def metadata_candidate(name: str, attachment: Attachment | None, notes: str = ""):
    """A Candidate that is fs-1.3 plus `attachment`'s columns, and nothing else."""
    from ml.devvalidation import Candidate

    def run(train, validate, config):
        if attachment is None:
            X_tr, X_va = train.features, validate.features
        else:
            a = attachment.fit(np.unique(train.participant_ids))
            X_tr = pd.concat(
                [train.features.reset_index(drop=True), a.columns(train.participant_ids)],
                axis=1)
            X_va = pd.concat(
                [validate.features.reset_index(drop=True), a.columns(validate.participant_ids)],
                axis=1)
        blend, pers = fit_blend(X_tr, train, config)
        return pers.apply(validate.participant_ids, blend.predict_deltas(X_va))

    return Candidate(name, run, notes)


def row(result, reference=None, extra: dict | None = None) -> dict:
    """Flatten an OofResult into the full Phase 6 metric set."""
    from ml.devvalidation import paired_participant_bootstrap
    from ml.metrics import mae, rmse

    pooled = np.abs(np.asarray(result.pooled_errors(), dtype="float64"))
    out = {
        "model": result.name,
        "episodes": result.n_validation_episodes,
        "mae": float(pooled.mean()) if pooled.size else float("nan"),
        "rmse": rmse(result.pooled_errors()),
        "bias": float(np.mean(result.pooled_errors())) if pooled.size else float("nan"),
    }
    for h in HORIZONS:
        out[f"mae@{h}"] = mae(result.errors.get(h, []))
    c = result.curve
    out["peak_mae"] = mae(c.peak_delta) if c.peak_delta else float("nan")
    out["ttp_mae"] = mae(c.time_to_peak) if c.time_to_peak else float("nan")
    out["iauc_mae"] = mae(c.iauc) if c.iauc else float("nan")
    spread = list(result.fold_mae.values())
    out["fold_mae_spread"] = round(max(spread) - min(spread), 3) if spread else float("nan")
    out["fit_seconds"] = round(result.fit_seconds, 1)
    out["n_participants"] = len(result.participant_mae)
    if reference is not None:
        ci = paired_participant_bootstrap(reference, result)
        point = ci.get("point")
        out["delta"] = float("nan") if point is None else round(float(point), 4)
        out["ci_low"] = round(ci.get("ci_low", float("nan")), 4)
        out["ci_high"] = round(ci.get("ci_high", float("nan")), 4)
        out["excludes_zero"] = bool(ci.get("excludes_zero", False))
        deltas = {p: reference.participant_mae[p] - v
                  for p, v in result.participant_mae.items()
                  if p in reference.participant_mae}
        if deltas:
            worst = min(deltas, key=deltas.get)
            out["worst_participant_delta"] = round(deltas[worst], 3)
            out["worst_participant"] = worst
            out["n_participants_worse"] = sum(1 for v in deltas.values() if v < 0)
    if extra:
        out.update(extra)
    return out


def score_all(folds, candidates, config, reference_name: str, label: str) -> list[dict]:
    """Score every candidate on identical folds; CIs are paired against the reference."""
    from ml.devvalidation import score_candidate

    scored = {}
    for cand in candidates:
        t0 = time.perf_counter()
        scored[cand.name] = score_candidate(folds, cand, config)
        print(f"    [{label}] {cand.name:46s} "
              f"MAE {scored[cand.name].mae:7.3f}  ({time.perf_counter()-t0:5.1f}s)", flush=True)
    ref = scored[reference_name]
    return [row(scored[n], None if n == reference_name else ref) for n in scored], scored
