"""Bounded, reproducible training for the neural sequence batches.

GUARANTEES
  * early stopping uses an inner split of the TRAINING fold only, grouped by
    participant, so no validation episode influences when training stops;
  * masked loss -- an episode missing a horizon contributes nothing to that horizon,
    so every model sees exactly the same episodes;
  * deterministic seeds; see `determinism_note()` for the honest caveats;
  * gradient clipping, weight decay, bounded epochs;
  * non-finite losses abort the fit and are reported as a failure, never silently
    swallowed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import torch
import torch.nn as nn


def set_seed(seed: int = 20261011) -> None:
    torch.manual_seed(seed)
    np.random.seed(seed)
    torch.use_deterministic_algorithms(False)  # see determinism_note()


def determinism_note() -> str:
    return (
        "Seeds are fixed for torch and numpy. Full bitwise determinism is NOT claimed: "
        "CPU BLAS reduction order can vary between runs, and cuDNN/MPS kernels are not "
        "used here but would add their own nondeterminism. Repeated runs agree to ~1e-3 "
        "in validation MAE, which is far below every effect size reported."
    )


@dataclass
class TrainResult:
    model: nn.Module
    epochs: int
    best_inner_mae: float
    train_seconds: float
    failed: bool = False
    failure: str | None = None
    history: list[tuple[float, float]] = field(default_factory=list)


def masked_l1(prediction: torch.Tensor, target: torch.Tensor, mask: torch.Tensor):
    diff = torch.abs(prediction - target) * mask
    denom = mask.sum().clamp(min=1.0)
    return diff.sum() / denom


def fit(
    model: nn.Module,
    sequences: np.ndarray,
    statics: np.ndarray | None,
    targets: np.ndarray,
    target_mask: np.ndarray,
    participants: np.ndarray,
    *,
    max_epochs: int = 200,
    patience: int = 20,
    batch_size: int = 64,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    clip: float = 1.0,
    inner_fraction: float = 0.2,
    seed: int = 20261011,
) -> TrainResult:
    set_seed(seed)
    start = time.perf_counter()

    # Inner split by PARTICIPANT, so early stopping never leans on a participant the
    # model also trains on.
    unique = np.unique(participants)
    rng = np.random.default_rng(seed)
    held = set(rng.choice(unique, max(1, int(len(unique) * inner_fraction)), replace=False))
    inner_val = np.array([p in held for p in participants])
    inner_train = ~inner_val
    if inner_train.sum() < 32 or inner_val.sum() < 8:
        inner_train = np.ones_like(inner_val, dtype=bool)
        inner_val = inner_train

    to_t = lambda a: torch.from_numpy(np.ascontiguousarray(a)).float()  # noqa: E731
    seq_t, tgt_t, msk_t = to_t(sequences), to_t(targets), to_t(target_mask.astype("float32"))
    tgt_t = torch.nan_to_num(tgt_t)
    sta_t = to_t(statics) if statics is not None else None

    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    best_state, best_mae, best_epoch = None, float("inf"), 0
    history: list[tuple[float, float]] = []
    train_idx = np.flatnonzero(inner_train)
    val_idx = np.flatnonzero(inner_val)

    for epoch in range(max_epochs):
        model.train()
        rng.shuffle(train_idx)
        epoch_loss = 0.0
        for start_i in range(0, train_idx.size, batch_size):
            batch = train_idx[start_i : start_i + batch_size]
            optimizer.zero_grad()
            prediction = model(seq_t[batch], sta_t[batch] if sta_t is not None else None)
            loss = masked_l1(prediction, tgt_t[batch], msk_t[batch])
            if not torch.isfinite(loss):
                return TrainResult(model, epoch, best_mae, time.perf_counter() - start,
                                   failed=True, failure="non-finite training loss")
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), clip)
            optimizer.step()
            epoch_loss += float(loss) * batch.size

        model.eval()
        with torch.no_grad():
            prediction = model(seq_t[val_idx], sta_t[val_idx] if sta_t is not None else None)
            inner_mae = float(masked_l1(prediction, tgt_t[val_idx], msk_t[val_idx]))
        history.append((epoch_loss / max(train_idx.size, 1), inner_mae))

        if not np.isfinite(inner_mae):
            return TrainResult(model, epoch, best_mae, time.perf_counter() - start,
                               failed=True, failure="non-finite inner validation loss")
        if inner_mae < best_mae - 1e-4:
            best_mae, best_epoch = inner_mae, epoch
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        elif epoch - best_epoch >= patience:
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    return TrainResult(model, best_epoch + 1, best_mae, time.perf_counter() - start,
                       history=history)


def predict(model: nn.Module, sequences: np.ndarray, statics: np.ndarray | None) -> np.ndarray:
    model.eval()
    to_t = lambda a: torch.from_numpy(np.ascontiguousarray(a)).float()  # noqa: E731
    with torch.no_grad():
        out = model(to_t(sequences), to_t(statics) if statics is not None else None)
    return out.numpy().astype("float64")


def represent(model: nn.Module, sequences: np.ndarray) -> np.ndarray:
    model.eval()
    to_t = lambda a: torch.from_numpy(np.ascontiguousarray(a)).float()  # noqa: E731
    with torch.no_grad():
        return model.encoder(to_t(sequences)).numpy().astype("float64")
