"""GLIMPSE FastAPI application."""

from backend.app.env_aliases import apply_env_aliases

# Before anything in this package reads a setting: see `env_aliases`.
apply_env_aliases()
