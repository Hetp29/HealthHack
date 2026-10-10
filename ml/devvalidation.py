"""Development validation: rolling, participant-aware, purged folds.

WHY THIS MODULE EXISTS
----------------------
The 394-episode Day-8 holdout has been inspected and its scores published (commit
`6a49ca3`, `docs/validation.md`). It is therefore **no longer a clean final test set**,
and using it to choose features, hyperparameters or model families would be selection
on the test — the exact failure the project's integrity rules forbid. Rather than
pretend otherwise, all model development happens here, on the 1,171-episode
development pool (the Day-8 train + validation folds), using rolling-origin validation
that never touches the held-out episodes.

PROTOCOL
--------
Per participant, episodes are ordered by meal time and cut into `n_folds` contiguous
chronological blocks. Fold *k* trains on blocks `0..k-1` and validates on block *k*
(an expanding window, so every validation episode is predicted from that participant's
own earlier meals only). Block 0 is never a validation block because it has no history.

Three properties this buys:

* **Participant-aware.** Folds are built per participant, so a fold boundary is a point
  in that person's own timeline. A globally sorted split would be meaningless here:
  CGMacros recording years span 2019-2023 per participant.
* **Purged.** A training episode at `t'` owns the response window `(t', t'+120]`. Any
  training episode within `embargo_minutes` of the validation block's start is dropped,
  so no training target can share CGM observations with a validation target.
* **Comparable.** `rolling_folds` is deterministic, so every candidate is scored on
  exactly the same validation cases, and out-of-fold predictions can be pooled.

Uncertainty is estimated by **participant-clustered bootstrap** over the pooled
out-of-fold predictions: participants, not episodes, are the resampling unit, because
meals from one person are not independent.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ml.config import HORIZONS, Config
from ml.metrics import CurveErrors, build_curve, mae, rmse, summarize_curve, summarize_observed
from ml.targets import Episode
from ml.train import EpisodeMatrices, episodes_to_matrices


class DevValidationError(AssertionError):
    """A fold violated its own purge or ordering guarantee."""


@dataclass
class Fold:
    """One rolling-origin fold."""

    index: int
    train: list[Episode]
    validate: list[Episode]

    def __repr__(self) -> str:
        return f"Fold({self.index}: train={len(self.train)} validate={len(self.validate)})"


def rolling_folds(
    episodes: list[Episode], config: Config, n_folds: int = 4
) -> list[Fold]:
    """Build expanding-window, per-participant, purged folds.

    Returns `n_folds - 1` folds: block 0 is training-only for every participant.
    """
    if n_folds < 2:
        raise ValueError("n_folds must be at least 2")
    embargo = dt.timedelta(minutes=config.day8.embargo_minutes)

    by_participant: dict[str, list[Episode]] = {}
    for episode in episodes:
        by_participant.setdefault(episode.participant_id, []).append(episode)

    blocks: dict[str, list[list[Episode]]] = {}
    for participant_id, owned in by_participant.items():
        owned = sorted(owned, key=lambda e: e.meal_timestamp)
        if len(owned) < n_folds:
            # Too few episodes to cut into blocks; contribute to training only.
            blocks[participant_id] = [owned] + [[] for _ in range(n_folds - 1)]
            continue
        # np.array_split gives contiguous, near-equal blocks preserving order.
        blocks[participant_id] = [
            list(part) for part in np.array_split(np.array(owned, dtype=object), n_folds)
        ]

    folds: list[Fold] = []
    for k in range(1, n_folds):
        train: list[Episode] = []
        validate: list[Episode] = []
        for participant_id, parts in blocks.items():
            block = parts[k]
            earlier = [e for part in parts[:k] for e in part]
            if not block:
                train.extend(earlier)
                continue
            cutoff = min(e.meal_timestamp for e in block)
            kept = [e for e in earlier if e.meal_timestamp <= cutoff - embargo]
            if not kept:
                # No purge-safe history: this participant cannot be validated in
                # this fold, but its earlier episodes still inform the cohort model.
                train.extend(earlier)
                continue
            train.extend(kept)
            validate.extend(block)

        key = lambda e: (e.meal_timestamp, e.meal_id or "")  # noqa: E731
        fold = Fold(k, sorted(train, key=key), sorted(validate, key=key))
        _assert_fold_is_purged(fold, config)
        folds.append(fold)
    return folds


def _assert_fold_is_purged(fold: Fold, config: Config) -> None:
    """Fail loudly if a fold's own guarantees do not hold."""
    embargo = dt.timedelta(minutes=config.day8.embargo_minutes)
    train_ids = {id(e) for e in fold.train}
    if train_ids & {id(e) for e in fold.validate}:
        raise DevValidationError(f"fold {fold.index}: train and validate overlap")

    earliest: dict[str, dt.datetime] = {}
    for episode in fold.validate:
        current = earliest.get(episode.participant_id)
        if current is None or episode.meal_timestamp < current:
            earliest[episode.participant_id] = episode.meal_timestamp
    for episode in fold.train:
        cutoff = earliest.get(episode.participant_id)
        if cutoff is not None and episode.meal_timestamp > cutoff - embargo:
            raise DevValidationError(
                f"fold {fold.index}: training episode {episode.meal_id!r} violates the "
                f"{config.day8.embargo_minutes:g}-minute embargo"
            )


# --------------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------------- #

#: A candidate fits on the training matrices and returns predicted horizon deltas for
#: the validation matrices. Everything it needs must come from `train`; touching
#: `validate`'s targets would be leakage.
FitPredict = Callable[[EpisodeMatrices, EpisodeMatrices, Config], dict[int, np.ndarray]]


@dataclass
class Candidate:
    name: str
    fit_predict: FitPredict
    notes: str = ""


# --------------------------------------------------------------------------- #
# Out-of-fold scoring
# --------------------------------------------------------------------------- #


@dataclass
class OofResult:
    """Pooled out-of-fold predictions and the metrics derived from them."""

    name: str
    #: horizon -> signed errors (predicted - observed), pooled across folds
    errors: dict[int, list[float]] = field(default_factory=dict)
    #: participant id per pooled error, parallel to `errors`
    participants: dict[int, list[str]] = field(default_factory=dict)
    curve: CurveErrors = field(default_factory=CurveErrors.empty)
    #: fold index -> overall MAE, for fold-to-fold variability
    fold_mae: dict[int, float] = field(default_factory=dict)
    #: participant -> overall MAE
    participant_mae: dict[str, float] = field(default_factory=dict)
    n_validation_episodes: int = 0
    fit_seconds: float = 0.0

    def pooled_errors(self) -> list[float]:
        return [e for horizon in HORIZONS for e in self.errors.get(horizon, [])]

    def pooled_participants(self) -> list[str]:
        return [p for horizon in HORIZONS for p in self.participants.get(horizon, [])]

    @property
    def mae(self) -> float | None:
        return mae(self.pooled_errors())

    @property
    def rmse(self) -> float | None:
        return rmse(self.pooled_errors())

    def horizon_mae(self, horizon: int) -> float | None:
        return mae(self.errors.get(horizon, []))

    def as_row(self) -> dict:
        return {
            "model": self.name,
            "n": len(self.pooled_errors()),
            "episodes": self.n_validation_episodes,
            "mae": self.mae,
            "rmse": self.rmse,
            **{f"mae@{h}": self.horizon_mae(h) for h in HORIZONS},
            "peak_mae": mae(self.curve.peak),
            "ttp_mae": mae(self.curve.time_to_peak),
            "iauc_mae": mae(self.curve.iauc),
            "fold_mae_spread": (
                round(max(self.fold_mae.values()) - min(self.fold_mae.values()), 2)
                if len(self.fold_mae) > 1
                else None
            ),
            "fit_seconds": round(self.fit_seconds, 1),
        }


def score_candidate(
    folds: list[Fold], candidate: Candidate, config: Config
) -> OofResult:
    """Fit `candidate` on each fold's training set and score its validation block."""
    import time

    result = OofResult(name=candidate.name)
    result.errors = {h: [] for h in HORIZONS}
    result.participants = {h: [] for h in HORIZONS}
    per_participant: dict[str, list[float]] = {}
    elapsed = 0.0

    for fold in folds:
        if not fold.validate:
            continue
        train_matrices = episodes_to_matrices(fold.train)
        val_matrices = episodes_to_matrices(fold.validate)

        start = time.perf_counter()
        predicted = candidate.fit_predict(train_matrices, val_matrices, config)
        elapsed += time.perf_counter() - start

        fold_errors: list[float] = []
        for horizon in HORIZONS:
            observed = val_matrices.deltas[horizon].to_numpy(dtype="float64")
            mask = val_matrices.valid_mask(horizon)
            errs = (predicted[horizon] - observed)[mask]
            ids = val_matrices.participant_ids[mask]
            result.errors[horizon].extend(float(e) for e in errs)
            result.participants[horizon].extend(str(p) for p in ids)
            fold_errors.extend(float(e) for e in errs)
            for pid, err in zip(ids, errs):
                per_participant.setdefault(str(pid), []).append(abs(float(err)))

        # Curve-derived errors on observed native CGM.
        for index, episode in enumerate(fold.validate):
            observed_summary = summarize_observed(
                episode.target.observed_curve, episode.target.baseline_glucose_mg_dl
            )
            if observed_summary is None:
                continue
            deltas = {h: float(predicted[h][index]) for h in HORIZONS}
            minutes, values, _ = build_curve(
                episode.target.baseline_glucose_mg_dl,
                deltas,
                clip_min=config.model.clip_glucose_min_mg_dl,
                clip_max=config.model.clip_glucose_max_mg_dl,
            )
            result.curve.add(
                summarize_curve(minutes, values, episode.target.baseline_glucose_mg_dl),
                observed_summary,
            )

        fold_value = mae(fold_errors)
        if fold_value is not None:
            result.fold_mae[fold.index] = round(fold_value, 3)
        result.n_validation_episodes += len(fold.validate)

    result.participant_mae = {
        pid: round(float(np.mean(v)), 3) for pid, v in sorted(per_participant.items())
    }
    result.fit_seconds = elapsed
    return result


def paired_participant_bootstrap(
    a: OofResult, b: OofResult, n_resamples: int = 2000, seed: int = 20261011
) -> dict:
    """Participant-clustered bootstrap CI for `a.mae - b.mae` (positive = b better).

    Participants are the resampling unit because meals from one person are not
    independent; resampling episodes would understate the interval badly.
    """
    errors_a = np.abs(np.asarray(a.pooled_errors(), dtype="float64"))
    errors_b = np.abs(np.asarray(b.pooled_errors(), dtype="float64"))
    ids_a = np.asarray(a.pooled_participants())
    ids_b = np.asarray(b.pooled_participants())

    if errors_a.size == 0 or errors_a.size != errors_b.size or not np.array_equal(ids_a, ids_b):
        return {
            "point": None,
            "ci_low": None,
            "ci_high": None,
            "note": "candidates were not scored on identical validation cases",
        }

    participants = np.unique(ids_a)
    index_by_participant = {p: np.flatnonzero(ids_a == p) for p in participants}
    rng = np.random.default_rng(seed)

    point = float(errors_a.mean() - errors_b.mean())
    draws = np.empty(n_resamples, dtype="float64")
    for i in range(n_resamples):
        picked = rng.choice(participants, size=participants.size, replace=True)
        idx = np.concatenate([index_by_participant[p] for p in picked])
        draws[i] = errors_a[idx].mean() - errors_b[idx].mean()

    low, high = np.percentile(draws, [2.5, 97.5])
    return {
        "point": round(point, 3),
        "ci_low": round(float(low), 3),
        "ci_high": round(float(high), 3),
        "participants": int(participants.size),
        "excludes_zero": bool(low > 0 or high < 0),
    }


def format_table(results: list[OofResult]) -> str:
    """Markdown comparison table, best MAE first."""
    rows = [r.as_row() for r in results]
    rows.sort(key=lambda r: (r["mae"] is None, r["mae"]))
    header = (
        "| model | MAE | RMSE | @30 | @60 | @90 | @120 | peak | ttp | iAUC | "
        "fold spread | fit s |"
    )
    lines = [header, "|" + "---|" * 12]
    for r in rows:
        f = lambda v, d=2: "—" if v is None else f"{v:.{d}f}"  # noqa: E731
        lines.append(
            f"| {r['model']} | **{f(r['mae'])}** | {f(r['rmse'])} | {f(r['mae@30'])} | "
            f"{f(r['mae@60'])} | {f(r['mae@90'])} | {f(r['mae@120'])} | "
            f"{f(r['peak_mae'])} | {f(r['ttp_mae'],1)} | {f(r['iauc_mae'],0)} | "
            f"{f(r['fold_mae_spread'])} | {r['fit_seconds']} |"
        )
    return "\n".join(lines)
