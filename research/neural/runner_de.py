"""Batches D and E on all locked folds, WITH the controls that isolate the sequence.

    python -m research.neural.runner_de --db-url sqlite:///data/local/cgmacros_v2.db

WHY THE CONTROLS MATTER
-----------------------
The single-fold screen showed every neural architecture beating fs-1.3. But those models
receive the 39 static fs-1.3 features AND the sequence, through an MLP head. So the gain
could come from the MLP head's nonlinearity over the static features, with the sequence
contributing nothing. Two controls separate those explanations:

  STATIC-ONLY MLP   -- identical head, identical static features, sequence input removed.
                       If this matches the full model, the sequence is doing nothing.
  SHUFFLED SEQUENCE -- the full architecture, but each episode is paired with ANOTHER
                       episode's sequence. If this matches the full model, the sequence
                       is being ignored rather than used.

Both are necessary. Without them a "neural models win" headline would be unfalsifiable.

Modes:
  direct         -- the network predicts the four horizon deltas itself.
  representation -- the trained encoder's output is appended to the fs-1.3 features and
                    the production blend is refitted on top (feature augmentation).
  residual       -- a sequence model predicts the fs-1.3 residual, trained on OOF base
                    predictions from a participant-grouped inner CV.

The 394 reserved Day-8 episodes are never loaded.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

import numpy as np
import pandas as pd

from ml.candidates import GbmModel, _personalize
from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devvalidation import participant_holdout_folds, rolling_folds
from ml.train import GeneralizedModel, episodes_to_matrices
from research.batch_b.sequences import load_series
from research.neural import models as M
from research.neural.data import build_tensors, standardize_sequences
from research.neural.train_loop import determinism_note, fit, predict, represent

RESULTS = pathlib.Path("research/neural/results")
MAX_EPOCHS, PATIENCE = 120, 12


class _View:
    def __init__(self, features, source):
        self.features = features
        self.deltas = source.deltas
        self.participant_ids = source.participant_ids
        self.baselines = source.baselines
        self.meal_timestamps = source.meal_timestamps
        self.meal_ids = source.meal_ids

    def __len__(self):
        return len(self.features)

    def valid_mask(self, h):
        return self.deltas[h].notna().to_numpy()


def _fit_blend(features, source, config):
    ridge = GeneralizedModel().fit(features, source.deltas, config)
    booster = GbmModel().fit(_View(features, source), config)

    class _Blend:
        def predict_deltas(self, f):
            r, g = ridge.predict_deltas(f), booster.predict_deltas(f)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    blend = _Blend()
    return blend, _personalize(blend, _View(features, source), config)


def _metrics(pred, obs, mask, participants):
    errors = np.abs(pred - obs)
    per_participant = {}
    for p in np.unique(participants):
        sel = (participants == p)[:, None] & mask
        if sel.any():
            per_participant[str(p)] = float(errors[sel].mean())
    return {
        "mae": float(errors[mask].mean()),
        "rmse": float(np.sqrt((((pred - obs) ** 2)[mask]).mean())),
        "bias": float((pred - obs)[mask].mean()),
        **{f"mae@{h}": float(errors[mask[:, j], j].mean()) for j, h in enumerate(HORIZONS)},
        "n_nonfinite": int((~np.isfinite(pred)).sum()),
        "max_abs_pred": float(np.abs(pred[np.isfinite(pred)]).max()) if np.isfinite(pred).any() else np.nan,
        "_per_participant": per_participant,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--window", type=int, default=180)
    args = parser.parse_args()

    from ml.experiments import load_dev_pool

    dev, meta = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    stamps, values = load_series(args.db_url)
    seq, tgt, msk, _ = build_tensors(dev, stamps, values, args.window)
    index = {(e.participant_id, e.meal_id): i for i, e in enumerate(dev)}
    print(f"dev {len(dev)} | window {args.window} min | reserved test NOT loaded: {meta['reserved_test']}")
    print(determinism_note())

    configs = [
        ("CTRL static-only MLP",   lambda: None,                                   "direct"),
        ("CTRL shuffled sequence", lambda: M.DilatedTCN(channels=16, dilations=(1,2,4,8,16)), "shuffled"),
        ("D1 CausalCNN-32",        lambda: M.CausalCNN(channels=32),               "direct"),
        ("D2 TCN-16 rf63",         lambda: M.DilatedTCN(channels=16, dilations=(1,2,4,8,16)), "direct"),
        ("D4 TCN repr -> fs-1.3",  lambda: M.DilatedTCN(channels=16, dilations=(1,2,4,8,16)), "representation"),
        ("E1 GRU-16",              lambda: M.GruEncoder(hidden=16),                "direct"),
        ("E3 GRU-D-16",            lambda: M.GruD(hidden=16),                      "direct"),
        ("E6 LSTM-16",             lambda: M.LstmEncoder(hidden=16),               "direct"),
    ]

    rows, failures = [], []
    for regime, folds in (
        ("within_person", rolling_folds(dev, DEFAULT_CONFIG, 4)),
        ("cold_start", participant_holdout_folds(dev, DEFAULT_CONFIG, 4)),
    ):
        print(f"\n################ {regime} ################")
        accum: dict[str, list[dict]] = {}
        base_accum: list[dict] = []

        for fold in folds:
            tr_i = np.array([index[(e.participant_id, e.meal_id)] for e in fold.train])
            va_i = np.array([index[(e.participant_id, e.meal_id)] for e in fold.validate])
            (s_tr, s_va), _ = standardize_sequences(seq[tr_i], seq[va_i])
            tm, vm = episodes_to_matrices(fold.train), episodes_to_matrices(fold.validate)
            med = tm.features.median(numeric_only=True)
            st_tr = tm.features.fillna(med).to_numpy("float32")
            st_va = vm.features.fillna(med).to_numpy("float32")
            parts_tr = np.array([e.participant_id for e in fold.train])
            parts_va = np.array([e.participant_id for e in fold.validate])
            obs = np.column_stack([vm.deltas[h].to_numpy("float64") for h in HORIZONS])
            vmask = np.isfinite(obs)

            blend, pers = _fit_blend(tm.features, tm, DEFAULT_CONFIG)
            base_map = pers.apply(vm.participant_ids, blend.predict_deltas(vm.features))
            base_pred = np.column_stack([base_map[h] for h in HORIZONS])
            base_accum.append(_metrics(base_pred, obs, vmask, parts_va))

            for name, factory, mode in configs:
                try:
                    t0 = time.perf_counter()
                    if mode == "direct" and factory() is None:
                        model = M.SequenceModel(_ZeroEncoder(), static_dim=st_tr.shape[1])
                        result = fit(model, s_tr * 0.0, st_tr, tgt[tr_i], msk[tr_i], parts_tr,
                                     max_epochs=MAX_EPOCHS, patience=PATIENCE)
                        pred = predict(result.model, s_va * 0.0, st_va)
                    elif mode == "shuffled":
                        rng = np.random.default_rng(20261011)
                        perm_tr = rng.permutation(s_tr.shape[0])
                        perm_va = rng.permutation(s_va.shape[0])
                        model = M.SequenceModel(factory(), static_dim=st_tr.shape[1])
                        result = fit(model, s_tr[perm_tr], st_tr, tgt[tr_i], msk[tr_i], parts_tr,
                                     max_epochs=MAX_EPOCHS, patience=PATIENCE)
                        pred = predict(result.model, s_va[perm_va], st_va)
                    elif mode == "direct":
                        model = M.SequenceModel(factory(), static_dim=st_tr.shape[1])
                        result = fit(model, s_tr, st_tr, tgt[tr_i], msk[tr_i], parts_tr,
                                     max_epochs=MAX_EPOCHS, patience=PATIENCE)
                        pred = predict(result.model, s_va, st_va)
                    else:  # representation
                        model = M.SequenceModel(factory(), static_dim=st_tr.shape[1])
                        result = fit(model, s_tr, st_tr, tgt[tr_i], msk[tr_i], parts_tr,
                                     max_epochs=MAX_EPOCHS, patience=PATIENCE)
                        rep_tr, rep_va = represent(result.model, s_tr), represent(result.model, s_va)
                        cols = [f"nn_{i}" for i in range(rep_tr.shape[1])]
                        X_tr = pd.concat([tm.features.reset_index(drop=True),
                                          pd.DataFrame(rep_tr, columns=cols)], axis=1)
                        X_va = pd.concat([vm.features.reset_index(drop=True),
                                          pd.DataFrame(rep_va, columns=cols)], axis=1)
                        b2, p2 = _fit_blend(X_tr, tm, DEFAULT_CONFIG)
                        m2 = p2.apply(vm.participant_ids, b2.predict_deltas(X_va))
                        pred = np.column_stack([m2[h] for h in HORIZONS])

                    if result.failed:
                        failures.append({"regime": regime, "fold": fold.index,
                                         "candidate": name, "error": result.failure})
                        continue
                    scores = _metrics(pred, obs, vmask, parts_va)
                    scores["params"] = M.count_parameters(model)
                    scores["epochs"] = result.epochs
                    scores["train_seconds"] = time.perf_counter() - t0
                    accum.setdefault(name, []).append(scores)
                except Exception as error:
                    failures.append({"regime": regime, "fold": fold.index,
                                     "candidate": name, "error": f"{type(error).__name__}: {error}"})

        def summarize(name, folds_scores):
            keys = [k for k in folds_scores[0] if not k.startswith("_")]
            mean = {k: float(np.nanmean([s[k] for s in folds_scores])) for k in keys}
            per = {}
            for s in folds_scores:
                per.update(s["_per_participant"])
            return mean, per

        base_mean, base_per = summarize("base", base_accum)
        print(f"{'candidate':26s}{'MAE':>8s}{'vs fs13':>9s}{'RMSE':>8s}{'bias':>7s}"
              f"{'params':>8s}{'epochs':>7s}{'sec':>6s}{'worstPpt':>10s}  nonfinite")
        print(f"{'fs-1.3 blend (reference)':26s}{base_mean['mae']:8.3f}{'--':>9s}"
              f"{base_mean['rmse']:8.2f}{base_mean['bias']:+7.2f}{'--':>8s}{'--':>7s}{'--':>6s}{'--':>10s}  0")
        rows.append({"regime": regime, "candidate": "fs-1.3 blend (reference)", **base_mean})

        for name, folds_scores in accum.items():
            mean, per = summarize(name, folds_scores)
            worst = min((base_per.get(p, 0) - v for p, v in per.items()), default=np.nan)
            print(f"{name:26s}{mean['mae']:8.3f}{base_mean['mae']-mean['mae']:+9.3f}"
                  f"{mean['rmse']:8.2f}{mean['bias']:+7.2f}{mean['params']:8.0f}"
                  f"{mean['epochs']:7.0f}{mean['train_seconds']:6.0f}{worst:+10.2f}"
                  f"  {int(mean['n_nonfinite'])}")
            rows.append({"regime": regime, "candidate": name, "delta_vs_fs13": base_mean["mae"] - mean["mae"],
                         "worst_participant": worst, **mean})

    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(RESULTS / "de_leaderboard.csv", index=False)
    (RESULTS / "de_failures.json").write_text(json.dumps(failures, indent=2) + "\n")
    print(f"\nwrote {RESULTS}/de_leaderboard.csv | failures: {len(failures)}")
    return 0


class _ZeroEncoder(M.nn.Module):
    """Static-only control: emits a constant, so the head sees statics alone."""

    out_dim = 1

    def forward(self, x):
        return x.new_zeros(x.shape[0], 1)


if __name__ == "__main__":
    from research.neural.runner_de import main as _main
    raise SystemExit(_main())
