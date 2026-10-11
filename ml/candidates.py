"""Model candidates for development validation.

Each candidate fits on a fold's TRAINING matrices and returns predicted horizon deltas
for the validation matrices. None of them may read a validation target.

Families:
  A. `ridge`            -- the current generalized model (CONTROL)
  B. `ridge_personal`   -- the current personalized model (BASELINE to beat)
  C. `gbm`              -- HistGradientBoostingRegressor, heavily regularized
  D. `gbm_personal`     -- C plus the same shrunk participant calibration
  plus `persistence` and `cohort_mean` reference points, personalization variants,
  and feature-ablation wrappers.

`HistGradientBoostingRegressor` is used rather than LightGBM/CatBoost/XGBoost: it ships
with scikit-learn (already pinned), handles NaN natively so no imputer is needed, and
adding a new binary dependency this close to the deadline is a risk with no measured
payoff. The master specification names it explicitly as the permitted boosting option
"when enough independent meals" exist.

With ~900 training episodes and 16 features the tree settings are deliberately
conservative -- shallow, slow, large leaves, strong L2 -- because the binding constraint
is sample size, not model capacity.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from ml.baselines import CohortMeanDeltaBaseline, PersistenceBaseline
from ml.config import HORIZONS, Config
from ml.devvalidation import Candidate
from ml.personalization import PersonalizationModel
from ml.train import GeneralizedModel, EpisodeMatrices


# --------------------------------------------------------------------------- #
# Reference points
# --------------------------------------------------------------------------- #


def _persistence(train, validate, config):
    return PersistenceBaseline().fit(train.features, train.deltas).predict_deltas(
        validate.features
    )


def _cohort_mean(train, validate, config):
    return (
        CohortMeanDeltaBaseline()
        .fit(train.features, train.deltas)
        .predict_deltas(validate.features)
    )


# --------------------------------------------------------------------------- #
# A/B: Ridge, with and without participant calibration
# --------------------------------------------------------------------------- #


def _fit_ridge(train: EpisodeMatrices, config: Config, columns: list[str] | None = None):
    features = train.features if columns is None else train.features[columns]
    return GeneralizedModel().fit(features, train.deltas, config)


def _personalize(
    model, train: EpisodeMatrices, config: Config, columns: list[str] | None = None
) -> PersonalizationModel:
    """Shrunk per-participant offsets from TRAINING-fold residuals only."""
    features = train.features if columns is None else train.features[columns]
    in_sample = model.predict_deltas(features)
    residuals = {
        h: train.deltas[h].to_numpy(dtype="float64") - in_sample[h] for h in HORIZONS
    }
    masks = {h: train.valid_mask(h) for h in HORIZONS}
    return PersonalizationModel(config.personalization).fit(
        train.participant_ids, residuals, valid_masks=masks
    )


def _ridge(train, validate, config):
    model = _fit_ridge(train, config)
    return model.predict_deltas(validate.features)


def _ridge_personal(train, validate, config):
    model = _fit_ridge(train, config)
    personalization = _personalize(model, train, config)
    return personalization.apply(
        validate.participant_ids, model.predict_deltas(validate.features)
    )


# --------------------------------------------------------------------------- #
# C/D: gradient boosting
# --------------------------------------------------------------------------- #

#: Conservative settings: the constraint is ~900 episodes, not model capacity.
GBM_PARAMS = {
    "max_depth": 3,
    "learning_rate": 0.05,
    "max_iter": 300,
    "min_samples_leaf": 25,
    "l2_regularization": 1.0,
    "max_leaf_nodes": 8,
    "early_stopping": False,
    "random_state": 20261011,
}


class GbmModel:
    """One HistGradientBoostingRegressor per horizon, predicting the delta.

    No imputer and no scaler: the estimator handles NaN natively by learning a default
    split direction for missing values, which is strictly more informative than
    median-filling, and trees are scale-invariant.
    """

    def __init__(self, params: dict | None = None, columns: list[str] | None = None):
        self.params = {**GBM_PARAMS, **(params or {})}
        self.columns = columns
        self.models: dict[int, HistGradientBoostingRegressor] = {}

    def fit(self, train: EpisodeMatrices, config: Config) -> GbmModel:
        features = train.features if self.columns is None else train.features[self.columns]
        for horizon in HORIZONS:
            mask = train.deltas[horizon].notna().to_numpy()
            estimator = HistGradientBoostingRegressor(**self.params)
            estimator.fit(
                features.loc[mask].reset_index(drop=True),
                train.deltas.loc[mask, horizon].to_numpy(dtype="float64"),
            )
            self.models[horizon] = estimator
        return self

    def predict_deltas(self, features: pd.DataFrame) -> dict[int, np.ndarray]:
        used = features if self.columns is None else features[self.columns]
        return {
            h: np.asarray(self.models[h].predict(used), dtype="float64")
            for h in HORIZONS
        }


def _gbm(train, validate, config):
    return GbmModel().fit(train, config).predict_deltas(validate.features)


def _gbm_personal(train, validate, config):
    model = GbmModel().fit(train, config)
    personalization = _personalize(model, train, config)
    return personalization.apply(
        validate.participant_ids, model.predict_deltas(validate.features)
    )


# --------------------------------------------------------------------------- #
# Blend
# --------------------------------------------------------------------------- #


def _blend_ridge_gbm_personal(train, validate, config):
    """Equal-weight average of the two personalized families.

    A fixed 50/50 weight, not a tuned one: with this few episodes a learned blend
    weight would itself need a validation level to choose it.
    """
    ridge = _fit_ridge(train, config)
    gbm = GbmModel().fit(train, config)
    ridge_pred = ridge.predict_deltas(validate.features)
    gbm_pred = gbm.predict_deltas(validate.features)
    averaged = {h: 0.5 * (ridge_pred[h] + gbm_pred[h]) for h in HORIZONS}

    class _Blend:
        def predict_deltas(self, features):
            r = ridge.predict_deltas(features)
            g = gbm.predict_deltas(features)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    personalization = _personalize(_Blend(), train, config)
    return personalization.apply(validate.participant_ids, averaged)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def core_candidates() -> list[Candidate]:
    return [
        Candidate("persistence", _persistence, "Model 0 reference"),
        Candidate("cohort_mean", _cohort_mean, "meal-agnostic reference"),
        Candidate("A ridge", _ridge, "CONTROL: current generalized"),
        Candidate("B ridge_personal", _ridge_personal, "BASELINE: current personalized"),
        Candidate("C gbm", _gbm, "HistGradientBoosting, regularized"),
        Candidate("D gbm_personal", _gbm_personal, "C + shrunk calibration"),
        Candidate("E blend_personal", _blend_ridge_gbm_personal, "0.5*ridge + 0.5*gbm, personalized"),
    ]


def ablation_candidates(groups: dict[str, list[str]], base: list[str]) -> list[Candidate]:
    """One candidate per feature group removed, plus the full control."""

    def make(columns: list[str], name: str) -> Candidate:
        def run(train, validate, config, columns=columns):
            model = _fit_ridge(train, config, columns)
            personalization = _personalize(model, train, config, columns)
            features = validate.features[columns]
            return personalization.apply(
                validate.participant_ids, model.predict_deltas(features)
            )

        return Candidate(name, run, f"{len(columns)} features")

    candidates = [make(base, "full (control)")]
    for group, members in groups.items():
        remaining = [c for c in base if c not in members]
        if remaining and len(remaining) < len(base):
            candidates.append(make(remaining, f"− {group}"))
    return candidates


# --------------------------------------------------------------------------- #
# Phase 5: personalization variants
# --------------------------------------------------------------------------- #


def _recency_weighted_personalize(
    model, train: EpisodeMatrices, config: Config, half_life_days: float
) -> dict[str, dict[int, float]]:
    """Exponentially recency-weighted shrunk offsets.

    A participant's response drifts (sensor changes, diet, activity), so their most
    recent meals should carry more weight than meals a week earlier. Weight for an
    episode `d` days before that participant's LAST training meal is `0.5 ** (d/H)`.

    The shrinkage denominator uses the EFFECTIVE sample size
    `(Σw)² / Σw²` rather than the raw count, so heavy down-weighting correctly reduces
    confidence instead of pretending the offset rests on n episodes.
    """
    in_sample = model.predict_deltas(train.features)
    timestamps = pd.to_datetime(pd.Series(train.meal_timestamps), utc=True)
    offsets: dict[str, dict[int, float]] = {}

    for participant_id in sorted(set(train.participant_ids.tolist())):
        member = train.participant_ids == participant_id
        latest = timestamps[member].max()
        age_days = (latest - timestamps[member]).dt.total_seconds().to_numpy() / 86400.0
        weights = 0.5 ** (age_days / half_life_days)

        horizon_offsets: dict[int, float] = {}
        for horizon in HORIZONS:
            mask = member & train.valid_mask(horizon)
            if not mask.any():
                horizon_offsets[horizon] = 0.0
                continue
            residual = (
                train.deltas[horizon].to_numpy(dtype="float64") - in_sample[horizon]
            )[mask]
            w = weights[train.valid_mask(horizon)[member]] if mask.sum() != member.sum() else weights
            w = w[: residual.size] if w.size >= residual.size else np.ones(residual.size)
            finite = np.isfinite(residual)
            residual, w = residual[finite], w[finite]
            if residual.size == 0 or w.sum() <= 0:
                horizon_offsets[horizon] = 0.0
                continue
            weighted_mean = float(np.sum(w * residual) / np.sum(w))
            effective_n = float(np.sum(w) ** 2 / np.sum(w**2))
            if effective_n < config.personalization.min_episodes:
                horizon_offsets[horizon] = 0.0
                continue
            factor = effective_n / (effective_n + config.personalization.shrinkage_k)
            horizon_offsets[horizon] = float(
                np.clip(
                    weighted_mean * factor,
                    -config.personalization.max_offset_mg_dl,
                    config.personalization.max_offset_mg_dl,
                )
            )
        offsets[participant_id] = horizon_offsets
    return offsets


def _apply_offsets(predictions, participant_ids, offsets) -> dict[int, np.ndarray]:
    adjusted = {}
    for horizon in HORIZONS:
        values = np.asarray(predictions[horizon], dtype="float64").copy()
        for i, pid in enumerate(participant_ids.tolist()):
            values[i] += offsets.get(str(pid), {}).get(horizon, 0.0)
        adjusted[horizon] = values
    return adjusted


def _blend_model(train, config):
    """The 0.5 ridge + 0.5 gbm ensemble as a single predict_deltas object."""
    ridge = _fit_ridge(train, config)
    gbm = GbmModel().fit(train, config)

    class _Blend:
        def predict_deltas(self, features):
            r = ridge.predict_deltas(features)
            g = gbm.predict_deltas(features)
            return {h: 0.5 * (r[h] + g[h]) for h in HORIZONS}

    return _Blend()


def _blend_shrinkage(k: float):
    """The blend with a specific shrinkage constant."""

    def run(train, validate, config, k=k):
        import dataclasses

        tuned = dataclasses.replace(
            config,
            personalization=dataclasses.replace(config.personalization, shrinkage_k=k),
        )
        model = _blend_model(train, tuned)
        personalization = _personalize(model, train, tuned)
        return personalization.apply(
            validate.participant_ids, model.predict_deltas(validate.features)
        )

    return run


def _blend_recency(half_life_days: float):
    def run(train, validate, config, half_life_days=half_life_days):
        model = _blend_model(train, config)
        offsets = _recency_weighted_personalize(model, train, config, half_life_days)
        return _apply_offsets(
            model.predict_deltas(validate.features), validate.participant_ids, offsets
        )

    return run


def personalization_candidates() -> list[Candidate]:
    return [
        Candidate("E blend k=6 (control)", _blend_shrinkage(6.0), "current shrinkage"),
        Candidate("F blend k=3", _blend_shrinkage(3.0), "weaker shrinkage"),
        Candidate("G blend k=12", _blend_shrinkage(12.0), "stronger shrinkage"),
        Candidate("H blend k=20", _blend_shrinkage(20.0), "much stronger shrinkage"),
        Candidate("I blend recency 3d", _blend_recency(3.0), "recency-weighted offsets"),
        Candidate("J blend recency 7d", _blend_recency(7.0), "recency-weighted offsets"),
    ]
