"""Questionnaire answers and consent.

The question bank, its wording and its skip logic belong to Neil's UI. The server
stores answers keyed by question id, validates the handful whose units matter, and
keeps consent as its own append-only, auditable record.

**No answer stored here is ever a model input.** The frozen feature vector has no slot
for any of it, and the orchestrator does not read this table.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import (
    CONSENT_NECESSARY, CONSENT_TYPES, V2Context, audit, consent_states, current_user,
    get_conn, get_ctx, request_id,
)
from backend.app.v2.schemas import Strict

router = APIRouter(tags=["onboarding"])

#: Question ids the server recognizes, by section. Values are stored as given; the
#: ones with units are checked in `_validate_answers`.
QUESTION_IDS: dict[str, tuple[str, ...]] = {
    "basics": ("date_of_birth", "sex_assigned_at_birth", "height", "weight", "primary_goal"),
    "metabolic": ("diabetes_status", "diagnosis_year", "hba1c", "fasting_glucose", "clinician_plan"),
    "monitoring": ("monitoring_method", "cgm_brand", "cgm_connect_preference", "cgm_usage_duration"),
    "medications": ("takes_glucose_medication", "medication_names", "medication_schedule",
                    "uses_insulin", "wants_medication_reminders", "discontinued_medications"),
    "meals": ("meals_per_day", "meal_time_ranges", "dietary_pattern", "dietary_restrictions",
              "meal_logging_method"),
    "wearables": ("uses_wearable", "wearable_platform", "wearable_connect_preference",
                  "wearable_permissions"),
    "alerts": ("wants_spike_alerts", "alert_threshold", "alert_channels", "quiet_hours"),
}
KNOWN_QUESTIONS = frozenset(q for ids in QUESTION_IDS.values() for q in ids)

#: Answers that are a measurement and therefore must state their unit.
_UNIT_RULES: dict[str, tuple[str, ...]] = {
    "height": ("cm", "in"),
    "weight": ("kg", "lb"),
    "hba1c": ("%", "mmol/mol"),
    "fasting_glucose": ("mg/dL", "mmol/L"),
    "alert_threshold": ("mg/dL", "mmol/L"),
}

_UNKNOWN = {"unknown", "prefer_not_to_say"}


class OnboardingPatch(Strict):
    #: Question id -> answer. `null` clears an answer.
    answers: dict[str, Any] = Field(default_factory=dict)
    completed: bool | None = None
    #: The `version` last read. When supplied and stale, the write is refused with 409.
    expected_version: int | None = Field(default=None, ge=0)


class ConsentPut(Strict):
    state: Literal["granted", "withdrawn"]


def _validate_answers(answers: dict[str, Any]) -> None:
    unknown = sorted(set(answers) - KNOWN_QUESTIONS)
    if unknown:
        raise core.invalid("Unknown question id.", {"unknown_question_ids": unknown})
    for question, units in _UNIT_RULES.items():
        value = answers.get(question)
        if value is None or (isinstance(value, str) and value in _UNKNOWN):
            continue
        if not isinstance(value, dict) or set(value) != {"value", "unit"}:
            raise core.invalid(
                f"{question} must be an object with exactly 'value' and 'unit', or 'unknown'.",
                {"field": question, "allowed_units": list(units)},
            )
        if value["unit"] not in units:
            # Never guessed: 6.5 is an HbA1c in percent and a plausible glucose in mmol/L.
            raise core.invalid(
                f"{question} unit is not recognized.",
                {"field": question, "allowed_units": list(units)},
            )
        number = core.require_finite(value["value"], question)
        if number <= 0:
            raise core.invalid(f"{question} must be positive.", {"field": question})


def _payload(row: dict | None) -> dict:
    if row is None:
        return {"answers": {}, "completed": False, "version": 0, "updated_at": None,
                "sections": {k: list(v) for k, v in QUESTION_IDS.items()}}
    return {
        "answers": row["answers"], "completed": row["completed"], "version": row["version"],
        "updated_at": core.rfc3339(row["updated_at"]),
        "sections": {k: list(v) for k, v in QUESTION_IDS.items()},
    }


def _load(conn: Connection, user_id: str) -> dict | None:
    row = conn.execute(
        select(t.onboarding_responses).where(t.onboarding_responses.c.user_id == user_id)
    ).mappings().first()
    return dict(row) if row else None


@router.get("/onboarding", summary="Saved questionnaire answers")
def get_onboarding(user: dict = Depends(current_user), conn: Connection = Depends(get_conn)) -> dict:
    return _payload(_load(conn, user["user_id"]))


@router.patch("/onboarding", summary="Merge questionnaire answers (incremental save)")
def patch_onboarding(
    body: OnboardingPatch,
    user: dict = Depends(current_user),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    _validate_answers(body.answers)
    existing = _load(conn, user["user_id"])
    current_version = existing["version"] if existing else 0
    if body.expected_version is not None and body.expected_version != current_version:
        raise core.conflict(
            "version_conflict", "The questionnaire was changed elsewhere; reload and retry.",
            {"current_version": current_version},
        )

    merged = dict(existing["answers"]) if existing else {}
    for question, value in body.answers.items():
        if value is None:
            merged.pop(question, None)
        else:
            merged[question] = value
    completed = body.completed if body.completed is not None else bool(existing and existing["completed"])
    values = {"answers": merged, "completed": completed, "version": current_version + 1,
              "updated_at": ctx.now()}
    if existing:
        conn.execute(
            t.onboarding_responses.update()
            .where(t.onboarding_responses.c.user_id == user["user_id"]).values(**values)
        )
    else:
        conn.execute(t.onboarding_responses.insert().values(user_id=user["user_id"], **values))
    return _payload({**values})


@router.get("/consents", summary="Current state of each consent")
def get_consents(user: dict = Depends(current_user), conn: Connection = Depends(get_conn)) -> dict:
    return {"consents": list(consent_states(conn, user["user_id"]).values())}


@router.put("/consents/{consent_type}", summary="Grant or withdraw one consent")
def put_consent(
    consent_type: str,
    body: ConsentPut,
    user: dict = Depends(current_user),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
    rid: str = Depends(request_id),
) -> dict:
    """Appends a record; nothing is overwritten, so the history stays auditable.

    Withdrawing `necessary_processing` immediately blocks every endpoint that stores or
    processes health data. It does not delete what is already stored: deletion is the
    separate, explicit `DELETE /account`. Withdrawing `personalization_history` stops
    completed meals being used as personal history from the next forecast onward.
    """
    if consent_type not in CONSENT_TYPES:
        raise core.not_found("consent type")
    conn.execute(
        t.consent_records.insert().values(
            consent_id=core.new_id("con"), user_id=user["user_id"], consent_type=consent_type,
            state=body.state, policy_version=ctx.settings.consent_policy_version,
            recorded_at=ctx.now(),
        )
    )
    audit(conn, ctx, user_id=user["user_id"], action=f"consent.{body.state}",
          entity_type="consent", entity_id=consent_type, request_id=rid,
          detail={"policy_version": ctx.settings.consent_policy_version})
    states = consent_states(conn, user["user_id"])
    return {
        "consents": list(states.values()),
        "processing_allowed": states[CONSENT_NECESSARY]["state"] == "granted",
    }
