"""Batch F on all cold-start folds, with the static-only control from Batch D/E.

The screen showed several CDE configs beating fs-1.3 on ONE fold. Batch D/E taught the
lesson that applies here: these models also receive the 39 static fs-1.3 features
through an MLP head, and a static-only MLP with NO sequence looked good on a single
fold too. So the static-only control is carried into this run, and the F5 irregularity
stress test is included -- if the CDE were exploiting the observed path, thinning that
path should HURT.
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
from research.batch_f.screen import drop_observations, make_path, train_once
from research.neural.data import build_tensors, standardize_sequences

RESULTS = pathlib.Path("research/batch_f/results")


class _StaticOnly(nn.Module):
    out_dim = 1

    def forward(self, path):
        return path.new_zeros(path.shape[0], 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--max-epochs", type=int, default=40)
    args = parser.parse_args()

    from ml.experiments import load_dev_pool

    dev, _ = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    stamps, values = load_series(args.db_url)
    seq, tgt, msk, lattice = build_tensors(dev, stamps, values, 180)
    index = {(e.participant_id, e.meal_id): i for i, e in enumerate(dev)}

    configs = [
        ("CTRL static-only (no path)", lambda: _StaticOnly(), 1.0),
        ("F1 CDE-8 linear",            lambda: NeuralCde(hidden=8, interpolation="linear"), 1.0),
        ("F2 CDE-8 rectilinear",       lambda: NeuralCde(hidden=8, interpolation="rectilinear"), 1.0),
        ("F6 ODE-8 (control)",         lambda: NeuralOde(hidden=8), 1.0),
        ("F5 CDE-8 keep 25%",          lambda: NeuralCde(hidden=8, interpolation="linear"), 0.25),
    ]

    accum: dict[str, list[float]] = {}
    base_maes: list[float] = []
    extras: dict[str, list[dict]] = {}

    for fold in participant_holdout_folds(dev, DEFAULT_CONFIG, 4):
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
        base_maes.append(float(np.abs(base_pred - obs)[vmask].mean()))

        for name, factory, keep in configs:
            raw_tr = seq[tr_i] if keep >= 1.0 else drop_observations(seq[tr_i], keep)
            raw_va = seq[va_i] if keep >= 1.0 else drop_observations(seq[va_i], keep, seed=11)
            (s_tr, s_va), _ = standardize_sequences(raw_tr, raw_va)
            path_tr, path_va = make_path(s_tr, lattice), make_path(s_va, lattice)
            model = PathModel(factory(), static_dim=st_tr.shape[1])
            t0 = time.perf_counter()
            try:
                inner, epochs, nonfinite = train_once(
                    model, path_tr, st_tr, tgt[tr_i], msk[tr_i], parts, args.max_epochs, 8
                )
            except Exception as error:
                extras.setdefault(name, []).append(
                    {"fold": fold.index, "status": f"FAILED {type(error).__name__}"})
                continue
            if inner is None:
                extras.setdefault(name, []).append({"fold": fold.index, "status": "diverged"})
                continue
            model.eval()
            with torch.no_grad():
                pred = model(path_va, torch.from_numpy(st_va).float()).numpy().astype("float64")
            accum.setdefault(name, []).append(float(np.abs(pred - obs)[vmask].mean()))
            extras.setdefault(name, []).append({
                "fold": fold.index, "status": "ok", "epochs": epochs,
                "params": sum(p.numel() for p in model.parameters()),
                "seconds": round(time.perf_counter() - t0, 1),
                "nonfinite_batches": nonfinite,
                "bias": float((pred - obs)[vmask].mean()),
                "max_abs_pred": float(np.abs(pred).max()),
            })

    base = float(np.mean(base_maes))
    print(f"\ncold-start, 4 folds. fs-1.3 reference MAE {base:.3f}\n")
    print(f"{'config':28s}{'MAE':>8s}{'vs fs13':>9s}{'bias':>8s}{'params':>8s}{'sec':>7s}  status")
    rows = [{"config": "fs-1.3 blend (reference)", "mae": base, "delta_vs_fs13": 0.0}]
    for name, maes in accum.items():
        meta = extras.get(name, [])
        mean = float(np.mean(maes))
        bias = float(np.mean([m["bias"] for m in meta if "bias" in m]))
        params = int(np.mean([m["params"] for m in meta if "params" in m]))
        secs = float(np.mean([m["seconds"] for m in meta if "seconds" in m]))
        nf = sum(m.get("nonfinite_batches", 0) for m in meta)
        print(f"{name:28s}{mean:8.3f}{base-mean:+9.3f}{bias:+8.2f}{params:8d}{secs:7.1f}"
              f"  folds={len(maes)}/4 nf_batches={nf}")
        rows.append({"config": name, "mae": mean, "delta_vs_fs13": base - mean,
                     "bias": bias, "params": params, "seconds": secs,
                     "folds_completed": len(maes), "nonfinite_batches": nf})

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "f_leaderboard.json").write_text(
        json.dumps({"fs13_mae": base, "rows": rows, "per_fold": extras}, indent=2) + "\n")
    print(f"\nwrote {RESULTS}/f_leaderboard.json")
    return 0


if __name__ == "__main__":
    from research.batch_f.runner import main as _main
    raise SystemExit(_main())
