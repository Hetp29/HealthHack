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
from ml.targets import Episode, build_episodes

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
        self, features: pd.DataFrame, deltas: pd.DataFrame, config: Config
    ) -> GeneralizedModel:
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

    def manifest(self) -> dict:
        return {
            "model_version": self.model_version,
            "feature_set_version": self.feature_set_version,
            "contract_version": self.contract_version,
            "trained_at": self.trained_at.isoformat().replace("+00:00", "Z"),
            "data_source": self.data_source,
            "feature_names": list(self.feature_names),
            "horizons": list(HORIZONS),
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
    data_source: str = "synthetic_dev",
    exclude_overlapping: bool = False,
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
    episodes, exclusions = build_episodes(
        repository, config, exclude_overlapping=exclude_overlapping
    )
    if not episodes:
        raise ValueError(
            "no usable meal episodes; check CGM coverage and macro completeness"
        )

    matrices = episodes_to_matrices(episodes)

    persistence = PersistenceBaseline().fit(matrices.features, matrices.deltas)
    cohort_mean = CohortMeanDeltaBaseline().fit(matrices.features, matrices.deltas)
    generalized = GeneralizedModel().fit(matrices.features, matrices.deltas, config)

    # Residuals of the cohort model on the fitting episodes -> personal offsets.
    cohort_predictions = generalized.predict_deltas(matrices.features)
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


def build_repository(source: str, days: int, seed: int):
    """Resolve a data source name to a repository implementation."""
    if source == "synthetic_dev":
        from ml.devfixture import SyntheticDevRepository

        return SyntheticDevRepository(days=days, seed=seed)
    raise SystemExit(
        f"unknown --source {source!r}. Only 'synthetic_dev' exists until the canonical "
        "data layer (data/**) lands; then pass 'sqlite'."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train GlucoTwin forecasting models.")
    parser.add_argument(
        "--source",
        default="synthetic_dev",
        help="data source name (currently only 'synthetic_dev')",
    )
    parser.add_argument("--days", type=int, default=7, help="fixture days to generate")
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_CONFIG.model.random_seed,
        help="deterministic seed",
    )
    parser.add_argument(
        "--out", default=str(ARTIFACT_DIR), help="artifact output directory"
    )
    parser.add_argument(
        "--exclude-overlapping",
        action="store_true",
        help="drop episodes with another meal inside the 120-minute window",
    )
    args = parser.parse_args(argv)

    repository = build_repository(args.source, args.days, args.seed)
    config = dataclasses.replace(
        DEFAULT_CONFIG,
        model=dataclasses.replace(DEFAULT_CONFIG.model, random_seed=args.seed),
    )

    artifact = train(
        repository,
        config,
        data_source=args.source,
        exclude_overlapping=args.exclude_overlapping,
    )
    artifact_path, manifest_path = save_artifact(artifact, pathlib.Path(args.out))

    summary = artifact.training_summary
    print(f"model_version        {artifact.model_version}")
    print(f"feature_set_version  {artifact.feature_set_version}")
    print(f"data_source          {artifact.data_source}")
    print(f"trained_at           {artifact.trained_at.isoformat()}")
    print(f"episodes             {summary['episodes']} across {summary['participants']} participants")
    print(f"exclusions           {summary['exclusions']['excluded']} {summary['exclusions']['reasons']}")
    print(f"overlapping tagged   {summary['exclusions']['overlapping_tagged']}")
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
