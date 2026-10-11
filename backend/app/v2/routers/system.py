"""Readiness and capability reporting. No authentication, no personal data."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from backend.app.v2 import core
from backend.app.v2.deps import V2Context, get_ctx

router = APIRouter(tags=["system"])


@router.get("/ready", summary="Readiness: database, model and artifact integrity")
def ready(ctx: V2Context = Depends(get_ctx)) -> dict:
    checks = {"database": False, "model_loaded": False, "model_integrity": False}
    try:
        with ctx.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        pass
    checks["model_loaded"] = bool(ctx.prediction_service.model_ready)
    checks["model_integrity"] = bool(ctx.model_integrity.get("verified"))
    if not all(checks.values()):
        raise core.V2Error(503, "not_ready", "The service is not ready.", {"checks": checks})
    return {"status": "ready", "checks": checks}


@router.get("/status", summary="Model identity and which integrations are configured")
def status(ctx: V2Context = Depends(get_ctx)) -> dict:
    from backend.app.v2.providers.registry import capability_matrix

    integrity = ctx.model_integrity
    return {
        "model": {
            "model_id": integrity.get("model_id"),
            "artifact_sha256": integrity.get("artifact_sha256"),
            "integrity_verified": integrity.get("verified"),
            "integrity_enforced": integrity.get("enforced"),
            "loaded": bool(ctx.prediction_service.model_ready),
        },
        "personalization_policy": ctx.settings.personalization_policy,
        "demo_mode": ctx.settings.demo_mode,
        "meal_analysis": {
            "provider": "openai",
            "configured": ctx.vision is not None,
            "model": ctx.settings.openai_vision_model,
        },
        "providers": capability_matrix(ctx.settings),
        "notification_channels": {
            "in_app": {"available": True},
            "push": {"available": False, "reason": "no push service is configured"},
            "email": {"available": False, "reason": "no email service is configured"},
        },
    }
