"""Shared setup for the demo command-line tools."""

from __future__ import annotations

import dataclasses
import pathlib
import sys
from typing import NoReturn

FROZEN_ARTIFACT = pathlib.Path("ml/artifacts_cgmacros_fs13/model.joblib")
FROZEN_SHA256 = "12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe"


def fail(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def build_app(*, app_db_url: str | None = None, image_dir: pathlib.Path | None = None,
              demo_password: str | None = None, bundle=None, create_schema: bool = False):
    """The real application, wired for a demo: frozen artifact, checksum enforced,
    demo mode on. Returns `(v2_app, ctx)`.

    With `app_db_url` it runs against a scratch database (verification, selection);
    without, against the database configured in the environment (seeding).
    """
    from backend.app.config import load_settings
    from backend.app.main import configure_logging
    from backend.app.services.prediction import PredictionService
    from backend.app.v2.app import create_v2_app, model_integrity
    from backend.app.v2.db.engine import create_all, make_engine
    from backend.app.v2.deps import V2Context
    from backend.app.v2.services.images import build_image_store
    from backend.app.v2.services.vision import build_vision_analyzer
    from backend.app.v2.settings import load_v2_settings

    if not FROZEN_ARTIFACT.exists():
        fail(f"the frozen fs-1.3 artifact is not at {FROZEN_ARTIFACT}. It is not in git; obtain "
             "it from the team's artifact store. The demo never runs on any other model.")

    v1_settings = dataclasses.replace(
        load_settings(), model_artifact=FROZEN_ARTIFACT,
        metrics_path=FROZEN_ARTIFACT.parent / "metrics.json", log_level="WARNING")
    configure_logging("WARNING")
    service = PredictionService(v1_settings)
    service.load()

    settings = load_v2_settings()
    changes = {"demo_mode": True, "expected_model_sha256": FROZEN_SHA256,
               "personalization_policy": "history_only"}
    if app_db_url:
        changes["app_db_url"] = app_db_url
    if image_dir:
        changes["image_dir"] = image_dir
    if demo_password:
        changes["demo_password"] = demo_password
    settings = dataclasses.replace(settings, **changes)
    if not settings.demo_password:
        fail("GLIMPSE_DEMO_PASSWORD is not set. Choose a password (10+ characters) for the "
             "demo account and export it.")

    engine = make_engine(settings.app_db_url)
    if create_schema:
        create_all(engine)
    ctx = V2Context(settings=settings, engine=engine, prediction_service=service,
                    image_store=build_image_store(settings), vision=build_vision_analyzer(settings))
    ctx.model_integrity = model_integrity(service, FROZEN_SHA256)
    ctx.demo_bundle = bundle
    return create_v2_app(ctx), ctx


def fail_with(error) -> NoReturn:
    """Report an application error (`core.V2Error`) and exit non-zero."""
    detail = f" {error.details}" if error.details else ""
    fail(f"{error.code}: {error.message}{detail}")


def run_guarded(function):
    """Turn an application error into a clear message and a non-zero exit."""
    from backend.app.v2 import core

    try:
        return function()
    except core.V2Error as error:
        fail_with(error)
