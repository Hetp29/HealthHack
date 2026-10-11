"""Compact, regularised Transformer encoders for pre-meal CGM.

WHY THESE ARE SMALL
-------------------
~900 training episodes per cold-start fold. A Transformer sized the way the
forecasting literature sizes them (d_model 128+, 6+ layers) has more parameters
than the dataset has target values, so it would be a study of how well Adam can
memorise 900 numbers. Every encoder here is d_model 32 or less, 1-2 layers, with
dropout, weight decay and gradient clipping, and the parameter count is reported
with every result so the reader can judge the ratio.

CAUSALITY
---------
Every input position lies at or before the meal timestamp, so the inputs are
already strictly causal with respect to the prediction. On top of that the
self-attention mask is lower-triangular everywhere, which is what makes the
next-step forecasting pretraining objective valid: no position may attend to a
later one, in pretraining or in fine-tuning.

A NOTE ON THE WORD "FOUNDATION MODEL"
-------------------------------------
These are small supervised or self-supervised sequence encoders that happen to
use attention. Nothing here is a foundation model and nothing here is described
as one.
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn


class PositionalEncoding(nn.Module):
    """Fixed sinusoidal positions. Learned embeddings would add parameters that
    ~900 episodes cannot pay for."""

    def __init__(self, d_model: int, max_len: int = 512):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(max_len).unsqueeze(1).float()
        div = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div[: pe[:, 1::2].shape[1]])
        self.register_buffer("pe", pe.unsqueeze(0))

    def forward(self, x):
        return x + self.pe[:, : x.shape[1]]


def _causal_mask(length: int, device) -> torch.Tensor:
    return torch.triu(torch.ones(length, length, device=device, dtype=torch.bool), diagonal=1)


class TemporalTransformer(nn.Module):
    """T1: per-timestep embedding + causal self-attention + masked mean pooling."""

    def __init__(self, in_channels: int, d_model: int = 32, nhead: int = 4,
                 layers: int = 2, ff: int = 64, dropout: float = 0.1):
        super().__init__()
        self.proj = nn.Linear(in_channels, d_model)
        self.pos = PositionalEncoding(d_model)
        layer = nn.TransformerEncoderLayer(
            d_model, nhead, dim_feedforward=ff, dropout=dropout,
            batch_first=True, norm_first=True, activation="gelu")
        self.encoder = nn.TransformerEncoder(layer, layers)
        self.out_dim = d_model

    def forward(self, x, key_mask=None):
        # x: [B, C, L] -> [B, L, C]
        h = self.pos(self.proj(x.transpose(1, 2)))
        h = self.encoder(h, mask=_causal_mask(h.shape[1], h.device))
        if key_mask is None:
            return h.mean(dim=1)
        w = key_mask.float().unsqueeze(-1)
        return (h * w).sum(1) / w.sum(1).clamp(min=1.0)


class PatchTransformer(nn.Module):
    """T2: PatchTST-inspired. Non-overlapping-ish patches are embedded as tokens,
    which shortens the sequence and lets a tiny model see 30-minute structure.

    This is PatchTST's patching and channel-independence idea at a size this
    dataset can support -- it is not PatchTST, and no pretrained PatchTST weights
    are involved."""

    def __init__(self, in_channels: int, patch: int = 6, stride: int = 3,
                 d_model: int = 32, nhead: int = 4, layers: int = 2, ff: int = 64,
                 dropout: float = 0.1):
        super().__init__()
        self.patch, self.stride = patch, stride
        self.proj = nn.Linear(in_channels * patch, d_model)
        self.pos = PositionalEncoding(d_model)
        layer = nn.TransformerEncoderLayer(
            d_model, nhead, dim_feedforward=ff, dropout=dropout,
            batch_first=True, norm_first=True, activation="gelu")
        self.encoder = nn.TransformerEncoder(layer, layers)
        self.out_dim = d_model

    def forward(self, x, key_mask=None):
        B, C, L = x.shape
        patches = x.unfold(-1, self.patch, self.stride)          # [B, C, P, patch]
        P = patches.shape[2]
        tokens = patches.permute(0, 2, 1, 3).reshape(B, P, C * self.patch)
        h = self.pos(self.proj(tokens))
        h = self.encoder(h, mask=_causal_mask(P, h.device))
        return h.mean(dim=1)


class MealConditionedTransformer(nn.Module):
    """T3: the meal/static context is projected to a token and PREPENDED, so
    attention can condition the sequence read on what was eaten.

    The context token sits at position 0 and the mask is causal, so every
    sequence position may attend to it and it may not attend to any of them."""

    def __init__(self, in_channels: int, n_static: int, d_model: int = 32,
                 nhead: int = 4, layers: int = 2, ff: int = 64, dropout: float = 0.1):
        super().__init__()
        self.proj = nn.Linear(in_channels, d_model)
        self.ctx = nn.Sequential(nn.Linear(n_static, d_model), nn.GELU(),
                                 nn.Linear(d_model, d_model))
        self.pos = PositionalEncoding(d_model)
        layer = nn.TransformerEncoderLayer(
            d_model, nhead, dim_feedforward=ff, dropout=dropout,
            batch_first=True, norm_first=True, activation="gelu")
        self.encoder = nn.TransformerEncoder(layer, layers)
        self.out_dim = d_model

    def forward(self, x, statics):
        h = self.proj(x.transpose(1, 2))
        h = torch.cat([self.ctx(statics).unsqueeze(1), h], dim=1)
        h = self.pos(h)
        h = self.encoder(h, mask=_causal_mask(h.shape[1], h.device))
        return h[:, 1:].mean(dim=1)


class HistoryCrossAttention(nn.Module):
    """T8: cross-attention from the current meal onto the participant's own earlier
    FULLY OBSERVED meals.

    Keys and values are prior response curves selected by the same causal filter
    fs-1.3 uses: the earlier meal's whole 120-minute window must have elapsed
    before the current meal timestamp. Padded slots are masked out of attention,
    so a participant with no admissible history attends to nothing and falls back
    on the learned bias.
    """

    def __init__(self, curve_dim: int, n_static: int, d_model: int = 32,
                 nhead: int = 4, dropout: float = 0.1):
        super().__init__()
        self.q = nn.Sequential(nn.Linear(n_static, d_model), nn.GELU(),
                               nn.Linear(d_model, d_model))
        self.kv = nn.Linear(curve_dim, d_model)
        self.attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout, batch_first=True)
        self.norm = nn.LayerNorm(d_model)
        self.empty = nn.Parameter(torch.zeros(d_model))
        self.out_dim = d_model

    def forward(self, statics, memory, memory_mask):
        q = self.q(statics).unsqueeze(1)
        kv = self.kv(memory)
        none_valid = ~memory_mask.any(dim=1)
        safe = memory_mask.clone()
        safe[none_valid, 0] = True          # avoid an all-masked softmax (NaN)
        out, _ = self.attn(q, kv, kv, key_padding_mask=~safe)
        out = self.norm(out.squeeze(1))
        return torch.where(none_valid.unsqueeze(-1), self.empty.expand_as(out), out)


class Head(nn.Module):
    """Shared prediction head: representation + statics -> four horizon deltas."""

    def __init__(self, rep_dim: int, n_static: int, hidden: int = 32, dropout: float = 0.1):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(rep_dim + n_static, hidden), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden, 4))

    def forward(self, rep, statics):
        return self.net(torch.cat([rep, statics], dim=-1))


class ForecastHead(nn.Module):
    """Pretraining head: next-30-minutes trajectory from the pooled representation."""

    def __init__(self, rep_dim: int, horizon: int = 6):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(rep_dim, 32), nn.GELU(), nn.Linear(32, horizon))

    def forward(self, rep):
        return self.net(rep)


class ReconstructionHead(nn.Module):
    """Pretraining head: reconstruct the masked positions of the input window."""

    def __init__(self, rep_dim: int, length: int):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(rep_dim, 64), nn.GELU(), nn.Linear(64, length))

    def forward(self, rep):
        return self.net(rep)


def count_parameters(module: nn.Module) -> int:
    return sum(p.numel() for p in module.parameters() if p.requires_grad)
