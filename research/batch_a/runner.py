"""Batch A leaderboard: few-shot personalization candidates vs the fs-1.3 control.

    python -m research.batch_a.runner --db-url sqlite:///data/local/cgmacros_v2.db

Every candidate is the fs-1.3 production family (Ridge + booster blend, then shrunk
participant calibration) and differs ONLY in what is appended to, removed from, or
added on top of the production features. Candidates are scored on identical folds and
identical episodes, so differences are attributable to the candidate.

Residual learners are fitted on OUT-OF-FOLD residuals: inside each training fold a
participant-grouped inner CV produces base predictions for episodes the inner model did
not train on, and the residual learner is fitted on those. Fitting a residual learner on
in-sample residuals would teach it to undo the base model's own fit rather than its
generalization error.

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
from research.batch_a.encoders import (
    AttentionEncoder,
    DeepSetsEncoder,
    KernelRetrieval,
    LearnedMetricRetrieval,
    ShuffledNeighbourControl,
)
from research.batch_a.support import build_support_sets

RESULTS = pathlib.Path("research/batch_a/results")
RETRIEVAL_COLUMNS = [c for c in FEATURE_NAMES if c.startswith("q_retr_")]
FINGERPRINT_COLUMNS = [c for c in FEATURE_NAMES if c.startswith("r_hist_")]
NO_CONTEXT_COLUMNS = [
    c for c in FEATURE_NAMES if not c.startswith(("q_retr_", "r_hist_"))
]

_SUPPORT: dict[tuple[str, str], object] = {}

#: Cap on GP residual training rows. See the note in `residual_candidate`.
GP_MAX_TRAIN = 400


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


def _keys(matrices) -> list[tuple[str, str]]:
    return [
        (str(p), str(m))
        for p, m in zip(matrices.participant_ids.tolist(), matrices.meal_ids.tolist())
    ]


def _targets(matrices) -> np.ndarray:
    return np.column_stack([matrices.deltas[h].to_numpy(dtype="float64") for h in HORIZONS])


def _fit_blend(features: pd.DataFrame, source, config: Config):
    ridge = GeneralizedModel().fit(features, source.deltas, config)
    booster = GbmModel().fit(_View(features, source), config)

    class _Blend:
        def predict_deltas(self, frame):
            r = ridge.predict_deltas(frame)
            g = booster.predict_deltas(frame)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    blend = _Blend()
    personalization = _personalize(blend, _View(features, source), config)
    return blend, personalization


def _encode_columns(matrices, encoder, prefix: str) -> pd.DataFrame:
    """Append an encoder's four horizon predictions as extra feature columns."""
    rows = np.vstack([
        encoder.encode(_SUPPORT[key]) if key in _SUPPORT else np.full(len(HORIZONS), np.nan)
        for key in _keys(matrices)
    ])
    extra = pd.DataFrame(
        rows, columns=[f"{prefix}_{h}" for h in HORIZONS]
    )
    return pd.concat([matrices.features.reset_index(drop=True), extra], axis=1)


# --------------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------------- #


def control(name: str, columns: list[str]) -> Candidate:
    def run(train, validate, config):
        blend, personalization = _fit_blend(train.features[columns], train, config)
        return personalization.apply(
            validate.participant_ids, blend.predict_deltas(validate.features[columns])
        )

    return Candidate(name, run, f"{len(columns)} features")


def encoder_candidate(name: str, factory, prefix: str) -> Candidate:
    def run(train, validate, config):
        encoder = factory()
        episodes = [_EPISODES[key] for key in _keys(train)]
        encoder.fit(_SUPPORT, episodes, _targets(train))
        train_X = _encode_columns(train, encoder, prefix)
        val_X = _encode_columns(validate, encoder, prefix)
        blend, personalization = _fit_blend(train_X, train, config)
        return personalization.apply(
            validate.participant_ids, blend.predict_deltas(val_X)
        )

    return Candidate(name, run, "fs-1.3 + 4 encoded columns")


def shuffled_control() -> Candidate:
    """fs-1.3 with the retrieval columns recomputed from ANOTHER participant's history."""

    def run(train, validate, config):
        encoder = ShuffledNeighbourControl()
        encoder.fit(_SUPPORT, None, None)
        keep = [c for c in FEATURE_NAMES if c not in RETRIEVAL_COLUMNS]
        train_X = _encode_columns(_View(train.features[keep], train), encoder, "q_shuf")
        val_X = _encode_columns(_View(validate.features[keep], validate), encoder, "q_shuf")
        blend, personalization = _fit_blend(train_X, train, config)
        return personalization.apply(
            validate.participant_ids, blend.predict_deltas(val_X)
        )

    return Candidate(
        "C1 control: shuffled neighbours", run, "retrieval from another participant"
    )


def residual_candidate(name: str, kind: str, inner_splits: int = 2) -> Candidate:
    """Base blend plus a residual learner fitted on OUT-OF-FOLD residuals."""

    def run(train, validate, config):
        from sklearn.model_selection import GroupKFold

        features = train.features
        targets = _targets(train)
        groups = train.participant_ids
        unique = np.unique(groups)
        splits = min(inner_splits, len(unique))

        oof = np.full_like(targets, np.nan)
        if splits >= 2:
            for inner_train, inner_val in GroupKFold(n_splits=splits).split(
                features, groups=groups
            ):
                inner_src = _View(features.iloc[inner_train].reset_index(drop=True), train)
                inner_src.deltas = train.deltas.iloc[inner_train].reset_index(drop=True)
                inner_src.participant_ids = groups[inner_train]
                inner_src.meal_ids = train.meal_ids[inner_train]
                inner_src.meal_timestamps = train.meal_timestamps[inner_train]
                inner_src.baselines = train.baselines[inner_train]
                blend, personalization = _fit_blend(inner_src.features, inner_src, config)
                predicted = personalization.apply(
                    groups[inner_val],
                    blend.predict_deltas(features.iloc[inner_val].reset_index(drop=True)),
                )
                for j, h in enumerate(HORIZONS):
                    oof[inner_val, j] = predicted[h]

        base_blend, base_personalization = _fit_blend(features, train, config)
        predictions = base_personalization.apply(
            validate.participant_ids, base_blend.predict_deltas(validate.features)
        )

        usable = np.isfinite(oof).all(axis=1) & np.isfinite(targets).all(axis=1)
        if usable.sum() < 80:
            return predictions

        residuals = targets[usable] - oof[usable]
        design = features.loc[usable].fillna(features.median(numeric_only=True)).to_numpy()
        query = validate.features.fillna(features.median(numeric_only=True)).to_numpy()

        from sklearn.preprocessing import StandardScaler

        scaler = StandardScaler().fit(design)
        design_s, query_s = scaler.transform(design), scaler.transform(query)

        if kind == "kernel_ridge":
            from sklearn.kernel_ridge import KernelRidge

            model = KernelRidge(alpha=10.0, kernel="rbf", gamma=1.0 / design_s.shape[1])
        else:
            from sklearn.gaussian_process import GaussianProcessRegressor
            from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel

            # Kernel hyperparameters are FIXED (optimizer=None) and the training set is
            # capped. Measured reason: with marginal-likelihood optimisation enabled this
            # candidate alone ran past 8.5 minutes without finishing a single regime --
            # exact GP inference is O(n^3) per optimiser iteration, times four outputs,
            # inside an inner CV, inside the outer folds. A fixed-kernel GP is still a
            # GP residual learner; an unbounded one was simply not affordable here, and
            # that is reported rather than quietly dropped.
            if design_s.shape[0] > GP_MAX_TRAIN:
                rng = np.random.default_rng(20261011)
                pick = rng.choice(design_s.shape[0], GP_MAX_TRAIN, replace=False)
                design_s, residuals = design_s[pick], residuals[pick]
            kernel = ConstantKernel(1.0) * RBF(
                length_scale=np.sqrt(design_s.shape[1])
            ) + WhiteKernel(1.0)
            model = GaussianProcessRegressor(
                kernel=kernel,
                alpha=1e-6,
                normalize_y=True,
                optimizer=None,
                random_state=20261011,
            )
        model.fit(design_s, residuals)
        correction = np.asarray(model.predict(query_s), dtype="float64")
        return {h: predictions[h] + correction[:, j] for j, h in enumerate(HORIZONS)}

    return Candidate(name, run, f"OOF residual, {kind}")


def hybrid_candidate() -> Candidate:
    """Best encoder plus the kernel-ridge residual on top of fs-1.3."""

    def run(train, validate, config):
        encoder = KernelRetrieval()
        episodes = [_EPISODES[key] for key in _keys(train)]
        encoder.fit(_SUPPORT, episodes, _targets(train))
        train_X = _encode_columns(train, encoder, "q_kern")
        val_X = _encode_columns(validate, encoder, "q_kern")

        residual = residual_candidate("tmp", "kernel_ridge")
        base = _View(train_X, train)
        base.deltas = train.deltas
        validate_view = _View(val_X, validate)
        validate_view.deltas = validate.deltas
        return residual.fit_predict(base, validate_view, config)

    return Candidate("A7 hybrid: retrieval + OOF residual", run, "encoder + residual")


_EPISODES: dict[tuple[str, str], object] = {}


def main(argv: list[str] | None = None) -> int:
    global _SUPPORT, _EPISODES

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--n-folds", type=int, default=4)
    args = parser.parse_args(argv)

    from ml.experiments import load_dev_pool

    dev, meta = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    _SUPPORT = build_support_sets(dev)
    _EPISODES = {(e.participant_id, e.meal_id): e for e in dev}
    sizes = np.array([s.n for s in _SUPPORT.values()])
    print(f"dev pool {len(dev)} episodes, {len({e.participant_id for e in dev})} participants")
    print(f"support sets: median {int(np.median(sizes))} items, {(sizes == 0).sum()} empty")
    print(f"reserved test NOT loaded: {meta['reserved_test']}")

    candidates = [
        control("A0 CONTROL fs-1.3", list(FEATURE_NAMES)),
        encoder_candidate("A1 + DeepSets (fixed phi)", DeepSetsEncoder, "q_ds"),
        encoder_candidate("A2 + attention (learned metric)", AttentionEncoder, "q_attn"),
        encoder_candidate("A3 + learned-metric retrieval", LearnedMetricRetrieval, "q_lm"),
        encoder_candidate("A4 + kernel retrieval (explicit)", KernelRetrieval, "q_kern"),
        residual_candidate("A5 + kernel-ridge OOF residual", "kernel_ridge"),
        residual_candidate("A6 + GP OOF residual", "gp"),
        hybrid_candidate(),
        shuffled_control(),
        control("C2 control: no personal context", NO_CONTEXT_COLUMNS),
    ]

    report: dict[str, list[dict]] = {}
    for regime, folds in (
        ("within_person", rolling_folds(dev, DEFAULT_CONFIG, n_folds=args.n_folds)),
        ("cold_start", participant_holdout_folds(dev, DEFAULT_CONFIG, n_folds=args.n_folds)),
    ):
        print(f"\n################ {regime} ################")
        print(f"folds: {[(f.index, len(f.train), len(f.validate)) for f in folds]}")
        results, failures = [], []
        for candidate in candidates:
            try:
                t0 = time.perf_counter()
                result = score_candidate(folds, candidate, DEFAULT_CONFIG)
                row = result.as_row()
                print(f"  {candidate.name:34s} MAE {result.mae:7.3f} RMSE {result.rmse:7.3f} "
                      f"peak {row['peak_mae']:6.2f} ttp {row['ttp_mae']:6.2f} "
                      f"({time.perf_counter()-t0:5.0f}s)")
                results.append(result)
            except Exception as error:
                print(f"  {candidate.name:34s} FAILED {type(error).__name__}: {str(error)[:70]}")
                failures.append({"candidate": candidate.name, "error": f"{type(error).__name__}: {error}"})

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
            worst = min(
                (baseline.participant_mae.get(p, 0) - v for p, v in r.participant_mae.items()),
                default=None,
            )
            print(f"  {r.name:34s} {ci['point']:+7.3f}  [{ci['ci_low']:+.3f}, {ci['ci_high']:+.3f}]"
                  f"  excl0={str(ci['excludes_zero']):5s} worst_participant {worst:+.2f}")
            rows.append({**row, "regime": regime, "delta": ci["point"], "ci_low": ci["ci_low"],
                         "ci_high": ci["ci_high"], "excludes_zero": ci["excludes_zero"],
                         "worst_participant": round(worst, 3) if worst is not None else None})

        print("\nfew-shot MAE by observed own-history depth:")
        buckets = sorted({b for r in results for b in r.fewshot_errors})
        print("  " + "model".ljust(34) + "".join(b.split("_", 1)[1].rjust(10) for b in buckets))
        for r in results:
            row = r.as_row()
            line = "  " + r.name.ljust(34)
            for b in buckets:
                v = row.get(f"fewshot_{b}")
                line += ("—" if v is None else f"{v:.2f}").rjust(10)
            print(line)

        report[regime] = rows
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"failures_{regime}.json").write_text(json.dumps(failures, indent=2) + "\n")

    flat = [r for rows in report.values() for r in rows]
    pd.DataFrame(flat).to_csv(RESULTS / "batch_a_leaderboard.csv", index=False)
    print(f"\nwrote {RESULTS}/batch_a_leaderboard.csv")
    return 0


if __name__ == "__main__":
    from research.batch_a.runner import main as _main

    raise SystemExit(_main())
