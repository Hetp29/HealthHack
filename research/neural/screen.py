"""Successive-fidelity step 1: screen every D/E architecture on ONE cold-start fold.

Cheap pass to find which architectures train stably and show any signal at all, before
spending compute on all folds. Direct-prediction mode only here -- it is the harder test
and the fastest to run.
"""

from __future__ import annotations

import argparse

import numpy as np

from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devvalidation import participant_holdout_folds
from ml.train import episodes_to_matrices
from research.batch_b.sequences import load_series
from research.neural import models as M
from research.neural.data import build_tensors, standardize_sequences
from research.neural.train_loop import fit, predict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--window", type=int, default=180)
    args = parser.parse_args()

    from ml.experiments import load_dev_pool

    dev, _ = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    stamps, values = load_series(args.db_url)
    seq, tgt, msk, _ = build_tensors(dev, stamps, values, args.window)
    index = {(e.participant_id, e.meal_id): i for i, e in enumerate(dev)}

    fold = participant_holdout_folds(dev, DEFAULT_CONFIG, 4)[0]
    tr_i = np.array([index[(e.participant_id, e.meal_id)] for e in fold.train])
    va_i = np.array([index[(e.participant_id, e.meal_id)] for e in fold.validate])
    (s_tr, s_va), _ = standardize_sequences(seq[tr_i], seq[va_i])

    tm, vm = episodes_to_matrices(fold.train), episodes_to_matrices(fold.validate)
    med = tm.features.median(numeric_only=True)
    st_tr = tm.features.fillna(med).to_numpy("float32")
    st_va = vm.features.fillna(med).to_numpy("float32")
    parts = np.array([e.participant_id for e in fold.train])

    # Reference: the fs-1.3 production blend on this same fold.
    from ml.candidates import GbmModel, _personalize
    from ml.train import GeneralizedModel
    from research.batch_c.runner import _View

    ridge = GeneralizedModel().fit(tm.features, tm.deltas, DEFAULT_CONFIG)
    booster = GbmModel().fit(_View(tm.features, tm), DEFAULT_CONFIG)

    class _Blend:
        def predict_deltas(self, f):
            r, g = ridge.predict_deltas(f), booster.predict_deltas(f)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    blend = _Blend()
    pers = _personalize(blend, _View(tm.features, tm), DEFAULT_CONFIG)
    base = pers.apply(vm.participant_ids, blend.predict_deltas(vm.features))
    obs = np.column_stack([vm.deltas[h].to_numpy("float64") for h in HORIZONS])
    vmask = np.isfinite(obs)
    base_pred = np.column_stack([base[h] for h in HORIZONS])
    print(f"fold {fold.index}: train {len(fold.train)} validate {len(fold.validate)}")
    print(f"  fs-1.3 reference MAE on this fold: "
          f"{np.abs(base_pred - obs)[vmask].mean():.3f}\n")

    configs = [
        ("D1 CausalCNN-8",  lambda: M.CausalCNN(channels=8)),
        ("D1 CausalCNN-16", lambda: M.CausalCNN(channels=16)),
        ("D1 CausalCNN-32", lambda: M.CausalCNN(channels=32)),
        ("D2 DilatedTCN-16", lambda: M.DilatedTCN(channels=16)),
        ("D2 DilatedTCN-16 rf63", lambda: M.DilatedTCN(channels=16, dilations=(1,2,4,8,16))),
        ("E1 GRU-8",  lambda: M.GruEncoder(hidden=8)),
        ("E1 GRU-16", lambda: M.GruEncoder(hidden=16)),
        ("E1 GRU-32", lambda: M.GruEncoder(hidden=32)),
        ("E3 GRU-D-16", lambda: M.GruD(hidden=16)),
        ("E6 LSTM-16", lambda: M.LstmEncoder(hidden=16)),
    ]
    print(f"{'architecture':24s}{'params':>8s}{'epochs':>8s}{'innerMAE':>10s}{'foldMAE':>9s}{'vs fs13':>9s}{'sec':>7s}  status")
    for name, factory in configs:
        model = M.SequenceModel(factory(), static_dim=st_tr.shape[1])
        result = fit(model, s_tr, st_tr, tgt[tr_i], msk[tr_i], parts, max_epochs=150, patience=15)
        if result.failed:
            print(f"{name:24s}{M.count_parameters(model):8d}{result.epochs:8d}{'--':>10s}{'--':>9s}{'--':>9s}"
                  f"{result.train_seconds:7.0f}  FAILED: {result.failure}")
            continue
        pred = predict(result.model, s_va, st_va)
        mae = np.abs(pred - obs)[vmask].mean()
        finite = np.isfinite(pred).all()
        print(f"{name:24s}{M.count_parameters(model):8d}{result.epochs:8d}{result.best_inner_mae:10.3f}"
              f"{mae:9.3f}{mae - np.abs(base_pred-obs)[vmask].mean():+9.3f}{result.train_seconds:7.0f}"
              f"  {'ok' if finite else 'NON-FINITE OUTPUT'}")
    return 0


if __name__ == "__main__":
    from research.neural.screen import main as _main
    raise SystemExit(_main())
