"""Phase 3/4: paired evaluation of the PRODUCTION retrieval integration.

    python -m research.advanced.promote_eval --db-url sqlite:///data/local/cgmacros_v2.db

Compares, on identical development folds and identical episodes:

  A  baseline      -- production features with the `q_retr_*` columns REMOVED
                      (i.e. exactly fs-1.2, computed by the fs-1.3 code path)
  B  + retrieval   -- the full fs-1.3 production feature set
  C  + retrieval + FPCA shape overlay (Phase 4; horizon predictions identical to B)

Candidate A is produced by dropping columns rather than by checking out the old code,
so A and B share one feature-computation pass and one set of episodes. Any difference
is therefore attributable to the retrieval columns alone.

The 394 reserved Day-8 episodes are never loaded.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

import numpy as np
import pandas as pd

from ml.candidates import GbmModel, _personalize
from ml.config import DEFAULT_CONFIG, FORECAST_MINUTES, HORIZONS, Config
from ml.devvalidation import (
    Candidate,
    format_table,
    paired_participant_bootstrap,
    rolling_folds,
    score_candidate,
)
from ml.features import FEATURE_NAMES
from ml.train import EpisodeMatrices, GeneralizedModel

RESULTS = pathlib.Path("research/advanced/results")
RETRIEVAL_COLUMNS = [name for name in FEATURE_NAMES if name.startswith("q_retr_")]
BASELINE_COLUMNS = [name for name in FEATURE_NAMES if not name.startswith("q_retr_")]

_EPISODES: dict[tuple[str, str], object] = {}


class _View:
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


def _fit_production(train_X, train_src, config: Config):
    ridge = GeneralizedModel().fit(train_X, train_src.deltas, config)
    booster = GbmModel().fit(_View(train_X, train_src), config)

    class _Blend:
        def predict_deltas(self, features):
            r = ridge.predict_deltas(features)
            g = booster.predict_deltas(features)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    blend = _Blend()
    personalization = _personalize(blend, _View(train_X, train_src), config)
    return blend, personalization


def _candidate(name: str, columns: list[str], overlay: bool = False) -> Candidate:
    def predict(train, validate, config):
        blend, personalization = _fit_production(train.features[columns], train, config)
        return personalization.apply(
            validate.participant_ids, blend.predict_deltas(validate.features[columns])
        )

    def predict_curves(train, validate, config):
        from research.advanced.trajectory import FpcaBasis, TrajectoryHead

        predictions = predict(train, validate, config)
        head = TrajectoryHead(FpcaBasis(4), "ridge")
        episodes = [_EPISODES[(str(p), str(m))] for p, m in
                    zip(train.participant_ids.tolist(), train.meal_ids.tolist())]
        head.fit(_View(train.features[columns], train), episodes, config)
        curves = head.predict_curves(validate.features[columns])

        # Anchor the functional shape so it passes through the production horizon
        # predictions: the overlay changes SHAPE only, never the horizon values.
        grid = np.asarray(FORECAST_MINUTES, dtype="float64")
        knots = np.asarray([0, *HORIZONS], dtype="float64")
        for i in range(curves.shape[0]):
            at_knots = np.interp(knots, grid, curves[i])
            wanted = np.asarray([0.0, *[predictions[h][i] for h in HORIZONS]], dtype="float64")
            curves[i] = curves[i] + np.interp(grid, knots, wanted - at_knots)
        return curves

    return Candidate(
        name, predict, f"{len(columns)} features",
        fit_predict_curves=predict_curves if overlay else None,
    )


def main(argv: list[str] | None = None) -> int:
    global _EPISODES

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--n-folds", type=int, default=4)
    args = parser.parse_args(argv)

    from ml.experiments import load_dev_pool

    dev, meta = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    _EPISODES = {(e.participant_id, e.meal_id): e for e in dev}
    print(f"dev pool {len(dev)} | reserved test NOT loaded: {meta['reserved_test']}")
    print(f"baseline columns {len(BASELINE_COLUMNS)} | retrieval columns {len(RETRIEVAL_COLUMNS)}")

    from ml.devvalidation import participant_holdout_folds

    report: dict[str, dict] = {}
    for regime, folds in (
        ("within_person", rolling_folds(dev, DEFAULT_CONFIG, n_folds=args.n_folds)),
        ("cold_start", participant_holdout_folds(dev, DEFAULT_CONFIG, n_folds=args.n_folds)),
    ):
        print(f"\n################ {regime} ################")
        print(f"folds: {[(f.index, len(f.train), len(f.validate)) for f in folds]}")
        candidates = [
            _candidate("A baseline (no retrieval)", BASELINE_COLUMNS),
            _candidate("B + retrieval", list(FEATURE_NAMES)),
            _candidate("C + retrieval + FPCA shape", list(FEATURE_NAMES), overlay=True),
        ]
        results = []
        for candidate in candidates:
            t0 = time.perf_counter()
            result = score_candidate(folds, candidate, DEFAULT_CONFIG)
            row = result.as_row()
            print(f"  {candidate.name:30s} MAE {result.mae:7.3f} RMSE {result.rmse:7.3f} "
                  f"traj {row['trajectory_mae']:6.2f} peak {row['peak_mae']:6.2f} "
                  f"ttp {row['ttp_mae']:6.2f} iAUC {row['iauc_mae']:7.1f} ({time.perf_counter()-t0:.0f}s)")
            results.append(result)

        print("\n" + format_table(results))

        baseline = results[0]
        rows = []
        print("\nparticipant-clustered paired bootstrap vs A:")
        for r in results:
            row = r.as_row()
            if r is baseline:
                rows.append({**row, "regime": regime, "delta": 0.0, "ci_low": None,
                             "ci_high": None, "excludes_zero": None})
                continue
            ci = paired_participant_bootstrap(baseline, r)
            print(f"  {r.name:30s} {ci['point']:+7.3f}  [{ci['ci_low']:+.3f}, {ci['ci_high']:+.3f}]   excl0={ci['excludes_zero']}")
            rows.append({**row, "regime": regime, "delta": ci["point"], "ci_low": ci["ci_low"],
                         "ci_high": ci["ci_high"], "excludes_zero": ci["excludes_zero"]})

        print("\nfew-shot MAE by observed own-history depth:")
        buckets = sorted({b for r in results for b in r.fewshot_errors})
        print("  " + "model".ljust(30) + "".join(b.split("_",1)[1].rjust(11) for b in buckets))
        for r in results:
            row = r.as_row()
            line = "  " + r.name.ljust(30)
            for b in buckets:
                v = row.get(f"fewshot_{b}")
                line += ("—" if v is None else f"{v:.2f}").rjust(11)
            print(line)

        report[regime] = {
            "rows": rows,
            "participant_mae": {r.name: r.participant_mae for r in results},
            "fold_mae": {r.name: r.fold_mae for r in results},
        }

    RESULTS.mkdir(parents=True, exist_ok=True)
    flat = [r for regime in report.values() for r in regime["rows"]]
    pd.DataFrame(flat).to_csv(RESULTS / "promotion_leaderboard.csv", index=False)
    (RESULTS / "promotion_detail.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nwrote {RESULTS}/promotion_leaderboard.csv")
    return 0


if __name__ == "__main__":
    from research.advanced.promote_eval import main as _main

    raise SystemExit(_main())
