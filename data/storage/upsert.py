"""Idempotent upsert on primary keys, with insert/update/unchanged accounting.

Re-importing identical data is a no-op (``unchanged``), so a re-run can never multiply
observations. A changed value for an existing key is an ``update`` and re-stamps
``ingestion_run_id`` so provenance points at the run that last wrote the row.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Table, and_, select, tuple_
from sqlalchemy.engine import Connection

_CHUNK = 500


@dataclass
class UpsertCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


def _existing(conn: Connection, table: Table, pk: tuple[str, ...], keys: list[tuple]) -> dict[tuple, dict]:
    found: dict[tuple, dict] = {}
    pk_cols = [table.c[name] for name in pk]
    for start in range(0, len(keys), _CHUNK):
        chunk = keys[start : start + _CHUNK]
        if len(pk) == 1:
            condition = pk_cols[0].in_([k[0] for k in chunk])
        else:
            condition = tuple_(*pk_cols).in_(chunk)
        for row in conn.execute(select(table).where(condition)).mappings():
            found[tuple(row[name] for name in pk)] = dict(row)
    return found


def upsert_rows(conn: Connection, table: Table, pk: tuple[str, ...], rows: list[dict], run_id: str) -> UpsertCounts:
    counts = UpsertCounts()
    if not rows:
        return counts
    keyed = {tuple(r[name] for name in pk): r for r in rows}
    existing = _existing(conn, table, pk, list(keyed))
    to_insert: list[dict] = []
    for key, row in keyed.items():
        current = existing.get(key)
        if current is None:
            to_insert.append({**row, "ingestion_run_id": run_id})
            continue
        changed = {name: value for name, value in row.items() if current.get(name) != value}
        if not changed:
            counts.unchanged += 1
            continue
        conn.execute(
            table.update()
            .where(and_(*[table.c[name] == key[i] for i, name in enumerate(pk)]))
            .values(**changed, ingestion_run_id=run_id)
        )
        counts.updated += 1
    for start in range(0, len(to_insert), _CHUNK):
        conn.execute(table.insert(), to_insert[start : start + _CHUNK])
    counts.inserted = len(to_insert)
    return counts
