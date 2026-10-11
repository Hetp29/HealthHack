"""Stage B: does any physiological signal group improve the production blend?

    python -m research.physiology.experiment_runner \
        --db-url sqlite:///data/local/cgmacros_v2.db

Protocol: the SAME rolling, participant-aware, purged development folds the production
model was selected on (`ml/devvalidation.py`). The 394 held-out Day-8 episodes are
never loaded.

Every candidate is the production family -- Ridge averaged 50/50 with a regularized
`HistGradientBoostingRegressor`, then shrunk per-participant calibration -- differing
only in which signal columns are appended to the 16 production features. So any
difference is attributable to the signals, not to a model change.

Baseline to beat: production features alone, development OOF MAE 24.960 mg/dL.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import pickle
import time

import numpy as np
import pandas as pd

from ml.candidates import GbmModel, _fit_ridge, _personalize
from ml.config import DEFAULT_CONFIG, HORIZONS, Config
from ml.devvalidation import (
    Candidate,
    format_table,
    paired_participant_bootstrap,
    rolling_folds,
    score_candidate,
)
from ml.features import FEATURE_NAMES
from ml.train import EpisodeMatrices, episodes_to_matrices
from research.physiology.signals import GROUPS, all_signal_names, names_for_group

RESULTS = pathlib.Path("research/physiology/results")
SIGNAL_CACHE = pathlib.Path("ml/_devdata/signals.pkl")
DEV_CACHE = pathlib.Path("ml/_devdata/dev_pool.pkl")

#: Module-level signal lookup, keyed by (participant_id, meal_id). Set by `main`.
#: The augmentation runs inside the fold loop, so it must be reachable from the
#: candidate closures without being re-pickled per fold.
_SIGNALS: dict[tuple[str, str], dict[str, float]] = {}


def load_signals() -> tuple[dict, list[str]]:
    with SIGNAL_CACHE.open("rb") as handle:
        payload = pickle.load(handle)
    frame = payload["frame"]
    lookup = {
        (pid, mid): frame.iloc[i].to_dict()
        for i, (pid, mid) in enumerate(payload["keys"])
    }
    return lookup, list(frame.columns)


def augment(matrices: EpisodeMatrices, columns: list[str]) -> pd.DataFrame:
    """Append the requested signal columns to a fold's design matrix.

    Rows are matched by (participant_id, meal_id). A missing key yields NaN, which the
    imputer and the booster both handle -- it is never zero-filled.
    """
    if not columns:
        return matrices.features
    extra = {name: np.full(len(matrices), np.nan) for name in columns}
    for i, (pid, mid) in enumerate(
        zip(matrices.participant_ids.tolist(), matrices.meal_ids.tolist())
    ):
        row = _SIGNALS.get((str(pid), str(mid)))
        if row is None:
            continue
        for name in columns:
            extra[name][i] = row.get(name, np.nan)
    return pd.concat(
        [matrices.features.reset_index(drop=True), pd.DataFrame(extra)], axis=1
    )


def make_candidate(name: str, signal_columns: list[str], notes: str = "") -> Candidate:
    """Production family (blend + calibration) on production features + `signal_columns`."""

    def run(train: EpisodeMatrices, validate: EpisodeMatrices, config: Config):
        train_X = augment(train, signal_columns)
        val_X = augment(validate, signal_columns)

        ridge = _fit_ridge_on(train_X, train, config)
        booster = GbmModel().fit(_WrappedMatrices(train_X, train), config)

        class _Blend:
            def predict_deltas(self, features):
                r = ridge.predict_deltas(features)
                g = booster.predict_deltas(features)
                return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

        blend = _Blend()
        personalization = _personalize(blend, _WrappedMatrices(train_X, train), config)
        return personalization.apply(
            validate.participant_ids, blend.predict_deltas(val_X)
        )

    return Candidate(name, run, notes or f"{len(FEATURE_NAMES) + len(signal_columns)} features")


class _WrappedMatrices:
    """EpisodeMatrices view with a substituted feature frame."""

    def __init__(self, features: pd.DataFrame, source: EpisodeMatrices):
        self.features = features
        self.deltas = source.deltas
        self.participant_ids = source.participant_ids
        self.baselines = source.baselines
        self.meal_timestamps = source.meal_timestamps
        self.meal_ids = source.meal_ids

    def __len__(self) -> int:
        return len(self.features)

    def valid_mask(self, horizon: int) -> np.ndarray:
        return self.deltas[horizon].notna().to_numpy()


def _fit_ridge_on(features: pd.DataFrame, source: EpisodeMatrices, config: Config):
    from ml.train import GeneralizedModel

    return GeneralizedModel().fit(features, source.deltas, config)


def build_candidates(signal_columns: list[str]) -> list[Candidate]:
    """Baseline, each group alone, the full vector, and leave-one-group-out."""
    candidates = [make_candidate("BASELINE (production features)", [], "16 features, control")]
    for group in GROUPS:
        names = [n for n in names_for_group(group) if n in signal_columns]
        if names:
            candidates.append(make_candidate(f"+ {group}", names, f"+{len(names)} signals"))
    candidates.append(make_candidate("+ ALL groups", list(signal_columns), "full vector"))
    return candidates


def main(argv: list[str] | None = None) -> int:
    global _SIGNALS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--n-folds", type=int, default=4)
    parser.add_argument(
        "--folds", default="rolling", choices=["rolling", "coldstart"],
        help="fold type; 'coldstart' holds out entire participants",
    )
    parser.add_argument(
        "--stage", default="groups",
        choices=["groups", "leave-one-out", "coldstart", "combos"],
    )
    args = parser.parse_args(argv)

    from ml.experiments import load_dev_pool

    dev, meta = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    _SIGNALS, signal_columns = load_signals()
    print(f"signals: {len(signal_columns)} columns for {len(_SIGNALS)} episodes")

    if args.stage == "coldstart" or args.folds == "coldstart":
        from ml.devvalidation import participant_holdout_folds

        folds = participant_holdout_folds(dev, DEFAULT_CONFIG, n_folds=args.n_folds)
        print("COLD-START folds (entire participants held out -- the identity-proxy test)")
    else:
        folds = rolling_folds(dev, DEFAULT_CONFIG, n_folds=args.n_folds)
    print(f"folds: {[(f.index, len(f.train), len(f.validate)) for f in folds]}")

    if args.stage in ("groups", "coldstart"):
        candidates = build_candidates(signal_columns)
    elif args.stage == "combos":
        pick = lambda g: [n for n in names_for_group(g) if n in signal_columns]  # noqa: E731
        glucose, profile, response = pick("glucose"), pick("profile"), pick("response")
        candidates = [
            make_candidate("BASELINE (production features)", [], "control"),
            make_candidate("+ glucose", glucose, f"+{len(glucose)}"),
            make_candidate("+ response", response, f"+{len(response)}"),
            make_candidate("+ profile", profile, f"+{len(profile)}"),
            make_candidate(
                "+ glucose + response", glucose + response,
                f"+{len(glucose)+len(response)}",
            ),
            make_candidate(
                "+ glucose + response + profile", glucose + response + profile,
                f"+{len(glucose)+len(response)+len(profile)}",
            ),
            make_candidate("+ ALL groups", list(signal_columns), "full vector"),
        ]
    else:
        candidates = [make_candidate("+ ALL groups", list(signal_columns), "full vector")]
        for group in GROUPS:
            names = [n for n in names_for_group(group) if n in signal_columns]
            remaining = [c for c in signal_columns if c not in names]
            if names:
                candidates.append(
                    make_candidate(f"ALL − {group}", remaining, f"-{len(names)} signals")
                )

    results = []
    for candidate in candidates:
        start = time.perf_counter()
        result = score_candidate(folds, candidate, DEFAULT_CONFIG)
        print(
            f"  {candidate.name:34s} MAE {result.mae:7.3f}  RMSE {result.rmse:7.3f}  "
            f"folds {result.fold_mae}  ({time.perf_counter()-start:.0f}s)"
        )
        results.append(result)

    print("\n" + format_table(results))

    baseline = results[0]
    print(f"\nparticipant-clustered bootstrap vs {baseline.name}:")
    print("  candidate                           ΔMAE    95% CI             excl 0")
    rows = []
    for result in results:
        if result is baseline:
            rows.append({**result.as_row(), "delta": 0.0, "ci_low": None, "ci_high": None,
                         "excludes_zero": None})
            continue
        ci = paired_participant_bootstrap(baseline, result)
        print(f"  {result.name:34s} {ci['point']:+7.3f}  "
              f"[{ci['ci_low']:+.3f}, {ci['ci_high']:+.3f}]   {ci['excludes_zero']}")
        rows.append({**result.as_row(), "delta": ci["point"], "ci_low": ci["ci_low"],
                     "ci_high": ci["ci_high"], "excludes_zero": ci["excludes_zero"]})

    RESULTS.mkdir(parents=True, exist_ok=True)
    name = {
        "groups": "ablations.csv",
        "leave-one-out": "leave_one_out.csv",
        "coldstart": "coldstart.csv",
        "combos": f"combos_{args.folds}.csv",
    }[args.stage]
    pd.DataFrame(rows).to_csv(RESULTS / name, index=False)
    participant = {r.name: r.participant_mae for r in results}
    (RESULTS / "participant_results.json").write_text(json.dumps(participant, indent=2) + "\n")
    (RESULTS / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "stage": args.stage,
                "n_folds": args.n_folds,
                "dev_pool": meta["dev_pool"],
                "reserved_test_not_loaded": meta["reserved_test"],
                "signal_columns": signal_columns,
                "production_features": list(FEATURE_NAMES),
                "folds": [{"index": f.index, "train": len(f.train), "validate": len(f.validate)} for f in folds],
                "candidates": [c.name for c in candidates],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {RESULTS}/{name}")
    return 0


if __name__ == "__main__":
    from research.physiology.experiment_runner import main as _main

    raise SystemExit(_main())
