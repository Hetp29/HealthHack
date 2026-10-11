"""Request-scoped dependencies: application context, database, identity, consent.

The rule this module enforces: **the acting user comes from the session token and from
nowhere else.** No route accepts a `user_id` from a path, query or body.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.engine import Connection, Engine

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.settings import V2Settings

MODEL_ID = "glucotwin-fs13-blend-personalized"

CONSENT_NECESSARY = "necessary_processing"
CONSENT_PERSONALIZATION = "personalization_history"
CONSENT_TYPES = (CONSENT_NECESSARY, CONSENT_PERSONALIZATION)


@dataclass
class V2Context:
    """Everything a request handler may reach. Built once per application."""

    settings: V2Settings
    engine: Engine
    #: The frozen v1 `PredictionService`. v2 reads its loaded artifact; it never
    #: reloads, replaces or mutates it.
    prediction_service: Any
    image_store: Any
    #: `None` when OpenAI is not configured.
    vision: Any = None
    #: Factory for outbound HTTP clients, replaceable in tests.
    http_client_factory: Callable[[], Any] | None = None
    clock: Callable[[], dt.datetime] = core.now_utc
    model_integrity: dict = field(default_factory=dict)
    #: Test hook: a bundle to replay instead of the one on disk.
    demo_bundle: Any = None

    def now(self) -> dt.datetime:
        """The current instant for whoever is being served.

        The wall clock, except inside a request (or a replay step) for a demo account,
        where it is that account's replay position.
        """
        return core.clock_override.get() or self.clock()

    def wall_now(self) -> dt.datetime:
        """The real clock, regardless of any replay. For sessions and bookkeeping."""
        return self.clock()


def get_ctx(request: Request) -> V2Context:
    return request.app.state.ctx


def get_conn(ctx: V2Context = Depends(get_ctx)) -> Iterator[Connection]:
    """One transaction per request: committed on success, rolled back on any error."""
    with ctx.engine.begin() as conn:
        yield conn


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


async def read_limited_body(request: Request, limit: int, code: str) -> bytes:
    """Read a raw upload with a hard ceiling, without buffering an oversized body."""
    declared = request.headers.get("content-length")
    too_large = core.V2Error(413, code, "The upload exceeds the size limit.", {"max_bytes": limit})
    if declared and declared.isdigit() and int(declared) > limit:
        raise too_large
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


def _authenticate(
    request: Request,
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise core.V2Error(401, "unauthenticated", "A bearer session token is required.")

    row = conn.execute(
        select(t.users, t.auth_sessions.c.session_id, t.auth_sessions.c.expires_at,
               t.auth_sessions.c.revoked_at,
               t.demo_replay_state.c.position.label("replay_position"))
        .select_from(
            t.auth_sessions.join(t.users)
            .outerjoin(t.demo_replay_state, t.demo_replay_state.c.user_id == t.users.c.user_id))
        .where(t.auth_sessions.c.token_hash == core.sha256_hex(token.strip()))
    ).mappings().first()

    # Sessions always live on the real clock, including a demo account's.
    if row is None or row["revoked_at"] is not None or row["expires_at"] <= ctx.wall_now():
        raise core.V2Error(401, "session_expired", "The session is invalid or has expired.")
    if row["status"] != "active":
        raise core.V2Error(403, "account_disabled", "This account is not active.")

    user = dict(row)
    user["is_demo"] = user["replay_position"] is not None
    request.state.user_id = user["user_id"]
    request.state.session_id = user["session_id"]
    return user


async def current_user(user: dict = Depends(_authenticate)) -> dict:
    """The signed-in user, with the request's clock set for them.

    Deliberately `async` and deliberately does no I/O: a context variable set here, in
    the request's own task, is inherited by the worker thread that runs the endpoint.
    Set from the synchronous `_authenticate` it would be lost when that thread returns.
    """
    core.clock_override.set(user["replay_position"])
    return user


# --------------------------------------------------------------------------- #
# consent
# --------------------------------------------------------------------------- #


def consent_states(conn: Connection, user_id: str) -> dict[str, dict]:
    """Current state of each consent type: the most recent record wins."""
    out: dict[str, dict] = {
        name: {"consent_type": name, "state": "not_recorded", "recorded_at": None,
               "policy_version": None}
        for name in CONSENT_TYPES
    }
    rows = conn.execute(
        select(t.consent_records)
        .where(t.consent_records.c.user_id == user_id)
        .order_by(t.consent_records.c.recorded_at, t.consent_records.c.consent_id)
    ).mappings()
    for row in rows:
        out[row["consent_type"]] = {
            "consent_type": row["consent_type"],
            "state": row["state"],
            "recorded_at": core.rfc3339(row["recorded_at"]),
            "policy_version": row["policy_version"],
        }
    return out


def has_consent(conn: Connection, user_id: str, consent_type: str) -> bool:
    return consent_states(conn, user_id)[consent_type]["state"] == "granted"


def require_processing_consent(
    user: dict = Depends(current_user), conn: Connection = Depends(get_conn)
) -> dict:
    """Gate for every endpoint that stores or processes health data."""
    if not has_consent(conn, user["user_id"], CONSENT_NECESSARY):
        raise core.V2Error(
            403,
            "consent_required",
            "Consent to necessary meal and glucose processing has not been granted.",
            {"consent_type": CONSENT_NECESSARY},
        )
    return user


# --------------------------------------------------------------------------- #
# bindings and audit
# --------------------------------------------------------------------------- #


def participant_id_for(conn: Connection, user_id: str) -> str:
    """The model participant bound to this user.

    Raises rather than inventing one: a forecast must never be attributed to a
    participant identity that was not deliberately created for this account.
    """
    participant_id = conn.execute(
        select(t.participant_bindings.c.participant_id).where(
            t.participant_bindings.c.user_id == user_id
        )
    ).scalar()
    if participant_id is None:
        raise core.V2Error(
            409, "participant_binding_missing",
            "This account has no model participant binding.",
        )
    return participant_id


def audit(
    conn: Connection,
    ctx: V2Context,
    *,
    user_id: str | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    detail: dict | None = None,
    actor: str = "user",
    request_id: str | None = None,
) -> None:
    conn.execute(
        t.audit_events.insert().values(
            audit_id=core.new_id("aud"),
            user_id=user_id,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail or {},
            request_id=request_id or None,
            created_at=ctx.now(),
        )
    )
