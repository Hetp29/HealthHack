"""Ingestion CLI. Safe to re-run: identical input produces no new or duplicate rows.

    python -m data.adapters.ingest --source synthetic_dev
    python -m data.adapters.ingest --source synthetic_dev --dry-run
    python -m data.adapters.ingest --source official --path <dir> --mapping data/mapping/official.toml

Exit codes: 0 completed (rejections, if any, are quarantined and reported), 1 failed.
"""

from __future__ import annotations

import argparse
import sys

from data.adapters import FIXTURE_DIR, IngestionError, MappingError, import_fixture, import_official
from data.storage.db import resolve_db_url


def _print_summary(result) -> None:
    r = result.report
    t = r["totals"]
    print(f"run_id:       {r['run_id']}")
    print(f"status:       {r['status']}")
    print(f"source:       {r['source']} ({r['data_source']})")
    print(f"input sha256: {r['input_sha256']}")
    print(
        f"rows read {t['rows_read']} | accepted {t['accepted']} (with warnings {t['accepted_with_warnings']})"
        f" | rejected {t['rejected']} | duplicates dropped {t['duplicates_dropped']} | skipped empty {t['skipped_empty']}"
    )
    print(f"db writes:    inserted {t['inserted']} | updated {t['updated']} | unchanged {t['unchanged']}")
    for name, m in r["modalities"].items():
        if m["rows_read"] or m["accepted"]:
            line = f"  {name:<12} read {m['rows_read']:>6}  accepted {m['accepted']:>6}  rejected {m['rejected']:>4}"
            if m["rejection_reasons"]:
                line += f"  reasons {m['rejection_reasons']}"
            print(line)
    for w in r["batch_warnings"]:
        print(f"  batch warning: {w}")
    if result.report_path:
        print(f"report:       {result.report_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["synthetic_dev", "official"], required=True)
    parser.add_argument("--path", default=None, help="source directory (default for synthetic_dev: committed fixtures)")
    parser.add_argument("--mapping", default=None, help="mapping TOML (required for --source official)")
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--dry-run", action="store_true", help="validate and report only; write nothing to the DB")
    args = parser.parse_args(argv)

    try:
        if args.source == "synthetic_dev":
            result = import_fixture(args.path or FIXTURE_DIR, db_url=args.db_url, dry_run=args.dry_run)
        else:
            if not args.path or not args.mapping:
                parser.error("--source official needs --path and --mapping")
            result = import_official(args.path, args.mapping, db_url=args.db_url, dry_run=args.dry_run)
    except (IngestionError, MappingError) as exc:
        print(f"ingestion failed: {exc}", file=sys.stderr)
        return 1
    if not args.dry_run:
        print(f"database:     {resolve_db_url(args.db_url)}")
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
