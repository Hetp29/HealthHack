"""Batch C leaderboard: shape-constrained trajectory reconstruction.

    python -m research.batch_c.runner --db-url sqlite:///data/local/cgmacros_v2.db

EFFICIENCY NOTE: every reconstruction consumes the SAME base horizon predictions, so
the fs-1.3 base model is fitted ONCE per fold and all reconstructions are then compared
against each other on identical predictions. That makes the comparison exactly paired
and costs one base fit per fold instead of one per candidate.

Scored per candidate:
  * horizon MAE (must be unchanged for horizon-preserving methods);
  * full-trajectory MAE against NATIVE observed CGM;
  * peak MAE, time-to-peak MAE, iAUC MAE;
  * turning points (>1 means non-physiological oscillation);
  * overshoot beyond the predicted knot envelope;
  * max horizon deviation (the horizon-preservation audit).

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
from ml.devvalidation import participant_holdout_folds, rolling_folds
from ml.metrics import incremental_auc, summarize_curve, summarize_observed
from ml.train import GeneralizedModel, episodes_to_matrices
from research.batch_c import reconstruct as R

RESULTS = pathlib.Path("research/batch_c/results")
GRID = list(FORECAST_MINUTES)


class _View:
    def __init__(self, features, source):
        self.features = features
        self.deltas = source.deltas
        self.participant_ids = source.participant_ids
        self.baselines = source.baselines
        self.meal_timestamps = source.meal_timestamps
        self.meal_ids = source.meal_ids

    def __len__(self):
        return len(self.features)

    def valid_mask(self, horizon):
        return self.deltas[horizon].notna().to_numpy()


def _fit_base(train, config: Config):
    ridge = GeneralizedModel().fit(train.features, train.deltas, config)
    booster = GbmModel().fit(_View(train.features, train), config)

    class _Blend:
        def predict_deltas(self, frame):
            r = ridge.predict_deltas(frame)
            g = booster.predict_deltas(frame)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    blend = _Blend()
    return blend, _personalize(blend, _View(train.features, train), config)


def _observed_peak_times(episodes) -> np.ndarray:
    """Observed time-to-peak from NATIVE CGM, or NaN when coverage is too thin."""
    out = np.full(len(episodes), np.nan)
    for i, episode in enumerate(episodes):
        summary = summarize_observed(
            episode.target.observed_curve, episode.target.baseline_glucose_mg_dl
        )
        if summary is not None:
            out[i] = float(summary.time_to_peak_minutes)
    return out


def _score(curves: np.ndarray, deltas: np.ndarray, episodes, baselines: np.ndarray, config):
    """Curve-quality metrics for one reconstruction."""
    traj_err, peak_err, ttp_err, iauc_err = [], [], [], []
    lo = config.model.clip_glucose_min_mg_dl
    hi = config.model.clip_glucose_max_mg_dl

    for i, episode in enumerate(episodes):
        baseline = baselines[i]
        values = np.clip(baseline + curves[i], lo, hi)
        values[0] = baseline
        by_minute = dict(zip(GRID, values))
        for minute, actual in episode.target.observed_curve.items():
            if minute == 0 or minute not in by_minute:
                continue
            traj_err.append(float(by_minute[minute] - actual))

        observed = summarize_observed(episode.target.observed_curve, baseline)
        if observed is None:
            continue
        predicted = summarize_curve(GRID, list(values), baseline)
        peak_err.append(predicted.peak_mg_dl - observed.peak_mg_dl)
        ttp_err.append(float(predicted.time_to_peak_minutes - observed.time_to_peak_minutes))
        iauc_err.append(
            predicted.incremental_auc_mg_dl_min - observed.incremental_auc_mg_dl_min
        )

    return {
        "trajectory_mae": float(np.mean(np.abs(traj_err))) if traj_err else np.nan,
        "peak_mae": float(np.mean(np.abs(peak_err))) if peak_err else np.nan,
        "ttp_mae": float(np.mean(np.abs(ttp_err))) if ttp_err else np.nan,
        "iauc_mae": float(np.mean(np.abs(iauc_err))) if iauc_err else np.nan,
        "n_curve": len(peak_err),
        "turning_points_mean": float(R.turning_points(curves).mean()),
        "pct_over_1_turn": float((R.turning_points(curves) > 1).mean()),
        "pct_over_3_turns": float((R.turning_points(curves) > 3).mean()),
        "overshoot_mean": float(R.overshoot(curves, deltas).mean()),
        "overshoot_max": float(R.overshoot(curves, deltas).max()),
        "horizon_dev_max": float(R.horizon_deviation(curves, deltas).max()),
        "n_nonfinite": int((~np.isfinite(curves)).sum()),
    }


def _horizon_mae(curves: np.ndarray, matrices) -> float:
    """Horizon MAE implied by the reconstructed curve (changes only for C3/C6)."""
    errors = []
    for j, h in enumerate(HORIZONS):
        observed = matrices.deltas[h].to_numpy(dtype="float64")
        mask = matrices.valid_mask(h)
        errors.extend(np.abs(curves[mask, GRID.index(h)] - observed[mask]))
    return float(np.mean(errors)) if errors else np.nan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--n-folds", type=int, default=4)
    args = parser.parse_args(argv)

    from ml.experiments import load_dev_pool

    dev, meta = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    print(f"dev pool {len(dev)} | reserved test NOT loaded: {meta['reserved_test']}")
    config = DEFAULT_CONFIG

    rows = []
    for regime, folds in (
        ("within_person", rolling_folds(dev, config, n_folds=args.n_folds)),
        ("cold_start", participant_holdout_folds(dev, config, n_folds=args.n_folds)),
    ):
        print(f"\n################ {regime} ################")
        accum: dict[str, list[dict]] = {}
        for fold in folds:
            train = episodes_to_matrices(fold.train)
            validate = episodes_to_matrices(fold.validate)
            blend, personalization = _fit_base(train, config)
            deltas_map = personalization.apply(
                validate.participant_ids, blend.predict_deltas(validate.features)
            )
            deltas = np.column_stack([deltas_map[h] for h in HORIZONS])

            # --- reconstructions -------------------------------------------- #
            base_curves = R.linear(deltas)
            unimodal, _ = R.unimodal_fit(deltas)
            curvature = R.curvature_spline(deltas)
            variants = {
                "BASE linear (fs-1.3)": base_curves,
                "C1 PCHIP": R.pchip(deltas),
                "C2 curvature spline": curvature,
                "C3 unimodal surge": unimodal,
                "C4 gated unimodal/flexible": R.gated(deltas, curvature, unimodal),
            }

            # C5: learned anchored correction, trained on OOF base residuals.
            train_deltas_map = personalization.apply(
                train.participant_ids, blend.predict_deltas(train.features)
            )
            train_deltas = np.column_stack([train_deltas_map[h] for h in HORIZONS])
            train_base = R.linear(train_deltas)
            target_curves, usable = [], []
            for i, episode in enumerate(fold.train):
                observed = episode.target.observed_curve
                if len(observed) < 20:
                    continue
                baseline = episode.target.baseline_glucose_mg_dl
                minutes = np.asarray(sorted(observed), dtype="float64")
                values = np.asarray([observed[int(m)] - baseline for m in minutes])
                target_curves.append(np.interp(np.asarray(GRID, dtype="float64"), minutes, values))
                usable.append(i)
            if len(usable) >= 60:
                from sklearn.linear_model import Ridge

                residual_target = np.vstack(target_curves) - train_base[usable]
                X = train.features.iloc[usable].fillna(
                    train.features.median(numeric_only=True)
                )
                corrector = Ridge(alpha=100.0).fit(X, residual_target)
                query = validate.features.fillna(train.features.median(numeric_only=True))
                variants["C5 learned anchored correction"] = R.anchored_correction(
                    base_curves, np.asarray(corrector.predict(query), dtype="float64")
                )

            # C6: direct peak-time prediction guiding the unimodal retiming.
            observed_ttp = _observed_peak_times(fold.train)
            ttp_mask = np.isfinite(observed_ttp)
            if ttp_mask.sum() >= 60:
                from sklearn.ensemble import HistGradientBoostingRegressor

                ttp_model = HistGradientBoostingRegressor(
                    max_depth=3, learning_rate=0.05, max_iter=200,
                    min_samples_leaf=25, l2_regularization=1.0,
                    random_state=20261011,
                ).fit(train.features.loc[ttp_mask], observed_ttp[ttp_mask])
                predicted_ttp = np.asarray(
                    ttp_model.predict(validate.features), dtype="float64"
                )
                variants["C6 direct peak-time + unimodal"] = R.retimed_unimodal(
                    deltas, predicted_ttp
                )

            for name, curves in variants.items():
                scores = _score(curves, deltas, fold.validate, validate.baselines, config)
                scores["horizon_mae"] = _horizon_mae(curves, validate)
                accum.setdefault(name, []).append(scores)

        print(f"{'candidate':32s}{'horMAE':>8s}{'trajMAE':>9s}{'peak':>8s}{'ttp':>8s}"
              f"{'iAUC':>9s}{'turns':>7s}{'>1turn':>8s}{'oversh':>8s}{'horDev':>9s}")
        for name, folds_scores in accum.items():
            mean = {k: float(np.nanmean([s[k] for s in folds_scores])) for k in folds_scores[0]}
            print(f"{name:32s}{mean['horizon_mae']:8.3f}{mean['trajectory_mae']:9.3f}"
                  f"{mean['peak_mae']:8.2f}{mean['ttp_mae']:8.2f}{mean['iauc_mae']:9.1f}"
                  f"{mean['turning_points_mean']:7.2f}{mean['pct_over_1_turn']:8.1%}"
                  f"{mean['overshoot_mean']:8.2f}{mean['horizon_dev_max']:9.2e}")
            rows.append({"regime": regime, "candidate": name, **mean})

    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(RESULTS / "batch_c_leaderboard.csv", index=False)
    print(f"\nwrote {RESULTS}/batch_c_leaderboard.csv")
    return 0


if __name__ == "__main__":
    from research.batch_c.runner import main as _main

    raise SystemExit(_main())
