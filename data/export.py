"""Export canonical tables as CSV for ML work (canonical columns and units only).

    python -m data.export --out data/local/export
    python -m data.export --out data/local/export --cutoff 2026-10-07T23:59:59Z
"""

from __future__ import annotations

import argparse

from data.repository import DataRepository
from data.timeutil import parse_timestamp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cutoff", default=None, help="RFC3339 with offset; only records at or before it")
    args = parser.parse_args(argv)
    cutoff = parse_timestamp(args.cutoff) if args.cutoff else None
    manifest = DataRepository(args.db_url).export_tables(args.out, cutoff)
    for name, info in manifest["files"].items():
        print(f"{name:<18} {info['rows']:>6} rows")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
