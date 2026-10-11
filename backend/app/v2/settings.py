"""Runtime settings for the v2 application layer.

Read from the environment, like the frozen v1 `Settings`, and kept in a separate
object so nothing here can alter how contract v1.0 is configured.

Every secret is read here and nowhere else. None has a default: an unset provider is
reported as `not_configured` by the API rather than quietly pointed at a guess.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

PERSONALIZATION_POLICIES = ("history_only", "history_plus_frozen_calibration")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)) or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)) or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OAuthClient:
    client_id: str | None = None
    client_secret: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


@dataclass(frozen=True)
class V2Settings:
    app_db_url: str = "sqlite:///data/local/glucotwin_app.db"
    cors_origins: list[str] = field(default_factory=list)
    registration_open: bool = True
    session_ttl_hours: int = 24 * 14
    consent_policy_version: str = "2026-10-10"

    # -- model integrity ---------------------------------------------------- #
    #: When set, v2 refuses to forecast unless the loaded artifact has this SHA-256.
    #: Production sets it to the frozen fs-1.3 checksum.
    expected_model_sha256: str | None = None
    personalization_policy: str = "history_only"
    #: Wait this long after a response window closes before scoring it, so readings
    #: that a sensor uploads late are not counted as missing.
    outcome_grace_minutes: float = 30.0

    # -- meal images -------------------------------------------------------- #
    image_dir: pathlib.Path = pathlib.Path("data/local/meal_images")
    image_s3_bucket: str | None = None
    image_s3_prefix: str = "meal-images/"
    max_image_bytes: int = 10 * 1024 * 1024
    max_image_pixels: int = 40_000_000

    # -- OpenAI ------------------------------------------------------------- #
    openai_api_key: str | None = None
    #: No default on purpose: the approved model is a team decision (master §17).
    openai_vision_model: str | None = None
    openai_timeout_seconds: float = 45.0
    #: Per-user ceiling on analyses per rolling 24 hours. A cost guard, not a quota.
    vision_daily_limit: int = 40

    # -- device providers --------------------------------------------------- #
    #: Fernet key protecting stored OAuth tokens. OAuth connect is refused without it.
    token_encryption_key: str | None = None
    #: Public base URL of this API, used to build OAuth redirect URIs.
    public_api_base_url: str | None = None
    #: Where the browser is sent after an OAuth callback. Unset returns JSON instead.
    frontend_return_url: str | None = None
    dexcom: OAuthClient = field(default_factory=OAuthClient)
    dexcom_environment: str = "sandbox"
    fitbit: OAuthClient = field(default_factory=OAuthClient)
    whoop: OAuthClient = field(default_factory=OAuthClient)

    @property
    def vision_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_vision_model)


def load_v2_settings() -> V2Settings:
    policy = _env("GLUCOTWIN_PERSONALIZATION_POLICY", "history_only")
    if policy not in PERSONALIZATION_POLICIES:
        raise ValueError(
            f"GLUCOTWIN_PERSONALIZATION_POLICY={policy!r}; expected one of "
            f"{PERSONALIZATION_POLICIES}"
        )
    origins = _env(
        "GLUCOTWIN_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    )
    return V2Settings(
        app_db_url=_env("GLUCOTWIN_APP_DB_URL", "sqlite:///data/local/glucotwin_app.db"),
        cors_origins=[o.strip() for o in (origins or "").split(",") if o.strip()],
        registration_open=_env_bool("GLUCOTWIN_REGISTRATION_OPEN", True),
        session_ttl_hours=_env_int("GLUCOTWIN_SESSION_TTL_HOURS", 24 * 14),
        consent_policy_version=_env("GLUCOTWIN_CONSENT_POLICY_VERSION", "2026-10-10"),
        expected_model_sha256=_env("GLUCOTWIN_EXPECTED_MODEL_SHA256"),
        personalization_policy=policy,
        outcome_grace_minutes=_env_float("GLUCOTWIN_OUTCOME_GRACE_MINUTES", 30.0),
        image_dir=pathlib.Path(_env("GLUCOTWIN_IMAGE_DIR", "data/local/meal_images")),
        image_s3_bucket=_env("GLUCOTWIN_IMAGE_S3_BUCKET"),
        image_s3_prefix=_env("GLUCOTWIN_IMAGE_S3_PREFIX", "meal-images/"),
        max_image_bytes=_env_int("GLUCOTWIN_MAX_IMAGE_BYTES", 10 * 1024 * 1024),
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_vision_model=_env("GLUCOTWIN_OPENAI_VISION_MODEL"),
        openai_timeout_seconds=_env_float("GLUCOTWIN_OPENAI_TIMEOUT_SECONDS", 45.0),
        vision_daily_limit=_env_int("GLUCOTWIN_VISION_DAILY_LIMIT", 40),
        token_encryption_key=_env("GLUCOTWIN_TOKEN_ENCRYPTION_KEY"),
        public_api_base_url=_env("GLUCOTWIN_PUBLIC_API_BASE_URL"),
        frontend_return_url=_env("GLUCOTWIN_FRONTEND_RETURN_URL"),
        dexcom=OAuthClient(_env("DEXCOM_CLIENT_ID"), _env("DEXCOM_CLIENT_SECRET")),
        dexcom_environment=_env("DEXCOM_ENVIRONMENT", "sandbox"),
        fitbit=OAuthClient(_env("FITBIT_CLIENT_ID"), _env("FITBIT_CLIENT_SECRET")),
        whoop=OAuthClient(_env("WHOOP_CLIENT_ID"), _env("WHOOP_CLIENT_SECRET")),
    )
