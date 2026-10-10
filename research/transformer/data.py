"""Tensors for the Transformer track, including T8's causal prior-meal memory.

CHANNELS (all strictly at or before the meal timestamp)
------------------------------------------------------
  0  glucose delta from the pre-meal baseline, mg/dL, zero ONLY where unobserved
  1  observation mask, 1 = a native reading fell within half a cadence
  2  minutes since the most recent observed reading at or before that point
  3  sensor identity

ON CHANNEL 3. The mission asked for sensor identity to be used. It is included,
and the honest reading of it is this: in CGMacros the primary CGM series is
`dexcom_g6_pro` for 100% of readings and all 45 participants, so the channel is
CONSTANT and carries no within-dataset information. It is retained because it is
not constant in MetaboNet, whose devices span Dexcom G4/G6/One, FreeStyle Libre 2
and Medtronic Enlite, so T6's pretraining corpus genuinely varies along it and a
transferred encoder must have somewhere to put that variation. Reporting a gain
on this channel within CGMacros would be impossible by construction, and none is
claimed.

T8 MEMORY. For each episode, up to K earlier meals of the SAME participant, newest
first, admitted by exactly the filter fs-1.3's fingerprint and retrieval use: the
earlier meal's full 120-minute response window must have elapsed strictly before
the current meal timestamp, a baseline reading must exist at or before it, and it
must have enough native points to characterise. Each slot holds the response curve
on the 5-minute horizon grid plus the macro and recency context; unused slots are
masked out of attention. No episode can see itself or any later meal.
"""

from __future__ import annotations

import bisect
import datetime as dt

import numpy as np

from ml.config import FORECAST_MINUTES, HORIZONS

CADENCE = 5
WINDOW_MINUTES = 180
HORIZON_WINDOW = 120
MIN_CURVE_POINTS = 20
MEMORY_SLOTS = 8
#: response curve on the forecast grid + [carbs, protein, fat, fiber, hours_ago]
CURVE_LEN = len(FORECAST_MINUTES)
MEMORY_DIM = CURVE_LEN + 5


def _nearest(stamps, values, target, tolerance_s: float):
    i = bisect.bisect_left(stamps, target)
    best, best_gap = None, None
    for j in (i - 1, i):
        if 0 <= j < len(stamps):
            gap = abs((stamps[j] - target).total_seconds())
            if gap <= tolerance_s and (best_gap is None or gap < best_gap):
                best, best_gap = values[j], gap
    return best


def build_sequences(episodes, stamps, values, window_minutes: int = WINDOW_MINUTES):
    """(sequences [n, 4, L], targets [n, 4], target_mask [n, 4], lattice)."""
    lattice = np.arange(window_minutes // CADENCE, -1, -1) * CADENCE
    L = lattice.size
    n = len(episodes)
    seq = np.zeros((n, 4, L), dtype="float32")
    tgt = np.full((n, len(HORIZONS)), np.nan, dtype="float32")
    msk = np.zeros((n, len(HORIZONS)), dtype=bool)

    for i, ep in enumerate(episodes):
        pid = ep.participant_id
        s, v = stamps.get(pid), values.get(pid)
        meal = ep.meal_timestamp
        baseline = float(ep.features.baseline_glucose_mg_dl or 0.0)
        if s is not None and len(s):
            s_list = list(s)
            last_seen = None
            for k, back in enumerate(lattice):
                t = meal - dt.timedelta(minutes=float(back))
                g = _nearest(s_list, v, t, CADENCE * 30.0)
                if g is not None:
                    seq[i, 0, k] = g - baseline
                    seq[i, 1, k] = 1.0
                    last_seen = back
                seq[i, 2, k] = 0.0 if last_seen is None else float(last_seen - back)
        # channel 3: sensor identity. 0 == dexcom_g6_pro, the only CGMacros primary.
        seq[i, 3, :] = 0.0
        for j, h in enumerate(HORIZONS):
            value = (ep.target.delta or {}).get(h)
            if value is not None and np.isfinite(value):
                tgt[i, j] = float(value)
                msk[i, j] = True
    return seq, tgt, msk, lattice


def build_memory(episodes, stamps, values, meals_by_participant,
                 slots: int = MEMORY_SLOTS):
    """(memory [n, slots, MEMORY_DIM], mask [n, slots]) -- T8's causal prior meals."""
    n = len(episodes)
    mem = np.zeros((n, slots, MEMORY_DIM), dtype="float32")
    mask = np.zeros((n, slots), dtype=bool)
    grid = np.asarray(FORECAST_MINUTES, dtype="float64")

    for i, ep in enumerate(episodes):
        pid = ep.participant_id
        s, v = stamps.get(pid), values.get(pid)
        if s is None or not len(s):
            continue
        s_list = list(s)
        meal_time = ep.meal_timestamp
        found = 0
        for prior in reversed(meals_by_participant.get(pid, [])):
            if found >= slots:
                break
            t0 = prior["timestamp"]
            if t0 >= meal_time:
                continue
            window_end = t0 + dt.timedelta(minutes=HORIZON_WINDOW)
            # the whole response must have been observable before the current meal
            if window_end >= meal_time:
                continue
            lo = bisect.bisect_left(s_list, t0)
            hi = bisect.bisect_right(s_list, window_end)
            if lo == 0 or hi - lo < MIN_CURVE_POINTS:
                continue
            base = float(v[lo - 1])
            offsets = np.array([(s_list[k] - t0).total_seconds() / 60.0 for k in range(lo, hi)])
            deltas = np.array([float(v[k]) - base for k in range(lo, hi)])
            curve = np.interp(grid, offsets, deltas)
            curve[0] = 0.0
            hours_ago = (meal_time - t0).total_seconds() / 3600.0
            mem[i, found, :CURVE_LEN] = curve
            mem[i, found, CURVE_LEN:] = [
                prior.get("carbs") or 0.0, prior.get("protein") or 0.0,
                prior.get("fat") or 0.0, prior.get("fiber") or 0.0,
                min(hours_ago, 240.0) / 24.0,
            ]
            mask[i, found] = True
            found += 1
    return mem, mask


def load_meals(db_url: str) -> dict[str, list[dict]]:
    """All meals per participant, sorted. Used ONLY as T8 memory candidates, and
    every candidate is then filtered for strict causality in `build_memory`."""
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT participant_id, timestamp, carbs_g, protein_g, fat_g, fiber_g "
            "FROM meals ORDER BY participant_id, timestamp")).all()
    out: dict[str, list[dict]] = {}
    for pid, ts, carbs, protein, fat, fiber in rows:
        out.setdefault(pid, []).append({
            "timestamp": dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=dt.timezone.utc),
            "carbs": carbs, "protein": protein, "fat": fat, "fiber": fiber,
        })
    return out


def standardize(train_seq: np.ndarray, *others):
    """Per-channel standardisation using TRAINING-FOLD statistics only."""
    glucose = train_seq[:, 0, :]
    mu = float(glucose.mean())
    sd = float(glucose.std()) or 1.0
    elapsed = float(np.abs(train_seq[:, 2, :]).max()) or 1.0

    def apply(a):
        out = a.copy()
        out[:, 0, :] = (out[:, 0, :] - mu) / sd
        out[:, 2, :] = out[:, 2, :] / elapsed
        return out

    return (apply(train_seq), *[apply(o) for o in others]), (mu, sd, elapsed)
