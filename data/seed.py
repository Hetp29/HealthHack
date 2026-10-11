"""One-command local demo database: (optionally reset) + load the synthetic_dev fixtures.

    python -m data.seed                 # create tables if needed, import fixtures (idempotent)
    python -m data.seed --reset --yes   # drop data-layer tables first (local dev only)
"""

from __future__ import annotations

import argparse
import sys

from data.adapters import import_fixture
from data.adapters.ingest import _print_summary
from data.storage.db import init_db, make_engine, resolve_db_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if args.reset and not args.yes:
        print("--reset drops every data-layer table. Re-run with --reset --yes to confirm.", file=sys.stderr)
        return 2
    engine = make_engine(args.db_url)
    init_db(engine, reset=args.reset)
    result = import_fixture(engine=engine)
    print(f"database:     {resolve_db_url(args.db_url)}")
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
