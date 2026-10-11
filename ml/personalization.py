"""Model 2: shrunk per-participant residual calibration.

The cohort model (Model 1) learns how an average person in the training data responds
to a meal. Individuals deviate from that systematically -- one participant runs
consistently higher at 60 minutes than the cohort model expects. That systematic
deviation is estimable from the participant's own earlier meals.

The estimator is a per-participant, per-horizon offset: the mean residual of the
cohort model on that participant's TRAINING episodes, shrunk toward zero by

    factor = n / (n + k)

where `n` is the participant's usable training episodes at that horizon and `k` is a
configured constant. The shrinkage is the whole point. With one training meal the raw
mean residual is almost entirely noise, and applying it would make predictions worse
while looking like personalization. With `k = 6`, one episode keeps 14% of the raw
offset and twenty keep 77%, so the correction earns its weight as evidence accumulates.

Two further guards:
  * participants below `min_episodes` get **no** offset at all and are reported as
    `generalized_fallback`, so the API never implies personalization that did not happen;
  * offsets are capped at `max_offset_mg_dl`, so one extreme training meal cannot
    produce an implausible personal correction.

Everything here is fitted on training episodes only. Fitting on an episode that is
later scored would make the personalized model look better than it is -- the exact
failure this module is structured to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ml.config import HORIZONS, PersonalizationConfig


@dataclass
class ParticipantCalibration:
    """Stored calibration for one participant."""

    participant_id: str
    #: horizon -> shrunk offset in mg/dL, applied additively to the cohort prediction.
    offsets: dict[int, float]
    #: horizon -> training episodes that contributed.
    episode_counts: dict[int, int]
    #: horizon -> raw (unshrunk) mean residual, kept for the methodology report.
    raw_offsets: dict[int, float]
    #: horizon -> shrinkage factor actually applied.
    shrinkage_factors: dict[int, float]
    #: True when the participant met the minimum-episode threshold.
    personalized: bool
    #: Plain-language reason, surfaced in the API's `limitations` on fallback.
    fallback_reason: str | None = None

    def offset_for(self, horizon: int) -> float:
        return self.offsets.get(horizon, 0.0) if self.personalized else 0.0

    def as_dict(self) -> dict:
        return {
            "participant_id": self.participant_id,
            "personalized": self.personalized,
            "fallback_reason": self.fallback_reason,
            "offsets_mg_dl": {str(h): round(v, 3) for h, v in sorted(self.offsets.items())},
            "raw_offsets_mg_dl": {
                str(h): round(v, 3) for h, v in sorted(self.raw_offsets.items())
            },
            "shrinkage_factors": {
                str(h): round(v, 4) for h, v in sorted(self.shrinkage_factors.items())
            },
            "episode_counts": {str(h): v for h, v in sorted(self.episode_counts.items())},
        }


@dataclass
class PersonalizationModel:
    """Container for every participant's calibration, plus the fallback policy."""

    config: PersonalizationConfig
    calibrations: dict[str, ParticipantCalibration] = field(default_factory=dict)

    def fit(
        self,
        participant_ids: np.ndarray,
        residuals: dict[int, np.ndarray],
        *,
        valid_masks: dict[int, np.ndarray] | None = None,
    ) -> PersonalizationModel:
        """Estimate shrunk offsets from TRAINING-fold residuals.

        Args:
            participant_ids: participant id per training episode, length n.
            residuals: horizon -> (observed - cohort_predicted) per episode, length n.
                Positive means the cohort model under-predicted.
            valid_masks: horizon -> boolean mask of episodes with an observed target.
        """
        self.calibrations = {}
        unique_ids = sorted(set(participant_ids.tolist()))

        for participant_id in unique_ids:
            member = participant_ids == participant_id
            offsets: dict[int, float] = {}
            raw_offsets: dict[int, float] = {}
            factors: dict[int, float] = {}
            counts: dict[int, int] = {}

            for horizon in HORIZONS:
                horizon_residuals = residuals[horizon]
                mask = member
                if valid_masks is not None:
                    mask = member & valid_masks[horizon]
                selected = horizon_residuals[mask]
                selected = selected[np.isfinite(selected)]

                n = int(selected.size)
                counts[horizon] = n
                if n == 0:
                    raw_offsets[horizon] = 0.0
                    factors[horizon] = 0.0
                    offsets[horizon] = 0.0
                    continue

                raw = float(np.mean(selected))
                factor = n / (n + self.config.shrinkage_k)
                shrunk = float(
                    np.clip(
                        raw * factor,
                        -self.config.max_offset_mg_dl,
                        self.config.max_offset_mg_dl,
                    )
                )
                raw_offsets[horizon] = raw
                factors[horizon] = factor
                offsets[horizon] = shrunk

            # Episode count for the threshold is the minimum across horizons: a
            # participant is only "personalized" if every horizon has real support.
            episodes = min(counts.values()) if counts else 0
            personalized = episodes >= self.config.min_episodes
            reason = None
            if not personalized:
                reason = (
                    f"Generalized cohort model used: this participant has {episodes} "
                    f"usable training meal episode(s), below the configured minimum of "
                    f"{self.config.min_episodes}, so no participant-specific "
                    "calibration was applied."
                )

            self.calibrations[participant_id] = ParticipantCalibration(
                participant_id=participant_id,
                offsets=offsets,
                episode_counts=counts,
                raw_offsets=raw_offsets,
                shrinkage_factors=factors,
                personalized=personalized,
                fallback_reason=reason,
            )

        return self

    def calibration_for(self, participant_id: str) -> ParticipantCalibration:
        """Calibration for a participant, or an explicit unseen-participant fallback.

        A participant absent from training (a brand-new user, or a held-out participant
        under the cohort protocol) is a legitimate fallback, not an error.
        """
        existing = self.calibrations.get(participant_id)
        if existing is not None:
            return existing
        return ParticipantCalibration(
            participant_id=participant_id,
            offsets={horizon: 0.0 for horizon in HORIZONS},
            episode_counts={horizon: 0 for horizon in HORIZONS},
            raw_offsets={horizon: 0.0 for horizon in HORIZONS},
            shrinkage_factors={horizon: 0.0 for horizon in HORIZONS},
            personalized=False,
            fallback_reason=(
                "Generalized cohort model used: this participant has no meal episodes in "
                "the training data, so no participant-specific calibration exists."
            ),
        )

    def apply(
        self, participant_ids: np.ndarray, cohort_deltas: dict[int, np.ndarray]
    ) -> dict[int, np.ndarray]:
        """Add each participant's offset to the cohort predictions."""
        adjusted: dict[int, np.ndarray] = {}
        for horizon in HORIZONS:
            base = np.asarray(cohort_deltas[horizon], dtype="float64").copy()
            for index, participant_id in enumerate(participant_ids.tolist()):
                base[index] += self.calibration_for(participant_id).offset_for(horizon)
            adjusted[horizon] = base
        return adjusted

    def as_dict(self) -> dict:
        return {
            "min_episodes": self.config.min_episodes,
            "shrinkage_k": self.config.shrinkage_k,
            "max_offset_mg_dl": self.config.max_offset_mg_dl,
            "participants": [
                calibration.as_dict()
                for calibration in sorted(
                    self.calibrations.values(), key=lambda c: c.participant_id
                )
            ],
        }
