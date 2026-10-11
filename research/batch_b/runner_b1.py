"""Gate B1: do non-neural sequence representations add anything to fs-1.3?

    python -m research.batch_b.runner_b1 --db-url sqlite:///data/local/cgmacros_v2.db

Every candidate is the fs-1.3 production family with sequence columns APPENDED, scored
on the same folds and episodes as the control. Representations that must be fitted (PCA,
kernel landmarks) are fitted inside each training fold.

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
from ml.config import DEFAULT_CONFIG, HORIZONS, Config
from ml.devvalidation import (
    Candidate,
    format_table,
    paired_participant_bootstrap,
    participant_holdout_folds,
    rolling_folds,
    score_candidate,
)
from ml.features import FEATURE_NAMES
from ml.train import EpisodeMatrices, GeneralizedModel
from research.batch_b.sequences import (
    RawLags,
    SequenceKernel,
    SequencePca,
    ShapeDescriptors,
    SpectralFeatures,
    extract_sequences,
    load_series,
)

RESULTS = pathlib.Path("research/batch_b/results")

_SEQ: dict[tuple[str, str], np.ndarray] = {}
_MASK: dict[tuple[str, str], np.ndarray] = {}


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


def _keys(m) -> list[tuple[str, str]]:
    return [(str(p), str(x)) for p, x in zip(m.participant_ids.tolist(), m.meal_ids.tolist())]


def _stack(m):
    keys = _keys(m)
    width = next(iter(_SEQ.values())).size
    seq = np.vstack([_SEQ.get(k, np.full(width, np.nan)) for k in keys])
    mask = np.vstack([_MASK.get(k, np.zeros(width)) for k in keys])
    return seq, mask


def _fit_blend(features: pd.DataFrame, source, config: Config):
    ridge = GeneralizedModel().fit(features, source.deltas, config)
    booster = GbmModel().fit(_View(features, source), config)

    class _Blend:
        def predict_deltas(self, frame):
            r = ridge.predict_deltas(frame)
            g = booster.predict_deltas(frame)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    blend = _Blend()
    return blend, _personalize(blend, _View(features, source), config)


def control_candidate() -> Candidate:
    def run(train, validate, config):
        blend, personalization = _fit_blend(train.features, train, config)
        return personalization.apply(
            validate.participant_ids, blend.predict_deltas(validate.features)
        )

    return Candidate("B0 CONTROL fs-1.3", run, f"{len(FEATURE_NAMES)} features")


def sequence_candidate(factory) -> Candidate:
    probe = factory()

    def run(train, validate, config):
        representation = factory()
        train_seq, train_mask = _stack(train)
        representation.fit(train_seq, train_mask)
        columns = representation.columns()

        train_X = pd.concat(
            [train.features.reset_index(drop=True),
             pd.DataFrame(representation.transform(train_seq, train_mask), columns=columns)],
            axis=1,
        )
        val_seq, val_mask = _stack(validate)
        val_X = pd.concat(
            [validate.features.reset_index(drop=True),
             pd.DataFrame(representation.transform(val_seq, val_mask), columns=columns)],
            axis=1,
        )
        blend, personalization = _fit_blend(train_X, train, config)
        return personalization.apply(validate.participant_ids, blend.predict_deltas(val_X))

    return Candidate(f"+ {probe.name}", run, f"+{len(probe.columns())} columns")


def main(argv: list[str] | None = None) -> int:
    global _SEQ, _MASK

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--n-folds", type=int, default=4)
    args = parser.parse_args(argv)

    from ml.experiments import load_dev_pool

    dev, meta = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    stamps, values = load_series(args.db_url)
    sequences, masks = extract_sequences(dev, stamps, values)
    _SEQ = {(e.participant_id, e.meal_id): sequences[i] for i, e in enumerate(dev)}
    _MASK = {(e.participant_id, e.meal_id): masks[i] for i, e in enumerate(dev)}
    print(f"dev pool {len(dev)} | sequences {sequences.shape} | mean coverage {masks.mean():.3f}")
    print(f"reserved test NOT loaded: {meta['reserved_test']}")

    candidates = [
        control_candidate(),
        sequence_candidate(RawLags),
        sequence_candidate(lambda: SequencePca(6)),
        sequence_candidate(lambda: SpectralFeatures(6)),
        sequence_candidate(lambda: SequenceKernel(12)),
        sequence_candidate(ShapeDescriptors),
    ]

    report = {}
    for regime, folds in (
        ("within_person", rolling_folds(dev, DEFAULT_CONFIG, n_folds=args.n_folds)),
        ("cold_start", participant_holdout_folds(dev, DEFAULT_CONFIG, n_folds=args.n_folds)),
    ):
        print(f"\n################ {regime} ################")
        results = []
        for candidate in candidates:
            t0 = time.perf_counter()
            result = score_candidate(folds, candidate, DEFAULT_CONFIG)
            row = result.as_row()
            print(f"  {candidate.name:34s} MAE {result.mae:7.3f} RMSE {result.rmse:7.3f} "
                  f"peak {row['peak_mae']:6.2f} ttp {row['ttp_mae']:6.2f} ({time.perf_counter()-t0:4.0f}s)")
            results.append(result)
        print("\n" + format_table(results))
        baseline = results[0]
        rows = []
        print(f"\nparticipant-clustered paired bootstrap vs {baseline.name}:")
        for r in results:
            row = r.as_row()
            if r is baseline:
                rows.append({**row, "regime": regime, "delta": 0.0, "ci_low": None,
                             "ci_high": None, "excludes_zero": None, "worst_participant": None})
                continue
            ci = paired_participant_bootstrap(baseline, r)
            worst = min((baseline.participant_mae.get(p, 0) - v for p, v in r.participant_mae.items()), default=None)
            print(f"  {r.name:34s} {ci['point']:+7.3f}  [{ci['ci_low']:+.3f}, {ci['ci_high']:+.3f}]"
                  f"  excl0={str(ci['excludes_zero']):5s} worst_ppt {worst:+.2f}")
            rows.append({**row, "regime": regime, "delta": ci["point"], "ci_low": ci["ci_low"],
                         "ci_high": ci["ci_high"], "excludes_zero": ci["excludes_zero"],
                         "worst_participant": round(worst, 3) if worst is not None else None})
        report[regime] = rows

    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([r for rows in report.values() for r in rows]).to_csv(
        RESULTS / "b1_leaderboard.csv", index=False)
    print(f"\nwrote {RESULTS}/b1_leaderboard.csv")
    return 0


if __name__ == "__main__":
    from research.batch_b.runner_b1 import main as _main

    raise SystemExit(_main())
