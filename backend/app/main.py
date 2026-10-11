"""GlucoTwin FastAPI application factory.

    uvicorn backend.app.main:app --reload

Serves contract v1.0 (`contracts/openapi.yaml`). The application is built by a
factory so tests can construct an isolated instance with their own settings rather
than mutating a module-level global.
"""

from __future__ import annotations

import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import Settings, load_settings
from backend.app.dependencies import get_service
from backend.app.errors import register_error_handlers
from backend.app.routes import model as model_routes
from backend.app.routes import participants as participant_routes
from backend.app.routes import predict as predict_routes
from backend.app.schemas import HealthResponse
from backend.app.services.prediction import PredictionService

logger = logging.getLogger("glucotwin")

DESCRIPTION = """
GlucoTwin postprandial glucose response forecasting API, contract v1.0.

Research prototype. Not a medical device, not for clinical decision-making, and it
offers no medication dosing, diagnostic or treatment advice.

`GET /health` reports liveness only. For readiness, read `GET /api/model/info`:
`model_version` is the literal string `"none"` when no trained artifact is loaded.
""".strip()


def configure_logging(level: str) -> None:
    """Single-line structured-ish logs to stdout, suitable for a container runtime."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root = logging.getLogger("glucotwin")
    root.handlers = [handler]
    root.setLevel(getattr(logging, level, logging.INFO))
    root.propagate = False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="GlucoTwin API",
        description=DESCRIPTION,
        version=settings.contract_version,
        openapi_url="/openapi.json",
        docs_url="/docs",
    )

    if settings.cors_origins:
        if settings.cors_allows_wildcard:
            logger.warning(
                "cors_wildcard_configured origins=* -- restrict GLUCOTWIN_CORS_ORIGINS "
                "to explicit frontend origins before any judged deployment"
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            # No credentials are used: the API has no auth and no cookies, so allowing
            # them would widen the surface for nothing.
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type"],
            max_age=600,
        )

    register_error_handlers(app)

    service = PredictionService(settings)
    service.load()
    app.state.settings = settings
    app.state.service = service

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="Liveness probe",
    )
    def health() -> HealthResponse:
        # Intentionally independent of model and data-layer readiness, so an
        # orchestrator can distinguish "process up" from "model ready".
        return HealthResponse(status="ok", version=settings.contract_version)

    app.include_router(participant_routes.router)
    app.include_router(predict_routes.router)
    app.include_router(model_routes.router)

    logger.info(
        "startup contract=%s data_backend=%s model_ready=%s metrics_available=%s "
        "cors_origins=%s",
        settings.contract_version,
        settings.data_backend,
        service.model_ready,
        service.metrics_available,
        ",".join(settings.cors_origins) or "(none)",
    )
    return app


#: Module-level ASGI app for `uvicorn backend.app.main:app`.
app = create_app()

__all__ = ["app", "create_app", "get_service"]
