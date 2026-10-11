"""Compact sequence architectures for Batches D, E and F.

Every model is deliberately small: ~900 training episodes from 45 participants is the
binding constraint, so parameter counts are reported alongside every result and the
architecture search is bounded.

All sequence processing is CAUSAL by construction. The input ends at the meal timestamp,
so there is no future to leak; causal padding is used anyway so that a model trained here
could be applied to a streaming series unchanged.
"""

from __future__ import annotations

import torch
import torch.nn as nn


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


class CausalConv1d(nn.Conv1d):
    """Conv1d with left-only padding, so output t depends on inputs <= t."""

    def __init__(self, in_ch, out_ch, kernel_size, dilation=1):
        self.pad = (kernel_size - 1) * dilation
        super().__init__(in_ch, out_ch, kernel_size, padding=self.pad, dilation=dilation)

    def forward(self, x):
        out = super().forward(x)
        return out[..., : -self.pad] if self.pad else out


class CausalCNN(nn.Module):
    """D1 -- two causal conv layers, then mean+last pooling."""

    def __init__(self, in_ch=3, channels=16, kernel_size=5, dropout=0.1):
        super().__init__()
        self.net = nn.Sequential(
            CausalConv1d(in_ch, channels, kernel_size), nn.ReLU(), nn.Dropout(dropout),
            CausalConv1d(channels, channels, kernel_size), nn.ReLU(), nn.Dropout(dropout),
        )
        self.out_dim = channels * 2

    def forward(self, x):
        h = self.net(x)
        return torch.cat([h.mean(dim=-1), h[..., -1]], dim=-1)


class DilatedTCN(nn.Module):
    """D2 -- dilated causal stack with residual connections."""

    def __init__(self, in_ch=3, channels=16, kernel_size=3, dilations=(1, 2, 4, 8), dropout=0.1):
        super().__init__()
        self.input = CausalConv1d(in_ch, channels, 1)
        blocks = []
        for d in dilations:
            blocks.append(nn.ModuleDict({
                "conv": CausalConv1d(channels, channels, kernel_size, dilation=d),
                "drop": nn.Dropout(dropout),
            }))
        self.blocks = nn.ModuleList(blocks)
        self.act = nn.ReLU()
        self.out_dim = channels * 2
        self.receptive_field = 1 + sum((kernel_size - 1) * d for d in dilations)

    def forward(self, x):
        h = self.input(x)
        for block in self.blocks:
            h = h + block["drop"](self.act(block["conv"](h)))
        return torch.cat([h.mean(dim=-1), h[..., -1]], dim=-1)


class GruEncoder(nn.Module):
    """E1/E2 -- single-layer GRU; the final hidden state is the representation.

    Hidden state is created fresh for every forward pass, so it NEVER carries over
    between episodes or participants.
    """

    def __init__(self, in_ch=3, hidden=16, dropout=0.0):
        super().__init__()
        self.rnn = nn.GRU(in_ch, hidden, batch_first=True)
        self.drop = nn.Dropout(dropout)
        self.out_dim = hidden

    def forward(self, x):
        _, h = self.rnn(x.transpose(1, 2))
        return self.drop(h[-1])


class LstmEncoder(nn.Module):
    """E6 -- matched LSTM control, to separate 'GRU' from 'recurrent'."""

    def __init__(self, in_ch=3, hidden=16, dropout=0.0):
        super().__init__()
        self.rnn = nn.LSTM(in_ch, hidden, batch_first=True)
        self.drop = nn.Dropout(dropout)
        self.out_dim = hidden

    def forward(self, x):
        _, (h, _) = self.rnn(x.transpose(1, 2))
        return self.drop(h[-1])


class GruD(nn.Module):
    """E3 -- decay-aware GRU in the GRU-D spirit.

    The input glucose channel decays toward the training mean as elapsed time since the
    last observation grows, and the hidden state is decayed by `exp(-relu(W*delta))`.
    An unobserved point is therefore treated as "no information, ageing", never as a
    real glucose value of zero.
    """

    def __init__(self, in_ch=3, hidden=16):
        super().__init__()
        self.hidden = hidden
        self.cell = nn.GRUCell(2, hidden)   # decayed value + mask
        self.gamma_x = nn.Linear(1, 1)
        self.gamma_h = nn.Linear(1, hidden)
        self.out_dim = hidden

    def forward(self, x):
        value, mask, delta = x[:, 0, :], x[:, 1, :], x[:, 2, :]
        batch, length = value.shape
        h = value.new_zeros(batch, self.hidden)
        last = value.new_zeros(batch)
        for t in range(length):
            d = delta[:, t : t + 1]
            decay_x = torch.exp(-torch.relu(self.gamma_x(d))).squeeze(-1)
            decay_h = torch.exp(-torch.relu(self.gamma_h(d)))
            m = mask[:, t]
            observed = value[:, t]
            imputed = decay_x * last           # decays toward 0 == the training mean
            fed = m * observed + (1.0 - m) * imputed
            last = torch.where(m > 0, observed, last)
            h = self.cell(torch.stack([fed, m], dim=-1), decay_h * h)
        return h


class Head(nn.Module):
    """Prediction head: sequence representation (+ optional static features) -> 4 horizons."""

    def __init__(self, seq_dim: int, static_dim: int = 0, hidden: int = 32, dropout=0.1):
        super().__init__()
        self.use_static = static_dim > 0
        in_dim = seq_dim + static_dim
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden), nn.ReLU(), nn.Dropout(dropout), nn.Linear(hidden, 4)
        )

    def forward(self, seq_repr, static=None):
        z = torch.cat([seq_repr, static], dim=-1) if self.use_static else seq_repr
        return self.net(z)


class SequenceModel(nn.Module):
    """Encoder + head, used both for direct prediction and as a representation source."""

    def __init__(self, encoder: nn.Module, static_dim: int = 0, head_hidden: int = 32):
        super().__init__()
        self.encoder = encoder
        self.head = Head(encoder.out_dim, static_dim, head_hidden)

    def forward(self, sequences, static=None):
        return self.head(self.encoder(sequences), static)

    def represent(self, sequences):
        with torch.no_grad():
            return self.encoder(sequences)
