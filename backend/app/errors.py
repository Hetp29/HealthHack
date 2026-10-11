"""The uniform error envelope.

Every non-2xx response the service emits -- including FastAPI's own request-validation
failures, which are translated -- has the shape

    {"code": "<stable_code>", "message": "<short, safe>", "detail": {...}}

`detail` is always a JSON object, never a bare string or array, so a client can render
it structurally. `code` is the stable thing to branch on; `message` is for humans and
may be reworded.

Two rules the handlers enforce:
  * no stack traces, internal paths or physiological values reach the client;
  * an unexpected exception becomes a generic `internal_error` while the real
    traceback goes to the server log.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("glucotwin.errors")

# Stable machine-readable codes. Mirrors the enum in contracts/openapi.yaml.
PARTICIPANT_NOT_FOUND = "participant_not_found"
INVALID_REQUEST = "invalid_request"
NAIVE_DATETIME = "naive_datetime"
FUTURE_CONTEXT_OVERRIDE = "future_context_override"
INSUFFICIENT_PREMEAL_CONTEXT = "insufficient_premeal_context"
MODEL_NOT_TRAINED = "model_not_trained"
DATA_LAYER_UNAVAILABLE = "data_layer_unavailable"
NOT_IMPLEMENTED = "not_implemented"
INTERNAL_ERROR = "internal_error"


class ApiError(Exception):
    """A deliberate, client-facing failure with a stable code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail or {}

    def as_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


def participant_not_found(participant_id: str) -> ApiError:
    return ApiError(
        status.HTTP_404_NOT_FOUND,
        PARTICIPANT_NOT_FOUND,
        "Unknown participant identifier.",
        {"participant_id": participant_id},
    )


def model_not_trained(artifact_path: str, reason: str | None = None) -> ApiError:
    detail: dict[str, Any] = {"artifact_path": artifact_path}
    if reason:
        detail["reason"] = reason
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        MODEL_NOT_TRAINED,
        "No trained model artifact is available. Run `python -m ml.train`.",
        detail,
    )


def data_layer_unavailable(backend: str, reason: str) -> ApiError:
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        DATA_LAYER_UNAVAILABLE,
        "The data layer could not be reached.",
        {"backend": backend, "reason": reason},
    )


def not_implemented(what: str) -> ApiError:
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        NOT_IMPLEMENTED,
        f"{what} is reserved by contract v1.0 but is not implemented yet.",
        {"endpoint": what},
    )


def invalid_request(message: str, detail: dict[str, Any] | None = None) -> ApiError:
    return ApiError(
        status.HTTP_422_UNPROCESSABLE_ENTITY, INVALID_REQUEST, message, detail
    )


def _classify_validation_error(errors: list[dict[str, Any]]) -> str:
    """Pick the most specific code for a Pydantic validation failure."""
    for error in errors:
        error_type = str(error.get("type", ""))
        message = str(error.get("msg", "")).lower()
        if "timezone" in message or error_type in {
            "timezone_aware",
            "datetime_aware",
        }:
            return NAIVE_DATETIME
        if "offset-aware" in message or "offset-naive" in message:
            return NAIVE_DATETIME
    return INVALID_REQUEST


def _safe_field_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce Pydantic errors to field/message pairs with no echoed input values.

    The submitted value is dropped deliberately: request bodies carry participant
    identifiers and physiological numbers, and an error response is the easiest place
    for those to end up in a log aggregator.
    """
    safe = []
    for error in errors:
        location = [str(part) for part in error.get("loc", ()) if part != "body"]
        safe.append(
            {
                "field": ".".join(location) or "(request)",
                "message": str(error.get("msg", "invalid value")),
                "type": str(error.get("type", "value_error")),
            }
        )
    return safe


def register_error_handlers(app: FastAPI) -> None:
    """Install handlers so every failure path produces the envelope."""

    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, error: ApiError) -> JSONResponse:
        if error.status_code >= 500:
            logger.error("api_error code=%s message=%s", error.code, error.message)
        else:
            logger.info("api_error code=%s status=%s", error.code, error.status_code)
        return JSONResponse(status_code=error.status_code, content=error.as_payload())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, error: RequestValidationError
    ) -> JSONResponse:
        errors = list(error.errors())
        code = _classify_validation_error(errors)
        message = (
            "A datetime was supplied without an explicit UTC offset."
            if code == NAIVE_DATETIME
            else "The request did not satisfy contract v1.0."
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": code,
                "message": message,
                "detail": {"errors": _safe_field_errors(errors)},
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        _: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        # Routing-level failures (404 on an unknown path, 405) still get the envelope.
        return JSONResponse(
            status_code=error.status_code,
            content={
                "code": INVALID_REQUEST
                if error.status_code < 500
                else INTERNAL_ERROR,
                "message": str(error.detail) if error.detail else "Request failed.",
                "detail": {"status_code": error.status_code},
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, error: Exception) -> JSONResponse:
        # The traceback goes to the log; the client gets nothing exploitable.
        logger.exception("unhandled_exception type=%s", type(error).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": INTERNAL_ERROR,
                "message": "An unexpected internal error occurred.",
                "detail": {},
            },
        )
