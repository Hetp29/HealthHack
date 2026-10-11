"""Device and data-source connections."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.engine import Connection

from backend.app.v2.db import tables as t
from backend.app.v2.deps import (
    V2Context, audit, current_user, get_conn, get_ctx, request_id, require_processing_consent,
)
from backend.app.v2.providers import registry
from backend.app.v2.services import devices

router = APIRouter(tags=["devices"])


@router.get("/devices", summary="Connections and what each provider can actually do")
def list_devices(
    user: dict = Depends(current_user),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    rows = conn.execute(
        select(t.device_connections).where(t.device_connections.c.user_id == user["user_id"])
        .order_by(t.device_connections.c.created_at)
    ).mappings().all()
    return {
        "connections": [devices.connection_payload(dict(r)) for r in rows],
        "providers": registry.capability_matrix(ctx.settings),
        "status_meanings": {
            "authorized": "Account linked; no data received yet.",
            "connected": "Authentic observations from this source are stored.",
        },
    }


@router.post("/devices/{provider}/connect", summary="Begin an OAuth connection")
def connect(
    provider: str,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    """Returns the provider's authorization URL for the browser to open. Fails with
    `503 provider_not_configured` -- naming the missing settings -- when the server
    has no credentials for this provider."""
    return devices.start_connect(conn, ctx, user["user_id"], provider)


@router.get("/devices/{provider}/callback", summary="OAuth redirect target")
def callback(
    provider: str,
    state: str | None = Query(default=None, max_length=512),
    code: str | None = Query(default=None, max_length=2048),
    error: str | None = Query(default=None, max_length=128),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
):
    """Called by the provider's redirect, so it carries no bearer token. It is
    authenticated by the single-use `state` issued by `connect`."""
    result = devices.complete_callback(conn, ctx, provider, state=state, code=code, error=error)
    target = ctx.settings.frontend_return_url
    if target:
        separator = "&" if "?" in target else "?"
        return RedirectResponse(f"{target}{separator}provider={provider}&status={result['status']}",
                                status_code=303)
    return JSONResponse(result)


@router.post("/devices/{provider}/sync", summary="Pull new data from a connected provider")
def sync(
    provider: str,
    user: dict = Depends(require_processing_consent),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
) -> dict:
    """Always 200 when the provider was contacted: read `outcome`
    (`ok`, `authorization_revoked`, `rate_limited`, `provider_error`,
    `provider_unreachable`). A failed sync updates the connection's stored status."""
    return devices.sync(conn, ctx, user["user_id"], provider)


@router.delete("/devices/{provider}", summary="Disconnect a provider")
def disconnect(
    provider: str,
    user: dict = Depends(current_user),
    ctx: V2Context = Depends(get_ctx),
    conn: Connection = Depends(get_conn),
    rid: str = Depends(request_id),
) -> dict:
    result = devices.disconnect(conn, ctx, user["user_id"], provider)
    audit(conn, ctx, user_id=user["user_id"], action="device.disconnected", entity_type="device",
          entity_id=provider, request_id=rid,
          detail={"revoked_upstream": result["revoked_upstream"]})
    return result
