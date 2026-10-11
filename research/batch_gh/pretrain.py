"""Self-supervised CGM pretraining corpora and objectives (Batches G and H).

CORPORA
  cgmacros  -- only the TRAINING participants of the current cold-start fold. Validation
               participants are entirely excluded, which is what makes this the inductive
               setting rather than a transductive one.
  metabonet -- a separate dataset (type 1 diabetes, 15 source studies). No CGMacros
               participant appears in it, so it carries no leakage risk at all.
  combined  -- both, with a source-indicator channel so the encoder is not forced to
               treat two different populations as identically distributed.

OBJECTIVES
  forecast     (G1) -- predict the next 30 minutes (6 points) from a 180-minute window.
  masked       (G2) -- reconstruct a contiguous masked span inside the window.
  shuffled     (G6 control) -- the forecast objective with TARGETS SHUFFLED across
                 windows, so the architecture and training budget are identical but the
                 objective carries no information. If "pretraining helped" survives this
                 control, the help was real; if the control matches, it was the training
                 budget or the architecture, not the objective.

LEAKAGE
  Every pretraining window lies strictly inside the permitted corpus. For CGMacros that
  means training-fold participants only. Masked-span targets come from inside the same
  permitted window, never from beyond it.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import torch
import torch.nn as nn

WINDOW = 36          # 180 minutes of 5-minute points
FORECAST_HORIZON = 6  # next 30 minutes


def cgmacros_windows(stamps, values, participants: set[str], stride: int = 6, limit: int = 60000):
    """Sliding pre-meal-style windows from the permitted CGMacros participants."""
    out = []
    for pid in sorted(participants):
        series = values.get(pid)
        if series is None or series.size < WINDOW + FORECAST_HORIZON:
            continue
        for start in range(0, series.size - WINDOW - FORECAST_HORIZON, stride):
            out.append(series[start : start + WINDOW + FORECAST_HORIZON])
            if len(out) >= limit:
                return np.asarray(out, dtype="float32")
    return np.asarray(out, dtype="float32")


def metabonet_windows(path: str, row_groups: int = 6, stride: int = 24, limit: int = 60000):
    """Windows streamed from a bounded number of MetaboNet row groups.

    Bounded deliberately: the full file is 132.8M rows and this machine has 8 GB. Row
    groups are read one at a time and only the CGM column is materialised.
    """
    import pandas as pd
    import pyarrow.parquet as pq

    handle = pq.ParquetFile(path)
    out = []
    for g in range(min(row_groups, handle.metadata.num_row_groups)):
        frame = handle.read_row_group(g, columns=["id", "source_file", "date", "CGM"]).to_pandas()
        frame = frame.dropna(subset=["CGM", "date"])
        for (_, _), group in frame.groupby(["source_file", "id"], sort=False):
            series = group.sort_values("date")["CGM"].to_numpy("float32")
            if series.size < WINDOW + FORECAST_HORIZON:
                continue
            for start in range(0, series.size - WINDOW - FORECAST_HORIZON, stride):
                out.append(series[start : start + WINDOW + FORECAST_HORIZON])
                if len(out) >= limit:
                    return np.asarray(out, dtype="float32")
    return np.asarray(out, dtype="float32")


def to_training_arrays(windows: np.ndarray, objective: str, seed: int = 20261011):
    """(inputs [n,1,WINDOW], targets) for the chosen objective, baseline-centred."""
    rng = np.random.default_rng(seed)
    history = windows[:, :WINDOW]
    baseline = history[:, -1:]
    centred = history - baseline

    if objective in ("forecast", "shuffled"):
        future = windows[:, WINDOW:] - baseline
        if objective == "shuffled":
            future = future[rng.permutation(future.shape[0])]
        return centred[:, None, :], future

    if objective == "masked":
        inputs = centred.copy()
        targets = np.zeros((centred.shape[0], FORECAST_HORIZON), dtype="float32")
        for i in range(centred.shape[0]):
            start = int(rng.integers(0, WINDOW - FORECAST_HORIZON))
            targets[i] = centred[i, start : start + FORECAST_HORIZON]
            inputs[i, start : start + FORECAST_HORIZON] = 0.0
        return inputs[:, None, :], targets

    raise ValueError(objective)


class PretrainEncoder(nn.Module):
    """GRU-16 encoder shared by pretraining and downstream use."""

    def __init__(self, hidden: int = 16, in_ch: int = 1):
        super().__init__()
        self.rnn = nn.GRU(in_ch, hidden, batch_first=True)
        self.out_dim = hidden

    def forward(self, x):
        _, h = self.rnn(x.transpose(1, 2))
        return h[-1]


class PretrainModel(nn.Module):
    def __init__(self, encoder: PretrainEncoder, out_dim: int = FORECAST_HORIZON):
        super().__init__()
        self.encoder = encoder
        self.head = nn.Linear(encoder.out_dim, out_dim)

    def forward(self, x):
        return self.head(self.encoder(x))


def pretrain(windows: np.ndarray, objective: str, epochs: int = 8, hidden: int = 16,
             batch: int = 256, lr: float = 1e-3, seed: int = 20261011):
    """Train the encoder on the self-supervised objective. Returns (encoder, history)."""
    torch.manual_seed(seed)
    inputs, targets = to_training_arrays(windows, objective, seed)
    scale = float(np.std(inputs)) or 1.0
    inputs = inputs / scale
    x = torch.from_numpy(inputs).float()
    y = torch.from_numpy(targets).float() / scale

    model = PretrainModel(PretrainEncoder(hidden))
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    rng = np.random.default_rng(seed)
    idx = np.arange(x.shape[0])
    history = []
    for _ in range(epochs):
        model.train()
        rng.shuffle(idx)
        total = 0.0
        for s in range(0, idx.size, batch):
            b = idx[s : s + batch]
            opt.zero_grad()
            loss = torch.abs(model(x[b]) - y[b]).mean()
            if not torch.isfinite(loss):
                continue
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += float(loss) * b.size
        history.append(total / max(idx.size, 1))
    return model.encoder, history, scale
