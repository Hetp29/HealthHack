"""Leakage-safe chronological evaluation.

    python -m ml.evaluate --protocol within_person_chronological

Two protocols, answering two different questions. Reporting only one would overstate
what has been shown.

**`within_person_chronological`** (primary). Per participant, meal episodes are sorted
by time; the earliest 75% train and the latest 25% are held out. This is the protocol
that can support a personalization claim, because each participant's calibration is
estimated from meals that occurred strictly before every meal it is scored on. It
answers: *given some history for this person, how well do we predict their next meals?*

**`cohort_holdout`**. Whole participants are held out. Personalization is impossible
for them by construction, so every held-out forecast is `generalized_fallback`. It
answers: *how well do we predict a person we have never seen?* -- the cold-start case.

What the protocols share, and why:

  * **Splits are by whole meal episode, never by CGM row.** Five-minute rows inside one
    120-minute window are not independent; splitting them would put part of an
    episode's answer in the training set.
  * **Every fitted transform is refit on the training fold.** Imputation medians,
    scaler statistics, the Ridge penalty chosen by inner CV, and the per-participant
    offsets are all computed inside `fit` on training rows only.
  * **Chronology is asserted, not assumed.** `_assert_no_leakage` fails the run if any
    held-out episode is not strictly later than every training episode for that
    participant, or if a participant appears in both folds under `cohort_holdout`.
  * **Four models are scored through one code path**: persistence (Model 0), cohort mean
    delta, generalized (Model 1) and personalized (Model 2), so the comparison is
    apples to apples.

Output conforms to `contracts/schemas/metrics_response.schema.json`, so
`GET /api/model/metrics` serves measured numbers directly with no reshaping.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import pathlib

import numpy as np
import pandas as pd

from ml.baselines import CohortMeanDeltaBaseline, PersistenceBaseline
from ml.config import DEFAULT_CONFIG, HORIZONS, RESEARCH_DISCLAIMER, Config
from ml.metrics import (
    CurveErrors,
    HorizonErrors,
    _round,
    mae,
    rmse,
    summarize_curve,
    summarize_observed,
)
from ml.personalization import PersonalizationModel
from ml.provenance import resolve_data_source
from ml.targets import Episode, build_episodes
from ml.train import (
    BlendedModel,
    BoosterModel,
    GeneralizedModel,
    episodes_to_matrices,
)

PROTOCOLS = ("day8_holdout", "within_person_chronological", "cohort_holdout")

#: Contract v1.0 freezes `evaluation_protocol.name` to two values. `day8_holdout` IS a
#: within-person chronological split -- the Day-8 rule restricts which episodes may
#: enter the TEST set, it does not change the split mechanism -- so it is reported
#: under that name, with the inclusion rule, the embargo and the train/validation
#: separation spelled out in `description`. No public field was added.
CONTRACT_PROTOCOL_NAME = {
    "day8_holdout": "within_person_chronological",
    "within_person_chronological": "within_person_chronological",
    "cohort_holdout": "cohort_holdout",
}
MODEL_NAMES = (
    "persistence",
    "cohort_mean_delta",
    "generalized",
    "personalized",
    "blend_personalized",
)

PROTOCOL_DESCRIPTIONS = {
    "day8_holdout": (
        "PRIMARY protocol. Per participant, the held-out TEST set is restricted to "
        "Day-8-eligible meals: at least {history_days} days of prior CGM history with "
        "at least {coverage:.0%} of the nominal 5-minute readings present, a baseline "
        "reading within 15 min before the meal, and an observed reading within +/-10 "
        "min of t+30/60/90/120. Episodes with another logged meal inside the "
        "120-minute window are excluded from the test set because their observed "
        "response blends two meals; they remain in training, tagged. "
        "Training uses only that participant's episodes strictly before the test "
        "cutoff, further purged by a {embargo:.0f}-minute EMBARGO so that no training "
        "episode's 120-minute response window can overlap a held-out window. The "
        "latest {validation:.0%} of each participant's pre-cutoff pool is held back as "
        "a VALIDATION set and is what selects the Ridge penalty; the test set is never "
        "used for tuning, model selection or calibration. Splits are by whole meal "
        "episode -- five-minute CGM rows are never split. Imputation medians, scaler "
        "statistics and the per-participant calibration offsets are fitted on the "
        "training fold only."
    ),
    "within_person_chronological": (
        "Per participant, meal episodes are ordered by timestamp; the earliest "
        "{train_pct:.0f}% train and the latest {test_pct:.0f}% are held out. Splits are "
        "by whole meal episode -- five-minute CGM rows are never split. Imputation "
        "medians, scaler statistics, the Ridge penalty and the per-participant "
        "calibration offsets are all fitted on the training fold only. Every held-out "
        "episode is strictly later than every training episode for that participant, "
        "which is asserted at run time."
    ),
    "cohort_holdout": (
        "Whole participants are held out ({test_pct:.0f}% of the cohort), so no held-out "
        "participant contributes any training episode. Personalization is unavailable "
        "for held-out participants by construction, so every held-out forecast is a "
        "generalized fallback. This measures cold-start generalization to an unseen "
        "person, not personalization."
    ),
}


class LeakageError(AssertionError):
    """Raised when a split violates its own chronological or isolation guarantee."""


# --------------------------------------------------------------------------- #
# Splits
# --------------------------------------------------------------------------- #


def split_within_person(
    episodes: list[Episode], config: Config
) -> tuple[list[Episode], list[Episode]]:
    """Per participant: earliest episodes train, latest `test_fraction` held out.

    A participant with fewer than `min_episodes_for_split` episodes contributes
    everything to training and nothing to the test fold. They still matter -- they
    inform the cohort model -- but they cannot be scored on held-out meals they do
    not have.
    """
    by_participant: dict[str, list[Episode]] = {}
    for episode in episodes:
        by_participant.setdefault(episode.participant_id, []).append(episode)

    train: list[Episode] = []
    test: list[Episode] = []
    for participant_id in sorted(by_participant):
        owned = sorted(by_participant[participant_id], key=lambda e: e.meal_timestamp)
        if len(owned) < config.evaluation.min_episodes_for_split:
            train.extend(owned)
            continue
        n_test = max(1, int(round(len(owned) * config.evaluation.test_fraction)))
        n_test = min(n_test, len(owned) - 1)  # always leave at least one training meal
        train.extend(owned[: len(owned) - n_test])
        test.extend(owned[len(owned) - n_test :])

    train.sort(key=lambda e: (e.meal_timestamp, e.meal_id or ""))
    test.sort(key=lambda e: (e.meal_timestamp, e.meal_id or ""))
    return train, test


def split_day8_holdout(
    episodes: list[Episode], config: Config
) -> tuple[list[Episode], list[Episode], list[Episode]]:
    """Return `(train, validation, test)` under the Day-8 rule (ruling D1).

    Per participant:
      1. test candidates = Day-8-eligible episodes, minus overlapping ones;
      2. cutoff = the earliest test-candidate meal time;
      3. pool = episodes at or before `cutoff - embargo` (purging any whose response
         window could overlap a held-out window);
      4. validation = the latest `validation_fraction` of that pool;
      5. train = the remainder of the pool.

    A participant with no test candidate contributes its whole pool to training: it
    still informs the cohort model, it just cannot be scored on a Day-8 meal. Train,
    validation and test are therefore disjoint by construction, which is exactly what
    ruling D1 requires -- never training on all episodes and scoring an overlapping
    subset.
    """
    day8 = config.day8
    embargo = dt.timedelta(minutes=day8.embargo_minutes)

    by_participant: dict[str, list[Episode]] = {}
    for episode in episodes:
        by_participant.setdefault(episode.participant_id, []).append(episode)

    train: list[Episode] = []
    validation: list[Episode] = []
    test: list[Episode] = []

    for participant_id in sorted(by_participant):
        owned = sorted(by_participant[participant_id], key=lambda e: e.meal_timestamp)

        candidates = [
            episode
            for episode in owned
            if episode.context is not None and episode.context.day8_eligible
        ]
        if day8.exclude_overlap_from_test:
            candidates = [
                episode
                for episode in candidates
                if not episode.target.has_overlapping_meal
            ]

        if not candidates:
            train.extend(owned)
            continue

        cutoff = min(episode.meal_timestamp for episode in candidates)
        pool = [
            episode for episode in owned if episode.meal_timestamp <= cutoff - embargo
        ]

        if not pool:
            # No purge-safe history for this participant: it cannot contribute a
            # held-out episode without its own training data being contaminated.
            train.extend(owned)
            continue

        n_validation = int(round(len(pool) * day8.validation_fraction))
        n_validation = min(max(n_validation, 0), max(0, len(pool) - 1))
        split_at = len(pool) - n_validation
        train.extend(pool[:split_at])
        validation.extend(pool[split_at:])
        test.extend(candidates)

    key = lambda e: (e.meal_timestamp, e.meal_id or "")  # noqa: E731
    return sorted(train, key=key), sorted(validation, key=key), sorted(test, key=key)


def _assert_day8_separation(
    train: list[Episode], validation: list[Episode], test: list[Episode], config: Config
) -> None:
    """Fail the run unless train/validation/test are disjoint, purged and ordered."""
    embargo = dt.timedelta(minutes=config.day8.embargo_minutes)

    ids = [{id(e) for e in group} for group in (train, validation, test)]
    if ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2]:
        raise LeakageError("day8_holdout: train/validation/test are not disjoint")

    earliest_test: dict[str, dt.datetime] = {}
    for episode in test:
        current = earliest_test.get(episode.participant_id)
        if current is None or episode.meal_timestamp < current:
            earliest_test[episode.participant_id] = episode.meal_timestamp

    for name, group in (("train", train), ("validation", validation)):
        for episode in group:
            cutoff = earliest_test.get(episode.participant_id)
            if cutoff is None:
                continue
            if episode.meal_timestamp > cutoff - embargo:
                raise LeakageError(
                    f"day8_holdout: {name} episode {episode.meal_id!r} at "
                    f"{episode.meal_timestamp.isoformat()} violates the "
                    f"{config.day8.embargo_minutes:g}-minute embargo before the test "
                    f"cutoff {cutoff.isoformat()}"
                )

    for episode in test:
        if episode.context is None or not episode.context.day8_eligible:
            raise LeakageError(
                f"day8_holdout: test episode {episode.meal_id!r} is not Day-8 eligible"
            )
        if config.day8.exclude_overlap_from_test and episode.target.has_overlapping_meal:
            raise LeakageError(
                f"day8_holdout: test episode {episode.meal_id!r} overlaps another meal"
            )


def split_cohort_holdout(
    episodes: list[Episode], config: Config
) -> tuple[list[Episode], list[Episode]]:
    """Hold out whole participants, chosen deterministically by sorted id."""
    participant_ids = sorted({episode.participant_id for episode in episodes})
    n_test = max(1, int(round(len(participant_ids) * config.evaluation.cohort_test_fraction)))
    n_test = min(n_test, len(participant_ids) - 1)
    held_out = set(participant_ids[-n_test:])

    train = [e for e in episodes if e.participant_id not in held_out]
    test = [e for e in episodes if e.participant_id in held_out]
    return train, test


def _assert_no_leakage(
    protocol: str, train: list[Episode], test: list[Episode]
) -> None:
    """Fail loudly if the split does not deliver what the protocol promises."""
    if protocol == "cohort_holdout":
        train_ids = {e.participant_id for e in train}
        test_ids = {e.participant_id for e in test}
        overlap = train_ids & test_ids
        if overlap:
            raise LeakageError(
                f"cohort_holdout: participants appear in both folds: {sorted(overlap)}"
            )
        return

    latest_train: dict[str, dt.datetime] = {}
    for episode in train:
        current = latest_train.get(episode.participant_id)
        if current is None or episode.meal_timestamp > current:
            latest_train[episode.participant_id] = episode.meal_timestamp

    for episode in test:
        boundary = latest_train.get(episode.participant_id)
        if boundary is None:
            raise LeakageError(
                f"{protocol}: held-out participant {episode.participant_id!r} has no "
                "training episodes, so its calibration cannot have been fitted"
            )
        if episode.meal_timestamp <= boundary:
            raise LeakageError(
                f"{protocol}: held-out episode {episode.meal_id!r} at "
                f"{episode.meal_timestamp.isoformat()} is not strictly later than the "
                f"last training episode at {boundary.isoformat()}"
            )


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def _fit_models(train_matrices, config: Config, validation_matrices=None):
    """Fit all four models on the training fold only.

    `validation_matrices`, when given, selects the Ridge penalty. It is a held-back
    slice of the TRAINING pool, never the test set.
    """
    persistence = PersistenceBaseline().fit(train_matrices.features, train_matrices.deltas)
    cohort_mean = CohortMeanDeltaBaseline().fit(
        train_matrices.features, train_matrices.deltas
    )
    validation = (
        (validation_matrices.features, validation_matrices.deltas)
        if validation_matrices is not None and len(validation_matrices) > 0
        else None
    )
    generalized = GeneralizedModel().fit(
        train_matrices.features, train_matrices.deltas, config, validation=validation
    )

    # The selected production family: Ridge averaged with a regularized booster.
    booster = BoosterModel().fit(train_matrices.features, train_matrices.deltas)
    blend = BlendedModel(generalized, booster)

    valid_masks = {
        horizon: train_matrices.valid_mask(horizon) for horizon in HORIZONS
    }

    def _calibrate(model) -> PersonalizationModel:
        """Shrunk offsets from TRAINING-fold residuals of that same model."""
        in_sample = model.predict_deltas(train_matrices.features)
        residuals = {
            horizon: train_matrices.deltas[horizon].to_numpy(dtype="float64")
            - in_sample[horizon]
            for horizon in HORIZONS
        }
        return PersonalizationModel(config.personalization).fit(
            train_matrices.participant_ids, residuals, valid_masks=valid_masks
        )

    personalization = _calibrate(generalized)
    blend_personalization = _calibrate(blend)
    return (
        persistence,
        cohort_mean,
        generalized,
        personalization,
        blend,
        blend_personalization,
    )


def _predict_all(
    test_matrices,
    persistence,
    cohort_mean,
    generalized,
    personalization,
    blend,
    blend_personalization,
) -> dict[str, dict[int, np.ndarray]]:
    """Predicted deltas per model on the held-out fold."""
    features = test_matrices.features
    cohort = generalized.predict_deltas(features)
    blended = blend.predict_deltas(features)
    return {
        "persistence": persistence.predict_deltas(features),
        "cohort_mean_delta": cohort_mean.predict_deltas(features),
        "generalized": cohort,
        "personalized": personalization.apply(test_matrices.participant_ids, cohort),
        "blend_personalized": blend_personalization.apply(
            test_matrices.participant_ids, blended
        ),
    }


def _score_model(
    test_matrices,
    test_episodes: list[Episode],
    predicted: dict[int, np.ndarray],
    config: Config,
) -> tuple[dict[int, HorizonErrors], CurveErrors, list[float]]:
    """Per-horizon errors, curve-summary errors, and the pooled error list."""
    horizon_errors = {
        horizon: HorizonErrors(horizon_minutes=horizon, errors=[]) for horizon in HORIZONS
    }
    pooled: list[float] = []

    for horizon in HORIZONS:
        observed = test_matrices.deltas[horizon].to_numpy(dtype="float64")
        mask = test_matrices.valid_mask(horizon)
        errors = (predicted[horizon] - observed)[mask]
        # Delta error equals absolute-glucose error: both share the same measured
        # baseline, so the baseline cancels. Reported in mg/dL either way.
        horizon_errors[horizon].errors = [float(e) for e in errors]
        pooled.extend(float(e) for e in errors)

    curve_errors = CurveErrors.empty()
    for index, episode in enumerate(test_episodes):
        observed_summary = summarize_observed(
            episode.target.observed_curve, episode.target.baseline_glucose_mg_dl
        )
        if observed_summary is None:
            continue
        deltas = {horizon: float(predicted[horizon][index]) for horizon in HORIZONS}
        from ml.metrics import build_curve

        minutes, values, _ = build_curve(
            episode.target.baseline_glucose_mg_dl,
            deltas,
            clip_min=config.model.clip_glucose_min_mg_dl,
            clip_max=config.model.clip_glucose_max_mg_dl,
        )
        predicted_summary = summarize_curve(
            minutes, values, episode.target.baseline_glucose_mg_dl
        )
        curve_errors.add(predicted_summary, observed_summary)

    return horizon_errors, curve_errors, pooled


def _personalization_comparison(
    test_matrices,
    predictions: dict[str, dict[int, np.ndarray]],
    personalization: PersonalizationModel,
) -> dict:
    """Per-participant generalized-vs-personalized MAE on held-out episodes."""
    participant_ids = test_matrices.participant_ids
    rows = []
    improved = worsened = unchanged = 0

    for participant_id in sorted(set(participant_ids.tolist())):
        member = participant_ids == participant_id
        general_errors: list[float] = []
        personal_errors: list[float] = []
        n = 0
        for horizon in HORIZONS:
            observed = test_matrices.deltas[horizon].to_numpy(dtype="float64")
            mask = member & test_matrices.valid_mask(horizon)
            n += int(mask.sum())
            general_errors.extend(
                (predictions["generalized"][horizon] - observed)[mask].tolist()
            )
            personal_errors.extend(
                (predictions["personalized"][horizon] - observed)[mask].tolist()
            )

        general_mae = mae(general_errors)
        personal_mae = mae(personal_errors)
        delta = (
            None
            if general_mae is None or personal_mae is None
            else general_mae - personal_mae
        )

        if delta is None or abs(delta) < 1e-9:
            unchanged += 1
        elif delta > 0:
            improved += 1
        else:
            worsened += 1

        rows.append(
            {
                "participant_id": participant_id,
                "n": n,
                "generalized_mae_mg_dl": _round(general_mae),
                "personalized_mae_mg_dl": _round(personal_mae),
                "delta_mae_mg_dl": _round(delta),
            }
        )

    return {
        "participants_improved": improved,
        "participants_worsened": worsened,
        "participants_unchanged": unchanged,
        "by_participant": rows,
    }


def _sample_counts(
    train: list[Episode],
    test: list[Episode],
    personalization: PersonalizationModel,
    validation: list[Episode] | None = None,
) -> dict:
    """Counts in the frozen `sample_counts` shape.

    Contract v1.0 fixes the member list and sets `additionalProperties: false`, so
    there is no `validation_episodes` field and adding one would be a contract change.
    Validation episodes are part of the training pool from the API's point of view --
    they are fitted/tuned on, never scored -- so they are counted inside
    `train_episodes`. The exact train/validation breakdown is reported in the
    evaluation detail file and in docs/validation.md.
    """
    fitting = list(train) + list(validation or [])
    train_counts = pd.Series([e.participant_id for e in fitting]).value_counts().to_dict()
    test_counts = pd.Series([e.participant_id for e in test]).value_counts().to_dict()
    all_ids = sorted(set(train_counts) | set(test_counts))
    return {
        "participants": len(all_ids),
        "train_episodes": len(fitting),
        "test_episodes": len(test),
        "by_participant": [
            {
                "participant_id": participant_id,
                "train_episodes": int(train_counts.get(participant_id, 0)),
                "test_episodes": int(test_counts.get(participant_id, 0)),
                "personalized": bool(
                    personalization.calibration_for(participant_id).personalized
                ),
            }
            for participant_id in all_ids
        ],
    }


def evaluate(
    repository,
    config: Config = DEFAULT_CONFIG,
    *,
    protocol: str = "within_person_chronological",
    data_source: str | None = None,
    exclude_overlapping: bool = False,
) -> dict:
    """Run one protocol end to end.

    Returns `(report, detail)` where `report` is valid against
    `contracts/schemas/metrics_response.schema.json` and `detail` carries the extra
    provenance (exclusion breakdown, fitted calibration offsets) that the frozen
    response shape has no slot for.
    """
    if protocol not in PROTOCOLS:
        raise ValueError(f"unknown protocol {protocol!r}; expected one of {PROTOCOLS}")

    # Ruling D2: provenance comes from the records, not from a CLI flag.
    if data_source is None:
        data_source = resolve_data_source(repository)

    episodes, exclusions = build_episodes(
        repository, config, exclude_overlapping=exclude_overlapping
    )
    if not episodes:
        raise ValueError("no usable meal episodes to evaluate")

    validation: list[Episode] = []
    if protocol == "day8_holdout":
        train, validation, test = split_day8_holdout(episodes, config)
        _assert_day8_separation(train, validation, test, config)
        # Reported as the share of scored episodes among all usable ones.
        total = len(train) + len(validation) + len(test)
        test_fraction = round(len(test) / total, 4) if total else 0.0
    elif protocol == "within_person_chronological":
        train, test = split_within_person(episodes, config)
        test_fraction = config.evaluation.test_fraction
        _assert_no_leakage(protocol, train, test)
    else:
        train, test = split_cohort_holdout(episodes, config)
        test_fraction = config.evaluation.cohort_test_fraction
        _assert_no_leakage(protocol, train, test)

    limitations: list[str] = [RESEARCH_DISCLAIMER]

    if not test:
        limitations.insert(
            0,
            "No held-out meal episodes were available under this protocol, so no "
            "validated metrics could be computed.",
        )
        empty = _report(
            protocol=protocol,
            test_fraction=test_fraction,
            data_source=data_source,
            by_model=[],
            by_horizon=[],
            sample_counts={
                "participants": len({e.participant_id for e in episodes}),
                "train_episodes": len(train),
                "test_episodes": 0,
                "excluded_episodes": exclusions.as_dict()["excluded"],
                "by_participant": [],
            },
            personalization=None,
            limitations=limitations,
            exclude_overlapping=exclude_overlapping,
        )
        return empty, {
            "protocol": protocol,
            "contract_protocol_name": CONTRACT_PROTOCOL_NAME[protocol],
            "exclusions": exclusions.as_dict(),
            "fold_sizes": {
                "train_episodes": len(train),
                "validation_episodes": len(validation),
                "test_episodes": 0,
            },
        }

    train_matrices = episodes_to_matrices(train)
    test_matrices = episodes_to_matrices(test)
    validation_matrices = episodes_to_matrices(validation) if validation else None

    (
        persistence,
        cohort_mean,
        generalized,
        personalization,
        blend,
        blend_personalization,
    ) = _fit_models(train_matrices, config, validation_matrices)
    predictions = _predict_all(
        test_matrices,
        persistence,
        cohort_mean,
        generalized,
        personalization,
        blend,
        blend_personalization,
    )

    by_model = []
    primary_by_horizon: list[dict] = []
    for model_name in MODEL_NAMES:
        horizon_errors, curve_errors, pooled = _score_model(
            test_matrices, test, predictions[model_name], config
        )
        block = {
            "model_name": model_name,
            "overall_mae_mg_dl": _round(mae(pooled)),
            "overall_rmse_mg_dl": _round(rmse(pooled)),
            "by_horizon": [horizon_errors[h].as_dict() for h in HORIZONS],
            "curve": curve_errors.as_dict(),
        }
        by_model.append(block)
        if model_name == "blend_personalized":
            primary_by_horizon = block["by_horizon"]

    comparison = _personalization_comparison(test_matrices, predictions, personalization)

    # Honest framing of what the numbers do and do not establish.
    counts = _sample_counts(train, test, personalization, validation)
    personalized_participants = [
        row["participant_id"] for row in counts["by_participant"] if row["personalized"]
    ]
    if protocol == "cohort_holdout":
        limitations.insert(
            0,
            "Under cohort_holdout no held-out participant has training episodes, so "
            "the personalized and generalized rows are identical by construction. "
            "This protocol measures cold-start generalization only.",
        )
    elif not personalized_participants:
        limitations.insert(
            0,
            "No participant met the minimum-episode threshold for calibration, so the "
            "personalized and generalized rows are identical.",
        )

    limitations.extend(
        [
            "Reported errors are on held-out meal episodes from the same seven-day "
            "window, not on an independent cohort or a later time period.",
            "Peak and time-to-peak errors are limited by the forecast's 30/60/90/120 "
            "minute knots: a piecewise-linear curve can only place its peak at a knot.",
            "Observed peak, time-to-peak and iAUC are computed from the sampled CGM "
            "curve, so sensor gaps inside a response window reduce the curve sample "
            "count below the horizon sample count.",
            "No calibrated prediction intervals exist, so interval coverage is not "
            "reported.",
        ]
    )
    if exclusions.overlapping_tagged:
        limitations.append(
            f"{exclusions.overlapping_tagged} episode(s) have another logged meal "
            "inside the 120-minute window; their observed response blends two meals. "
            "They are "
            + ("excluded" if exclude_overlapping else "included and tagged")
            + "; rerun with the opposite --exclude-overlapping setting for a "
            "sensitivity check."
        )
    if data_source == "synthetic_dev":
        limitations.append(
            "Evaluated on synthetic development fixtures. These numbers demonstrate "
            "that the pipeline is leakage-safe and self-consistent; they are NOT "
            "evidence of accuracy on real physiology."
        )

    report = _report(
        protocol=protocol,
        test_fraction=test_fraction,
        data_source=data_source,
        by_model=by_model,
        by_horizon=primary_by_horizon,
        sample_counts={**counts, "excluded_episodes": exclusions.as_dict()["excluded"]},
        personalization=comparison,
        limitations=limitations,
        exclude_overlapping=exclude_overlapping,
    )
    # Extra provenance that the frozen MetricsResponse shape has no slot for. Returned
    # alongside rather than inside the report, so metrics.json stays directly valid
    # against contracts/schemas/metrics_response.schema.json and can be served with no
    # reshaping.
    detail = {
        "protocol": protocol,
        "contract_protocol_name": CONTRACT_PROTOCOL_NAME[protocol],
        "exclusions": exclusions.as_dict(),
        "personalization_model": personalization.as_dict(),
        # Breakdown the frozen sample_counts shape has no slot for.
        "fold_sizes": {
            "train_episodes": len(train),
            "validation_episodes": len(validation),
            "test_episodes": len(test),
        },
        "day8": {
            "history_days": config.day8.history_days,
            "min_history_coverage": config.day8.min_history_coverage,
            "embargo_minutes": config.day8.embargo_minutes,
            "validation_fraction": config.day8.validation_fraction,
            "exclude_overlap_from_test": config.day8.exclude_overlap_from_test,
            "eligible_episodes_total": exclusions.day8_eligible,
        },
        "chosen_alpha": {
            str(h): a for h, a in sorted(generalized.chosen_alpha.items())
        },
    }
    return report, detail


def _report(
    *,
    protocol: str,
    test_fraction: float,
    data_source: str,
    by_model: list[dict],
    by_horizon: list[dict],
    sample_counts: dict,
    personalization: dict | None,
    limitations: list[str],
    exclude_overlapping: bool,
) -> dict:
    description = PROTOCOL_DESCRIPTIONS[protocol].format(
        train_pct=100.0 * (1.0 - test_fraction),
        test_pct=100.0 * test_fraction,
        history_days=DEFAULT_CONFIG.day8.history_days,
        coverage=DEFAULT_CONFIG.day8.min_history_coverage,
        embargo=DEFAULT_CONFIG.day8.embargo_minutes,
        validation=DEFAULT_CONFIG.day8.validation_fraction,
    )
    report = {
        "dataset_source": data_source,
        "evaluation_protocol": {
            "name": CONTRACT_PROTOCOL_NAME[protocol],
            "description": description,
            "split_unit": "meal_episode",
            "test_fraction": test_fraction,
            "evaluated_at": dt.datetime.now(dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "excluded_overlapping_meals": exclude_overlapping,
        },
        "by_model": by_model,
        "by_horizon": by_horizon,
        "sample_counts": sample_counts,
        "limitations": limitations,
    }
    if personalization is not None:
        report["personalization"] = personalization
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _print_summary(report: dict) -> None:
    protocol = report["evaluation_protocol"]["name"]
    counts = report["sample_counts"]
    print(f"\n=== {protocol} ===")
    print(
        f"participants {counts['participants']} | "
        f"train episodes {counts['train_episodes']} | "
        f"test episodes {counts['test_episodes']} | "
        f"excluded {counts.get('excluded_episodes')}"
    )
    if not report["by_model"]:
        print("no held-out episodes; no metrics computed")
        return

    header = f"{'model':<20}{'MAE':>9}{'RMSE':>9}" + "".join(
        f"{'MAE@' + str(h):>10}" for h in HORIZONS
    )
    print(header)
    print("-" * len(header))
    for block in report["by_model"]:
        row = f"{block['model_name']:<20}{block['overall_mae_mg_dl']:>9.2f}{block['overall_rmse_mg_dl']:>9.2f}"
        for horizon_block in block["by_horizon"]:
            row += f"{horizon_block['mae_mg_dl']:>10.2f}"
        print(row)

    print(f"\n{'model':<20}{'peak MAE':>10}{'ttp MAE':>10}{'iAUC MAE':>12}{'n':>6}")
    for block in report["by_model"]:
        curve = block["curve"]
        print(
            f"{block['model_name']:<20}"
            f"{curve['peak_mae_mg_dl'] or 0:>10.2f}"
            f"{curve['time_to_peak_mae_minutes'] or 0:>10.2f}"
            f"{curve['iauc_mae_mg_dl_min'] or 0:>12.1f}"
            f"{curve['n']:>6}"
        )

    comparison = report.get("personalization")
    if comparison:
        print(
            f"\npersonalization: improved {comparison['participants_improved']} | "
            f"worsened {comparison['participants_worsened']} | "
            f"unchanged {comparison['participants_unchanged']}"
        )
        print(f"  {'participant':<20}{'general':>10}{'personal':>10}{'delta':>10}{'n':>6}")
        for row in comparison["by_participant"]:
            print(
                f"  {row['participant_id']:<20}"
                f"{row['generalized_mae_mg_dl'] or 0:>10.2f}"
                f"{row['personalized_mae_mg_dl'] or 0:>10.2f}"
                f"{row['delta_mae_mg_dl'] or 0:>+10.2f}"
                f"{row['n']:>6}"
            )


def main(argv: list[str] | None = None) -> int:
    from ml.train import add_source_arguments, build_repository

    parser = argparse.ArgumentParser(
        description="Evaluate GlucoTwin models with leakage-safe chronological splits."
    )
    add_source_arguments(parser)
    parser.add_argument(
        "--protocol",
        default="day8_holdout",
        choices=[*PROTOCOLS, "all"],
        help=(
            "protocol to run; 'all' runs every protocol and makes the first "
            "(day8_holdout, the primary result) the served report"
        ),
    )
    parser.add_argument("--out", default="ml/artifacts")
    parser.add_argument(
        "--exclude-overlapping",
        action="store_true",
        help="drop episodes with another meal inside the 120-minute window",
    )
    args = parser.parse_args(argv)

    repository = build_repository(args.source, args.days, args.seed, args.db_url)
    config = dataclasses.replace(
        DEFAULT_CONFIG,
        model=dataclasses.replace(DEFAULT_CONFIG.model, random_seed=args.seed),
    )

    protocols = list(PROTOCOLS) if args.protocol == "all" else [args.protocol]
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: dict[str, dict] = {}
    for protocol in protocols:
        report, detail = evaluate(
            repository,
            config,
            protocol=protocol,
            # data_source=None -> resolved from the participant records (ruling D2).
            exclude_overlapping=args.exclude_overlapping,
        )
        reports[protocol] = report
        _print_summary(report)

        path = out_dir / f"metrics_{protocol}.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        detail_path = out_dir / f"evaluation_detail_{protocol}.json"
        detail_path.write_text(json.dumps(detail, indent=2) + "\n")
        print(f"\nwrote {path}")
        print(f"wrote {detail_path}")

    # The primary report is what GET /api/model/metrics serves.
    primary = reports[protocols[0]]
    primary_path = out_dir / "metrics.json"
    primary_path.write_text(json.dumps(primary, indent=2) + "\n")
    print(f"wrote {primary_path} (primary: {protocols[0]})")
    return 0


if __name__ == "__main__":
    from ml.evaluate import main as _main

    raise SystemExit(_main())
