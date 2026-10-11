"""Batch F screen: Neural CDE / ODE on one cold-start fold, with the F5 stress test.

Successive fidelity step 1. CDE integration is far more expensive than a GRU, so the
screen decides whether a full-fold run is warranted at all.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

import numpy as np
import torch
import torch.nn as nn

from ml.candidates import GbmModel, _personalize
from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devvalidation import participant_holdout_folds
from ml.train import GeneralizedModel, episodes_to_matrices
from research.batch_b.sequences import load_series
from research.batch_c.runner import _View
from research.batch_f.models import NeuralCde, NeuralOde, PathModel
from research.neural.data import build_tensors, standardize_sequences
from research.neural.train_loop import masked_l1, set_seed

RESULTS = pathlib.Path("research/batch_f/results")


def make_path(seq: np.ndarray, lattice: np.ndarray) -> torch.Tensor:
    """(batch, length, 4): time, glucose, mask, elapsed. Time is channel 0 for torchcde."""
    n, _, length = seq.shape
    t = (lattice.max() - lattice) / max(lattice.max(), 1.0)
    time_ch = np.broadcast_to(t.astype("float32"), (n, length))
    return torch.from_numpy(
        np.stack([time_ch, seq[:, 0, :], seq[:, 1, :], seq[:, 2, :]], axis=-1).astype("float32")
    )


def drop_observations(seq: np.ndarray, keep: float, seed: int = 7) -> np.ndarray:
    """F5 -- artificially thin the observed history, keeping the mask honest."""
    rng = np.random.default_rng(seed)
    out = seq.copy()
    drop = (rng.random(out[:, 1, :].shape) > keep) & (out[:, 1, :] > 0)
    out[:, 0, :][drop] = 0.0
    out[:, 1, :][drop] = 0.0
    return out


def train_once(model, path_tr, st_tr, tgt_tr, msk_tr, parts, max_epochs, patience, lr=1e-3):
    set_seed()
    unique = np.unique(parts)
    rng = np.random.default_rng(20261011)
    held = set(rng.choice(unique, max(1, len(unique) // 5), replace=False))
    val = np.array([p in held for p in parts]); tr = ~val
    tgt = torch.nan_to_num(torch.from_numpy(tgt_tr).float())
    msk = torch.from_numpy(msk_tr.astype("float32"))
    st = torch.from_numpy(st_tr).float()
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    best, best_state, best_epoch = float("inf"), None, 0
    tr_idx, val_idx = np.flatnonzero(tr), np.flatnonzero(val)
    nonfinite = 0
    for epoch in range(max_epochs):
        model.train(); rng.shuffle(tr_idx)
        for s in range(0, tr_idx.size, 64):
            b = tr_idx[s : s + 64]
            opt.zero_grad()
            loss = masked_l1(model(path_tr[b], st[b]), tgt[b], msk[b])
            if not torch.isfinite(loss):
                nonfinite += 1
                continue
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        model.eval()
        with torch.no_grad():
            v = float(masked_l1(model(path_tr[val_idx], st[val_idx]), tgt[val_idx], msk[val_idx]))
        if not np.isfinite(v):
            return None, epoch, nonfinite
        if v < best - 1e-4:
            best, best_epoch = v, epoch
            best_state = {k: t.detach().clone() for k, t in model.state_dict().items()}
        elif epoch - best_epoch >= patience:
            break
    if best_state:
        model.load_state_dict(best_state)
    return best, best_epoch + 1, nonfinite


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--max-epochs", type=int, default=40)
    args = parser.parse_args()

    from ml.experiments import load_dev_pool

    dev, _ = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    stamps, values = load_series(args.db_url)
    seq, tgt, msk, lattice = build_tensors(dev, stamps, values, 180)
    index = {(e.participant_id, e.meal_id): i for i, e in enumerate(dev)}

    fold = participant_holdout_folds(dev, DEFAULT_CONFIG, 4)[0]
    tr_i = np.array([index[(e.participant_id, e.meal_id)] for e in fold.train])
    va_i = np.array([index[(e.participant_id, e.meal_id)] for e in fold.validate])
    tm, vm = episodes_to_matrices(fold.train), episodes_to_matrices(fold.validate)
    med = tm.features.median(numeric_only=True)
    st_tr = tm.features.fillna(med).to_numpy("float32")
    st_va = vm.features.fillna(med).to_numpy("float32")
    parts = np.array([e.participant_id for e in fold.train])
    obs = np.column_stack([vm.deltas[h].to_numpy("float64") for h in HORIZONS])
    vmask = np.isfinite(obs)

    ridge = GeneralizedModel().fit(tm.features, tm.deltas, DEFAULT_CONFIG)
    booster = GbmModel().fit(_View(tm.features, tm), DEFAULT_CONFIG)

    class _B:
        def predict_deltas(self, f):
            r, g = ridge.predict_deltas(f), booster.predict_deltas(f)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    b = _B()
    pers = _personalize(b, _View(tm.features, tm), DEFAULT_CONFIG)
    base = pers.apply(vm.participant_ids, b.predict_deltas(vm.features))
    base_pred = np.column_stack([base[h] for h in HORIZONS])
    base_mae = np.abs(base_pred - obs)[vmask].mean()
    print(f"fold {fold.index}: train {len(fold.train)} validate {len(fold.validate)}")
    print(f"fs-1.3 reference MAE on this fold: {base_mae:.3f}\n")

    rows = []
    configs = [
        ("F1 CDE-8 linear",      lambda: NeuralCde(hidden=8, interpolation="linear"), 1.0),
        ("F1 CDE-16 linear",     lambda: NeuralCde(hidden=16, interpolation="linear"), 1.0),
        ("F2 CDE-8 rectilinear", lambda: NeuralCde(hidden=8, interpolation="rectilinear"), 1.0),
        ("F6 ODE-8 (control)",   lambda: NeuralOde(hidden=8), 1.0),
        ("F5 CDE-8 keep 50%",    lambda: NeuralCde(hidden=8, interpolation="linear"), 0.5),
        ("F5 CDE-8 keep 25%",    lambda: NeuralCde(hidden=8, interpolation="linear"), 0.25),
    ]
    print(f"{'config':24s}{'params':>8s}{'epochs':>8s}{'innerMAE':>10s}{'foldMAE':>9s}{'vs fs13':>9s}{'sec':>7s}  status")
    for name, factory, keep in configs:
        s_tr_raw = seq[tr_i] if keep >= 1.0 else drop_observations(seq[tr_i], keep)
        s_va_raw = seq[va_i] if keep >= 1.0 else drop_observations(seq[va_i], keep, seed=11)
        (s_tr, s_va), _ = standardize_sequences(s_tr_raw, s_va_raw)
        path_tr, path_va = make_path(s_tr, lattice), make_path(s_va, lattice)
        model = PathModel(factory(), static_dim=st_tr.shape[1])
        params = sum(p.numel() for p in model.parameters())
        t0 = time.perf_counter()
        try:
            inner, epochs, nonfinite = train_once(
                model, path_tr, st_tr, tgt[tr_i], msk[tr_i], parts, args.max_epochs, 8
            )
        except Exception as error:
            print(f"{name:24s}{params:8d}{'--':>8s}{'--':>10s}{'--':>9s}{'--':>9s}"
                  f"{time.perf_counter()-t0:7.0f}  FAILED {type(error).__name__}: {str(error)[:40]}")
            rows.append({"config": name, "status": f"FAILED {type(error).__name__}", "params": params})
            continue
        if inner is None:
            print(f"{name:24s}{params:8d}{epochs:8d}{'--':>10s}{'--':>9s}{'--':>9s}"
                  f"{time.perf_counter()-t0:7.0f}  DIVERGED (non-finite loss)")
            rows.append({"config": name, "status": "diverged", "params": params})
            continue
        model.eval()
        with torch.no_grad():
            pred = model(path_va, torch.from_numpy(st_va).float()).numpy().astype("float64")
        mae = np.abs(pred - obs)[vmask].mean()
        finite = bool(np.isfinite(pred).all())
        print(f"{name:24s}{params:8d}{epochs:8d}{inner:10.3f}{mae:9.3f}{base_mae-mae:+9.3f}"
              f"{time.perf_counter()-t0:7.0f}  {'ok' if finite else 'NON-FINITE'} nf_batches={nonfinite}")
        rows.append({"config": name, "params": params, "epochs": epochs, "inner_mae": inner,
                     "fold_mae": float(mae), "delta_vs_fs13": float(base_mae - mae),
                     "seconds": time.perf_counter() - t0, "finite": finite,
                     "nonfinite_batches": nonfinite, "status": "ok"})

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "f_screen.json").write_text(
        json.dumps({"fold": fold.index, "fs13_fold_mae": float(base_mae), "rows": rows}, indent=2) + "\n")
    print(f"\nwrote {RESULTS}/f_screen.json")
    return 0


if __name__ == "__main__":
    from research.batch_f.screen import main as _main
    raise SystemExit(_main())
