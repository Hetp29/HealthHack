"""Schema migrations: forward, drift-free against the models, and reversible."""

from __future__ import annotations

import os
import subprocess
import sys

from sqlalchemy import create_engine, inspect

from backend.app.v2.db import tables


def alembic(*args: str, url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], capture_output=True, text=True,
        env={**os.environ, "GLUCOTWIN_APP_DB_URL": url},
    )


def test_upgrade_matches_the_models_and_downgrade_removes_everything(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    up = alembic("upgrade", "head", url=url)
    assert up.returncode == 0, up.stderr

    inspector = inspect(create_engine(url))
    migrated = set(inspector.get_table_names()) - {"alembic_version"}
    assert migrated == set(tables.metadata.tables)
    for name, table in tables.metadata.tables.items():
        assert {c["name"] for c in inspector.get_columns(name)} == set(table.c.keys()), name

    # `alembic check` fails if the models have drifted from the migrations.
    drift = alembic("check", url=url)
    assert drift.returncode == 0, drift.stdout + drift.stderr

    down = alembic("downgrade", "base", url=url)
    assert down.returncode == 0, down.stderr
    assert set(inspect(create_engine(url)).get_table_names()) == {"alembic_version"}

    again = alembic("upgrade", "head", url=url)        # and it can be re-applied
    assert again.returncode == 0, again.stderr


def test_migration_renders_for_postgresql_with_timezone_aware_timestamps():
    """Offline DDL generation only -- nothing connects to a server. This proves the
    migration compiles for PostgreSQL; it does not prove it has run on one."""
    sql = alembic("upgrade", "head", "--sql", url="postgresql://user:pw@localhost/glucotwin")
    assert sql.returncode == 0, sql.stderr
    assert sql.stdout.count("CREATE TABLE") == len(tables.metadata.tables) + 1
    assert "TIMESTAMP WITH TIME ZONE" in sql.stdout
    assert "TIMESTAMP WITHOUT TIME ZONE" not in sql.stdout


def test_foreign_keys_are_enforced_on_sqlite(tmp_path):
    from sqlalchemy.exc import IntegrityError
    import datetime as dt
    import pytest
    from backend.app.v2.db.engine import create_all, make_engine

    engine = make_engine(f"sqlite:///{tmp_path / 'fk.db'}")
    create_all(engine)
    now = dt.datetime.now(dt.timezone.utc)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(tables.meals.insert().values(
            meal_id="m1", user_id="usr_missing", occurred_at=now, entry_method="manual",
            status="draft", consumption_status="as_logged", created_at=now, updated_at=now))
    with pytest.raises((ValueError, Exception)) as naive, engine.begin() as conn:
        conn.execute(tables.audit_events.insert().values(
            audit_id="a1", actor="system", action="x", detail={}, created_at=dt.datetime(2026, 1, 1)))
    assert "naive" in str(naive.value)
