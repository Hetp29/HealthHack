"""Device connections: OAuth, token custody, sync, and what "connected" means.

Status vocabulary, deliberately strict:

    pending       a connect was started
    authorized    OAuth completed; NO data has been received yet
    connected     authentic observations from this source are stored
    error         the last attempt failed (`last_error_code` says how)
    revoked       the provider says the grant is gone
    disconnected  the user disconnected it here

A linked account is `authorized`. It becomes `connected` only when real observations
have actually arrived -- a successful login screen is not a data connection.

Tokens are encrypted at rest with Fernet and never leave this module: no endpoint
returns one, and no log line contains one.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import logging
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import and_, select, update
from sqlalchemy.engine import Connection

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import V2Context
from backend.app.v2.providers import registry
from backend.app.v2.providers.base import (
    ProviderAdapter, ProviderAuthError, ProviderError, ProviderRateLimited, ProviderRevoked,
)
from backend.app.v2.services import glucose, wearables

logger = logging.getLogger("glucotwin.v2.devices")

STATE_TTL = dt.timedelta(minutes=10)
REFRESH_MARGIN = dt.timedelta(seconds=60)


def _cipher(ctx: V2Context):
    from cryptography.fernet import Fernet

    return Fernet(ctx.settings.token_encryption_key.encode())


def _encrypt(ctx: V2Context, value: str | None) -> str | None:
    return None if not value else _cipher(ctx).encrypt(value.encode()).decode()


def _decrypt(ctx: V2Context, value: str | None) -> str | None:
    return None if not value else _cipher(ctx).decrypt(value.encode()).decode()


def _http(ctx: V2Context):
    if ctx.http_client_factory is not None:
        return ctx.http_client_factory()
    import httpx

    return httpx.Client(timeout=20.0)


def _adapter(ctx: V2Context, provider: str) -> ProviderAdapter:
    adapter = registry.oauth_adapters(ctx.settings).get(provider)
    if adapter is None:
        raise core.not_found("provider")
    return adapter


def _require_configured(ctx: V2Context, provider: str) -> None:
    missing = registry.missing_configuration(ctx.settings, provider)
    if missing:
        raise core.V2Error(
            503, "provider_not_configured",
            "This provider has not been set up on the server yet.",
            {"provider": provider, "missing_configuration": missing},
        )


def _redirect_uri(ctx: V2Context, provider: str) -> str:
    return f"{ctx.settings.public_api_base_url.rstrip('/')}/api/v2/devices/{provider}/callback"


def _connection(conn: Connection, user_id: str, provider: str) -> dict | None:
    row = conn.execute(select(t.device_connections).where(and_(
        t.device_connections.c.user_id == user_id,
        t.device_connections.c.provider == provider))).mappings().first()
    return dict(row) if row else None


def _set(conn: Connection, connection_id: str, **values) -> None:
    conn.execute(update(t.device_connections)
                 .where(t.device_connections.c.connection_id == connection_id).values(**values))


def connection_payload(row: dict) -> dict:
    """Public view of a connection. Token columns are never included."""
    return {
        "connection_id": row["connection_id"], "provider": row["provider"],
        "kind": row["kind"], "status": row["status"], "scopes": row["scopes"],
        "last_sync_at": core.rfc3339(row["last_sync_at"]),
        "last_success_at": core.rfc3339(row["last_success_at"]),
        "last_error_code": row["last_error_code"],
        "authentic_data_received": row["authentic_data_received_at"] is not None,
        "authentic_data_received_at": core.rfc3339(row["authentic_data_received_at"]),
        "created_at": core.rfc3339(row["created_at"]),
    }


# --------------------------------------------------------------------------- #
# connect
# --------------------------------------------------------------------------- #


def start_connect(conn: Connection, ctx: V2Context, user_id: str, provider: str) -> dict:
    adapter = _adapter(ctx, provider)
    _require_configured(ctx, provider)
    client = registry.oauth_client(ctx.settings, provider)
    now = ctx.now()

    state = core.new_token()
    verifier = core.new_token() + core.new_token() if adapter.spec.use_pkce else None
    redirect_uri = _redirect_uri(ctx, provider)
    conn.execute(t.oauth_states.insert().values(
        state_hash=core.sha256_hex(state), user_id=user_id, provider=provider,
        code_verifier=verifier, redirect_uri=redirect_uri, created_at=now,
        expires_at=now + STATE_TTL))

    existing = _connection(conn, user_id, provider)
    if existing is None:
        conn.execute(t.device_connections.insert().values(
            connection_id=core.new_id("dev"), user_id=user_id, provider=provider,
            kind=adapter.kind, status="pending", scopes=[], created_at=now, updated_at=now))

    params = {"response_type": "code", "client_id": client.client_id,
              "redirect_uri": redirect_uri, "scope": " ".join(adapter.spec.scopes),
              "state": state}
    if verifier:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        params.update(code_challenge=challenge.rstrip(b"=").decode(), code_challenge_method="S256")
    return {"provider": provider, "authorization_url": f"{adapter.spec.authorize_url}?{urlencode(params)}",
            "state_expires_at": core.rfc3339(now + STATE_TTL),
            "requested_scopes": list(adapter.spec.scopes)}


def _token_request(ctx: V2Context, adapter: ProviderAdapter, provider: str, form: dict) -> dict:
    client = registry.oauth_client(ctx.settings, provider)
    headers = {"Accept": "application/json"}
    if adapter.spec.client_auth == "basic":
        raw = f"{client.client_id}:{client.client_secret}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        form = {**form, "client_id": client.client_id}
    else:
        form = {**form, "client_id": client.client_id, "client_secret": client.client_secret}
    with _http(ctx) as http:
        response = http.post(adapter.spec.token_url, data=form, headers=headers)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        if payload.get("error") in ("invalid_grant", "invalid_token") or response.status_code == 401:
            raise ProviderRevoked("grant no longer valid")
        raise ProviderError(f"token endpoint returned HTTP {response.status_code}")
    if not payload.get("access_token"):
        raise ProviderError("token endpoint returned no access token")
    return payload


def _store_tokens(conn: Connection, ctx: V2Context, connection_id: str, payload: dict,
                  previous_refresh: str | None = None) -> None:
    now = ctx.now()
    expires = payload.get("expires_in")
    scope = payload.get("scope")
    values: dict[str, Any] = {
        "access_token_enc": _encrypt(ctx, payload["access_token"]),
        # Some providers rotate the refresh token, some omit it on refresh.
        "refresh_token_enc": _encrypt(ctx, payload.get("refresh_token") or previous_refresh),
        "token_expires_at": None if not isinstance(expires, (int, float)) else now + dt.timedelta(seconds=float(expires)),
        "updated_at": now,
    }
    if isinstance(scope, str):
        values["scopes"] = sorted(scope.replace(",", " ").split())
    _set(conn, connection_id, **values)


def complete_callback(conn: Connection, ctx: V2Context, provider: str, *, state: str | None,
                      code: str | None, error: str | None) -> dict:
    """Finish an OAuth round trip. Authenticated by the single-use `state`, since the
    provider's redirect carries no session token."""
    adapter = _adapter(ctx, provider)
    now = ctx.now()
    row = None
    if state:
        row = conn.execute(select(t.oauth_states).where(and_(
            t.oauth_states.c.state_hash == core.sha256_hex(state),
            t.oauth_states.c.provider == provider))).mappings().first()
    if row is None or row["used_at"] is not None or row["expires_at"] <= now:
        raise core.V2Error(400, "invalid_oauth_state",
                           "The authorization response is invalid, reused or expired.")
    conn.execute(update(t.oauth_states).where(t.oauth_states.c.state_hash == row["state_hash"])
                 .values(used_at=now))

    connection = _connection(conn, row["user_id"], provider)
    if connection is None:
        raise core.V2Error(400, "invalid_oauth_state", "No connection is awaiting authorization.")

    if error or not code:
        # The user declined, or the provider refused. Recorded, not treated as a crash.
        _set(conn, connection["connection_id"], status="error", updated_at=now,
             last_error_code="authorization_denied" if error == "access_denied" else "authorization_failed")
        return {"provider": provider, "status": "error",
                "error_code": "authorization_denied" if error == "access_denied" else "authorization_failed"}

    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": row["redirect_uri"]}
    if row["code_verifier"]:
        form["code_verifier"] = row["code_verifier"]
    try:
        payload = _token_request(ctx, adapter, provider, form)
    except ProviderError:
        _set(conn, connection["connection_id"], status="error", updated_at=now,
             last_error_code="token_exchange_failed")
        return {"provider": provider, "status": "error", "error_code": "token_exchange_failed"}

    _store_tokens(conn, ctx, connection["connection_id"], payload)
    granted = set(str(payload.get("scope") or " ".join(adapter.spec.scopes)).replace(",", " ").split())
    missing = sorted(set(adapter.spec.scopes) - granted)
    _set(conn, connection["connection_id"], status="authorized", last_error_code=None,
         external_user_id=payload.get("user_id") and str(payload["user_id"]))
    return {"provider": provider, "status": "authorized", "scopes_not_granted": missing,
            "note": "Authorized. No data has been received yet; run a sync."}


# --------------------------------------------------------------------------- #
# sync
# --------------------------------------------------------------------------- #


def _fresh_access_token(conn: Connection, ctx: V2Context, adapter: ProviderAdapter,
                        connection: dict, *, force: bool = False) -> str:
    access = _decrypt(ctx, connection["access_token_enc"])
    expires = connection["token_expires_at"]
    stale = force or access is None or (expires is not None and expires <= ctx.now() + REFRESH_MARGIN)
    if not stale:
        return access
    refresh = _decrypt(ctx, connection["refresh_token_enc"])
    if not refresh:
        raise ProviderRevoked("no refresh token")
    payload = _token_request(ctx, adapter, connection["provider"],
                             {"grant_type": "refresh_token", "refresh_token": refresh})
    _store_tokens(conn, ctx, connection["connection_id"], payload, previous_refresh=refresh)
    return payload["access_token"]


def sync(conn: Connection, ctx: V2Context, user_id: str, provider: str) -> dict:
    """Pull new data. Always returns a result describing what happened: a failed sync
    is a recorded state of the connection, so it is reported, not raised and lost."""
    adapter = _adapter(ctx, provider)
    _require_configured(ctx, provider)
    connection = _connection(conn, user_id, provider)
    if connection is None or connection["status"] in ("pending", "disconnected"):
        raise core.conflict("not_authorized", "This provider is not connected.",
                            {"provider": provider})
    if connection["status"] == "revoked":
        raise core.conflict("authorization_revoked",
                            "Access was revoked. Connect the provider again.", {"provider": provider})

    now = ctx.now()
    connection_id = connection["connection_id"]
    since = now - dt.timedelta(days=adapter.max_sync_days)
    if connection["sync_cursor"]:
        try:
            # Re-read a day of overlap: providers back-fill and revise recent data.
            since = max(since, dt.datetime.fromisoformat(connection["sync_cursor"]) - dt.timedelta(days=1))
        except ValueError:
            pass

    def failed(outcome: str, code: str, status: str = "error", **extra) -> dict:
        values = {"status": status, "last_sync_at": now, "last_error_code": code, "updated_at": now}
        if status == "revoked":
            values.update(access_token_enc=None, refresh_token_enc=None, token_expires_at=None)
        _set(conn, connection_id, **values)
        logger.info("device_sync provider=%s outcome=%s", provider, outcome)
        return {"provider": provider, "outcome": outcome, "synced": False,
                "connection": connection_payload(_connection(conn, user_id, provider)), **extra}

    try:
        token = _fresh_access_token(conn, ctx, adapter, connection)
        with _http(ctx) as http:
            try:
                result = adapter.sync_history(http, token, since, now, connection["sync_cursor"])
            except ProviderAuthError:
                # The token was rejected early; refresh once and retry.
                connection = _connection(conn, user_id, provider)
                token = _fresh_access_token(conn, ctx, adapter, connection, force=True)
                result = adapter.sync_history(http, token, since, now, connection["sync_cursor"])
    except ProviderRevoked:
        return failed("authorization_revoked", "authorization_revoked", status="revoked")
    except ProviderAuthError:
        return failed("authorization_revoked", "authorization_revoked", status="revoked")
    except ProviderRateLimited as error:
        return failed("rate_limited", "rate_limited", retry_after_seconds=error.retry_after_seconds)
    except ProviderError:
        return failed("provider_error", "provider_error")
    except Exception as error:  # network failure, malformed payload
        logger.warning("device_sync provider=%s failed type=%s", provider, type(error).__name__)
        return failed("provider_unreachable", "provider_unreachable")

    data_class = getattr(adapter, "data_class", "user_device")
    summary: dict = {}
    received_any = False
    if result.glucose:
        summary["glucose"] = glucose.ingest_readings(
            conn, ctx, user_id, provider, result.glucose, source_format=f"{provider}_api",
            data_class=data_class, connection_id=connection_id)["counts"]
        counts = summary["glucose"]
        received_any |= bool(counts["inserted"] or counts["duplicates"] or counts["corrected"])
    if result.wearable:
        summary["wearable"] = wearables.ingest(conn, ctx, user_id, provider, result.wearable,
                                               data_class=data_class)
        counts = summary["wearable"]
        received_any |= bool(counts["inserted"] or counts["duplicates"] or counts["updated"])

    current = _connection(conn, user_id, provider)
    values: dict[str, Any] = {"last_sync_at": now, "last_success_at": now, "last_error_code": None,
                              "sync_cursor": result.cursor, "updated_at": now}
    if result.external_user_id:
        values["external_user_id"] = result.external_user_id
    if received_any and data_class == "user_device":
        values["status"] = "connected"
        values["authentic_data_received_at"] = current["authentic_data_received_at"] or now
    elif current["status"] in ("error", "connected") and not received_any and \
            current["authentic_data_received_at"] is None:
        values["status"] = "authorized"
    elif current["status"] == "error":
        values["status"] = "connected" if current["authentic_data_received_at"] else "authorized"
    _set(conn, connection_id, **values)
    logger.info("device_sync provider=%s outcome=ok", provider)
    return {"provider": provider, "outcome": "ok", "synced": True, "data_class": data_class,
            "summary": summary, "unavailable_metrics": result.unavailable,
            "connection": connection_payload(_connection(conn, user_id, provider))}


# --------------------------------------------------------------------------- #
# disconnect
# --------------------------------------------------------------------------- #


def _revoke_upstream(ctx: V2Context, adapter: ProviderAdapter, provider: str, connection: dict) -> bool:
    spec = adapter.spec
    token = _decrypt(ctx, connection["access_token_enc"])
    if not spec.revoke_url or not token:
        return False
    try:
        with _http(ctx) as http:
            if spec.revoke_method == "DELETE":
                response = http.request("DELETE", spec.revoke_url,
                                        headers={"Authorization": f"Bearer {token}"})
            else:
                client = registry.oauth_client(ctx.settings, provider)
                raw = f"{client.client_id}:{client.client_secret}".encode()
                response = http.post(spec.revoke_url, data={"token": token},
                                     headers={"Authorization": "Basic " + base64.b64encode(raw).decode()})
        return response.status_code < 400
    except Exception:
        return False


def disconnect(conn: Connection, ctx: V2Context, user_id: str, provider: str) -> dict:
    """Stop using a source. Tokens are destroyed here whether or not the provider
    could be told; `revoked_upstream` reports which."""
    connection = _connection(conn, user_id, provider)
    if connection is None:
        raise core.not_found("device connection")
    revoked_upstream = False
    adapter = registry.oauth_adapters(ctx.settings).get(provider)
    if adapter is not None and ctx.settings.token_encryption_key and connection["access_token_enc"]:
        revoked_upstream = _revoke_upstream(ctx, adapter, provider, connection)
    _set(conn, connection["connection_id"], status="disconnected", access_token_enc=None,
         refresh_token_enc=None, token_expires_at=None, updated_at=ctx.now())
    return {"provider": provider, "status": "disconnected", "revoked_upstream": revoked_upstream,
            "upstream_revocation_supported": bool(adapter and adapter.spec.revoke_url),
            "stored_data_retained": True}


def revoke_all(conn: Connection, ctx: V2Context, user_id: str) -> dict:
    out = {}
    rows = conn.execute(select(t.device_connections.c.provider).where(
        t.device_connections.c.user_id == user_id)).all()
    for (provider,) in rows:
        out[provider] = disconnect(conn, ctx, user_id, provider)["revoked_upstream"]
    return out
