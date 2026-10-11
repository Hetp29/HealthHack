"""Alembic environment for the application database.

The URL comes from `GLUCOTWIN_APP_DB_URL` (or `-x db_url=...` on the command line).
This manages only the v2 application schema. The research warehouse under
`data/storage` has its own lifecycle and is not touched.
"""

from __future__ import annotations

import os

from alembic import context

from backend.app.v2.db import tables
from backend.app.v2.db.engine import make_engine

target_metadata = tables.metadata


def _url() -> str:
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    return override or os.environ.get("GLUCOTWIN_APP_DB_URL") or "sqlite:///data/local/glucotwin_app.db"


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_url())
    with engine.connect() as connection:
        # Batch mode lets SQLite, which cannot ALTER most things, run the same scripts.
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
