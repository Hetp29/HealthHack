"""Create the canonical SQLite schema.

    python -m data.storage.init_db                       # uses $GLUCOTWIN_DB_URL or data/glucotwin.db
    python -m data.storage.init_db --db-url sqlite:///data/local/dev.db
    python -m data.storage.init_db --reset --yes         # DROP and recreate (local dev only)
"""

from __future__ import annotations

import argparse
import sys

from data.storage.db import init_db, make_engine, resolve_db_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--reset", action="store_true", help="drop all data-layer tables first")
    parser.add_argument("--yes", action="store_true", help="confirm --reset")
    args = parser.parse_args(argv)

    if args.reset and not args.yes:
        print("--reset drops every data-layer table. Re-run with --reset --yes to confirm.", file=sys.stderr)
        return 2
    url = resolve_db_url(args.db_url)
    init_db(make_engine(url), reset=args.reset)
    print(f"initialized {url}" + (" (reset)" if args.reset else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
