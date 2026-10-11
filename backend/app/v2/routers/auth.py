"""Accounts and sessions.

Self-contained email + password identity with opaque, revocable session tokens. The
team has not chosen an identity provider (master §17); this is the working default and
is isolated behind `deps.current_user`, so a hosted IdP can replace token verification
without touching any route. See DECISIONS D-004.
"""

from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, Depends, Response
from pydantic import Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import (
    MODEL_ID, V2Context, audit, consent_states, current_user, get_conn, get_ctx,
    participant_id_for, request_id,
)
from backend.app.v2.schemas import Strict

router = APIRouter(tags=["auth"])

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


class RegisterRequest(Strict):
    email: str = Field(max_length=320)
    password: str = Field(min_length=10, max_length=256)
    timezone: str = Field(max_length=64, description="IANA zone, e.g. America/New_York")
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL.match(value):
            raise ValueError("not a valid email address")
        return value

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        if not core.is_valid_timezone(value):
            raise ValueError("not a known IANA timezone")
        return value


class LoginRequest(Strict):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


class ProfilePatch(Strict):
    display_name: str | None = Field(default=None, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        if value is not None and not core.is_valid_timezone(value):
            raise ValueError("not a known IANA timezone")
        return value


class DeleteAccountRequest(Strict):
    password: str = Field(max_length=256)


def _user_payload(conn: Connection, user: dict) -> dict:
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "timezone": user["timezone"],
        "participant_id": participant_id_for(conn, user["user_id"]),
        "consents": list(consent_states(conn, user["user_id"]).values()),
        "created_at": core.rfc3339(user["created_at"]),
    }


def _issue_session(conn: Connection, ctx: V2Context, user_id: str) -> dict:
    token = core.new_token()
    now = ctx.now()
    expires = now + dt.timedelta(hours=ctx.settings.session_ttl_hours)
    conn.execute(
        t.auth_sessions.insert().values(
            session_id=core.new_id("ses"), user_id=user_id,
            token_hash=core.sha256_hex(token), created_at=now, expires_at=expires,
        )
    )
    return {"token": token, "token_type": "Bearer", "expires_at": core.rfc3339(expires)}


@router.post("/auth/register", status_code=201, summary="Create an account")
def register(
    body: RegisterRequest,
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
    rid: str = Depends(request_id),
) -> dict:
    if not ctx.settings.registration_open:
        raise core.V2Error(403, "registration_closed", "Registration is not open.")

    now = ctx.now()
    user_id = core.new_id("usr")
    try:
        with conn.begin_nested():
            conn.execute(
                t.users.insert().values(
                    user_id=user_id, email=body.email,
                    password_hash=core.hash_password(body.password),
                    display_name=body.display_name, timezone=body.timezone,
                    status="active", created_at=now, updated_at=now,
                )
            )
    except IntegrityError:
        raise core.conflict("email_taken", "An account with this email already exists.")

    # A fresh participant identity. It is never a research-dataset participant id, so
    # the frozen artifact holds no calibration for it and says so in `limitations`.
    conn.execute(
        t.participant_bindings.insert().values(
            participant_id=f"gtp_{user_id[4:]}", user_id=user_id,
            binding_kind="app_user", model_id=MODEL_ID, created_at=now,
        )
    )
    audit(conn, ctx, user_id=user_id, action="account.created", entity_type="user",
          entity_id=user_id, request_id=rid)
    user = dict(conn.execute(select(t.users).where(t.users.c.user_id == user_id)).mappings().one())
    return {"user": _user_payload(conn, user), "session": _issue_session(conn, ctx, user_id)}


@router.post("/auth/login", summary="Exchange credentials for a session token")
def login(
    body: LoginRequest,
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    row = conn.execute(
        select(t.users).where(t.users.c.email == body.email.strip().lower())
    ).mappings().first()
    # One message for both failures, so the endpoint does not reveal which emails exist.
    if row is None or not core.verify_password(body.password, row["password_hash"]):
        raise core.V2Error(401, "invalid_credentials", "Email or password is incorrect.")
    if row["status"] != "active":
        raise core.V2Error(403, "account_disabled", "This account is not active.")
    user = dict(row)
    return {"user": _user_payload(conn, user), "session": _issue_session(conn, ctx, user["user_id"])}


@router.post("/auth/logout", status_code=204, summary="Revoke the current session")
def logout(
    user: dict = Depends(current_user),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> Response:
    conn.execute(
        update(t.auth_sessions)
        .where(t.auth_sessions.c.session_id == user["session_id"])
        .values(revoked_at=ctx.now())
    )
    return Response(status_code=204)


@router.get("/me", summary="The signed-in user")
def me(user: dict = Depends(current_user), conn: Connection = Depends(get_conn)) -> dict:
    return _user_payload(conn, user)


@router.patch("/me", summary="Update display name or timezone")
def patch_me(
    body: ProfilePatch,
    user: dict = Depends(current_user),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if changes.get("timezone") is None:
        changes.pop("timezone", None)
    if changes:
        conn.execute(
            update(t.users).where(t.users.c.user_id == user["user_id"])
            .values(**changes, updated_at=ctx.now())
        )
        user = {**user, **changes}
    return _user_payload(conn, user)


@router.delete("/account", status_code=204, summary="Delete the account and all its data")
def delete_account(
    body: DeleteAccountRequest,
    user: dict = Depends(current_user),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
    rid: str = Depends(request_id),
) -> Response:
    """Irreversible. Removes every row owned by the user and every stored meal photo.

    Provider tokens are revoked upstream on a best-effort basis first; a provider that
    cannot be reached does not block deletion of our copy.
    """
    if not core.verify_password(body.password, user["password_hash"]):
        raise core.V2Error(401, "invalid_credentials", "Password is incorrect.")

    from backend.app.v2.services import devices as device_service

    user_id = user["user_id"]
    revoked = device_service.revoke_all(conn, ctx, user_id)
    keys = [
        row[0]
        for row in conn.execute(
            select(t.meal_photos.c.object_key).where(t.meal_photos.c.user_id == user_id)
        )
    ]
    conn.execute(t.users.delete().where(t.users.c.user_id == user_id))
    removed = sum(1 for key in keys if ctx.image_store.delete(key))
    audit(
        conn, ctx, user_id=user_id, action="account.deleted", entity_type="user",
        entity_id=user_id, request_id=rid,
        detail={"photos_removed": removed, "photos_expected": len(keys),
                "provider_revocations": revoked},
    )
    return Response(status_code=204)
