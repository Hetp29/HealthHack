"""Tensor construction for the neural sequence batches.

INPUT CHANNELS (all strictly pre-meal)
--------------------------------------
Each episode contributes a length-37 sequence on a 5-minute lattice ending AT the meal
timestamp, with three channels:

  0  glucose delta from the pre-meal baseline, mg/dL (0.0 where unobserved)
  1  observation mask, 1 = a native reading fell within half a cadence of that point
  2  elapsed time since the most recent OBSERVED reading at or before that point, in
     minutes -- the quantity a decay-aware recurrent model needs

Channel 0 is zero-filled ONLY where channel 1 says the point is missing, so no model is
ever told a gap is a real glucose value: the mask and the elapsed-time channel carry the
missingness explicitly. This is the GRU-D convention.

Static features are the 39 fs-1.3 production columns, median-imputed using
TRAINING-FOLD medians only.

Targets are the four horizon deltas. Episodes missing a horizon are masked out of that
horizon's loss rather than dropped, so every model sees the same episodes.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from ml.config import HORIZONS

CADENCE = 5


def build_tensors(episodes, stamps, values, window_minutes: int = 180):
    """(sequences [n, 3, L], statics-as-raw [n, F], targets [n, 4], target_mask [n, 4])."""
    lattice = np.arange(window_minutes // CADENCE, -1, -1) * CADENCE
    length = lattice.size
    n = len(episodes)

    sequences = np.zeros((n, 3, length), dtype="float32")
    targets = np.full((n, len(HORIZONS)), np.nan, dtype="float32")

    for i, episode in enumerate(episodes):
        series = stamps.get(episode.participant_id)
        t = episode.meal_timestamp
        baseline = episode.target.baseline_glucose_mg_dl
        if series is not None and series.size:
            readings = values[episode.participant_id]
            hi = int(np.searchsorted(series, t, side="right"))
            lo = int(np.searchsorted(series, t - dt.timedelta(minutes=window_minutes)))
            if hi > lo:
                offsets = np.asarray(
                    [(t - s).total_seconds() / 60.0 for s in series[lo:hi]], dtype="float64"
                )
                window_values = readings[lo:hi]
                last_seen = np.nan
                for j, target_offset in enumerate(lattice):
                    distance = np.abs(offsets - target_offset)
                    k = int(np.argmin(distance))
                    if distance[k] <= CADENCE / 2.0:
                        sequences[i, 0, j] = window_values[k] - baseline
                        sequences[i, 1, j] = 1.0
                        last_seen = target_offset
                    # elapsed time since the most recent observed lattice point
                    sequences[i, 2, j] = (
                        0.0 if np.isnan(last_seen) else float(last_seen - target_offset)
                    )
        for j, horizon in enumerate(HORIZONS):
            value = episode.target.delta.get(horizon)
            if value is not None:
                targets[i, j] = value

    mask = np.isfinite(targets)
    return sequences, targets, mask, lattice


def standardize_sequences(train_seq: np.ndarray, *others: np.ndarray):
    """Scale channel 0 and channel 2 by TRAINING-fold statistics only."""
    glucose = train_seq[:, 0, :]
    observed = train_seq[:, 1, :] > 0
    mean = float(glucose[observed].mean()) if observed.any() else 0.0
    std = float(glucose[observed].std()) if observed.any() else 1.0
    std = std if std > 1e-6 else 1.0
    elapsed_scale = float(max(train_seq[:, 2, :].max(), 1.0))

    def apply(array: np.ndarray) -> np.ndarray:
        out = array.copy()
        out[:, 0, :] = (out[:, 0, :] - mean) / std * out[:, 1, :]
        out[:, 2, :] = out[:, 2, :] / elapsed_scale
        return out

    return (apply(train_seq), *[apply(o) for o in others]), (mean, std, elapsed_scale)
