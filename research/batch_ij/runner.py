"""Batch I -- learned gating -- and Batch J -- classical dynamical controls.

    python -m research.batch_ij.runner --db-url sqlite:///data/local/cgmacros_v2.db

BATCH I asks whether the fixed 50/50 Ridge+booster blend and the 5-meal retrieval
fallback should instead be decided per episode from prediction-time context.

BATCH J provides the classical sequence-model controls that make the neural results in
Batches B/D/E interpretable: if a regularized ARX or a Kalman state matches the neural
models, then "deep learning did not help" is really "temporal structure did not help".

RELATION TO EXISTING EVIDENCE (mission 11.2): Gate B1 already tested lagged glucose,
PCA, spectral and sequence-kernel features as static columns. Batch J is NOT a rerun:
every model here adds the EXOGENOUS meal input and/or a recursive state update, which
B1's static columns did not represent.

All gates read prediction-time information only. The 394 reserved episodes are never
loaded.
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
from ml.train import GeneralizedModel, episodes_to_matrices

RESULTS = pathlib.Path("research/batch_ij/results")

#: Context the gates may read. All are prediction-time features.
GATE_CONTEXT = [
    "q_retr_n", "q_retr_weight", "q_retr_mean_distance", "q_retr_curve_spread",
    "r_hist_n", "premeal_glucose_mg_dl", "cgm_slope_30min_mg_dl_per_min",
    "g_slope_15", "g_cv_60", "carbs_g", "minutes_since_last_cgm",
]
_AUDIT: dict[str, dict] = {}


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

    def valid_mask(self, h):
        return self.deltas[h].notna().to_numpy()


def _fit_parts(train, config: Config):
    """Ridge, booster and the calibration, fitted on the training fold."""
    ridge = GeneralizedModel().fit(train.features, train.deltas, config)
    booster = GbmModel().fit(_View(train.features, train), config)

    class _Blend:
        def predict_deltas(self, f):
            r, g = ridge.predict_deltas(f), booster.predict_deltas(f)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    personalization = _personalize(_Blend(), _View(train.features, train), config)
    return ridge, booster, personalization


def _oof_component_errors(train, config: Config):
    """Per-episode |error| of Ridge and booster from a participant-grouped inner CV.

    These are the gate's TRAINING LABELS: which component was locally better. Using
    in-sample errors would teach the gate to trust whichever component overfitted more.
    """
    from sklearn.model_selection import GroupKFold

    groups = train.participant_ids
    unique = np.unique(groups)
    splits = min(3, len(unique))
    n = len(train)
    ridge_err = np.full((n, len(HORIZONS)), np.nan)
    booster_err = np.full((n, len(HORIZONS)), np.nan)
    if splits < 2:
        return ridge_err, booster_err

    for inner_tr, inner_va in GroupKFold(n_splits=splits).split(train.features, groups=groups):
        sub = _View(train.features.iloc[inner_tr].reset_index(drop=True), train)
        sub.deltas = train.deltas.iloc[inner_tr].reset_index(drop=True)
        sub.participant_ids = groups[inner_tr]
        sub.meal_ids = train.meal_ids[inner_tr]
        sub.meal_timestamps = train.meal_timestamps[inner_tr]
        sub.baselines = train.baselines[inner_tr]
        ridge = GeneralizedModel().fit(sub.features, sub.deltas, config)
        booster = GbmModel().fit(sub, config)
        query = train.features.iloc[inner_va].reset_index(drop=True)
        rp, bp = ridge.predict_deltas(query), booster.predict_deltas(query)
        for j, h in enumerate(HORIZONS):
            obs = train.deltas[h].to_numpy("float64")[inner_va]
            ridge_err[inner_va, j] = np.abs(rp[h] - obs)
            booster_err[inner_va, j] = np.abs(bp[h] - obs)
    return ridge_err, booster_err


# --------------------------------------------------------------------------- #
# Batch I candidates
# --------------------------------------------------------------------------- #


def constant_blend() -> Candidate:
    def run(train, validate, config):
        ridge, booster, pers = _fit_parts(train, config)
        r, g = ridge.predict_deltas(validate.features), booster.predict_deltas(validate.features)
        return pers.apply(validate.participant_ids,
                          {h: 0.5 * (r[h] + g[h]) for h in HORIZONS})

    return Candidate("I0 CONTROL constant 50/50 blend", run, "fs-1.3")


def gated_blend(name: str, gate_kind: str, context: list[str] | None = None) -> Candidate:
    """I1/I3/I6 -- learn a per-episode Ridge weight from prediction-time context."""
    columns = context or GATE_CONTEXT

    def run(train, validate, config):
        ridge, booster, pers = _fit_parts(train, config)
        ridge_err, booster_err = _oof_component_errors(train, config)

        usable = np.isfinite(ridge_err).all(axis=1) & np.isfinite(booster_err).all(axis=1)
        r_v = ridge.predict_deltas(validate.features)
        g_v = booster.predict_deltas(validate.features)

        if gate_kind == "hard_threshold":
            # Trust the booster when retrieval evidence is thin, else the blend.
            thin = (validate.features["q_retr_n"].fillna(0) < 5).to_numpy()
            weights = np.where(thin, 0.0, 0.5)
        elif usable.sum() < 80:
            weights = np.full(len(validate), 0.5)
        else:
            # Label: the Ridge weight that would have minimised OOF error, in {0, .5, 1}.
            r_mean = ridge_err[usable].mean(axis=1)
            b_mean = booster_err[usable].mean(axis=1)
            label = np.where(r_mean < b_mean * 0.9, 1.0,
                             np.where(b_mean < r_mean * 0.9, 0.0, 0.5))
            X = train.features.loc[usable, columns].fillna(
                train.features[columns].median()).to_numpy()
            Q = validate.features[columns].fillna(
                train.features[columns].median()).to_numpy()
            if gate_kind == "linear":
                from sklearn.linear_model import Ridge as RidgeGate
                from sklearn.preprocessing import StandardScaler

                scaler = StandardScaler().fit(X)
                gate = RidgeGate(alpha=10.0).fit(scaler.transform(X), label)
                weights = np.clip(gate.predict(scaler.transform(Q)), 0.0, 1.0)
            elif gate_kind == "tree":
                from sklearn.tree import DecisionTreeRegressor

                gate = DecisionTreeRegressor(max_depth=3, min_samples_leaf=40,
                                             random_state=20261011).fit(X, label)
                weights = np.clip(gate.predict(Q), 0.0, 1.0)
            else:
                raise ValueError(gate_kind)

        _AUDIT[name] = {
            "weight_mean": float(np.mean(weights)),
            "weight_std": float(np.std(weights)),
            "pct_extreme": float(np.mean((weights <= 0.05) | (weights >= 0.95))),
            "pct_ridge_favoured": float(np.mean(weights > 0.5)),
        }
        blended = {h: weights * r_v[h] + (1.0 - weights) * g_v[h] for h in HORIZONS}
        return pers.apply(validate.participant_ids, blended)

    return Candidate(name, run, f"gate={gate_kind}")


def calibration_gate() -> Candidate:
    """I2 -- decide per episode whether to APPLY the participant calibration offset."""

    def run(train, validate, config):
        ridge, booster, pers = _fit_parts(train, config)
        r, g = ridge.predict_deltas(validate.features), booster.predict_deltas(validate.features)
        base = {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}
        adjusted = pers.apply(validate.participant_ids, base)
        # Apply the offset only where the participant has real evidence behind it.
        evidence = validate.features["r_hist_n"].fillna(0).to_numpy()
        apply_mask = evidence >= config.personalization.min_episodes
        _AUDIT["I2 calibration gate"] = {
            "pct_calibration_applied": float(apply_mask.mean()),
        }
        return {h: np.where(apply_mask, adjusted[h], base[h]) for h in HORIZONS}

    return Candidate("I2 calibration gate", run, "apply offset only with evidence")


def regime_experts(n_experts: int) -> Candidate:
    """I4/I5 -- separate blends per pre-meal dynamic regime, chosen at prediction time."""

    def run(train, validate, config):
        key = "g_slope_15"
        train_values = train.features[key].fillna(0.0).to_numpy()
        quantiles = np.quantile(train_values, np.linspace(0, 1, n_experts + 1)[1:-1])
        train_bin = np.digitize(train_values, quantiles)
        val_bin = np.digitize(validate.features[key].fillna(0.0).to_numpy(), quantiles)

        global_ridge, global_booster, pers = _fit_parts(train, config)
        out = {h: np.empty(len(validate)) for h in HORIZONS}
        usage = {}
        for b in range(n_experts):
            tr_sel = train_bin == b
            va_sel = val_bin == b
            usage[int(b)] = {"train": int(tr_sel.sum()), "validate": int(va_sel.sum())}
            if not va_sel.any():
                continue
            if tr_sel.sum() < 120:
                # Too little data for a specialist: fall back to the global experts.
                r = global_ridge.predict_deltas(validate.features[va_sel] if False else validate.features)
                g = global_booster.predict_deltas(validate.features)
                for h in HORIZONS:
                    out[h][va_sel] = 0.5 * (r[h][va_sel] + g[h][va_sel])
                continue
            sub = _View(train.features.loc[tr_sel].reset_index(drop=True), train)
            sub.deltas = train.deltas.loc[tr_sel].reset_index(drop=True)
            sub.participant_ids = train.participant_ids[tr_sel]
            sub.meal_ids = train.meal_ids[tr_sel]
            sub.meal_timestamps = train.meal_timestamps[tr_sel]
            sub.baselines = train.baselines[tr_sel]
            er, eb, _ = _fit_parts(sub, config)
            r = er.predict_deltas(validate.features)
            g = eb.predict_deltas(validate.features)
            for h in HORIZONS:
                out[h][va_sel] = 0.5 * (r[h][va_sel] + g[h][va_sel])
        _AUDIT[f"I{3+n_experts} {n_experts}-expert regime"] = {"expert_usage": usage}
        return pers.apply(validate.participant_ids, out)

    return Candidate(f"I{3+n_experts} {n_experts}-expert regime", run, f"split on g_slope_15")


# --------------------------------------------------------------------------- #
# Batch J candidates -- classical dynamics
# --------------------------------------------------------------------------- #

LAG_COLUMNS = None  # filled by main()


def arx_candidate(name: str, nonlinear: bool) -> Candidate:
    """J1/J2 -- (N)ARX: lagged glucose + EXOGENOUS meal input, predicting each horizon.

    Distinct from Gate B1's lag columns: B1 appended lags to fs-1.3 and refitted the
    blend. Here the ARX model is the predictor, fitted on lags plus the meal, which is
    the classical control the neural comparison needs.
    """

    def run(train, validate, config):
        cols = LAG_COLUMNS
        exog = ["carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal",
                "premeal_glucose_mg_dl"]
        X_tr = train.features[cols + exog].fillna(train.features[cols + exog].median())
        X_va = validate.features[cols + exog].fillna(train.features[cols + exog].median())
        out = {}
        for h in HORIZONS:
            mask = train.deltas[h].notna().to_numpy()
            y = train.deltas.loc[mask, h].to_numpy("float64")
            if nonlinear:
                from sklearn.ensemble import HistGradientBoostingRegressor

                model = HistGradientBoostingRegressor(
                    max_depth=3, learning_rate=0.05, max_iter=300, min_samples_leaf=25,
                    l2_regularization=1.0, random_state=20261011,
                ).fit(X_tr.loc[mask], y)
            else:
                from sklearn.linear_model import RidgeCV

                model = RidgeCV(alphas=(1.0, 10.0, 100.0, 1000.0)).fit(X_tr.loc[mask], y)
            out[h] = np.asarray(model.predict(X_va), dtype="float64")
        return out

    return Candidate(name, run, "lags + exogenous meal")


def kalman_state_candidate() -> Candidate:
    """J4 -- a scalar Kalman-filtered level/trend state appended to fs-1.3.

    A local-linear-trend filter is run causally over each episode's pre-meal lattice;
    its final level, trend and innovation variance become three features. This tests
    recursive state estimation, which B1's static summaries did not.
    """

    def run(train, validate, config):
        def features_for(matrices):
            cols = LAG_COLUMNS
            seq = matrices.features[cols].to_numpy("float64")
            out = np.zeros((seq.shape[0], 3))
            for i in range(seq.shape[0]):
                row = seq[i]
                observed = row[np.isfinite(row)]
                if observed.size < 3:
                    out[i] = [0.0, 0.0, 0.0]
                    continue
                level, trend, variance = observed[0], 0.0, 1.0
                q_level, q_trend, r_obs = 1.0, 0.05, 4.0
                innovations = []
                for value in observed[1:]:
                    level, trend = level + trend, trend
                    variance += q_level + q_trend
                    innovation = value - level
                    gain = variance / (variance + r_obs)
                    level += gain * innovation
                    trend += 0.3 * gain * innovation
                    variance *= (1 - gain)
                    innovations.append(innovation)
                out[i] = [level, trend, float(np.var(innovations)) if innovations else 0.0]
            return pd.DataFrame(out, columns=["kf_level", "kf_trend", "kf_innovar"])

        X_tr = pd.concat([train.features.reset_index(drop=True), features_for(train)], axis=1)
        X_va = pd.concat([validate.features.reset_index(drop=True), features_for(validate)], axis=1)
        ridge = GeneralizedModel().fit(X_tr, train.deltas, config)
        booster = GbmModel().fit(_View(X_tr, train), config)

        class _B:
            def predict_deltas(self, f):
                r, g = ridge.predict_deltas(f), booster.predict_deltas(f)
                return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

        b = _B()
        pers = _personalize(b, _View(X_tr, train), config)
        return pers.apply(validate.participant_ids, b.predict_deltas(X_va))

    return Candidate("J4 Kalman state -> fs-1.3", run, "+3 recursive state features")


def main() -> int:
    global LAG_COLUMNS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    args = parser.parse_args()

    from ml.experiments import load_dev_pool
    from research.batch_b.sequences import RawLags, extract_sequences, load_series

    dev, meta = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    stamps, values = load_series(args.db_url)
    seq, mask = extract_sequences(dev, stamps, values)
    rep = RawLags()
    LAG_COLUMNS = [c for c in rep.columns() if c.startswith("seq_lag_")]
    # Attach lag columns to every episode's FeatureRow so matrices carry them.
    for i, episode in enumerate(dev):
        for j, col in enumerate(LAG_COLUMNS):
            episode.features.values[col] = float(seq[i, j]) if np.isfinite(seq[i, j]) else np.nan

    import ml.features as F
    original = F.FEATURE_NAMES
    F.FEATURE_NAMES = tuple(list(original) + LAG_COLUMNS)

    print(f"dev {len(dev)} | lag columns {len(LAG_COLUMNS)} | reserved test NOT loaded {meta['reserved_test']}")

    candidates = [
        constant_blend(),
        gated_blend("I1 linear soft gate", "linear"),
        gated_blend("I3 retrieval-aware gate", "linear",
                    ["q_retr_n", "q_retr_weight", "q_retr_mean_distance",
                     "q_retr_curve_spread", "r_hist_n", "minutes_since_last_cgm"]),
        gated_blend("I6a tree gate", "tree"),
        gated_blend("I6b hard threshold gate", "hard_threshold"),
        calibration_gate(),
        regime_experts(2),
        regime_experts(3),
        arx_candidate("J1 regularized ARX", nonlinear=False),
        arx_candidate("J2 NARX (boosted)", nonlinear=True),
        kalman_state_candidate(),
    ]

    rows = []
    try:
        for regime, folds in (
            ("within_person", rolling_folds(dev, DEFAULT_CONFIG, 4)),
            ("cold_start", participant_holdout_folds(dev, DEFAULT_CONFIG, 4)),
        ):
            print(f"\n################ {regime} ################")
            results = []
            for candidate in candidates:
                t0 = time.perf_counter()
                try:
                    result = score_candidate(folds, candidate, DEFAULT_CONFIG)
                except Exception as error:
                    print(f"  {candidate.name:34s} FAILED {type(error).__name__}: {str(error)[:60]}")
                    continue
                print(f"  {candidate.name:34s} MAE {result.mae:7.3f} RMSE {result.rmse:7.3f} "
                      f"({time.perf_counter()-t0:4.0f}s)")
                results.append(result)
            print("\n" + format_table(results))
            baseline = results[0]
            print(f"\nparticipant-clustered paired bootstrap vs {baseline.name}:")
            for r in results:
                row = r.as_row()
                if r is baseline:
                    rows.append({"regime": regime, **row, "delta": 0.0,
                                 "ci_low": None, "ci_high": None, "excludes_zero": None})
                    continue
                ci = paired_participant_bootstrap(baseline, r)
                worst = min((baseline.participant_mae.get(p, 0) - v
                             for p, v in r.participant_mae.items()), default=np.nan)
                print(f"  {r.name:34s} {ci['point']:+7.3f}  [{ci['ci_low']:+.3f}, {ci['ci_high']:+.3f}]"
                      f"  excl0={str(ci['excludes_zero']):5s} worst_ppt {worst:+.2f}")
                rows.append({"regime": regime, **row, "delta": ci["point"],
                             "ci_low": ci["ci_low"], "ci_high": ci["ci_high"],
                             "excludes_zero": ci["excludes_zero"],
                             "worst_participant": round(float(worst), 3)})
    finally:
        F.FEATURE_NAMES = original

    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(RESULTS / "ij_leaderboard.csv", index=False)
    (RESULTS / "gate_audit.json").write_text(json.dumps(_AUDIT, indent=2, default=str) + "\n")
    print(f"\nwrote {RESULTS}/ij_leaderboard.csv")
    print("gate audit:", json.dumps(_AUDIT, indent=1, default=str)[:900])
    return 0


if __name__ == "__main__":
    from research.batch_ij.runner import main as _main
    raise SystemExit(_main())
