"""Runtime settings, read from the environment.

Everything configurable lives here so the service can be pointed at a different data
backend, artifact path or CORS allowlist without a code change -- which is what makes
one image deployable both locally and to a cloud host.

Deliberately plain `os.environ` rather than pydantic-settings: one fewer dependency to
pin, and the parsing rules (comma-separated origins, the no-wildcard check) are explicit.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

CONTRACT_VERSION_DEFAULT = "1.0.0"


#: Settings were renamed GLUCOTWIN_* -> GLIMPSE_* (team decision 2026-10-11). The new
#: name wins; the legacy name is still honoured so existing .env files and deployments
#: keep working unchanged.
_PREFIX, _LEGACY_PREFIX = "GLIMPSE_", "GLUCOTWIN_"


def _raw_env(name: str) -> str | None:
    value = os.environ.get(name)
    if (value is None or value.strip() == "") and name.startswith(_PREFIX):
        value = os.environ.get(_LEGACY_PREFIX + name[len(_PREFIX):])
    return value


def _env(name: str, default: str) -> str:
    value = _raw_env(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _split_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@dataclass(frozen=True)
class Settings:
    """Immutable view of the process configuration."""

    contract_version: str = CONTRACT_VERSION_DEFAULT
    model_artifact: pathlib.Path = pathlib.Path("ml/artifacts/model.joblib")
    metrics_path: pathlib.Path = pathlib.Path("ml/artifacts/metrics.json")
    data_backend: str = "synthetic_dev"
    db_url: str = "sqlite:///data/glucotwin.db"
    cors_origins: list[str] = field(default_factory=list)
    log_level: str = "INFO"
    #: Dev-fixture parameters; ignored once the canonical data layer is bound.
    #: 16 days so the fixture can exercise the Day-8 primary protocol (see
    #: ml/devfixture.py). Override with GLIMPSE_FIXTURE_DAYS.
    fixture_days: int = 16
    fixture_seed: int = 20261011

    @property
    def cors_allows_wildcard(self) -> bool:
        return "*" in self.cors_origins


def load_settings() -> Settings:
    """Build settings from the environment.

    A wildcard CORS origin is permitted but is surfaced in the startup log as a
    warning, because shipping `*` to a judged deployment is a real finding rather
    than a convenience.
    """
    return Settings(
        contract_version=_env("GLIMPSE_CONTRACT_VERSION", CONTRACT_VERSION_DEFAULT),
        model_artifact=pathlib.Path(
            _env("GLIMPSE_MODEL_ARTIFACT", "ml/artifacts/model.joblib")
        ),
        metrics_path=pathlib.Path(
            _env("GLIMPSE_METRICS_PATH", "ml/artifacts/metrics.json")
        ),
        data_backend=_env("GLIMPSE_DATA_BACKEND", "synthetic_dev"),
        db_url=_env("GLIMPSE_DB_URL", "sqlite:///data/glucotwin.db"),
        cors_origins=_split_origins(
            _env(
                "GLIMPSE_CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            )
        ),
        log_level=_env("GLIMPSE_LOG_LEVEL", "INFO").upper(),
        fixture_days=_env_int("GLIMPSE_FIXTURE_DAYS", 16),
        fixture_seed=_env_int("GLIMPSE_FIXTURE_SEED", 20261011),
    )
