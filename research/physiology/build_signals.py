"""Compute every candidate signal for a set of episodes, once, with a cache.

    python -m research.physiology.build_signals --db-url sqlite:///data/local/cgmacros_v2.db

LEAKAGE CONTROL
---------------
Two independent mechanisms, because this is where a physiology-flavoured feature set
is most likely to cheat:

1. **Truncated history.** Every signal reads `history.truncated_at(meal_timestamp)`,
   so post-meal rows are absent from the object the signal functions see.
2. **Strict earlier-episode filter.** The `r_` fingerprint group summarizes OBSERVED
   past responses. Those are legitimate inputs -- they had already happened -- but only
   under a strict `meal_timestamp <` comparison, per participant. Equal timestamps are
   excluded, so a meal can never contribute to its own fingerprint.

`tests/test_research_signals.py` attacks both.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import pathlib
import pickle
import time
import zipfile

import numpy as np
import pandas as pd

from ml.config import DEFAULT_CONFIG, Config
from ml.targets import Episode
from research.physiology.signals import (
    absorption_signals,
    activity_signals,
    all_signal_names,
    glucose_signals,
    mob_signals,
    profile_signals,
    response_signals,
)

CACHE = pathlib.Path("ml/_devdata/signals.pkl")

#: bio.csv lives inside the published archive. Read in place, never extracted, so the
#: raw dataset directory is not modified.
CGMACROS_ZIP = pathlib.Path(
    "data/cgmacros-a-scientific-dataset-for-personalized-nutrition-and-diet-monitoring-1.0.0"
    "/CGMacros_dateshifted365.zip"
)
BIO_MEMBER = "CGMacros/bio.csv"


def _f(row: dict, key: str) -> float | None:
    raw = (row.get(key) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def load_participant_profiles() -> dict[str, dict]:
    """Participant lab/anthropometric covariates, keyed by canonical participant id.

    Returns `{}` when the archive is unavailable, so every profile signal becomes NaN
    and the pipeline degrades instead of failing.
    """
    if not CGMACROS_ZIP.exists():
        print(f"  bio.csv unavailable ({CGMACROS_ZIP} missing); profile signals -> NaN")
        return {}
    with zipfile.ZipFile(CGMACROS_ZIP) as archive:
        names = [n for n in archive.namelist() if n.endswith("bio.csv")]
        if not names:
            return {}
        raw = archive.read(names[0]).decode("utf-8-sig")
    profiles: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(raw)):
        subject = (row.get("subject") or "").strip()
        if not subject:
            continue
        pid = f"cgmacros_{int(float(subject)):03d}"
        profiles[pid] = {
            "age": _f(row, "Age"),
            "bmi": _f(row, "BMI"),
            "a1c": _f(row, "A1c PDL (Lab)"),
            "fasting_glucose": _f(row, "Fasting GLU - PDL (Lab)"),
            "fasting_insulin": _f(row, "Insulin "),
        }
    print(f"  loaded {len(profiles)} participant profiles from bio.csv (read in place)")
    return profiles


def _observed_response(episode: Episode) -> dict | None:
    """Observed peak delta / time-to-peak / 120-min delta for an episode, or None."""
    curve = episode.target.observed_curve
    if len(curve) < 8:
        return None
    baseline = episode.target.baseline_glucose_mg_dl
    minutes = sorted(curve)
    values = [curve[m] for m in minutes]
    peak = max(values)
    ttp = next(m for m, v in zip(minutes, values) if v == peak)
    last = max(m for m in minutes if m <= 120)
    return {
        "peak_delta": peak - baseline,
        "ttp": float(ttp),
        "delta_120": curve[last] - baseline,
        "carbs": float(episode.features.values.get("carbs_g", 0.0)),
    }


def build_signal_frame(
    repository,
    episodes: list[Episode],
    config: Config = DEFAULT_CONFIG,
    profiles: dict[str, dict] | None = None,
) -> pd.DataFrame:
    """One row of candidate signals per episode, aligned to `episodes` order."""
    profiles = profiles if profiles is not None else load_participant_profiles()

    # Observed responses per participant, chronologically ordered.
    responses_by_participant: dict[str, list[tuple[dt.datetime, dict]]] = {}
    for episode in episodes:
        observed = _observed_response(episode)
        if observed is not None:
            responses_by_participant.setdefault(episode.participant_id, []).append(
                (episode.meal_timestamp, observed)
            )
    for entries in responses_by_participant.values():
        entries.sort(key=lambda pair: pair[0])

    histories: dict[str, object] = {}
    rows: list[dict] = []

    for episode in episodes:
        pid = episode.participant_id
        if pid not in histories:
            histories[pid] = repository.get_history(pid)
        past = histories[pid].truncated_at(episode.meal_timestamp)
        t = episode.meal_timestamp
        values = episode.features.values
        baseline = episode.target.baseline_glucose_mg_dl

        # STRICT `<`: a meal can never enter its own fingerprint.
        prior = [
            observed
            for stamp, observed in responses_by_participant.get(pid, [])
            if stamp < t
        ]

        row: dict[str, float] = {}
        row.update(glucose_signals(past, t, baseline))
        row.update(mob_signals(past, t))
        row.update(
            absorption_signals(
                values["carbs_g"], values["protein_g"], values["fat_g"],
                values["fiber_g"], values["calories_kcal"],
            )
        )
        row.update(activity_signals(past, t))
        row.update(profile_signals(profiles.get(pid)))
        row.update(response_signals(prior, config.personalization.shrinkage_k))
        rows.append(row)

    frame = pd.DataFrame(rows, columns=all_signal_names(), dtype="float64")
    return frame


def main(argv: list[str] | None = None) -> int:
    from ml.evaluate import split_day8_holdout
    from ml.targets import build_episodes
    from ml.train import build_repository

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)

    repository = build_repository("sqlite", db_url=args.db_url)
    start = time.perf_counter()
    episodes, _ = build_episodes(repository, DEFAULT_CONFIG)
    train, validation, test = split_day8_holdout(episodes, DEFAULT_CONFIG)
    dev = sorted(train + validation, key=lambda e: (e.meal_timestamp, e.meal_id or ""))
    print(f"dev pool {len(dev)} episodes ({len(test)} test episodes NOT loaded)")

    frame = build_signal_frame(repository, dev, DEFAULT_CONFIG)
    elapsed = time.perf_counter() - start
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    keys = [(e.participant_id, e.meal_id) for e in dev]
    with CACHE.open("wb") as handle:
        pickle.dump({"db_url": args.db_url, "keys": keys, "frame": frame}, handle)

    print(f"built {frame.shape[0]} x {frame.shape[1]} signal matrix in {elapsed:.0f}s")
    missing = frame.isna().mean().sort_values(ascending=False)
    print("\nmissing-rate per signal (top 12):")
    for name, rate in missing.head(12).items():
        print(f"  {name:24s} {rate:6.1%}")
    print(f"\nsignals fully observed: {(missing == 0).sum()} / {len(missing)}")
    print(f"wrote {CACHE}")
    return 0


if __name__ == "__main__":
    from research.physiology.build_signals import main as _main

    raise SystemExit(_main())
