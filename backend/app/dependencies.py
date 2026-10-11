"""FastAPI dependency wiring.

The `PredictionService` is created once per application and stashed on `app.state`,
so the model artifact is deserialized at startup rather than per request. The
dependency reads it back off the request's app, which keeps the service testable:
`tests/test_api.py` builds an app with its own settings and gets its own instance.
"""

from __future__ import annotations

from fastapi import Request

from backend.app.services.prediction import PredictionService


def get_service(request: Request) -> PredictionService:
    service: PredictionService = request.app.state.service
    return service
