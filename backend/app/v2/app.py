"""The v2 application: authenticated, persistent, versioned.

Built as its own FastAPI application rather than as routers on the frozen v1 app. The
v1 application object is checksummed in the freeze manifest, so nothing may be added
to it; and v2 needs its own error envelope, its own CORS policy (bearer auth, PATCH,
PUT, DELETE) and its own OpenAPI document. `backend/app/v2/asgi.py` serves the two
side by side.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
import subprocess
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.v2 import core
from backend.app.v2.deps import MODEL_ID, V2Context

logger = logging.getLogger("glucotwin.v2")

V2_VERSION = "2.0.0-draft"

DESCRIPTION = """
GlucoTwin application API. Authenticated per-user storage and orchestration around the
frozen fs-1.3 forecasting model.

Research prototype. Forecasts, alerts, nutrition estimates and medication content are
informational. They are not a diagnosis and not medication or insulin dosing advice.
Prediction uncertainty is not calibrated.
""".strip()


def _envelope(request: Request, code: str, message: str, details: dict) -> dict:
    return {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", ""),
        "details": details,
    }


def _safe_field_errors(errors: list[dict]) -> list[dict]:
    """Field path and message only. The submitted value is dropped: request bodies
    carry health data, and an error response is the easiest place for it to leak."""
    return [
        {
            "field": ".".join(str(p) for p in e.get("loc", ()) if p != "body") or "(request)",
            "message": str(e.get("msg", "invalid value")),
            "type": str(e.get("type", "value_error")),
        }
        for e in errors
    ]


def model_integrity(prediction_service, expected_sha256: str | None) -> dict:
    """Checksum the artifact file the frozen service loaded.

    Read-only. The result is attached to every stored prediction, and when an expected
    checksum is configured a mismatch disables forecasting.
    """
    path = pathlib.Path(prediction_service.settings.model_artifact)
    sha = None
    if path.exists():
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
            cwd=pathlib.Path(__file__).resolve().parents[3],
        ).stdout.strip() or None
    except Exception:
        commit = None
    return {
        "artifact_path": str(path),
        "artifact_sha256": sha,
        "expected_sha256": expected_sha256,
        "verified": bool(sha) and (expected_sha256 is None or sha == expected_sha256),
        "enforced": expected_sha256 is not None,
        "source_commit": commit,
        "model_id": MODEL_ID,
    }


def create_v2_app(ctx: V2Context) -> FastAPI:
    app = FastAPI(
        title="GlucoTwin Application API",
        description=DESCRIPTION,
        version=V2_VERSION,
        openapi_url="/openapi.json",
        docs_url="/docs",
    )
    app.state.ctx = ctx

    if not ctx.model_integrity:
        ctx.model_integrity = model_integrity(
            ctx.prediction_service, ctx.settings.expected_model_sha256
        )
    if ctx.model_integrity["enforced"] and not ctx.model_integrity["verified"]:
        logger.error("model_integrity_failure forecasting disabled")

    if ctx.settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=ctx.settings.cors_origins,
            # Bearer tokens travel in a header, not a cookie, so credentials stay off.
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "If-Match"],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )

    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        request.state.request_id = f"req_{uuid.uuid4().hex[:20]}"
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(core.V2Error)
    async def _v2_error(request: Request, error: core.V2Error) -> JSONResponse:
        logger.info(
            "v2_error code=%s status=%s request_id=%s",
            error.code, error.status_code, getattr(request.state, "request_id", ""),
        )
        return JSONResponse(
            status_code=error.status_code,
            content=_envelope(request, error.code, error.message, error.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_envelope(
                request, "invalid_request", "The request did not satisfy the v2 contract.",
                {"errors": _safe_field_errors(list(error.errors()))},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(
            error.status_code, "invalid_request" if error.status_code < 500 else "internal_error"
        )
        return JSONResponse(
            status_code=error.status_code,
            content=_envelope(request, code, str(error.detail or "Request failed."), {}),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, error: Exception) -> JSONResponse:
        logger.exception("v2_unhandled type=%s", type(error).__name__)
        return JSONResponse(
            status_code=500,
            content=_envelope(request, "internal_error", "An unexpected internal error occurred.", {}),
        )

    from backend.app.v2.routers import (
        alerts, auth, cgm, devices, meals, medications, onboarding, system, wearables,
    )

    for module in (system, auth, onboarding, meals, cgm, devices, wearables, medications, alerts):
        app.include_router(module.router)

    logger.info(
        "v2_startup db=%s vision=%s model_verified=%s policy=%s",
        ctx.settings.app_db_url.split(":", 1)[0],
        "configured" if ctx.vision is not None else "not_configured",
        ctx.model_integrity["verified"],
        ctx.settings.personalization_policy,
    )
    return app
