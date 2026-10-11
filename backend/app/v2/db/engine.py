"""Engine construction for the application database."""

from __future__ import annotations

import pathlib

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from backend.app.v2.db import tables

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


def make_engine(db_url: str) -> Engine:
    """Create an engine. SQLite gets foreign keys switched on, which it leaves off by
    default -- without that, every `ON DELETE CASCADE` in the schema is decorative."""
    kwargs: dict = {"future": True}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if db_url in ("sqlite://", "sqlite:///:memory:"):
            # One shared connection, or each checkout would see its own empty database.
            kwargs["poolclass"] = StaticPool
        else:
            path = pathlib.Path(db_url[len("sqlite:///"):])
            if not path.is_absolute():
                path = REPO_ROOT / path
                db_url = f"sqlite:///{path}"
            path.parent.mkdir(parents=True, exist_ok=True)
    else:
        kwargs["pool_pre_ping"] = True

    engine = create_engine(db_url, **kwargs)

    if db_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_all(engine: Engine) -> None:
    """Create the schema directly. For tests; deployments run Alembic."""
    tables.metadata.create_all(engine)
