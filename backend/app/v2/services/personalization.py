"""Which personalization policy a forecast runs under.

Vocabulary, fixed by the project brief:

  * **Level A** -- a verified completed meal is appended to the user's history. The
    frozen `r_hist_*` and `q_retr_*` features then individualize the forecast with no
    fitted parameter and no retraining. Always on (subject to consent). This is done
    in `services/history.py`, not here.
  * **Level B** -- the frozen shrunk-calibration POLICY (`n/(n+k)`, k=3, minimum 4
    episodes, cap 60 mg/dL) is applied to the user's own completed episodes. That is
    what this module implements, and it is **off unless explicitly enabled**.
  * **Level C** -- fitting new model parameters. Not implemented, not authorized.

Why Level B is gated: the frozen artifact contains calibrations for its 45 training
participants only. Running the same policy for a new user uses the frozen estimator
class and the frozen configuration unchanged, and never touches the artifact file --
but it yields an offset the artifact does not contain, applied under the frozen
version string. Whether that is "applying the frozen policy" or "a new calibration" is
a governance call, so it ships behind `GLIMPSE_PERSONALIZATION_POLICY` and every
stored prediction records which policy produced it. See DECISIONS D-007.
"""

from __future__ import annotations

import copy
import datetime as dt

import numpy as np
from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from backend.app.v2.db import tables as t
from ml.config import HORIZONS
from ml.personalization import ParticipantCalibration, PersonalizationModel

POLICY_HISTORY_ONLY = "history_only"
POLICY_FROZEN_CALIBRATION = "history_plus_frozen_calibration"


def _eligible_residuals(conn: Connection, user_id: str, as_of: dt.datetime) -> list[dict]:
    """Residuals from episodes that were complete strictly before `as_of`."""
    e, p, m = t.response_episodes, t.predictions, t.meals
    rows = conn.execute(
        select(e.c.episode_id, e.c.errors)
        .select_from(e.join(p, p.c.prediction_id == e.c.prediction_id)
                      .join(m, and_(m.c.meal_id == p.c.meal_id,
                                    m.c.current_confirmation_id == p.c.confirmation_id)))
        .where(and_(
            e.c.user_id == user_id, e.c.status == "scored",
            e.c.eligible_for_personalization.is_(True),
            # Strict, matching the retrieval rule: an episode whose window ends exactly
            # at the query instant is excluded.
            e.c.window_end_at < as_of,
        ))
        .order_by(e.c.window_end_at, e.c.episode_id)
    ).mappings().all()
    return [dict(r) for r in rows]


def fit_user_calibration(
    conn: Connection, artifact, user_id: str, participant_id: str, as_of: dt.datetime
) -> tuple[ParticipantCalibration, list[str]]:
    """Run the frozen estimator on this user's completed, eligible episodes."""
    rows = _eligible_residuals(conn, user_id, as_of)
    model = PersonalizationModel(config=artifact.config.personalization)
    if not rows:
        return model.calibration_for(participant_id), []

    residuals = {
        h: np.asarray(
            [float(r["errors"]["residual_vs_cohort"].get(str(h), float("nan"))) for r in rows],
            dtype="float64",
        )
        for h in HORIZONS
    }
    ids = np.asarray([participant_id] * len(rows), dtype=object)
    model.fit(ids, residuals)
    return model.calibration_for(participant_id), [r["episode_id"] for r in rows]


def artifact_view(artifact, calibration: ParticipantCalibration):
    """A shallow copy of the loaded artifact carrying one extra calibration.

    The loaded artifact object and the file on disk are not modified: only the copy's
    `personalization` attribute is replaced, with a new container.
    """
    view = copy.copy(artifact)
    view.personalization = PersonalizationModel(
        config=artifact.config.personalization,
        calibrations={calibration.participant_id: calibration},
    )
    return view


def calibration_from_record(participant_id: str, record: dict) -> ParticipantCalibration:
    """Rebuild a stored calibration, for reproducing a prediction."""
    as_int = lambda d: {int(k): v for k, v in d.items()}  # noqa: E731
    return ParticipantCalibration(
        participant_id=participant_id,
        offsets=as_int(record["offsets_mg_dl"]),
        episode_counts=as_int(record["episode_counts"]),
        raw_offsets=as_int(record["raw_offsets_mg_dl"]),
        shrinkage_factors=as_int(record["shrinkage_factors"]),
        personalized=record["personalized"],
        fallback_reason=record["fallback_reason"],
    )


def calibration_record(calibration: ParticipantCalibration) -> dict:
    """Full-precision record. `as_dict()` rounds, which would break exact replay."""
    as_str = lambda d: {str(k): v for k, v in sorted(d.items())}  # noqa: E731
    return {
        "personalized": calibration.personalized,
        "fallback_reason": calibration.fallback_reason,
        "offsets_mg_dl": as_str(calibration.offsets),
        "raw_offsets_mg_dl": as_str(calibration.raw_offsets),
        "shrinkage_factors": as_str(calibration.shrinkage_factors),
        "episode_counts": as_str(calibration.episode_counts),
    }
