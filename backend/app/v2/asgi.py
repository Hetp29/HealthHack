"""Deployment entry point: the frozen v1 API and the v2 application, side by side.

    uvicorn backend.app.v2.asgi:app --host 0.0.0.0 --port 8000

Requests under `/api/v2` go to the v2 application. **Everything else is handed to the
unmodified v1 application object**, exactly as `backend.app.main:create_app` builds it:
same routes, same middleware, same error envelope. v1 is not wrapped, re-registered or
re-configured, which is what keeps the seven frozen endpoints byte-identical.
"""

from __future__ import annotations

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.v2.app import create_v2_app
from backend.app.v2.db.engine import make_engine
from backend.app.v2.deps import V2Context
from backend.app.v2.settings import V2Settings, load_v2_settings

V2_PREFIX = "/api/v2"


class PlatformApp:
    """A two-way ASGI dispatcher. No behaviour of its own beyond routing by prefix."""

    def __init__(self, v1_app, v2_app) -> None:
        self.v1 = v1_app
        self.v2 = v2_app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == V2_PREFIX or path.startswith(V2_PREFIX + "/"):
                scope = dict(scope)
                scope["root_path"] = scope.get("root_path", "") + V2_PREFIX
                scope["path"] = path[len(V2_PREFIX):] or "/"
                if scope.get("raw_path") is not None:
                    scope["raw_path"] = scope["raw_path"][len(V2_PREFIX):] or b"/"
                await self.v2(scope, receive, send)
                return
        # http/websocket outside the prefix, and lifespan, belong to v1.
        await self.v1(scope, receive, send)


def build_context(v2_settings: V2Settings, prediction_service) -> V2Context:
    from backend.app.v2.services.images import build_image_store
    from backend.app.v2.services.vision import build_vision_analyzer

    return V2Context(
        settings=v2_settings,
        engine=make_engine(v2_settings.app_db_url),
        prediction_service=prediction_service,
        image_store=build_image_store(v2_settings),
        vision=build_vision_analyzer(v2_settings),
    )


def create_platform(
    v1_settings: Settings | None = None, v2_settings: V2Settings | None = None
) -> PlatformApp:
    if v1_settings is None:
        # Importing `backend.app.main` already built the v1 application at module
        # level. Reuse that object rather than loading the artifact a second time.
        from backend.app import main as v1_main

        v1_app = v1_main.app
    else:
        v1_app = create_app(v1_settings)
    ctx = build_context(v2_settings or load_v2_settings(), v1_app.state.service)
    return PlatformApp(v1_app, create_v2_app(ctx))


def __getattr__(name: str):
    # Built on first access so importing this module (for `PlatformApp`) has no side
    # effects; `uvicorn backend.app.v2.asgi:app` triggers construction.
    if name == "app":
        platform = create_platform()
        globals()["app"] = platform
        return platform
    raise AttributeError(name)
