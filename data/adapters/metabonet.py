"""MetaboNet adapter (Wolff et al., J Diabetes Sci Technol 2026; doi:10.1177/19322968261441637).

    python -m data.adapters.metabonet audit  --path data/official_raw/Type1Diabetes.parquet
    python -m data.adapters.metabonet ingest --path ... --db-url sqlite:///data/metabonet.db [--max-subjects N]

One Parquet file: 132.8M rows x 37 columns, 129 row groups, one row per subject per
5-minute slot. It is never loaded whole: row groups are streamed with PyArrow and each
subject (stored contiguously) is processed on its own.

Mapping decisions
-----------------
* **Identity.** The same numeric ``id`` occurs in different source studies (222 ids
  appear in more than one ``source_file``; e.g. ``540`` in both OhioT1DM and Loop), so a
  participant is ``(source_file, id)``: ``metabonet_<source>_<id>``.
* **CGM** -> ``cgm`` with ``sensor`` = ``cgm_device`` as supplied.
* **Meals** -> a row with ``carbs > 0`` (carbs are zero-filled when nothing was eaten).
  Only ``carbs_g`` exists; protein/fat/fiber/kcal stay null. ``meal_label`` (free text) is
  not stored.
* **Insulin** -> ``medications``: every ``bolus > 0`` as class ``insulin_bolus`` with unit
  ``U``. Basal delivery (``basal``, per 5-min slot) is NOT converted into events; it is
  reported. Units are inferred from the data (dictionary not machine-readable).
* **Heart rate / activity** -> ``heart_rate``; ``steps`` and ``calories_burned`` as activity.
* **Time.** Parquet stores ``isAdjustedToUTC=false`` (offset-naive local wall clock, dates
  shifted, no timezone). Participants get ``time_basis="source_local_unknown_tz"`` and a
  null timezone; the original text is kept in ``source_timestamp``; the canonical
  ``timestamp`` is an ordering key with a +00:00 label, NOT a real UTC instant.
* **Scope.** The full dataset (111M CGM rows) is audited in streaming. Ingestion loads a
  bounded, deterministic window per eligible subject: the 7 calendar days before the
  subject's FIRST eligible Day-8 meal plus that day (8 days). See ``docs/data_audit``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from data.adapters.pipeline import DEFAULT_REPORT_DIR, IngestionResult, PreparedSource, run_prepared_ingestion
from data.adapters.readers import RawRow, sha256_file
from data.eligibility import meal_episodes, summarize
from data.storage.db import REPO_ROOT, make_engine

DATASET = "metabonet"
DEFAULT_PATH = REPO_ROOT / "data" / "official_raw" / "Type1Diabetes.parquet"
SUBJECT_COLUMNS = ["source_file", "id", "date"]
AUDIT_COLUMNS = SUBJECT_COLUMNS + ["CGM", "carbs", "bolus", "basal", "heartrate", "steps", "calories_burned",
                                   "cgm_device", "is_test", "subject_split_across_traintest"]
INGEST_COLUMNS = SUBJECT_COLUMNS + ["CGM", "carbs", "bolus", "heartrate", "steps", "calories_burned", "cgm_device"]


def participant_id(source_file: str, subject_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", source_file.lower())
    return f"{DATASET}_{slug}_{subject_id}"


def iter_subjects(path: str | Path, columns: list[str]) -> Iterator[tuple[str, str, pd.DataFrame]]:
    """Yield (source_file, id, rows sorted by date), one subject at a time.

    Subjects are stored contiguously across row groups; a subject spanning a row-group
    boundary is carried over. Memory is bounded by the largest single subject.
    """
    f = pq.ParquetFile(path)
    carry: pd.DataFrame | None = None
    for i in range(f.num_row_groups):
        frame = f.read_row_group(i, columns=columns).to_pandas()
        if carry is not None:
            frame = pd.concat([carry, frame], ignore_index=True)
        key = frame["source_file"].astype(str) + "|" + frame["id"].astype(str)
        last_key = key.iloc[-1]
        done = frame[key != last_key]
        carry = frame[key == last_key]
        for (src, sid), g in done.groupby(["source_file", "id"], sort=False):
            yield str(src), str(sid), g.sort_values("date", kind="stable").reset_index(drop=True)
    if carry is not None and len(carry):
        src, sid = carry["source_file"].iloc[0], carry["id"].iloc[0]
        yield str(src), str(sid), carry.sort_values("date", kind="stable").reset_index(drop=True)


def subject_audit(src: str, sid: str, g: pd.DataFrame) -> dict:
    cgm_t = g.loc[g["CGM"].notna(), "date"]
    meals = g.loc[g["carbs"] > 0, "date"]
    episodes = meal_episodes(cgm_t, meals)
    span = (g["date"].max() - g["date"].min()).total_seconds() / 86400 if len(g) else 0.0
    gaps = cgm_t.diff().dt.total_seconds().div(60)
    return {
        "dataset": DATASET,
        "source": src,
        "subject_id": sid,
        "participant_id": participant_id(src, sid),
        "rows": len(g),
        "first": str(g["date"].min()),
        "last": str(g["date"].max()),
        "span_days": round(span, 2),
        "duplicate_timestamps": int(g["date"].duplicated().sum()),
        "cgm_readings": int(len(cgm_t)),
        "cgm_coverage_vs_5min": round(len(cgm_t) / max(span * 288, 1), 4),
        "cgm_gaps_over_15min": int((gaps > 15).sum()),
        "cgm_devices": ";".join(sorted(g["cgm_device"].dropna().astype(str).unique())),
        "meal_events": int(len(meals)),
        "bolus_events": int((g["bolus"] > 0).sum()),
        "negative_bolus": int((g["bolus"] < 0).sum()),
        "basal_rows": int(g["basal"].notna().sum()),
        "heart_rate_rows": int(g["heartrate"].notna().sum()),
        "steps_rows": int(g["steps"].notna().sum()),
        "calories_burned_rows": int(g["calories_burned"].notna().sum()),
        "is_test_rows": int(g["is_test"].fillna(False).sum()),
        "split_across_traintest": bool(g["subject_split_across_traintest"].fillna(False).any()),
        **summarize(episodes),
    }


def audit(path: str | Path = DEFAULT_PATH, *, limit: int | None = None, progress: bool = False) -> pd.DataFrame:
    rows = []
    for n, (src, sid, g) in enumerate(iter_subjects(path, AUDIT_COLUMNS), start=1):
        rows.append(subject_audit(src, sid, g))
        if progress and n % 100 == 0:
            print(f"  audited {n} subjects", file=sys.stderr, flush=True)
        if limit and n >= limit:
            break
    return pd.DataFrame(rows).sort_values(["source", "subject_id"]).reset_index(drop=True)


def schema_report(path: str | Path) -> dict:
    f = pq.ParquetFile(path)
    md = f.metadata
    schema = f.schema_arrow
    date_idx = schema.get_field_index("date")
    logical = str(md.schema.column(date_idx).logical_type)
    return {
        "file": Path(path).name,
        "bytes": Path(path).stat().st_size,
        "sha256": sha256_file(Path(path)),
        "rows": md.num_rows,
        "row_groups": md.num_row_groups,
        "columns": [{"name": fld.name, "type": str(fld.type)} for fld in schema],
        "date_logical_type": logical,
        "created_by": md.created_by,
    }


def ingestion_window(g: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """[start, end): 7 calendar days before the first eligible meal's day, through that day."""
    cgm_t = g.loc[g["CGM"].notna(), "date"]
    meals = g.loc[g["carbs"] > 0, "date"]
    ep = meal_episodes(cgm_t, meals)
    if not len(ep) or not ep["eligible"].any():
        return None
    day = pd.Timestamp(ep.loc[ep["eligible"], "meal_time"].min()).normalize()
    return day - pd.Timedelta(days=7), day + pd.Timedelta(days=1)


def _ts(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def _rows_for_subject(src: str, sid: str, g: pd.DataFrame) -> dict[str, list[RawRow]]:
    pid = participant_id(src, sid)
    label = f"{DATASET}/{src}/{sid}"
    line = g.index.to_numpy() + 1

    def raw(i, values):
        return RawRow(label, int(i), values)

    rows: dict[str, list[RawRow]] = {
        "participants": [raw(0, {"participant_id": pid, "display_name": f"MetaboNet {src} {sid}",
                                 "timezone": None, "source_dataset": DATASET,
                                 "time_basis": "source_local_unknown_tz"})],
    }
    c = g["CGM"].notna()
    rows["cgm"] = [raw(i, {"participant_id": pid, "timestamp": _ts(t), "glucose_mg_dl": float(v),
                           "sensor": None if d is None or pd.isna(d) else str(d)})
                   for i, t, v, d in zip(line[c], g.loc[c, "date"], g.loc[c, "CGM"], g.loc[c, "cgm_device"])]
    m = g["carbs"] > 0
    rows["meals"] = [raw(i, {"meal_id": f"{pid}_meal_{pd.Timestamp(t):%Y%m%dT%H%M}", "participant_id": pid,
                             "timestamp": _ts(t), "carbs_g": float(v)})
                     for i, t, v in zip(line[m], g.loc[m, "date"], g.loc[m, "carbs"])]
    b = g["bolus"].notna() & (g["bolus"] != 0)
    rows["medications"] = [raw(i, {"participant_id": pid, "timestamp": _ts(t), "medication_class": "insulin_bolus",
                                   "dose": float(v), "dose_unit": "U"})
                           for i, t, v in zip(line[b], g.loc[b, "date"], g.loc[b, "bolus"])]
    h = g["heartrate"].notna()
    rows["heart_rate"] = [raw(i, {"participant_id": pid, "timestamp": _ts(t), "heart_rate_bpm": float(v)})
                          for i, t, v in zip(line[h], g.loc[h, "date"], g.loc[h, "heartrate"])]
    rows["activity"] = []
    for column, unit in (("steps", "steps"), ("calories_burned", "kcal")):
        a = g[column].notna()
        rows["activity"] += [raw(i, {"participant_id": pid, "timestamp": _ts(t), "activity_value": float(v),
                                     "activity_unit": unit})
                             for i, t, v in zip(line[a], g.loc[a, "date"], g.loc[a, column])]
    return rows


def import_metabonet(
    path: str | Path = DEFAULT_PATH,
    *,
    engine=None,
    db_url: str | None = None,
    max_subjects: int | None = None,
    sources: list[str] | None = None,
    batch_size: int = 50,
    dry_run: bool = False,
    report_dir: str | Path | None = DEFAULT_REPORT_DIR,
    progress: bool = False,
) -> list[IngestionResult]:
    """Ingest the 8-day window of every eligible subject, in batches of ``batch_size``."""
    if engine is None and not dry_run:
        engine = make_engine(db_url)
    file_sha = sha256_file(Path(path))
    results: list[IngestionResult] = []
    batch: dict[str, list[RawRow]] = {}
    batch_subjects: list[str] = []
    skipped = Counter()
    taken = 0

    def flush():
        nonlocal batch, batch_subjects
        if not batch_subjects:
            return
        prepared = PreparedSource(
            name=f"{DATASET}_adapter_v1",
            label=f"{DATASET}/{Path(path).name}[{batch_subjects[0]}..{batch_subjects[-1]}]",
            sha256=file_sha,
            data_source="official",
            rows=batch,
            assume_timezone="floating",
            notes=[
                {"code": "metabonet_window", "detail": "7 days before first eligible meal day + that day",
                 "subjects": len(batch_subjects)},
                {"code": "time_basis_source_local_unknown_tz", "detail": "parquet isAdjustedToUTC=false"},
                {"code": "basal_insulin_not_ingested_as_events"},
                {"code": "macros_other_than_carbs_absent_in_source"},
            ],
        )
        results.append(run_prepared_ingestion(prepared, engine, dry_run=dry_run, report_dir=report_dir))
        if progress:
            print(f"  ingested batch ending {batch_subjects[-1]} ({taken} subjects)", file=sys.stderr, flush=True)
        batch, batch_subjects = {}, []

    for src, sid, g in iter_subjects(path, INGEST_COLUMNS):
        if sources and src not in sources:
            continue
        window = ingestion_window(g)
        if window is None:
            skipped["no_eligible_episode"] += 1
            continue
        w = g[(g["date"] >= window[0]) & (g["date"] < window[1])]
        for modality, rows in _rows_for_subject(src, sid, w).items():
            batch.setdefault(modality, []).extend(rows)
        batch_subjects.append(participant_id(src, sid))
        taken += 1
        if len(batch_subjects) >= batch_size:
            flush()
        if max_subjects and taken >= max_subjects:
            break
    flush()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("audit", help="stream the whole file; per-subject audit CSV + schema JSON")
    a.add_argument("--path", default=str(DEFAULT_PATH))
    a.add_argument("--out-dir", default=str(REPO_ROOT / "data" / "reports" / "profiles"))
    a.add_argument("--limit", type=int, default=None)
    i = sub.add_parser("ingest", help="load 8-day windows of eligible subjects")
    i.add_argument("--path", default=str(DEFAULT_PATH))
    i.add_argument("--db-url", default=None)
    i.add_argument("--max-subjects", type=int, default=None)
    i.add_argument("--sources", default=None, help="comma-separated source_file values, e.g. OhioT1DM,Loop")
    i.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "audit":
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        frame = audit(args.path, limit=args.limit, progress=True)
        frame.to_csv(out / "metabonet_subjects.csv", index=False)
        (out / "metabonet_schema.json").write_text(json.dumps(schema_report(args.path), indent=2) + "\n")
        print(f"subjects {len(frame)} | eligible subjects {(frame.eligible_episodes > 0).sum()} | "
              f"eligible episodes {frame.eligible_episodes.sum()} -> {out}")
        return 0

    results = import_metabonet(
        args.path, db_url=args.db_url, max_subjects=args.max_subjects, dry_run=args.dry_run, progress=True,
        sources=args.sources.split(",") if args.sources else None,
    )
    totals = Counter()
    for r in results:
        totals.update(r.report["totals"])
    print(f"batches {len(results)} | " + " | ".join(f"{k} {v}" for k, v in sorted(totals.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
