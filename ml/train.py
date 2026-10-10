"""Model training and artifact production.

    python -m ml.train --source synthetic_dev

Fits, in order:

  * **Model 0** persistence and a cohort-mean-delta benchmark (`ml/baselines.py`);
  * **Model 1** the generalized cohort model: one Ridge regression per horizon inside
    a pipeline of median imputation (+ missing indicators) and standardization;
  * **Model 2** shrunk per-participant residual calibration (`ml/personalization.py`).

Two properties this module is built to guarantee:

**Fold-respecting transforms.** Imputation and scaling are pipeline steps, not
preprocessing. `Pipeline.fit` therefore computes medians and scales from the fitting
rows only, and `predict` reuses those statistics. The evaluation in `ml/evaluate.py`
calls `fit` on the training fold alone, so no held-out statistic can leak in through a
median. The same fitted object is what inference uses, so train-time and serve-time
transforms are identical by construction rather than by careful duplication.

**Determinism.** Every stochastic step is seeded from `ModelConfig.random_seed`.
Retraining from a fresh checkout reproduces the artifact's coefficients, and
`tests/test_reproducibility.py` asserts it.

Alpha is chosen by chronological inner cross-validation on the fitting rows
(`TimeSeriesSplit` over episodes already sorted by meal time), never by a random
k-fold, which would let a later episode help select the penalty used to predict an
earlier one.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import pathlib
from dataclasses import dataclass, field

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.baselines import CohortMeanDeltaBaseline, PersistenceBaseline
from ml.config import (
    CONTRACT_VERSION,
    DEFAULT_CONFIG,
    FEATURE_SET_VERSION,
    HORIZONS,
    MODEL_VERSION,
    Config,
)
from ml.features import FEATURE_NAMES, rows_to_frame
from ml.personalization import PersonalizationModel
from ml.provenance import resolve_data_source
from ml.targets import Episode, build_episodes  # noqa: F401  (Episode used in annotations)

ARTIFACT_DIR = pathlib.Path("ml/artifacts")
ARTIFACT_NAME = "model.joblib"
MANIFEST_NAME = "model_manifest.json"


# --------------------------------------------------------------------------- #
# Episode -> matrices
# --------------------------------------------------------------------------- #


@dataclass
class EpisodeMatrices:
    """Design matrix, per-horizon targets and provenance for a set of episodes."""

    features: pd.DataFrame
    #: horizon -> target delta per episode; NaN where the target was unobserved.
    deltas: pd.DataFrame
    participant_ids: np.ndarray
    baselines: np.ndarray
    meal_timestamps: np.ndarray
    meal_ids: np.ndarray

    def __len__(self) -> int:
        return len(self.features)

    def valid_mask(self, horizon: int) -> np.ndarray:
        return self.deltas[horizon].notna().to_numpy()


def episodes_to_matrices(episodes: list[Episode]) -> EpisodeMatrices:
    """Vectorize episodes, preserving their chronological order."""
    features = rows_to_frame([episode.features for episode in episodes])
    deltas = pd.DataFrame(
        {
            horizon: [episode.target.delta.get(horizon, np.nan) for episode in episodes]
            for horizon in HORIZONS
        },
        dtype="float64",
    )
    return EpisodeMatrices(
        features=features,
        deltas=deltas,
        participant_ids=np.array([e.participant_id for e in episodes], dtype=object),
        baselines=np.array(
            [e.target.baseline_glucose_mg_dl for e in episodes], dtype="float64"
        ),
        meal_timestamps=np.array(
            [e.meal_timestamp.isoformat() for e in episodes], dtype=object
        ),
        meal_ids=np.array([e.meal_id or "" for e in episodes], dtype=object),
    )


# --------------------------------------------------------------------------- #
# Model 1: generalized cohort model
# --------------------------------------------------------------------------- #


def make_pipeline(alpha: float, seed: int) -> Pipeline:
    """Median imputation (+ indicators) -> standardization -> Ridge.

    `add_indicator=True` appends a binary column for each feature that had missing
    values during fitting, so "heart rate was unavailable" is information the model can
    use rather than a silently median-filled value pretending to be an observation.
    """
    return Pipeline(
        steps=[
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            ("ridge", Ridge(alpha=alpha, random_state=seed)),
        ]
    )


@dataclass
class GeneralizedModel:
    """Model 1: one Ridge pipeline per horizon, sharing the frozen feature contract."""

    name: str = "generalized"
    feature_set_version: str = FEATURE_SET_VERSION
    pipelines: dict[int, Pipeline] = field(default_factory=dict)
    chosen_alpha: dict[int, float] = field(default_factory=dict)
    #: horizon -> column names after imputation, including appended indicators.
    feature_names_out: dict[int, list[str]] = field(default_factory=dict)
    fit_episode_counts: dict[int, int] = field(default_factory=dict)

    def fit(
        self,
        features: pd.DataFrame,
        deltas: pd.DataFrame,
        config: Config,
        validation: tuple[pd.DataFrame, pd.DataFrame] | None = None,
    ) -> GeneralizedModel:
        """Fit one Ridge pipeline per horizon on the training fold.

        When `validation` is supplied, the Ridge penalty is chosen by error on that
        explicit held-back set instead of by an inner `TimeSeriesSplit`. That is
        required for real data: the organizer timestamps are date-shifted per
        participant, so a globally sorted "chronological" inner split is not a
        chronology at all (participants were recorded in different years). The
        validation set is each participant's own LATEST pre-cutoff slice, so it is
        participant-aware and chronological within person, and it is never the final
        test set.
        """
        seed = config.model.random_seed
        self.pipelines = {}
        self.chosen_alpha = {}
        self.feature_names_out = {}
        self.fit_episode_counts = {}

        for horizon in HORIZONS:
            mask = deltas[horizon].notna().to_numpy()
            X = features.loc[mask].reset_index(drop=True)
            y = deltas.loc[mask, horizon].to_numpy(dtype="float64")
            self.fit_episode_counts[horizon] = int(len(y))

            if len(y) == 0:
                raise ValueError(f"no training episodes with a target at t+{horizon} min")

            if validation is not None:
                val_features, val_deltas = validation
                val_mask = val_deltas[horizon].notna().to_numpy()
                alpha = self._select_alpha_on_validation(
                    X,
                    y,
                    val_features.loc[val_mask].reset_index(drop=True),
                    val_deltas.loc[val_mask, horizon].to_numpy(dtype="float64"),
                    config,
                )
            else:
                alpha = self._select_alpha(X, y, config)
            pipeline = make_pipeline(alpha, seed).fit(X, y)

            self.pipelines[horizon] = pipeline
            self.chosen_alpha[horizon] = alpha
            self.feature_names_out[horizon] = [
                str(name) for name in pipeline[:-1].get_feature_names_out()
            ]

        return self

    def _select_alpha(self, X: pd.DataFrame, y: np.ndarray, config: Config) -> float:
        """Pick the Ridge penalty by chronological inner CV on the fitting rows only."""
        alphas = list(config.model.ridge_alphas)
        # TimeSeriesSplit needs at least n_splits+1 samples; fall back to the middle
        # of the grid when the fold is too small to cross-validate honestly.
        n_splits = min(config.model.inner_cv_splits, max(2, len(y) - 1))
        if len(y) < n_splits + 1 or len(y) < 12:
            return float(alphas[len(alphas) // 2])

        search = GridSearchCV(
            estimator=make_pipeline(alphas[0], config.model.random_seed),
            param_grid={"ridge__alpha": alphas},
            cv=TimeSeriesSplit(n_splits=n_splits),
            scoring="neg_mean_absolute_error",
            n_jobs=1,
        ).fit(X, y)
        return float(search.best_params_["ridge__alpha"])

    def _select_alpha_on_validation(
        self,
        X: pd.DataFrame,
        y: np.ndarray,
        X_val: pd.DataFrame,
        y_val: np.ndarray,
        config: Config,
    ) -> float:
        """Pick the penalty with the lowest MAE on the explicit validation slice.

        Falls back to the middle of the grid when the validation slice is too small to
        discriminate, rather than selecting on two or three points.
        """
        alphas = list(config.model.ridge_alphas)
        if len(y_val) < 8 or len(y) < 12:
            return float(alphas[len(alphas) // 2])

        best_alpha, best_error = alphas[0], float("inf")
        for alpha in alphas:
            pipeline = make_pipeline(alpha, config.model.random_seed).fit(X, y)
            error = float(np.mean(np.abs(pipeline.predict(X_val) - y_val)))
            if error < best_error:
                best_alpha, best_error = alpha, error
        return float(best_alpha)

    def predict_deltas(self, features: pd.DataFrame) -> dict[int, np.ndarray]:
        if not self.pipelines:
            raise RuntimeError("GeneralizedModel is not fitted")
        return {
            horizon: np.asarray(
                self.pipelines[horizon].predict(features), dtype="float64"
            )
            for horizon in HORIZONS
        }

    def coefficients(self, horizon: int) -> tuple[list[str], np.ndarray, float]:
        """(column names after imputation, Ridge coefficients, intercept)."""
        pipeline = self.pipelines[horizon]
        ridge: Ridge = pipeline.named_steps["ridge"]
        return (
            self.feature_names_out[horizon],
            np.asarray(ridge.coef_, dtype="float64"),
            float(ridge.intercept_),
        )

    def transform_to_scaled(self, horizon: int, features: pd.DataFrame) -> np.ndarray:
        """Apply the fitted imputer + scaler, yielding the Ridge's actual input.

        Used by `ml/explain.py`: because the scaler centers, a scaled value IS the
        feature's standardized deviation from the training mean, which is what makes
        `coef * scaled_value` an exact additive contribution.
        """
        return np.asarray(
            self.pipelines[horizon][:-1].transform(features), dtype="float64"
        )


# --------------------------------------------------------------------------- #
# Model 3: gradient-boosted residual-free regressor, blended with Ridge
# --------------------------------------------------------------------------- #

#: Conservative tree settings. The binding constraint is ~900 independent meal
#: episodes, not model capacity, so: shallow, slow, large leaves, strong L2.
#: `HistGradientBoostingRegressor` ships with scikit-learn (already pinned), handles
#: NaN natively -- so no imputer is needed and missingness stays informative -- and is
#: the boosting option the master specification names.
BOOSTER_PARAMS: dict = {
    "max_depth": 3,
    "learning_rate": 0.05,
    "max_iter": 300,
    "min_samples_leaf": 25,
    "l2_regularization": 1.0,
    "max_leaf_nodes": 8,
    "early_stopping": False,
    "random_state": 20261011,
}

#: Equal weighting of the two families. Deliberately FIXED, not tuned: with this few
#: episodes a learned blend weight would itself need another validation level to
#: choose it, and the 0.5/0.5 average already captured the gain on rolling validation.
BLEND_WEIGHT_RIDGE = 0.5


@dataclass
class BoosterModel:
    """One HistGradientBoostingRegressor per horizon, predicting the delta."""

    name: str = "booster"
    params: dict = field(default_factory=lambda: dict(BOOSTER_PARAMS))
    models: dict = field(default_factory=dict)
    fit_episode_counts: dict[int, int] = field(default_factory=dict)

    def fit(self, features: pd.DataFrame, deltas: pd.DataFrame) -> BoosterModel:
        from sklearn.ensemble import HistGradientBoostingRegressor

        self.models = {}
        for horizon in HORIZONS:
            mask = deltas[horizon].notna().to_numpy()
            estimator = HistGradientBoostingRegressor(**self.params)
            estimator.fit(
                features.loc[mask].reset_index(drop=True),
                deltas.loc[mask, horizon].to_numpy(dtype="float64"),
            )
            self.models[horizon] = estimator
            self.fit_episode_counts[horizon] = int(mask.sum())
        return self

    def predict_deltas(self, features: pd.DataFrame) -> dict[int, np.ndarray]:
        if not self.models:
            raise RuntimeError("BoosterModel is not fitted")
        return {
            horizon: np.asarray(self.models[horizon].predict(features), dtype="float64")
            for horizon in HORIZONS
        }


@dataclass
class BlendedModel:
    """Fixed-weight average of the Ridge and booster families.

    Kept as a thin wrapper rather than a new estimator so that the Ridge half stays
    directly available for attribution: `ml/explain.py` decomposes the linear
    component exactly and reports the booster's share as explicitly unexplained,
    rather than pretending the whole blend is additively decomposable.
    """

    ridge: GeneralizedModel
    booster: BoosterModel
    ridge_weight: float = BLEND_WEIGHT_RIDGE
    name: str = "blend"

    @property
    def booster_weight(self) -> float:
        return 1.0 - self.ridge_weight

    def predict_deltas(self, features: pd.DataFrame) -> dict[int, np.ndarray]:
        ridge = self.ridge.predict_deltas(features)
        booster = self.booster.predict_deltas(features)
        return {
            horizon: self.ridge_weight * ridge[horizon]
            + self.booster_weight * booster[horizon]
            for horizon in HORIZONS
        }


# --------------------------------------------------------------------------- #
# Artifact
# --------------------------------------------------------------------------- #


@dataclass
class TrainedArtifact:
    """Everything inference needs, plus the provenance needed to defend it."""

    model_version: str
    feature_set_version: str
    contract_version: str
    trained_at: dt.datetime
    data_source: str
    config: Config
    generalized: GeneralizedModel
    personalization: PersonalizationModel
    persistence: PersistenceBaseline
    cohort_mean: CohortMeanDeltaBaseline
    training_summary: dict
    feature_names: tuple[str, ...] = FEATURE_NAMES
    #: Fitted booster, present only for family="blend". `None` keeps older artifacts
    #: and the pure-Ridge family loadable unchanged.
    booster: BoosterModel | None = None
    #: "ridge" or "blend". Recorded so inference cannot guess wrong.
    family: str = "ridge"
    ridge_weight: float = BLEND_WEIGHT_RIDGE

    @property
    def response_model(self):
        """The object whose `predict_deltas` the cohort-level forecast should use."""
        if self.family == "blend" and self.booster is not None:
            return BlendedModel(self.generalized, self.booster, self.ridge_weight)
        return self.generalized

    def manifest(self) -> dict:
        return {
            "model_version": self.model_version,
            "feature_set_version": self.feature_set_version,
            "contract_version": self.contract_version,
            "trained_at": self.trained_at.isoformat().replace("+00:00", "Z"),
            "data_source": self.data_source,
            "feature_names": list(self.feature_names),
            "horizons": list(HORIZONS),
            "family": self.family,
            "ridge_weight": self.ridge_weight if self.family == "blend" else None,
            "booster_params": dict(self.booster.params) if self.booster else None,
            "chosen_alpha": {str(h): a for h, a in sorted(self.generalized.chosen_alpha.items())},
            "fit_episode_counts": {
                str(h): n for h, n in sorted(self.generalized.fit_episode_counts.items())
            },
            "personalization": self.personalization.as_dict(),
            "training_summary": self.training_summary,
            "config": _config_to_dict(self.config),
        }


def _config_to_dict(config: Config) -> dict:
    return {
        section.name: dataclasses.asdict(getattr(config, section.name))
        for section in dataclasses.fields(config)
    }


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Training entry point
# --------------------------------------------------------------------------- #


def train(
    repository,
    config: Config = DEFAULT_CONFIG,
    *,
    data_source: str | None = None,
    exclude_overlapping: bool = False,
    holdout: str = "none",
    family: str = "ridge",
) -> TrainedArtifact:
    """Fit all three models on every usable episode and return the artifact.

    The production artifact is fitted on ALL available episodes, which is what you
    want from a deployed model. Its reported performance comes from `ml/evaluate.py`,
    which refits on a training fold and scores strictly later episodes.

    NOTE on the personalization offsets in this artifact: they are computed from the
    cohort model's IN-SAMPLE residuals on the training episodes, as the role manual
    prescribes. In-sample residuals are mildly optimistic, which is why the offsets
    are shrunk and why no performance claim is ever made from this object -- the
    held-out comparison in `ml/evaluate.py` fits calibration on the training fold only
    and scores it on unseen later meals.
    """
    # Ruling D2: the public `data_source` label is read off the participant records,
    # never taken from a CLI flag. "sqlite" is a storage backend, not an origin, and
    # it is not a value the frozen contract enum permits.
    if data_source is None:
        data_source = resolve_data_source(repository)

    episodes, exclusions = build_episodes(
        repository, config, exclude_overlapping=exclude_overlapping
    )
    if not episodes:
        raise ValueError(
            "no usable meal episodes; check CGM coverage and macro completeness"
        )

    # Ruling D1: "never train, calibrate, preprocess, tune or select models using
    # target responses reserved for final evaluation." With holdout="day8" the
    # artifact is fitted on the Day-8 training pool ONLY, so the 394 held-out Day-8
    # responses are untouched by the SERVED model too -- not just by the evaluation
    # run. A Day-8 meal predicted in the demo is then genuinely out of sample.
    validation_split: list[Episode] = []
    if holdout == "day8":
        from ml.evaluate import _assert_day8_separation, split_day8_holdout

        fit_episodes, validation_split, reserved = split_day8_holdout(episodes, config)
        _assert_day8_separation(fit_episodes, validation_split, reserved, config)
        if not fit_episodes:
            raise ValueError(
                "the Day-8 split left no training episodes; check prior-history coverage"
            )
        episodes = fit_episodes + validation_split
        episodes.sort(key=lambda e: (e.meal_timestamp, e.meal_id or ""))
    elif holdout != "none":
        raise ValueError(f"unknown holdout {holdout!r}; expected 'none' or 'day8'")

    matrices = episodes_to_matrices(episodes)

    persistence = PersistenceBaseline().fit(matrices.features, matrices.deltas)
    cohort_mean = CohortMeanDeltaBaseline().fit(matrices.features, matrices.deltas)
    # Alpha selected on the held-back validation slice when we have one; never on the
    # reserved Day-8 responses.
    validation = None
    if validation_split:
        validation_matrices = episodes_to_matrices(validation_split)
        validation = (validation_matrices.features, validation_matrices.deltas)
    generalized = GeneralizedModel().fit(
        matrices.features, matrices.deltas, config, validation=validation
    )

    booster: BoosterModel | None = None
    if family == "blend":
        booster = BoosterModel().fit(matrices.features, matrices.deltas)
    elif family != "ridge":
        raise ValueError(f"unknown family {family!r}; expected 'ridge' or 'blend'")

    response_model = (
        BlendedModel(generalized, booster) if booster is not None else generalized
    )

    # Residuals of the cohort model on the fitting episodes -> personal offsets.
    # Taken from the BLEND when blending, so the calibration corrects what is actually
    # served rather than the Ridge half alone.
    cohort_predictions = response_model.predict_deltas(matrices.features)
    residuals = {
        horizon: matrices.deltas[horizon].to_numpy(dtype="float64")
        - cohort_predictions[horizon]
        for horizon in HORIZONS
    }
    valid_masks = {horizon: matrices.valid_mask(horizon) for horizon in HORIZONS}
    personalization = PersonalizationModel(config.personalization).fit(
        matrices.participant_ids, residuals, valid_masks=valid_masks
    )

    participant_episode_counts = (
        pd.Series(matrices.participant_ids).value_counts().sort_index().to_dict()
    )

    training_summary = {
        "episodes": len(episodes),
        "participants": len(participant_episode_counts),
        "episodes_by_participant": {
            str(k): int(v) for k, v in participant_episode_counts.items()
        },
        "exclusions": exclusions.as_dict(),
        "exclude_overlapping": exclude_overlapping,
        "holdout": holdout,
        "fitted_episodes": len(episodes),
        "validation_episodes": len(validation_split),
        "target_availability": {
            str(horizon): int(valid_masks[horizon].sum()) for horizon in HORIZONS
        },
        "first_meal_timestamp": str(matrices.meal_timestamps[0]),
        "last_meal_timestamp": str(matrices.meal_timestamps[-1]),
    }

    return TrainedArtifact(
        model_version=MODEL_VERSION,
        feature_set_version=FEATURE_SET_VERSION,
        contract_version=CONTRACT_VERSION,
        trained_at=dt.datetime.now(dt.timezone.utc),
        data_source=data_source,
        config=config,
        generalized=generalized,
        booster=booster,
        family=family,
        personalization=personalization,
        persistence=persistence,
        cohort_mean=cohort_mean,
        training_summary=training_summary,
    )


def save_artifact(artifact: TrainedArtifact, out_dir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Write model.joblib plus a human-readable manifest with a checksum."""
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = out_dir / ARTIFACT_NAME
    joblib.dump(artifact, artifact_path, compress=3)

    manifest = artifact.manifest()
    manifest["artifact_file"] = ARTIFACT_NAME
    manifest["artifact_sha256"] = _sha256(artifact_path)
    manifest["artifact_bytes"] = artifact_path.stat().st_size

    manifest_path = out_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n")
    return artifact_path, manifest_path


def load_artifact(path: pathlib.Path) -> TrainedArtifact:
    """Load an artifact, refusing one built against a different feature contract.

    A feature-set mismatch means the stored coefficients correspond to a different
    column layout. Serving it would produce confident nonsense, so this raises instead.
    """
    artifact: TrainedArtifact = joblib.load(path)
    if artifact.feature_set_version != FEATURE_SET_VERSION:
        raise ValueError(
            f"artifact feature_set_version {artifact.feature_set_version!r} does not "
            f"match running code {FEATURE_SET_VERSION!r}; retrain with `python -m ml.train`"
        )
    if tuple(artifact.feature_names) != FEATURE_NAMES:
        raise ValueError(
            "artifact feature names do not match the running feature contract; retrain"
        )
    return artifact


#: Source names the ML CLIs accept. `synthetic_dev` is the labelled in-memory
#: development fixture; `sqlite` is the canonical data layer (owner: Het) that the
#: organizer datasets are ingested into.
SOURCES = ("synthetic_dev", "sqlite")


def build_repository(
    source: str, days: int = 16, seed: int = 20261011, db_url: str | None = None
):
    """Resolve a data source name to a repository implementation.

    The branches are mutually exclusive on purpose. Selecting `sqlite` must NEVER fall
    back to the synthetic fixture: a fixture-backed run reported as a real-data result
    would be the most damaging mistake this project could make. An unopenable or empty
    database therefore raises rather than degrading.
    """
    if source == "synthetic_dev":
        from ml.devfixture import SyntheticDevRepository

        return SyntheticDevRepository(days=days, seed=seed)

    if source == "sqlite":
        from data.storage.repository import SqliteDataRepository

        try:
            repository = SqliteDataRepository(db_url)
        except Exception as error:
            raise SystemExit(
                f"could not open the canonical database "
                f"({db_url or '$GLUCOTWIN_DB_URL'}): {error}\n"
                "Build one first, e.g.\n"
                "  python -m data.storage.init_db --db-url sqlite:///data/local/cgmacros.db\n"
                "  python -m data.adapters.cgmacros --path <CGMacros dir> "
                "--db-url sqlite:///data/local/cgmacros.db"
            ) from error

        if not repository.list_participants():
            raise SystemExit(
                f"the canonical database ({db_url or '$GLUCOTWIN_DB_URL'}) has no "
                "participants. Refusing to train on an empty database rather than "
                "quietly producing a model from nothing."
            )
        return repository

    raise SystemExit(f"unknown --source {source!r}; expected one of {SOURCES}")


def add_source_arguments(parser: argparse.ArgumentParser) -> None:
    """Shared `--source` / `--db-url` / fixture arguments for the ML CLIs."""
    parser.add_argument(
        "--source",
        default="synthetic_dev",
        choices=list(SOURCES),
        help="'synthetic_dev' in-memory fixture, or 'sqlite' canonical database",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="database URL for --source sqlite (default: $GLUCOTWIN_DB_URL)",
    )
    parser.add_argument(
        "--days", type=int, default=16, help="fixture days (synthetic_dev only)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_CONFIG.model.random_seed,
        help="deterministic seed",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train GlucoTwin forecasting models.")
    add_source_arguments(parser)
    parser.add_argument(
        "--out", default=str(ARTIFACT_DIR), help="artifact output directory"
    )
    parser.add_argument(
        "--exclude-overlapping",
        action="store_true",
        help="drop episodes with another meal inside the 120-minute window",
    )
    parser.add_argument(
        "--family",
        default="ridge",
        choices=["ridge", "blend"],
        help=(
            "'ridge' is the interpretable linear family; 'blend' averages it with a "
            "regularized gradient booster (lower validated error, partial attribution)"
        ),
    )
    parser.add_argument(
        "--holdout",
        default="none",
        choices=["none", "day8"],
        help=(
            "'day8' fits on the Day-8 training pool only, leaving the held-out Day-8 "
            "responses untouched by the served model as well as by evaluation "
            "(required for real-data artifacts under ruling D1)"
        ),
    )
    args = parser.parse_args(argv)

    repository = build_repository(args.source, args.days, args.seed, args.db_url)
    config = dataclasses.replace(
        DEFAULT_CONFIG,
        model=dataclasses.replace(DEFAULT_CONFIG.model, random_seed=args.seed),
    )

    artifact = train(
        repository,
        config,
        # data_source=None -> resolved from the participant records (ruling D2).
        exclude_overlapping=args.exclude_overlapping,
        holdout=args.holdout,
        family=args.family,
    )
    artifact_path, manifest_path = save_artifact(artifact, pathlib.Path(args.out))

    summary = artifact.training_summary
    print(f"model_version        {artifact.model_version}")
    print(f"feature_set_version  {artifact.feature_set_version}")
    print(f"data_source          {artifact.data_source}")
    print(f"trained_at           {artifact.trained_at.isoformat()}")
    print(f"episodes             {summary['episodes']} across {summary['participants']} participants")
    print(f"holdout              {summary['holdout']} (fitted {summary['fitted_episodes']}, validation {summary['validation_episodes']})")
    print(f"exclusions           {summary['exclusions']['excluded']} {summary['exclusions']['reasons']}")
    print(f"overlapping tagged   {summary['exclusions']['overlapping_tagged']}")
    print(f"family               {artifact.family}" + (f" (ridge weight {artifact.ridge_weight})" if artifact.family == "blend" else ""))
    print(f"chosen alpha         {artifact.generalized.chosen_alpha}")
    personalized = [
        c.participant_id
        for c in artifact.personalization.calibrations.values()
        if c.personalized
    ]
    print(f"personalized         {len(personalized)}/{summary['participants']}: {sorted(personalized)}")
    print(f"artifact             {artifact_path}")
    print(f"manifest             {manifest_path}")
    return 0


if __name__ == "__main__":
    # Re-import through the package before running, so that the dataclasses pickled
    # into the artifact are qualified as `ml.train.*` rather than `__main__.*`.
    # Without this, `python -m ml.train` writes an artifact that only a process
    # running train.py as __main__ can unpickle.
    from ml.train import main as _main

    raise SystemExit(_main())
