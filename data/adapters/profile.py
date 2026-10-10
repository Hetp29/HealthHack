"""Schema profile of a new dataset, BEFORE any modification. First task on arrival.

    python -m data.adapters.profile --path <official_dir>
    python -m data.adapters.profile --path <official_dir> --mapping data/mapping/official.toml

Stage 1 (no mapping): file inventory with checksums, row counts, columns, per-column
null/NaN counts, inferred types, numeric ranges, timestamp spans/offset presence, image
file counts, unit hints and column names that look like leakage risks.
Stage 2 (with mapping): a validation dry run (nothing written to the DB) that adds
participant/CGM/meal counts, timestamp coverage, missingness by modality, image and
label availability, duplicate/invalid counts and unit warnings.

Privacy: the profile never copies cell values except numeric min/median/max and
timestamp bounds; no free-text samples are emitted.
Writes JSON + Markdown to data/reports/profiles/ (gitignored).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

from data.adapters.mapping import load_mapping
from data.adapters.pipeline import run_ingestion
from data.adapters.readers import SourceReadError, iter_rows, sha256_file
from data.storage.db import REPO_ROOT

DEFAULT_PROFILE_DIR = REPO_ROOT / "data" / "reports" / "profiles"
TABULAR = {".csv", ".tsv", ".json", ".jsonl", ".xlsx", ".xls", ".parquet"}
IMAGES = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".bmp"}
NULL_TOKENS = {"", "NA", "N/A", "n/a", "null", "NULL", "None"}
NAN_TOKENS = {"nan", "NaN", "NAN"}
LEAKAGE_PATTERN = re.compile(
    r"(target|label|outcome|response|post|peak|auc|iauc|delta|excursion|future|holdout|test|day.?8|ground.?truth|hidden)",
    re.I,
)
UNIT_HINTS = re.compile(r"(mmol|mg.?dl|mg/dl|kj|kcal|cal|bpm|gram|_g$|units?|iu|mcg|steps|met)", re.I)
TS_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$")
OFFSET = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")
MAX_DISTINCT = 10000


def _profile_column(values: list) -> dict:
    nulls = sum(1 for v in values if v is None or (isinstance(v, str) and v.strip() in NULL_TOKENS))
    nans = sum(1 for v in values if (isinstance(v, str) and v.strip() in NAN_TOKENS) or (isinstance(v, float) and math.isnan(v)))
    present = [v for v in values if not (v is None or (isinstance(v, str) and v.strip() in NULL_TOKENS | NAN_TOKENS))]
    numbers, timestamps, with_offset = [], [], 0
    for v in present:
        text = str(v).strip()
        try:
            n = float(text)
            if math.isfinite(n):
                numbers.append(n)
            continue
        except ValueError:
            pass
        if TS_LIKE.match(text):
            timestamps.append(text)
            with_offset += bool(OFFSET.search(text))
    distinct = len({str(v) for v in present[:200000]})
    out = {
        "non_null": len(present),
        "null": nulls,
        "nan_tokens": nans,
        "distinct": min(distinct, MAX_DISTINCT),
        "distinct_capped": distinct >= MAX_DISTINCT,
    }
    if present and len(numbers) >= 0.95 * len(present):
        out["inferred_type"] = "numeric"
        out["min"], out["median"], out["max"] = min(numbers), statistics.median(numbers), max(numbers)
        out["negative_count"] = sum(1 for n in numbers if n < 0)
        if numbers and 9.4e8 < statistics.median(numbers) < 2.5e9:
            out["note"] = "values look like unix epoch seconds"
        elif numbers and 9.4e11 < statistics.median(numbers) < 2.5e12:
            out["note"] = "values look like unix epoch milliseconds"
    elif present and len(timestamps) >= 0.95 * len(present):
        out["inferred_type"] = "timestamp"
        out["min"], out["max"] = min(timestamps), max(timestamps)
        out["with_utc_offset"] = with_offset
        out["without_utc_offset"] = len(timestamps) - with_offset
    else:
        out["inferred_type"] = "text" if present else "empty"
    return out


def inventory(root: Path) -> dict:
    files, images, unreadable = [], [], []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        if suffix in IMAGES:
            images.append(rel)
            continue
        entry = {"path": rel, "bytes": path.stat().st_size, "sha256": sha256_file(path), "type": suffix or "none"}
        if suffix in TABULAR:
            try:
                rows = [r.values for r in iter_rows(path, root)]
            except (SourceReadError, ValueError, UnicodeDecodeError) as exc:
                unreadable.append({"path": rel, "error": str(exc)[:300]})
                files.append(entry)
                continue
            columns = list(rows[0].keys()) if rows else []
            entry["rows"] = len(rows)
            entry["columns"] = {c: _profile_column([r.get(c) for r in rows]) for c in columns}
            entry["exact_duplicate_rows"] = len(rows) - len({json.dumps(r, sort_keys=True, default=str) for r in rows})
            entry["leakage_review_columns"] = [c for c in columns if LEAKAGE_PATTERN.search(c)]
            entry["unit_hint_columns"] = [c for c in columns if UNIT_HINTS.search(c)]
        files.append(entry)
    return {
        "files": files,
        "image_files": {"count": len(images), "extensions": sorted({Path(i).suffix.lower() for i in images})},
        "unreadable": unreadable,
    }


def _mapped_profile(root: Path, mapping_path: Path) -> dict:
    mapping = load_mapping(mapping_path)
    result = run_ingestion(root, mapping, None, dry_run=True, report_dir=None)
    report = result.report
    quality = report["quality"]
    meals_total = sum(p["meals"].get("count", 0) for p in quality["participants"].values())
    with_image = sum(p["meals"].get("with_image_ref", 0) for p in quality["participants"].values())
    complete = sum(p["meals"].get("complete_nutrition", 0) for p in quality["participants"].values())
    return {
        "mapping": report["mapping"],
        "participant_count": quality["participant_count"],
        "record_counts": quality["record_counts"],
        "totals": report["totals"],
        "by_modality": {
            m: {k: v for k, v in d.items() if k in ("rows_read", "accepted", "rejected", "duplicates_dropped",
                                                    "skipped_empty", "rejection_reasons", "warnings", "unit_conversions")}
            for m, d in report["modalities"].items()
        },
        "batch_warnings": report["batch_warnings"],
        "timestamp_coverage": {
            pid: {k: q["cgm"].get(k) for k in ("first", "last", "span_days", "coverage_ratio_vs_5min_grid",
                                                "gaps_over_15min", "longest_gap_minutes")}
            for pid, q in quality["participants"].items()
        },
        "missing_modalities_by_participant": {pid: q["absent_modalities"] for pid, q in quality["participants"].items()},
        "images": {"meals_with_image_ref": with_image, "meals_total": meals_total},
        "labels": {"meals_with_complete_nutrition": complete, "meals_total": meals_total},
        "per_participant_quality": quality["participants"],
    }


def profile(root: str | Path, mapping_path: str | Path | None = None) -> dict:
    root = Path(root)
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    inv = inventory(root)
    result = {
        "profiled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset_root_name": root.resolve().name,
        "file_count": len(inv["files"]),
        "inventory": inv,
        "leakage_review": sorted({f"{f['path']}:{c}" for f in inv["files"] for c in f.get("leakage_review_columns", [])}),
        "naive_timestamp_columns": sorted(
            f"{f['path']}:{c}" for f in inv["files"] for c, s in f.get("columns", {}).items()
            if s.get("inferred_type") == "timestamp" and s.get("without_utc_offset")
        ),
        "nan_token_columns": sorted(
            f"{f['path']}:{c}" for f in inv["files"] for c, s in f.get("columns", {}).items() if s.get("nan_tokens")
        ),
    }
    if mapping_path:
        result["mapped"] = _mapped_profile(root, Path(mapping_path))
    return result


def to_markdown(p: dict) -> str:
    lines = [f"# Dataset schema profile — {p['dataset_root_name']}", "", f"Profiled at {p['profiled_at']}.", ""]
    lines += ["## File inventory", "", "| file | rows | columns | bytes | sha256 |", "|---|---|---|---|---|"]
    for f in p["inventory"]["files"]:
        lines.append(f"| {f['path']} | {f.get('rows', '')} | {len(f.get('columns', {}))} | {f['bytes']} | {f['sha256'][:12]}… |")
    imgs = p["inventory"]["image_files"]
    lines += ["", f"Image files: {imgs['count']} {imgs['extensions']}", ""]
    for f in p["inventory"]["files"]:
        if not f.get("columns"):
            continue
        lines += [f"### {f['path']}", "", "| column | type | non-null | null | NaN | distinct | min | max |", "|---|---|---|---|---|---|---|---|"]
        for c, s in f["columns"].items():
            lines.append(f"| {c} | {s['inferred_type']} | {s['non_null']} | {s['null']} | {s['nan_tokens']} | "
                         f"{s['distinct']} | {s.get('min', '')} | {s.get('max', '')} |")
        lines.append(f"\nExact duplicate rows: {f['exact_duplicate_rows']}\n")
    lines += ["## Risks to review", "",
              f"- Possible leakage columns: {p['leakage_review'] or 'none detected by name'}",
              f"- Timestamp columns without UTC offset: {p['naive_timestamp_columns'] or 'none'}",
              f"- Columns containing NaN tokens: {p['nan_token_columns'] or 'none'}", ""]
    if "mapped" in p:
        m = p["mapped"]
        lines += ["## Mapped dry run (nothing written)", "",
                  f"- Participants: {m['participant_count']}", f"- Record counts: {m['record_counts']}",
                  f"- Totals: {m['totals']}", f"- Images: {m['images']}", f"- Nutrition labels: {m['labels']}",
                  f"- Batch warnings: {m['batch_warnings'] or 'none'}", "", "| modality | read | accepted | rejected | duplicates | reasons |",
                  "|---|---|---|---|---|---|"]
        for mod, d in m["by_modality"].items():
            lines.append(f"| {mod} | {d['rows_read']} | {d['accepted']} | {d['rejected']} | {d['duplicates_dropped']} | {d['rejection_reasons']} |")
        lines += ["", "| participant | first | last | span days | coverage | gaps >15m | missing modalities |", "|---|---|---|---|---|---|---|"]
        for pid, cov in m["timestamp_coverage"].items():
            lines.append(f"| {pid} | {cov.get('first')} | {cov.get('last')} | {cov.get('span_days')} | "
                         f"{cov.get('coverage_ratio_vs_5min_grid')} | {cov.get('gaps_over_15min')} | "
                         f"{', '.join(m['missing_modalities_by_participant'][pid]) or '-'} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", required=True)
    parser.add_argument("--mapping", default=None)
    parser.add_argument("--out-dir", default=str(DEFAULT_PROFILE_DIR))
    args = parser.parse_args(argv)
    p = profile(args.path, args.mapping)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"profile_{p['dataset_root_name']}_{p['profiled_at'].replace(':', '').replace('-', '')}"
    (out / f"{stem}.json").write_text(json.dumps(p, indent=2, default=str) + "\n")
    md = to_markdown(p)
    (out / f"{stem}.md").write_text(md)
    print(md)
    print(f"wrote {out / stem}.json and .md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
