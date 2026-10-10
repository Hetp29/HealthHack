"""Engine creation, database URL resolution and schema initialization."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.engine import Engine

from data.schema import SCHEMA_VERSION
from data.storage import tables

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_URL = "sqlite:///data/glucotwin.db"
DB_URL_ENV = "GLUCOTWIN_DB_URL"


class SchemaVersionMismatch(RuntimeError):
    pass


def resolve_db_url(db_url: str | None = None) -> str:
    """Explicit argument > ``$GLUCOTWIN_DB_URL`` > ``sqlite:///data/glucotwin.db``.

    A relative SQLite path is resolved against the repository root, so commands behave
    the same from any working directory.
    """
    url = db_url or os.environ.get(DB_URL_ENV) or DEFAULT_DB_URL
    prefix = "sqlite:///"
    if url.startswith(prefix) and url != "sqlite:///:memory:":
        path = Path(url[len(prefix):])
        if not path.is_absolute():
            path = REPO_ROOT / path
        url = prefix + str(path)
    return url


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def make_engine(db_url: str | None = None) -> Engine:
    url = resolve_db_url(db_url)
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):
        _enable_sqlite_foreign_keys(engine)
    return engine


def init_db(engine: Engine, *, reset: bool = False) -> None:
    """Create all tables if missing. Never drops anything unless ``reset=True``."""
    if reset:
        tables.metadata.drop_all(engine)
    tables.metadata.create_all(engine)
    with engine.begin() as conn:
        stored = conn.execute(
            select(tables.schema_meta.c.value).where(tables.schema_meta.c.key == "schema_version")
        ).scalar_one_or_none()
        if stored is None:
            conn.execute(tables.schema_meta.insert().values(key="schema_version", value=SCHEMA_VERSION))
        elif stored != SCHEMA_VERSION:
            raise SchemaVersionMismatch(
                f"database schema_version {stored!r} != code schema_version {SCHEMA_VERSION!r}; "
                "a migration is required (see docs/data_provenance.md)"
            )


def is_initialized(engine: Engine) -> bool:
    return inspect(engine).has_table("schema_meta")
