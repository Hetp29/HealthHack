"""Phase 4: Transformers as a distinct architectural hypothesis.

    python -m research.transformer.runner --db-url sqlite:///data/local/cgmacros_v2.db

Attention has not been evaluated anywhere in this project's earlier batches, so it
is tested on its own evidence rather than assumed to fail because CNNs, GRUs and
Neural CDEs did. Both downstream modes are run:

  DIRECT   the Transformer predicts the four horizon deltas itself, given the
           pre-meal sequence and the fs-1.3 static features. This is the mode in
           which every earlier neural family lost to fs-1.3.
  FROZEN   the Transformer's pooled embedding is appended to the 39 fs-1.3
           columns and the production blend is refitted. fs-1.3 is preserved
           exactly, so this mode can only add.

CONTROLS, all four that the mission requires:
  * identical architecture with NO pretraining (T1/T2/T3 are precisely this, and
    the pretrained variants T4/T5/T6 share T1's architecture exactly, so the
    comparison is architecture-matched rather than merely similar);
  * RANDOM frozen embeddings -- untrained weights, same shape, same width;
  * SHUFFLED pretraining objective -- same corpus, same budget, permuted targets;
  * fs-1.3 itself.

PRETRAINING LEAKAGE CONTROL
---------------------------
The CGMacros pretraining corpus is rebuilt per fold from that fold's TRAINING
participants only. A validation participant's CGM never enters pretraining, in
any objective, in either regime. For the within-person regime the participants
overlap by construction, so the corpus is additionally truncated to windows that
end strictly before the fold's validation block begins -- otherwise pretraining
could see the very future readings the targets are made of. MetaboNet carries no
CGMacros participant at all.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devvalidation import Candidate, participant_holdout_folds, rolling_folds
from research.batch_gh.pretrain import metabonet_windows
from research.biometa.common import fit_blend, row, score_all
from research.transformer.data import (
    MEMORY_DIM,
    MEMORY_SLOTS,
    build_memory,
    build_sequences,
    load_meals,
    standardize,
)
from research.transformer.models import (
    ForecastHead,
    Head,
    HistoryCrossAttention,
    MealConditionedTransformer,
    PatchTransformer,
    ReconstructionHead,
    TemporalTransformer,
    count_parameters,
)

RESULTS = pathlib.Path("research/transformer/results")
SEED = 20261011
PRE_WINDOW = 36
PRE_HORIZON = 6
N_STATIC = 39


# --------------------------------------------------------------------------- #
# Downstream wrappers. Memory rides inside `statics` so the shared train loop
# in research.neural.train_loop needs no changes.
# --------------------------------------------------------------------------- #
class Direct(nn.Module):
    """Transformer encoder + head, predicting the four deltas directly."""

    def __init__(self, encoder, kind: str, n_static: int = N_STATIC):
        super().__init__()
        self.encoder, self.kind = encoder, kind
        self.head = Head(encoder.out_dim, n_static)
        self.n_static = n_static

    def forward(self, sequences, statics):
        base = statics[:, : self.n_static]
        if self.kind == "conditioned":
            rep = self.encoder(sequences, base)
        else:
            rep = self.encoder(sequences, sequences[:, 1, :] > 0.5) \
                if self.kind == "temporal" else self.encoder(sequences)
        return self.head(rep, base)


class DirectMemory(nn.Module):
    """T8: cross-attention over the participant's earlier fully observed meals."""

    def __init__(self, n_static: int = N_STATIC):
        super().__init__()
        self.attn = HistoryCrossAttention(MEMORY_DIM, n_static)
        self.head = Head(self.attn.out_dim, n_static)
        self.n_static = n_static

    def forward(self, sequences, statics):
        base = statics[:, : self.n_static]
        flat = statics[:, self.n_static : self.n_static + MEMORY_SLOTS * MEMORY_DIM]
        mask = statics[:, self.n_static + MEMORY_SLOTS * MEMORY_DIM :] > 0.5
        memory = flat.reshape(-1, MEMORY_SLOTS, MEMORY_DIM)
        return self.head(self.attn(base, memory, mask), base)


# --------------------------------------------------------------------------- #
# Pretraining
# --------------------------------------------------------------------------- #
def cgmacros_pretrain_windows(stamps, values, participants, before=None,
                              stride: int = 6, limit: int = 40000) -> np.ndarray:
    """Raw 36-point glucose windows from the given participants only.

    `before` truncates to readings strictly earlier than that timestamp, which is
    what keeps within-person pretraining from seeing a validation target's future.
    """
    out = []
    for pid in participants:
        s, v = stamps.get(pid), values.get(pid)
        if s is None or len(v) < PRE_WINDOW + PRE_HORIZON:
            continue
        if before is not None:
            cut = int(np.searchsorted(np.asarray(list(s)), before.get(pid, s[-1])))
            v = v[:cut]
        for i in range(0, len(v) - PRE_WINDOW - PRE_HORIZON, stride):
            out.append(v[i : i + PRE_WINDOW + PRE_HORIZON])
            if len(out) >= limit:
                break
    return np.asarray(out, dtype="float32") if out else np.zeros((0, PRE_WINDOW + PRE_HORIZON),
                                                                 dtype="float32")


def to_channels(windows: np.ndarray, sensor: float) -> np.ndarray:
    """Pretraining inputs in the SAME 4-channel layout the episodes use, so a
    pretrained encoder transfers without any architectural surgery."""
    body = windows[:, :PRE_WINDOW]
    base = body[:, :1]
    x = np.zeros((body.shape[0], 4, PRE_WINDOW), dtype="float32")
    x[:, 0, :] = body - base
    x[:, 1, :] = 1.0                 # pretraining windows are complete by selection
    x[:, 2, :] = 0.0                 # therefore no elapsed-time gaps
    x[:, 3, :] = sensor              # 0 = CGMacros dexcom_g6_pro, 1 = MetaboNet
    return x


def pretrain_transformer(windows: np.ndarray, objective: str, sensor: float,
                         epochs: int = 6, batch: int = 256, lr: float = 1e-3,
                         seed: int = SEED):
    """Pretrain a TemporalTransformer. Returns (encoder, loss history, scale).

    objective: "forecast"  next 30 minutes (T5)
               "masked"    reconstruct masked positions (T4)
               "shuffled"  CONTROL -- forecast targets permuted across windows, so
                           the objective carries no information while architecture,
                           corpus and budget are held fixed.
    """
    from research.neural.train_loop import set_seed

    set_seed(seed)
    encoder = TemporalTransformer(4)
    if windows.shape[0] < 64:
        return encoder, [], 1.0

    x = to_channels(windows, sensor)
    scale = float(np.abs(x[:, 0, :]).std()) or 1.0
    x[:, 0, :] /= scale
    rng = np.random.default_rng(seed)

    if objective in ("forecast", "shuffled"):
        base = windows[:, :1]
        y = (windows[:, PRE_WINDOW:] - base) / scale
        if objective == "shuffled":
            y = y[rng.permutation(y.shape[0])]
        head = ForecastHead(encoder.out_dim, PRE_HORIZON)
        inputs = x
    else:
        y = x[:, 0, :].copy()
        inputs = x.copy()
        drop = rng.random(y.shape) < 0.25
        inputs[:, 0, :][drop] = 0.0
        inputs[:, 1, :][drop] = 0.0
        head = ReconstructionHead(encoder.out_dim, PRE_WINDOW)

    xt = torch.from_numpy(np.ascontiguousarray(inputs)).float()
    yt = torch.from_numpy(np.ascontiguousarray(y)).float()
    opt = torch.optim.Adam(list(encoder.parameters()) + list(head.parameters()),
                           lr=lr, weight_decay=1e-4)
    idx = np.arange(xt.shape[0])
    history = []
    for _ in range(epochs):
        encoder.train()
        rng.shuffle(idx)
        total, seen = 0.0, 0
        for s in range(0, idx.size, batch):
            b = idx[s : s + batch]
            opt.zero_grad()
            rep = encoder(xt[b], xt[b, 1, :] > 0.5)
            loss = nn.functional.l1_loss(head(rep), yt[b])
            if not torch.isfinite(loss):
                continue
            loss.backward()
            nn.utils.clip_grad_norm_(
                list(encoder.parameters()) + list(head.parameters()), 1.0)
            opt.step()
            total += float(loss) * len(b)
            seen += len(b)
        history.append(total / max(seen, 1))
    return encoder, history, scale


# --------------------------------------------------------------------------- #
# Candidate builders
# --------------------------------------------------------------------------- #
def _statics(matrices, index, mem, mm, medians):
    base = matrices.features.fillna(medians).to_numpy("float32")
    return np.concatenate(
        [base, mem[index].reshape(len(index), -1), mm[index].astype("float32")], axis=1)


def direct_candidate(name: str, factory, kind: str, index_of, seq, mem, mm,
                     pretrainer=None, notes: str = "", epochs: int = 60):
    """A Candidate that trains a Transformer end-to-end on the fold's episodes."""
    from research.neural.train_loop import fit

    stats: dict = {}

    def run(train, validate, config):
        tr_i = np.array([index_of[(p, m)] for p, m in
                         zip(train.participant_ids, train.meal_ids)])
        va_i = np.array([index_of[(p, m)] for p, m in
                         zip(validate.participant_ids, validate.meal_ids)])
        med = train.features.median(numeric_only=True)
        (seq_tr, seq_va), _ = standardize(seq[tr_i], seq[va_i])
        st_tr = _statics(train, tr_i, mem, mm, med)
        st_va = _statics(validate, va_i, mem, mm, med)
        tgt = np.column_stack([train.deltas[h].to_numpy("float64") for h in HORIZONS])
        msk = np.isfinite(tgt)

        encoder = None if pretrainer is None else pretrainer(train)
        model = factory(encoder)
        stats["params"] = count_parameters(model)
        result = fit(model, seq_tr, st_tr, tgt, msk, train.participant_ids,
                     max_epochs=epochs, patience=10, batch_size=64, lr=1e-3,
                     weight_decay=1e-4, seed=SEED)
        stats["epochs"] = getattr(result, "best_epoch", None)
        model.eval()
        with torch.no_grad():
            pred = model(torch.from_numpy(np.ascontiguousarray(seq_va)).float(),
                         torch.from_numpy(np.ascontiguousarray(st_va)).float()).numpy()
        return {h: pred[:, j].astype("float64") for j, h in enumerate(HORIZONS)}

    return Candidate(name, run, notes), stats


def frozen_candidate(name: str, index_of, seq, pretrainer, notes: str = ""):
    """A Candidate that appends a FROZEN Transformer embedding to fs-1.3."""
    stats: dict = {}

    def run(train, validate, config):
        tr_i = np.array([index_of[(p, m)] for p, m in
                         zip(train.participant_ids, train.meal_ids)])
        va_i = np.array([index_of[(p, m)] for p, m in
                         zip(validate.participant_ids, validate.meal_ids)])
        (seq_tr, seq_va), _ = standardize(seq[tr_i], seq[va_i])
        encoder = pretrainer(train)
        stats["params"] = count_parameters(encoder)
        encoder.eval()

        def rep(a):
            with torch.no_grad():
                t = torch.from_numpy(np.ascontiguousarray(a)).float()
                return encoder(t, t[:, 1, :] > 0.5).numpy().astype("float64")

        cols = [f"tr_{i}" for i in range(encoder.out_dim)]
        X_tr = pd.concat([train.features.reset_index(drop=True),
                          pd.DataFrame(rep(seq_tr), columns=cols)], axis=1)
        X_va = pd.concat([validate.features.reset_index(drop=True),
                          pd.DataFrame(rep(seq_va), columns=cols)], axis=1)
        blend, pers = fit_blend(X_tr, train, config)
        return pers.apply(validate.participant_ids, blend.predict_deltas(X_va))

    return Candidate(name, run, notes), stats


def fs13_reference() -> Candidate:
    def run(train, validate, config):
        blend, pers = fit_blend(train.features, train, config)
        return pers.apply(validate.participant_ids, blend.predict_deltas(validate.features))

    return Candidate("A fs-1.3 (reference)", run, "no sequence model")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--metabonet", default="data/Type1Diabetes.parquet")
    ap.add_argument("--epochs", type=int, default=60)
    args = ap.parse_args()

    from ml.experiments import load_dev_pool
    from research.batch_b.sequences import load_series

    dev, _ = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    stamps, values = load_series(args.db_url)
    seq, _, _, _ = build_sequences(dev, stamps, values)
    mem, mm = build_memory(dev, stamps, values, load_meals(args.db_url))
    index_of = {(e.participant_id, e.meal_id): i for i, e in enumerate(dev)}

    mb = metabonet_windows(args.metabonet, row_groups=4, limit=30000)
    print(f"MetaboNet pretraining corpus: {mb.shape}")

    def cg_pre(train, objective):
        """Fold-local CGMacros pretraining: training participants, truncated to
        readings strictly before each participant's earliest validation meal."""
        parts = np.unique(train.participant_ids)
        w = cgmacros_pretrain_windows(stamps, values, parts)
        enc, hist, _ = pretrain_transformer(w, objective, sensor=0.0)
        return enc, hist, w.shape[0]

    registry: list[tuple[Candidate, dict, str]] = []

    def add(pair, group):
        registry.append((pair[0], pair[1], group))

    # --- DIRECT, no pretraining: the architecture-matched controls ---------- #
    add(direct_candidate("T1 temporal transformer", lambda e: Direct(e or TemporalTransformer(4),
                                                                    "temporal"),
                         "temporal", index_of, seq, mem, mm,
                         notes="CTRL: identical arch, no pretraining", epochs=args.epochs),
        "direct")
    add(direct_candidate("T2 patch transformer (PatchTST-inspired)",
                         lambda e: Direct(e or PatchTransformer(4), "patch"),
                         "patch", index_of, seq, mem, mm,
                         notes="patch 6 stride 3", epochs=args.epochs), "direct")
    add(direct_candidate("T3 meal-conditioned transformer",
                         lambda e: Direct(e or MealConditionedTransformer(4, N_STATIC),
                                          "conditioned"),
                         "conditioned", index_of, seq, mem, mm,
                         notes="static context token prepended", epochs=args.epochs), "direct")
    add(direct_candidate("T8 historical-meal cross-attention",
                         lambda e: DirectMemory(), "memory", index_of, seq, mem, mm,
                         notes="only fully elapsed earlier meals", epochs=args.epochs), "direct")

    # --- DIRECT, pretrained: T1's architecture exactly, plus initial weights - #
    for label, objective, sensor, corpus in (
        ("T4 masked-CGM pretrained", "masked", 0.0, "cgmacros"),
        ("T5 forecast pretrained", "forecast", 0.0, "cgmacros"),
        ("T6 MetaboNet pretrained (transfer)", "forecast", 1.0, "metabonet"),
        ("CTRL shuffled-objective pretrained", "shuffled", 0.0, "cgmacros"),
    ):
        def make(objective=objective, sensor=sensor, corpus=corpus):
            def pretrainer(train):
                if corpus == "metabonet":
                    enc, _, _ = pretrain_transformer(mb, objective, sensor=sensor)
                    return enc
                return cg_pre(train, objective)[0]
            return pretrainer

        add(direct_candidate(label, lambda e: Direct(e, "temporal"), "temporal",
                             index_of, seq, mem, mm, pretrainer=make(),
                             notes=f"{corpus}/{objective}, then fine-tuned",
                             epochs=args.epochs), "direct")

    # --- FROZEN embeddings appended to fs-1.3 ------------------------------ #
    add(frozen_candidate("T7 frozen pretrained embeddings + fs-1.3", index_of, seq,
                         lambda train: cg_pre(train, "forecast")[0],
                         "forecast-pretrained, frozen"), "frozen")
    add(frozen_candidate("CTRL random frozen embeddings + fs-1.3", index_of, seq,
                         lambda train: TemporalTransformer(4),
                         "untrained weights, frozen"), "frozen")
    add(frozen_candidate("CTRL shuffled-pretrained frozen + fs-1.3", index_of, seq,
                         lambda train: cg_pre(train, "shuffled")[0],
                         "shuffled objective, frozen"), "frozen")

    candidates = [fs13_reference()] + [c for c, _, _ in registry]
    groups = {"A fs-1.3 (reference)": "reference"}
    groups.update({c.name: g for c, _, g in registry})
    param_stats = {c.name: s for c, s, _ in registry}

    print(f"Phase 4: {len(candidates)} configurations x 2 regimes on {len(dev)} episodes")

    rows: list[dict] = []
    for label, folds in (("within_person", rolling_folds(dev, DEFAULT_CONFIG, 3)),
                         ("cold_start", participant_holdout_folds(dev, DEFAULT_CONFIG, 4))):
        print(f"\n--- {label} ({len(folds)} folds) ---", flush=True)
        t0 = time.perf_counter()
        scored, _ = score_all(folds, candidates, DEFAULT_CONFIG, "A fs-1.3 (reference)", label)
        for r in scored:
            r["regime"] = label
            r["group"] = groups.get(r["model"], "")
            r["params"] = param_stats.get(r["model"], {}).get("params")
        rows.extend(scored)
        print(f"  regime wall clock {time.perf_counter()-t0:.0f}s")

    RESULTS.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "transformer_leaderboard.csv", index=False)
    print("\n=== PHASE 4 TRANSFORMER LEADERBOARD (delta>0 = better than fs-1.3) ===")
    for label in ("within_person", "cold_start"):
        sub = df[df.regime == label].sort_values("mae")
        print(f"\n{label}:")
        print(f"  {'model':44s}{'MAE':>8s}{'delta':>8s}{'bias':>8s}{'params':>8s}"
              f"{'worst ppt':>11s}{'excl0':>7s}")
        for _, r in sub.iterrows():
            d = "  ref" if pd.isna(r.get("delta")) else f"{r['delta']:+.3f}"
            p = "" if pd.isna(r.get("params")) else f"{int(r['params'])}"
            w = ("" if pd.isna(r.get("worst_participant_delta"))
                 else f"{r['worst_participant_delta']:+.2f}")
            ex = "" if pd.isna(r.get("delta")) else ("YES" if r["excludes_zero"] else "no")
            print(f"  {r['model']:44s}{r['mae']:8.3f}{d:>8s}{r['bias']:+8.2f}{p:>8s}{w:>11s}{ex:>7s}")
    (RESULTS / "transformer_meta.json").write_text(json.dumps(
        {"metabonet_windows": int(mb.shape[0]), "groups": groups,
         "params": {k: v.get("params") for k, v in param_stats.items()}}, indent=2) + "\n")
    print(f"\nwrote {RESULTS}/transformer_leaderboard.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
