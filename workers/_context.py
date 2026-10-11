"""Build the application context for a worker process (no HTTP server)."""

from __future__ import annotations

from backend.app.config import load_settings
from backend.app.main import configure_logging
from backend.app.services.prediction import PredictionService
from backend.app.v2.app import model_integrity
from backend.app.v2.asgi import build_context
from backend.app.v2.deps import V2Context
from backend.app.v2.settings import load_v2_settings


def build_worker_context() -> V2Context:
    v1_settings = load_settings()
    configure_logging(v1_settings.log_level)
    service = PredictionService(v1_settings)
    service.load()
    v2_settings = load_v2_settings()
    ctx = build_context(v2_settings, service)
    ctx.model_integrity = model_integrity(service, v2_settings.expected_model_sha256)
    return ctx
