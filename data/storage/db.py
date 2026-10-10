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
# Internal storage layout version (independent of the API contract version). Bump when
# tables/columns change; an older local DB must then be rebuilt (`python -m data.seed --reset --yes`).
STORAGE_VERSION = "3"


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
        # WAL: readers (the API) never block on a bulk import; NORMAL sync is safe under WAL.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
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
        meta = dict(conn.execute(select(tables.schema_meta.c.key, tables.schema_meta.c.value)).all())
        if not meta:
            conn.execute(
                tables.schema_meta.insert(),
                [{"key": "schema_version", "value": SCHEMA_VERSION}, {"key": "storage_version", "value": STORAGE_VERSION}],
            )
            return
        for key, expected in (("schema_version", SCHEMA_VERSION), ("storage_version", STORAGE_VERSION)):
            if meta.get(key) != expected:
                raise SchemaVersionMismatch(
                    f"database {key} {meta.get(key)!r} != code {key} {expected!r}; rebuild the local "
                    "database with `python -m data.seed --reset --yes` (see docs/data_provenance.md)"
                )


def is_initialized(engine: Engine) -> bool:
    return inspect(engine).has_table("schema_meta")
