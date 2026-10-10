"""Advanced model-family leaderboard.

    python -m research.advanced.runner --db-url sqlite:///data/local/cgmacros_v2.db \
        --folds rolling     # within-person, purged
    python -m research.advanced.runner --db-url ... --folds coldstart   # unseen participants

Uses the same frozen development folds the production model was selected on. The 394
reserved Day-8 episodes are never loaded.

Every candidate is the production family (Ridge + booster blend, then shrunk
participant calibration) unless stated otherwise, so differences are attributable to
the paradigm under test rather than to a model swap.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

import numpy as np
import pandas as pd

from ml.candidates import GbmModel, _personalize
from ml.config import DEFAULT_CONFIG, HORIZONS, Config
from ml.devvalidation import (
    Candidate,
    format_table,
    paired_participant_bootstrap,
    rolling_folds,
    score_candidate,
)
from ml.train import EpisodeMatrices, GeneralizedModel
from research.advanced.retrieval import RETRIEVAL_FEATURES, build_retrieval_features
from research.advanced.trajectory import (
    FpcaBasis,
    SplineBasis,
    TrajectoryHead,
)

RESULTS = pathlib.Path("research/advanced/results")

#: (participant_id, meal_id) -> Episode, and -> retrieval feature row.
_EPISODES: dict[tuple[str, str], object] = {}
_RETRIEVAL: dict[tuple[str, str], dict[str, float]] = {}


def _keys(matrices: EpisodeMatrices) -> list[tuple[str, str]]:
    return [
        (str(p), str(m))
        for p, m in zip(matrices.participant_ids.tolist(), matrices.meal_ids.tolist())
    ]


def _episodes_for(matrices: EpisodeMatrices) -> list:
    return [_EPISODES[key] for key in _keys(matrices)]


def _augment(matrices: EpisodeMatrices, columns: list[str]) -> pd.DataFrame:
    if not columns:
        return matrices.features
    extra = {name: np.full(len(matrices), np.nan) for name in columns}
    for i, key in enumerate(_keys(matrices)):
        row = _RETRIEVAL.get(key)
        if row is None:
            continue
        for name in columns:
            extra[name][i] = row.get(name, np.nan)
    return pd.concat(
        [matrices.features.reset_index(drop=True), pd.DataFrame(extra)], axis=1
    )


class _View:
    """EpisodeMatrices with a substituted feature frame."""

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


def _blend_with_calibration(train_X, train_src, val_X, val_src, config):
    """Production family on arbitrary feature frames."""
    ridge = GeneralizedModel().fit(train_X, train_src.deltas, config)
    booster = GbmModel().fit(_View(train_X, train_src), config)

    class _Blend:
        def predict_deltas(self, features):
            r = ridge.predict_deltas(features)
            g = booster.predict_deltas(features)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    blend = _Blend()
    personalization = _personalize(blend, _View(train_X, train_src), config)
    return personalization.apply(
        val_src.participant_ids, blend.predict_deltas(val_X)
    ), blend


# --------------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------------- #


def baseline_candidate() -> Candidate:
    def run(train, validate, config):
        predictions, _ = _blend_with_calibration(
            train.features, train, validate.features, validate, config
        )
        return predictions

    return Candidate("BASELINE blend (fs-1.2)", run, "control, 29 features")


def retrieval_candidate(name: str = "+ retrieval") -> Candidate:
    columns = list(RETRIEVAL_FEATURES)

    def run(train, validate, config):
        predictions, _ = _blend_with_calibration(
            _augment(train, columns), train, _augment(validate, columns), validate, config
        )
        return predictions

    return Candidate(name, run, f"+{len(columns)} retrieval features")


def trajectory_candidate(basis_kind: str, estimator: str, n: int) -> Candidate:
    """Functional head: predict basis coefficients, evaluate on the 5-minute grid."""
    label = f"trajectory {basis_kind}-{n} ({estimator})"

    def _fit(train, config):
        basis = FpcaBasis(n) if basis_kind == "fpca" else SplineBasis(n)
        head = TrajectoryHead(basis, estimator)
        head.fit(train, _episodes_for(train), config)
        return head

    def run(train, validate, config):
        return _fit(train, config).predict_deltas(validate.features)

    def run_curves(train, validate, config):
        return _fit(train, config).predict_curves(validate.features)

    return Candidate(label, run, f"{basis_kind} basis", fit_predict_curves=run_curves)


def hybrid_candidate(n: int = 4) -> Candidate:
    """Production horizons for level accuracy, FPCA curve for shape.

    Rationale: the two heads are good at different things. The production blend has the
    lower horizon error; the functional head has a curve that is not forced through four
    knots. This keeps the production horizon predictions and uses the functional head
    only to redistribute the SHAPE between them, by rescaling the predicted trajectory
    so it passes through the production horizon values.
    """

    def run(train, validate, config):
        predictions, _ = _blend_with_calibration(
            train.features, train, validate.features, validate, config
        )
        return predictions

    def run_curves(train, validate, config):
        predictions, _ = _blend_with_calibration(
            train.features, train, validate.features, validate, config
        )
        head = TrajectoryHead(FpcaBasis(n), "ridge")
        head.fit(train, _episodes_for(train), config)
        curves = head.predict_curves(validate.features)

        # Anchor the functional shape to the production horizon predictions by
        # piecewise-linear correction of the residual at the knots.
        from ml.config import FORECAST_MINUTES

        grid = np.asarray(FORECAST_MINUTES, dtype="float64")
        knots = np.asarray([0, *HORIZONS], dtype="float64")
        for i in range(curves.shape[0]):
            at_knots = np.interp(knots, grid, curves[i])
            wanted = np.asarray(
                [0.0, *[predictions[h][i] for h in HORIZONS]], dtype="float64"
            )
            correction = np.interp(grid, knots, wanted - at_knots)
            curves[i] = curves[i] + correction
        return curves

    return Candidate(
        "hybrid: blend horizons + FPCA shape",
        run,
        "production levels, functional shape",
        fit_predict_curves=run_curves,
    )


def retrieval_plus_trajectory(n: int = 4) -> Candidate:
    columns = list(RETRIEVAL_FEATURES)

    def run(train, validate, config):
        predictions, _ = _blend_with_calibration(
            _augment(train, columns), train, _augment(validate, columns), validate, config
        )
        return predictions

    def run_curves(train, validate, config):
        train_X, val_X = _augment(train, columns), _augment(validate, columns)
        predictions, _ = _blend_with_calibration(train_X, train, val_X, validate, config)
        head = TrajectoryHead(FpcaBasis(n), "ridge")
        head.fit(_View(train_X, train), _episodes_for(train), config)
        curves = head.predict_curves(val_X)

        from ml.config import FORECAST_MINUTES

        grid = np.asarray(FORECAST_MINUTES, dtype="float64")
        knots = np.asarray([0, *HORIZONS], dtype="float64")
        for i in range(curves.shape[0]):
            at_knots = np.interp(knots, grid, curves[i])
            wanted = np.asarray([0.0, *[predictions[h][i] for h in HORIZONS]], dtype="float64")
            curves[i] = curves[i] + np.interp(grid, knots, wanted - at_knots)
        return curves

    return Candidate(
        "+ retrieval + FPCA shape", run, "both families", fit_predict_curves=run_curves
    )


def main(argv: list[str] | None = None) -> int:
    global _EPISODES, _RETRIEVAL

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--folds", default="rolling", choices=["rolling", "coldstart"])
    parser.add_argument("--n-folds", type=int, default=4)
    parser.add_argument("--k", type=int, default=5, help="retrieval neighbours")
    args = parser.parse_args(argv)

    from ml.experiments import load_dev_pool

    dev, meta = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    _EPISODES = {(e.participant_id, e.meal_id): e for e in dev}

    start = time.perf_counter()
    frame = build_retrieval_features(dev, k=args.k)
    _RETRIEVAL = {
        (e.participant_id, e.meal_id): frame.iloc[i].to_dict()
        for i, e in enumerate(dev)
    }
    print(f"retrieval features built in {time.perf_counter()-start:.1f}s (k={args.k})")

    if args.folds == "coldstart":
        from ml.devvalidation import participant_holdout_folds

        folds = participant_holdout_folds(dev, DEFAULT_CONFIG, n_folds=args.n_folds)
        print("COLD-START folds (entire participants held out)")
    else:
        folds = rolling_folds(dev, DEFAULT_CONFIG, n_folds=args.n_folds)
    print(f"folds: {[(f.index, len(f.train), len(f.validate)) for f in folds]}")

    candidates = [
        baseline_candidate(),
        trajectory_candidate("fpca", "ridge", 4),
        trajectory_candidate("fpca", "gbm", 4),
        trajectory_candidate("spline", "ridge", 4),
        hybrid_candidate(4),
        retrieval_candidate(),
        retrieval_plus_trajectory(4),
    ]

    results, failures = [], []
    for candidate in candidates:
        try:
            t0 = time.perf_counter()
            result = score_candidate(folds, candidate, DEFAULT_CONFIG)
            print(
                f"  {candidate.name:38s} MAE {result.mae:7.3f}  traj {('%.3f' % (result.as_row()['trajectory_mae'] or float('nan'))):>8s}  "
                f"ttp {result.as_row()['ttp_mae']:6.2f}  ({time.perf_counter()-t0:.0f}s)"
            )
            results.append(result)
        except Exception as error:
            print(f"  {candidate.name:38s} FAILED: {type(error).__name__}: {error}")
            failures.append({"candidate": candidate.name, "error": f"{type(error).__name__}: {error}"})

    print("\n" + format_table(results))

    print("\nfew-shot MAE by observed own-history depth:")
    buckets = sorted({b for r in results for b in r.fewshot_errors})
    header = "  " + "model".ljust(38) + "".join(b.split("_", 1)[1].rjust(10) for b in buckets)
    print(header)
    for r in results:
        row = r.as_row()
        line = "  " + r.name.ljust(38)
        for b in buckets:
            v = row.get(f"fewshot_{b}")
            line += ("—" if v is None else f"{v:.2f}").rjust(10)
        print(line)

    baseline = results[0]
    print(f"\nparticipant-clustered bootstrap vs {baseline.name}:")
    rows = []
    for r in results:
        row = r.as_row()
        if r is baseline:
            rows.append({**row, "delta": 0.0, "ci_low": None, "ci_high": None, "excludes_zero": None})
            continue
        ci = paired_participant_bootstrap(baseline, r)
        print(f"  {r.name:38s} {ci['point']:+7.3f}  [{ci['ci_low']:+.3f}, {ci['ci_high']:+.3f}]   {ci['excludes_zero']}")
        rows.append({**row, "delta": ci["point"], "ci_low": ci["ci_low"],
                     "ci_high": ci["ci_high"], "excludes_zero": ci["excludes_zero"]})

    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(RESULTS / f"leaderboard_{args.folds}.csv", index=False)
    (RESULTS / f"manifest_{args.folds}.json").write_text(
        json.dumps(
            {
                "folds": args.folds,
                "n_folds": args.n_folds,
                "retrieval_k": args.k,
                "dev_pool": meta["dev_pool"],
                "reserved_test_not_loaded": meta["reserved_test"],
                "candidates": [c.name for c in candidates],
                "failures": failures,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {RESULTS}/leaderboard_{args.folds}.csv")
    return 0


if __name__ == "__main__":
    from research.advanced.runner import main as _main

    raise SystemExit(_main())
