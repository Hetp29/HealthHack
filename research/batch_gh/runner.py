"""Batches G and H: does self-supervised CGM pretraining help downstream forecasting?

    python -m research.batch_gh.runner --db-url sqlite:///data/local/cgmacros_v2.db

Run on COLD-START folds, where the pretraining scope is unambiguous: the CGMacros
corpus is restricted to that fold's TRAINING participants, so validation participants
are entirely unseen by the encoder. This is the inductive setting.

Every configuration appends the FROZEN encoder's 16-dimensional representation to the
39 fs-1.3 features and refits the production blend, so the only thing that varies is
what the encoder learned. The one exception is G5, which fine-tunes the encoder on the
supervised episode task first.

CONTROLS (mission G6), without which "pretraining helped" is unfalsifiable:
  * random untrained encoder, frozen -- same architecture, same representation width,
    no training at all;
  * shuffled-objective pretraining -- identical architecture, identical training budget,
    but the forecast targets are permuted across windows so the objective carries no
    information;
  * the supervised-only GRU from Batch E, already measured;
  * fs-1.3's hand-engineered glucose features, which are the real incumbent.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

import numpy as np
import pandas as pd
import torch

from ml.candidates import GbmModel, _personalize
from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devvalidation import participant_holdout_folds
from ml.train import GeneralizedModel, episodes_to_matrices
from research.batch_b.sequences import load_series
from research.batch_c.runner import _View
from research.batch_gh.pretrain import (
    WINDOW,
    PretrainEncoder,
    cgmacros_windows,
    metabonet_windows,
    pretrain,
)
from research.neural.data import build_tensors

RESULTS = pathlib.Path("research/batch_gh/results")


def represent(encoder, sequences: np.ndarray, scale: float) -> np.ndarray:
    """Frozen 16-d representation of each episode's pre-meal glucose channel."""
    glucose = sequences[:, 0, -WINDOW:] / (scale or 1.0)
    encoder.eval()
    with torch.no_grad():
        return encoder(torch.from_numpy(glucose[:, None, :]).float()).numpy().astype("float64")


def fit_blend(features, source, config):
    ridge = GeneralizedModel().fit(features, source.deltas, config)
    booster = GbmModel().fit(_View(features, source), config)

    class _B:
        def predict_deltas(self, f):
            r, g = ridge.predict_deltas(f), booster.predict_deltas(f)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    b = _B()
    return b, _personalize(b, _View(features, source), config)


def finetune(encoder, sequences, statics, targets, mask, scale, epochs=25, lr=3e-4):
    """G5 -- continue training the pretrained encoder on the supervised episode task."""
    import torch.nn as nn

    from research.neural.train_loop import masked_l1

    glucose = torch.from_numpy(sequences[:, 0, -WINDOW:] / (scale or 1.0))[:, None, :].float()
    st = torch.from_numpy(statics).float()
    y = torch.nan_to_num(torch.from_numpy(targets).float())
    m = torch.from_numpy(mask.astype("float32"))
    head = nn.Sequential(nn.Linear(encoder.out_dim + st.shape[1], 32), nn.ReLU(), nn.Linear(32, 4))
    opt = torch.optim.Adam(list(encoder.parameters()) + list(head.parameters()),
                           lr=lr, weight_decay=1e-4)
    idx = np.arange(glucose.shape[0])
    rng = np.random.default_rng(20261011)
    for _ in range(epochs):
        encoder.train(); rng.shuffle(idx)
        for s in range(0, idx.size, 64):
            b = idx[s : s + 64]
            opt.zero_grad()
            loss = masked_l1(head(torch.cat([encoder(glucose[b]), st[b]], dim=-1)), y[b], m[b])
            if not torch.isfinite(loss):
                continue
            loss.backward()
            nn.utils.clip_grad_norm_(list(encoder.parameters()) + list(head.parameters()), 1.0)
            opt.step()
    return encoder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--metabonet", default="data/Type1Diabetes.parquet")
    parser.add_argument("--pretrain-epochs", type=int, default=8)
    args = parser.parse_args()

    from ml.experiments import load_dev_pool

    dev, _ = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    stamps, values = load_series(args.db_url)
    seq, tgt, msk, _ = build_tensors(dev, stamps, values, 180)
    index = {(e.participant_id, e.meal_id): i for i, e in enumerate(dev)}

    # MetaboNet corpus is dataset-level, so it is built once and reused across folds.
    t0 = time.perf_counter()
    mb_windows = metabonet_windows(args.metabonet, row_groups=6, limit=60000)
    print(f"MetaboNet corpus: {mb_windows.shape} windows in {time.perf_counter()-t0:.0f}s")

    accum: dict[str, list[float]] = {}
    base_maes: list[float] = []
    audit: dict[str, dict] = {}

    for fold in participant_holdout_folds(dev, DEFAULT_CONFIG, 4):
        tr_i = np.array([index[(e.participant_id, e.meal_id)] for e in fold.train])
        va_i = np.array([index[(e.participant_id, e.meal_id)] for e in fold.validate])
        tm, vm = episodes_to_matrices(fold.train), episodes_to_matrices(fold.validate)
        med = tm.features.median(numeric_only=True)
        st_tr = tm.features.fillna(med).to_numpy("float32")
        obs = np.column_stack([vm.deltas[h].to_numpy("float64") for h in HORIZONS])
        vmask = np.isfinite(obs)

        base_b, base_p = fit_blend(tm.features, tm, DEFAULT_CONFIG)
        base_pred = np.column_stack(
            [base_p.apply(vm.participant_ids, base_b.predict_deltas(vm.features))[h]
             for h in HORIZONS])
        base_maes.append(float(np.abs(base_pred - obs)[vmask].mean()))

        # CGMacros corpus: TRAINING participants of this fold only.
        train_participants = {e.participant_id for e in fold.train}
        cg_windows = cgmacros_windows(stamps, values, train_participants, limit=60000)
        combined = np.concatenate([cg_windows, mb_windows], axis=0)
        audit[f"fold{fold.index}"] = {
            "train_participants": len(train_participants),
            "cgmacros_windows": int(cg_windows.shape[0]),
            "metabonet_windows": int(mb_windows.shape[0]),
            "combined_windows": int(combined.shape[0]),
        }

        variants = []
        variants.append(("G6a CTRL random encoder", PretrainEncoder(16), 1.0, False))
        for label, corpus, objective in (
            ("G6b CTRL shuffled objective", cg_windows, "shuffled"),
            ("G1 CGMacros forecast", cg_windows, "forecast"),
            ("G2 CGMacros masked", cg_windows, "masked"),
            ("H2 MetaboNet forecast", mb_windows, "forecast"),
            ("H4 combined forecast", combined, "forecast"),
        ):
            enc, hist, scale = pretrain(corpus, objective, epochs=args.pretrain_epochs)
            variants.append((label, enc, scale, False))
            audit.setdefault(label, {})[f"fold{fold.index}_loss"] = [round(h, 4) for h in hist[:1] + hist[-1:]]
        # G5: fine-tune the CGMacros-pretrained encoder on the supervised task.
        enc_ft, _, scale_ft = pretrain(cg_windows, "forecast", epochs=args.pretrain_epochs)
        enc_ft = finetune(enc_ft, seq[tr_i], st_tr, tgt[tr_i], msk[tr_i], scale_ft)
        variants.append(("G5 CGMacros forecast + fine-tune", enc_ft, scale_ft, True))

        for label, encoder, scale, _ft in variants:
            rep_tr = represent(encoder, seq[tr_i], scale)
            rep_va = represent(encoder, seq[va_i], scale)
            cols = [f"ssl_{i}" for i in range(rep_tr.shape[1])]
            X_tr = pd.concat([tm.features.reset_index(drop=True),
                              pd.DataFrame(rep_tr, columns=cols)], axis=1)
            X_va = pd.concat([vm.features.reset_index(drop=True),
                              pd.DataFrame(rep_va, columns=cols)], axis=1)
            b, p = fit_blend(X_tr, tm, DEFAULT_CONFIG)
            pred = np.column_stack(
                [p.apply(vm.participant_ids, b.predict_deltas(X_va))[h] for h in HORIZONS])
            accum.setdefault(label, []).append(float(np.abs(pred - obs)[vmask].mean()))

    base = float(np.mean(base_maes))
    print(f"\ncold-start, 4 folds. fs-1.3 reference MAE {base:.3f}\n")
    print(f"{'configuration':36s}{'MAE':>8s}{'vs fs13':>9s}{'vs random ctrl':>16s}")
    random_mae = float(np.mean(accum["G6a CTRL random encoder"]))
    rows = [{"config": "fs-1.3 blend (reference)", "mae": base, "delta_vs_fs13": 0.0}]
    for label, maes in sorted(accum.items(), key=lambda kv: np.mean(kv[1])):
        mean = float(np.mean(maes))
        print(f"{label:36s}{mean:8.3f}{base-mean:+9.3f}{random_mae-mean:+16.3f}")
        rows.append({"config": label, "mae": mean, "delta_vs_fs13": base - mean,
                     "delta_vs_random_encoder": random_mae - mean,
                     "folds": len(maes)})

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "gh_leaderboard.json").write_text(
        json.dumps({"fs13_mae": base, "rows": rows, "corpus_audit": audit}, indent=2) + "\n")
    print(f"\nwrote {RESULTS}/gh_leaderboard.json")
    print("corpus audit (fold 0):", json.dumps(audit.get("fold0", {})))
    return 0


if __name__ == "__main__":
    from research.batch_gh.runner import main as _main
    raise SystemExit(_main())
