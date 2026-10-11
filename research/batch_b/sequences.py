"""Pre-meal CGM sequence extraction and non-neural representations (Gate B1).

THE QUESTION
------------
fs-1.3 already carries twelve glucose-dynamic features (pre-meal level, 10/15/30/60-min
slopes, acceleration, rise persistence, 60-min variability, 120-min range, deviation
from the participant's overnight floor). Gate B1 asks whether the raw pre-meal sequence
holds predictive information those summaries miss.

WINDOW DESIGN (Gate B0 evidence)
--------------------------------
`W = 180 minutes` = 36 native Dexcom readings. Chosen from the inventory:

| window | episodes >=90% complete | consecutive episodes whose windows overlap |
|---|---|---|
| 60 min | 99% | — |
| 120 min | 98% | 14% |
| **180 min** | **97%** | — |
| 360 min | 95% | **63%** |

180 minutes keeps near-complete coverage without the 63% window overlap a 6-hour window
creates. Overlapping windows would inflate the apparent independence of what are already
only 45 participants.

The sequence is resampled onto a fixed 5-minute lattice ending AT the meal timestamp and
expressed as a delta from the pre-meal baseline, so representations describe shape rather
than level (level is already a feature). Missing lattice points carry an explicit
observation mask; they are never silently zero-filled.

LEAKAGE
-------
Every sequence spans `[t - 180 min, t]` strictly. Any representation that must be FITTED
(PCA, kernel landmarks) is fitted on training-fold episodes only, inside the fold.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

WINDOW_MINUTES = 180
CADENCE = 5
N_LAGS = WINDOW_MINUTES // CADENCE  # 36

#: Offsets, in minutes before the meal: 180, 175, ..., 5, 0
LATTICE = np.arange(N_LAGS, -1, -1) * CADENCE


def extract_sequences(episodes, series: dict[str, np.ndarray], values: dict[str, np.ndarray]):
    """(sequences, masks) on the fixed lattice, as deltas from each meal's baseline.

    `series[pid]` is that participant's sorted native CGM timestamps; `values[pid]` the
    matching glucose readings. Only readings at or before the meal are ever consulted.
    """
    n = len(episodes)
    sequences = np.full((n, LATTICE.size), np.nan)
    masks = np.zeros((n, LATTICE.size))

    for i, episode in enumerate(episodes):
        stamps = series.get(episode.participant_id)
        if stamps is None or stamps.size == 0:
            continue
        readings = values[episode.participant_id]
        t = episode.meal_timestamp
        baseline = episode.target.baseline_glucose_mg_dl

        hi = int(np.searchsorted(stamps, t, side="right"))
        lo = int(np.searchsorted(stamps, t - dt.timedelta(minutes=WINDOW_MINUTES)))
        if hi <= lo:
            continue
        window_times = stamps[lo:hi]
        window_values = readings[lo:hi]
        offsets = np.asarray(
            [(t - s).total_seconds() / 60.0 for s in window_times], dtype="float64"
        )

        # Nearest native reading within half a cadence; otherwise the point is missing.
        for j, target_offset in enumerate(LATTICE):
            distance = np.abs(offsets - target_offset)
            k = int(np.argmin(distance))
            if distance[k] <= CADENCE / 2.0:
                sequences[i, j] = window_values[k] - baseline
                masks[i, j] = 1.0
    return sequences, masks


def load_series(db_url: str):
    """Native primary-sensor CGM per participant, sorted."""
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT participant_id, timestamp, glucose_mg_dl FROM cgm "
                 "ORDER BY participant_id, timestamp")
        ).all()
    stamps: dict[str, list] = {}
    values: dict[str, list] = {}
    for pid, ts, glucose in rows:
        stamps.setdefault(pid, []).append(
            dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)
        )
        values.setdefault(pid, []).append(float(glucose))
    return (
        {p: np.asarray(v, dtype=object) for p, v in stamps.items()},
        {p: np.asarray(v, dtype="float64") for p, v in values.items()},
    )


# --------------------------------------------------------------------------- #
# B1 representations
# --------------------------------------------------------------------------- #


def _filled(sequences: np.ndarray) -> np.ndarray:
    """Forward/back-filled copy for methods that cannot consume NaN.

    Filling is WITHIN one already-observed pre-meal window, never across the meal
    boundary, and the observation mask is kept as a separate feature so the model can
    tell a filled point from a measured one.
    """
    out = sequences.copy()
    for i in range(out.shape[0]):
        row = out[i]
        if np.isnan(row).all():
            row[:] = 0.0
            continue
        index = np.arange(row.size)
        good = ~np.isnan(row)
        row[:] = np.interp(index, index[good], row[good])
    return out


class RawLags:
    """B1a -- the sequence itself as 36 lag columns plus a coverage summary."""

    name = "B1a raw lags"

    def fit(self, sequences, masks):
        return self

    def transform(self, sequences, masks):
        return np.column_stack([sequences, masks.mean(axis=1)])

    def columns(self):
        return [f"seq_lag_{int(m)}" for m in LATTICE] + ["seq_coverage"]


class SequencePca:
    """B1b -- principal components of training-fold pre-meal sequences."""

    def __init__(self, n_components: int = 6):
        self.n_components = n_components
        self.model = None
        self.name = f"B1b sequence PCA-{n_components}"

    def fit(self, sequences, masks):
        from sklearn.decomposition import PCA

        usable = masks.mean(axis=1) >= 0.8
        if usable.sum() < 40:
            return self
        self.model = PCA(
            n_components=min(self.n_components, int(usable.sum()) - 1),
            random_state=20261011,
        ).fit(_filled(sequences[usable]))
        return self

    def transform(self, sequences, masks):
        if self.model is None:
            return np.full((sequences.shape[0], self.n_components), np.nan)
        return self.model.transform(_filled(sequences))

    def columns(self):
        n = self.n_components if self.model is None else self.model.n_components_
        return [f"seq_pc_{i}" for i in range(n)]


class SpectralFeatures:
    """B1c -- low-frequency magnitudes of the pre-meal window.

    The lattice is a uniform 5-minute grid of 37 points, so an rFFT is well defined.
    Only the lowest bins are kept: at this length the high bins are sensor noise, and
    the physiological content (slow drift, one or two oscillations over three hours)
    lives in the first few.
    """

    def __init__(self, n_bins: int = 6):
        self.n_bins = n_bins
        self.name = f"B1c spectral-{n_bins}"

    def fit(self, sequences, masks):
        return self

    def transform(self, sequences, masks):
        filled = _filled(sequences)
        spectrum = np.abs(np.fft.rfft(filled - filled.mean(axis=1, keepdims=True), axis=1))
        return spectrum[:, : self.n_bins]

    def columns(self):
        return [f"seq_fft_{i}" for i in range(self.n_bins)]


class SequenceKernel:
    """B1d -- regularized sequence-kernel similarity via RBF landmarks (Nystroem-style).

    Landmarks are training-fold sequences selected by k-means, so each feature is "how
    similar is this pre-meal shape to training shape L". Fitted inside the fold.
    """

    def __init__(self, n_landmarks: int = 12):
        self.n_landmarks = n_landmarks
        self.landmarks = None
        self.gamma = None
        self.name = f"B1d sequence kernel-{n_landmarks}"

    def fit(self, sequences, masks):
        from sklearn.cluster import KMeans

        usable = masks.mean(axis=1) >= 0.8
        if usable.sum() < self.n_landmarks * 3:
            return self
        filled = _filled(sequences[usable])
        self.landmarks = KMeans(
            n_clusters=self.n_landmarks, n_init=4, random_state=20261011
        ).fit(filled).cluster_centers_
        # Median heuristic bandwidth, computed on the training fold only.
        sample = filled[:: max(1, filled.shape[0] // 200)]
        distances = np.linalg.norm(sample[:, None, :] - sample[None, :, :], axis=2)
        median = np.median(distances[distances > 0]) if (distances > 0).any() else 1.0
        self.gamma = 1.0 / max(median**2, 1e-6)
        return self

    def transform(self, sequences, masks):
        if self.landmarks is None:
            return np.full((sequences.shape[0], self.n_landmarks), np.nan)
        filled = _filled(sequences)
        distances = np.linalg.norm(
            filled[:, None, :] - self.landmarks[None, :, :], axis=2
        )
        return np.exp(-self.gamma * distances**2)

    def columns(self):
        return [f"seq_kern_{i}" for i in range(self.n_landmarks)]


class ShapeDescriptors:
    """B1e -- compact hand-crafted descriptors of the pre-meal trajectory.

    Deliberately chosen to describe structure the fs-1.3 summaries cannot: WHERE in the
    window the extremes sat, how many direction reversals occurred, how long the current
    monotone run has lasted, and the signed area above and below baseline.
    """

    name = "B1e shape descriptors"

    def fit(self, sequences, masks):
        return self

    def transform(self, sequences, masks):
        filled = _filled(sequences)
        n = filled.shape[0]
        out = np.zeros((n, 9))
        for i in range(n):
            row = filled[i]
            diffs = np.diff(row)
            signs = np.sign(diffs)
            reversals = int((np.diff(signs[signs != 0]) != 0).sum()) if (signs != 0).any() else 0
            run = 0
            if signs.size:
                last = signs[-1]
                for value in signs[::-1]:
                    if value == last and value != 0:
                        run += 1
                    else:
                        break
            above = np.clip(row, 0, None).sum() * CADENCE
            below = np.clip(-row, 0, None).sum() * CADENCE
            out[i] = [
                float(LATTICE[int(np.argmax(row))]),       # minutes-ago of the maximum
                float(LATTICE[int(np.argmin(row))]),       # minutes-ago of the minimum
                float(row.max() - row.min()),              # window range
                float(reversals),                          # direction reversals
                float(run * CADENCE),                      # current monotone run, minutes
                float(above),                              # area above baseline
                float(below),                              # area below baseline
                float(np.abs(diffs).mean()),               # mean absolute step
                float(masks[i].mean()),                    # observed coverage
            ]
        return out

    def columns(self):
        return [
            "seq_argmax_min", "seq_argmin_min", "seq_range", "seq_reversals",
            "seq_monotone_run", "seq_area_above", "seq_area_below",
            "seq_mean_step", "seq_coverage_d",
        ]
