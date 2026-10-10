"""CGMacros adapter (Das et al., Sci Data 12, 1557 (2025); PhysioNet package v1.0.0).

    python -m data.adapters.cgmacros --path data/official_raw/cgmacros_extracted/CGMacros
    python -m data.adapters.cgmacros --path ... --primary-sensor libre --participants 001,002 --dry-run

Source layout: ``CGMacros-NNN/CGMacros-NNN.csv`` (one row per minute) plus
``CGMacros-NNN/photos/*.jpg``. The participant ID exists only in the file name.

What this adapter does, and why
-------------------------------
* **Sensor de-interpolation.** Both CGM columns are linearly interpolated to 1-minute
  rows: Dexcom G6 Pro reads every 5 min, Libre Pro every 15 min, and every minute in
  between is a straight line toward the NEXT real reading. Interpolated minutes encode
  future glucose, so they are never stored. For each contiguous run of values we pick
  the sampling phase whose values are integer-valued (sensor readings are whole mg/dL)
  and keep only those rows; all other rows are counted as dropped interpolation.
* **Primary vs secondary sensor.** ``cgm`` holds one sensor (default Dexcom: native 5-min
  cadence matches the contract); the other goes to ``cgm_secondary`` with its sensor name.
* **Meals.** A meal is a row with ``Meal Type`` set. Photos without a meal type are
  end-of-meal photos, not separate meals. ``image_ref`` = ``cgmacros://<folder>/<path>``
  when the file exists. Macros are stored as reported; ``Amount Consumed`` (% eaten) and
  ``Meal Type`` have no canonical field, are NOT applied, and are reported.
* **Activity.** METs (stored as MET x10 per the data dictionary, converted to MET),
  Fitbit ``Intensity`` level, ``Steps`` and ``Calories (Activity)`` (kcal/min), per minute.
* **Time.** Timestamps are date-shifted, offset-naive local wall clock with no stated
  timezone. The original text is preserved in ``source_timestamp`` on every row; the
  participant is marked ``time_basis="source_local_unknown_tz"`` with ``timezone`` null.
  The canonical ``timestamp`` (which the frozen contract requires to carry an offset)
  encodes that wall clock with a +00:00 label as an ordering key: it is NOT a real UTC
  instant. Within-participant order, durations and local time-of-day are exact.
* **Preserved source fields.** ``Amount Consumed`` (per meal) and ``bio.csv`` A1c (per
  participant) go to the data-layer-only ``source_attributes`` table, unexposed by the API.
* **Medications.** CGMacros has none. Absent means unrecorded, not "not taking any".
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from data.adapters.pipeline import DEFAULT_REPORT_DIR, IngestionResult, PreparedSource, run_prepared_ingestion
from data.adapters.readers import RawRow, sha256_file
from data.storage.db import REPO_ROOT, make_engine

DATASET = "cgmacros"
ID_PREFIX = "cgmacros_"
DEFAULT_PATH = REPO_ROOT / "data" / "official_raw" / "cgmacros_extracted" / "CGMacros"
SENSORS = {
    "dexcom": {"column": "Dexcom GL", "cadence_min": 5, "name": "dexcom_g6_pro"},
    "libre": {"column": "Libre GL", "cadence_min": 15, "name": "libre_pro"},
}
FOLDER_RE = re.compile(r"^CGMacros-(\d{3})$")
INT_TOL = 1e-6


def discover(root: str | Path) -> tuple[list[Path], list[dict]]:
    """Participant CSVs under ``root`` and the folders that were skipped (with why)."""
    root = Path(root)
    found, skipped = [], []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        m = FOLDER_RE.match(folder.name)
        if not m:
            continue
        csv_path = folder / f"{folder.name}.csv"
        if csv_path.is_file():
            found.append(csv_path)
        else:
            skipped.append({"folder": folder.name, "reason": "no participant csv"})
    return found, skipped


def read_participant_csv(path: Path) -> pd.DataFrame:
    """Read one file, normalizing the header variants found across the release."""
    frame = pd.read_csv(path)
    frame.columns = [c.strip() for c in frame.columns]
    frame = frame.drop(columns=[c for c in frame.columns if c.startswith("Unnamed")])
    frame["_ts"] = pd.to_datetime(frame["Timestamp"], errors="coerce")
    frame["_row"] = range(2, len(frame) + 2)  # 1-based file line (header is line 1)
    return frame


def native_readings(frame: pd.DataFrame, column: str, cadence: int) -> tuple[pd.DataFrame, dict]:
    """Rows of ``frame`` holding real sensor readings for ``column`` (interpolation removed).

    Values are split into contiguous runs (consecutive minutes, no NaN). Within a run the
    sampling phase (minute index mod cadence) whose values are integer-valued most often
    marks the true readings; only those rows are kept, and only if integer-valued.
    """
    values = frame[column]
    present = values.notna() & frame["_ts"].notna()
    sub = frame.loc[present, ["_ts", "_row", column]].copy()
    stats = {"rows_with_value": int(len(sub)), "kept": 0, "phase_non_integer": 0, "off_phase": 0, "runs": 0}
    if sub.empty:
        return sub.iloc[0:0], stats
    minute = (sub["_ts"].astype("int64") // 60_000_000_000).astype("int64")
    run_id = (minute.diff() != 1).cumsum()
    keep_idx = []
    for _, run in sub.assign(_m=minute, _run=run_id).groupby("_run", sort=False):
        stats["runs"] += 1
        is_int = (run[column] - run[column].round()).abs() < INT_TOL
        phases = run["_m"] % cadence
        best = max(range(cadence), key=lambda p: (int((is_int & (phases == p)).sum()), -p))
        on_phase = phases == best
        stats["phase_non_integer"] += int((on_phase & ~is_int).sum())
        stats["off_phase"] += int((~on_phase).sum())
        keep_idx.extend(run.index[on_phase & is_int])
    kept = sub.loc[keep_idx].sort_values("_ts")
    stats["kept"] = int(len(kept))
    return kept, stats


def _num(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def _ts(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def read_bio(root: Path) -> dict[str, dict]:
    """A1c per participant number from ``bio.csv`` (if present). Other columns stay in the file."""
    path = Path(root) / "bio.csv"
    if not path.is_file():
        return {}
    frame = pd.read_csv(path)
    frame.columns = [c.strip() for c in frame.columns]
    out = {}
    for i, r in frame.iterrows():
        a1c = pd.to_numeric(r.get("A1c PDL (Lab)"), errors="coerce")
        out[f"{int(r['subject']):03d}"] = {"row": i + 2, "a1c": None if pd.isna(a1c) else float(a1c)}
    return out


def build_prepared(csv_path: Path, primary: str = "dexcom", bio: dict | None = None) -> tuple[PreparedSource, dict]:
    """Canonical rows + an audit record for one participant file."""
    folder = csv_path.parent
    number = FOLDER_RE.match(folder.name).group(1)
    pid = f"{ID_PREFIX}{number}"
    rel = f"{folder.name}/{csv_path.name}"
    frame = read_participant_csv(csv_path)
    audit: dict = {"participant_id": pid, "source_file": rel, "rows": len(frame),
                   "unparseable_timestamps": int(frame["_ts"].isna().sum()),
                   "duplicate_timestamps": int(frame["_ts"].duplicated().sum()),
                   "columns": [c for c in frame.columns if not c.startswith("_")]}

    def raw(row_no: int, values: dict) -> RawRow:
        return RawRow(rel, int(row_no), values)

    rows: dict[str, list[RawRow]] = {
        "participants": [raw(0, {"participant_id": pid, "display_name": f"CGMacros {number}",
                                 "timezone": None, "source_dataset": DATASET,
                                 "time_basis": "source_local_unknown_tz"})],
        "cgm": [], "cgm_secondary": [], "heart_rate": [], "meals": [], "activity": [], "source_attributes": [],
    }
    if bio is not None and bio.get("a1c") is not None:
        rows["source_attributes"].append(RawRow("bio.csv", int(bio["row"]), {
            "participant_id": pid, "entity_type": "participant", "entity_id": pid, "attribute": "a1c_lab",
            "value_num": bio["a1c"], "unit": None,
            "value_text": "bio.csv 'A1c PDL (Lab)'; dictionary says mmol/mol, values 4.6-8.5 look like %",
        }))
    for key, spec in SENSORS.items():
        kept, stats = native_readings(frame, spec["column"], spec["cadence_min"])
        audit[f"{key}_stats"] = stats
        target = "cgm" if key == primary else "cgm_secondary"
        rows[target] += [
            raw(r, {"participant_id": pid, "timestamp": _ts(t), "glucose_mg_dl": float(v), "sensor": spec["name"]})
            for t, r, v in zip(kept["_ts"], kept["_row"], kept[spec["column"]])
        ]

    hr = frame.loc[frame["HR"].notna() & frame["_ts"].notna()]
    rows["heart_rate"] = [raw(r, {"participant_id": pid, "timestamp": _ts(t), "heart_rate_bpm": float(v)})
                          for t, r, v in zip(hr["_ts"], hr["_row"], hr["HR"])]

    activity_specs = [("METs", "MET", 0.1), ("Intensity", "fitbit_intensity_level", 1.0),
                      ("Steps", "steps", 1.0), ("Calories (Activity)", "kcal", 1.0)]
    audit["activity_columns"] = [c for c, _, _ in activity_specs if c in frame.columns]
    for column, unit, factor in activity_specs:
        if column not in frame.columns:
            continue
        a = frame.loc[frame[column].notna() & frame["_ts"].notna()]
        rows["activity"] += [raw(r, {"participant_id": pid, "timestamp": _ts(t),
                                     "activity_value": float(v) * factor, "activity_unit": unit})
                             for t, r, v in zip(a["_ts"], a["_row"], a[column])]

    meal_rows = frame.loc[frame["Meal Type"].notna()]
    photo_rows = frame.loc[frame["Image path"].notna()] if "Image path" in frame.columns else frame.iloc[0:0]
    missing_photos = 0
    for _, m in meal_rows.iterrows():
        ref = None
        path = m.get("Image path")
        if isinstance(path, str) and path.strip():
            if (folder / path.strip()).is_file():
                ref = f"cgmacros://{folder.name}/{path.strip()}"
            else:
                missing_photos += 1
        ts = m["_ts"]
        meal_id = f"{pid}_meal_{ts:%Y%m%dT%H%M}" if pd.notna(ts) else None
        consumed = pd.to_numeric(m.get("Amount Consumed"), errors="coerce")
        if meal_id and pd.notna(consumed):
            rows["source_attributes"].append(raw(m["_row"], {
                "participant_id": pid, "entity_type": "meal", "entity_id": meal_id,
                "attribute": "amount_consumed_pct", "value_num": float(consumed), "unit": "%",
            }))
        rows["meals"].append(raw(m["_row"], {
            "meal_id": meal_id,
            "participant_id": pid,
            "timestamp": _ts(ts) if pd.notna(ts) else None,
            "carbs_g": _num(m.get("Carbs")), "protein_g": _num(m.get("Protein")), "fat_g": _num(m.get("Fat")),
            "fiber_g": _num(m.get("Fiber")), "calories_kcal": _num(m.get("Calories")), "image_ref": ref,
        }))
    amount = pd.to_numeric(meal_rows.get("Amount Consumed"), errors="coerce") if "Amount Consumed" in frame else None
    audit.update({
        "meal_events": len(meal_rows),
        "meal_types": dict(Counter(str(x).strip().lower() for x in meal_rows["Meal Type"])),
        "photo_rows": len(photo_rows),
        "photos_on_meal_rows": int(meal_rows["Image path"].notna().sum()) if "Image path" in frame else 0,
        "photo_rows_without_meal_type": int(len(photo_rows) - (meal_rows["Image path"].notna().sum() if "Image path" in frame else 0)),
        "meal_photo_files_missing": missing_photos,
        "photo_files_in_folder": len(list((folder / "photos").glob("*.jpg"))) if (folder / "photos").is_dir() else 0,
        "amount_consumed_present": int(amount.notna().sum()) if amount is not None else 0,
        "amount_consumed_below_100": int((amount < 100).sum()) if amount is not None else 0,
        "amount_consumed_above_100": int((amount > 100).sum()) if amount is not None else 0,
        "first_timestamp": str(frame["_ts"].min()), "last_timestamp": str(frame["_ts"].max()),
    })
    notes = [
        {"code": "cgm_deinterpolated", "sensor": SENSORS[k]["name"], **audit[f"{k}_stats"]} for k in SENSORS
    ] + [
        {"code": "time_basis_source_local_unknown_tz",
         "detail": "date-shifted, offset-naive local wall clock; original text in source_timestamp"},
        {"code": "preserved_in_source_attributes", "fields": ["Amount Consumed", "bio.csv A1c"]},
        {"code": "non_canonical_fields_not_stored", "fields": ["Meal Type", "Sugar", "RecordIndex"]},
        {"code": "medications_absent_in_source"},
    ]
    if audit["photo_rows_without_meal_type"]:
        notes.append({"code": "end_of_meal_photos_not_counted_as_meals", "count": audit["photo_rows_without_meal_type"]})
    if missing_photos:
        notes.append({"code": "meal_photo_file_missing", "count": missing_photos})
    prepared = PreparedSource(
        name=f"{DATASET}_adapter_v1",
        label=f"{DATASET}/{rel}",
        sha256=sha256_file(csv_path),
        data_source="official",
        rows=rows,
        assume_timezone="floating",
        notes=notes,
    )
    return prepared, audit


def import_cgmacros(
    root: str | Path = DEFAULT_PATH,
    *,
    engine=None,
    db_url: str | None = None,
    primary: str = "dexcom",
    participants: list[str] | None = None,
    dry_run: bool = False,
    report_dir: str | Path | None = DEFAULT_REPORT_DIR,
) -> list[IngestionResult]:
    """One ingestion run per participant file (isolates failures, bounds memory)."""
    if primary not in SENSORS:
        raise ValueError(f"primary sensor must be one of {list(SENSORS)}")
    files, _skipped = discover(root)
    if participants:
        wanted = {p.zfill(3) for p in participants}
        files = [f for f in files if FOLDER_RE.match(f.parent.name).group(1) in wanted]
    if not files:
        raise FileNotFoundError(f"no CGMacros-NNN/CGMacros-NNN.csv files under {root}")
    if engine is None and not dry_run:
        engine = make_engine(db_url)
    bio = read_bio(root)
    results = []
    for path in files:
        number = FOLDER_RE.match(path.parent.name).group(1)
        prepared, _audit = build_prepared(path, primary, bio.get(number))
        results.append(run_prepared_ingestion(prepared, engine, dry_run=dry_run, report_dir=report_dir))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", default=str(DEFAULT_PATH), help="extracted CGMacros/ directory")
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--primary-sensor", choices=list(SENSORS), default="dexcom")
    parser.add_argument("--participants", default=None, help="comma-separated numbers, e.g. 001,002")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        results = import_cgmacros(
            args.path, db_url=args.db_url, primary=args.primary_sensor, dry_run=args.dry_run,
            participants=args.participants.split(",") if args.participants else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"cgmacros import failed: {exc}", file=sys.stderr)
        return 1
    totals = Counter()
    for r in results:
        totals.update({k: v for k, v in r.report["totals"].items()})
        t = r.report["totals"]
        print(f"{r.report['source']:<40} {r.status:<26} accepted {t['accepted']:>6} rejected {t['rejected']:>4}"
              f" inserted {t['inserted']:>6} unchanged {t['unchanged']:>6}")
    print(f"participants {len(results)} | " + " | ".join(f"{k} {v}" for k, v in sorted(totals.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
