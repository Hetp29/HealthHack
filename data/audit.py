"""Reproducible dataset audit -> docs/data_audit/ (small files, safe to commit).

    python -m data.audit                                   # both datasets, default paths
    python -m data.audit --metabonet-subjects data/reports/profiles/metabonet_subjects.csv

Reads raw sources directly (not the database), so it can be re-run from a clean clone
that has the datasets in data/official_raw/. The MetaboNet per-subject table is produced
by ``python -m data.adapters.metabonet audit`` (a ~5 min streaming pass); pass it in to
avoid re-scanning, or omit it to scan now.

Outputs: summary.md, data_quality.md, cgmacros_participants.csv, metabonet_schema.json,
modality_coverage.csv, day8_eligibility.csv. No row-level physiological values are
written; only counts, coverage ratios and date ranges of date-shifted timestamps.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from data.adapters import cgmacros, metabonet
from data.eligibility import (
    BASELINE_TOLERANCE_MIN,
    HISTORY_DAYS,
    HORIZON_TOLERANCE_MIN,
    MIN_HISTORY_COVERAGE,
    OVERLAP_WINDOW_MIN,
    meal_episodes,
    summarize,
)
from data.schema import MACRO_FIELDS
from data.storage.db import REPO_ROOT

OUT_DIR = REPO_ROOT / "docs" / "data_audit"
ELIG_COLS = ["candidate_meals", "with_7d_history", "history_coverage_ok", "baseline_ok", "horizons_ok",
             "eligible_episodes", "eligible_without_overlap", "first_eligible_meal_time"]


def _gaps(times: pd.Series) -> tuple[int, float]:
    d = pd.to_datetime(times).sort_values().diff().dt.total_seconds().div(60)
    big = d[d > 15]
    return int(len(big)), float(big.max()) if len(big) else 0.0


def audit_cgmacros(root: Path) -> pd.DataFrame:
    files, skipped = cgmacros.discover(root)
    rows = []
    for path in files:
        prepared, a = cgmacros.build_prepared(path, "dexcom")
        cgm_t = [r.values["timestamp"] for r in prepared.rows["cgm"]]
        meals = sorted((r.values for r in prepared.rows["meals"] if r.values["timestamp"]), key=lambda m: m["timestamp"])
        complete = np.array([all(m.get(f) is not None for f in MACRO_FIELDS) for m in meals], dtype=bool)
        ep = meal_episodes(cgm_t, [m["timestamp"] for m in meals])
        gaps, longest = _gaps(pd.Series(cgm_t))
        first, last = pd.Timestamp(a["first_timestamp"]), pd.Timestamp(a["last_timestamp"])
        dex, lib = a["dexcom_stats"], a["libre_stats"]
        hr_minutes = len(prepared.rows["heart_rate"])
        rows.append({
            "participant_id": a["participant_id"],
            "source_file": a["source_file"],
            "first_timestamp_shifted": str(first),
            "last_timestamp_shifted": str(last),
            "span_days": round((last - first).total_seconds() / 86400, 2),
            "rows_1min": a["rows"],
            "duplicate_timestamps": a["duplicate_timestamps"],
            "unparseable_timestamps": a["unparseable_timestamps"],
            "dexcom_native_readings": dex["kept"],
            "dexcom_interpolated_rows_dropped": dex["off_phase"] + dex["phase_non_integer"],
            "libre_native_readings": lib["kept"],
            "libre_interpolated_rows_dropped": lib["off_phase"] + lib["phase_non_integer"],
            "primary_cgm_gaps_over_15min": gaps,
            "primary_cgm_longest_gap_min": round(longest, 1),
            "heart_rate_minutes": hr_minutes,
            "heart_rate_coverage": round(hr_minutes / max(a["rows"], 1), 4),
            "activity_columns": ";".join(a["activity_columns"]),
            "meal_events": a["meal_events"],
            "meals_complete_macros": int(complete.sum()),
            "meal_photos_linked": sum(1 for m in meals if m["image_ref"]),
            "meal_photo_files_missing": a["meal_photo_files_missing"],
            "end_of_meal_photos": a["photo_rows_without_meal_type"],
            "photo_files_in_folder": a["photo_files_in_folder"],
            "amount_consumed_below_100pct": a["amount_consumed_below_100"],
            "amount_consumed_above_100pct": a["amount_consumed_above_100"],
            "medication_records": 0,
            **summarize(ep, complete),
        })
    frame = pd.DataFrame(rows)
    frame.attrs["skipped"] = skipped
    return frame


def modality_coverage(cg: pd.DataFrame, mn: pd.DataFrame) -> pd.DataFrame:
    rows = [{
        "dataset": "cgmacros", "source": "CGMacros",
        "participants": len(cg),
        "with_cgm": int((cg.dexcom_native_readings > 0).sum()),
        "with_second_cgm_sensor": int((cg.libre_native_readings > 0).sum()),
        "with_meals": int((cg.meal_events > 0).sum()),
        "meal_events": int(cg.meal_events.sum()),
        "meals_with_complete_macros": int(cg.meals_complete_macros.sum()),
        "meals_with_photo": int(cg.meal_photos_linked.sum()),
        "with_heart_rate": int((cg.heart_rate_minutes > 0).sum()),
        "with_activity": int((cg.activity_columns != "").sum()),
        "with_insulin_or_medication": 0,
        "eligible_participants": int((cg.eligible_episodes > 0).sum()),
        "eligible_episodes": int(cg.eligible_episodes.sum()),
    }]
    for src, g in mn.groupby("source"):
        rows.append({
            "dataset": "metabonet", "source": src,
            "participants": len(g),
            "with_cgm": int((g.cgm_readings > 0).sum()),
            "with_second_cgm_sensor": 0,
            "with_meals": int((g.meal_events > 0).sum()),
            "meal_events": int(g.meal_events.sum()),
            "meals_with_complete_macros": 0,
            "meals_with_photo": 0,
            "with_heart_rate": int((g.heart_rate_rows > 0).sum()),
            "with_activity": int(((g.steps_rows > 0) | (g.calories_burned_rows > 0)).sum()),
            "with_insulin_or_medication": int((g.bolus_events > 0).sum()),
            "eligible_participants": int((g.eligible_episodes > 0).sum()),
            "eligible_episodes": int(g.eligible_episodes.sum()),
        })
    return pd.DataFrame(rows)


def day8_table(cg: pd.DataFrame, mn: pd.DataFrame) -> pd.DataFrame:
    a = cg.assign(dataset="cgmacros", source="CGMacros")[["dataset", "source", "participant_id", *ELIG_COLS,
                                                          "eligible_with_complete_macros"]]
    b = mn.assign(eligible_with_complete_macros=0)[["dataset", "source", "participant_id", *ELIG_COLS,
                                                    "eligible_with_complete_macros"]]
    return pd.concat([a, b], ignore_index=True)


def _md_table(frame: pd.DataFrame) -> str:
    cols = list(frame.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in frame.iterrows():
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def write_reports(cg: pd.DataFrame, mn: pd.DataFrame, schema: dict, out: Path = OUT_DIR) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cg.to_csv(out / "cgmacros_participants.csv", index=False)
    cov = modality_coverage(cg, mn)
    cov.to_csv(out / "modality_coverage.csv", index=False)
    d8 = day8_table(cg, mn)
    d8.to_csv(out / "day8_eligibility.csv", index=False)
    by_source = mn.groupby("source").agg(
        subjects=("participant_id", "size"), rows=("rows", "sum"), cgm_readings=("cgm_readings", "sum"),
        meal_events=("meal_events", "sum"), bolus_events=("bolus_events", "sum"),
        heart_rate_rows=("heart_rate_rows", "sum"), steps_rows=("steps_rows", "sum"),
        negative_bolus=("negative_bolus", "sum"), eligible_subjects=("eligible_episodes", lambda s: int((s > 0).sum())),
        eligible_episodes=("eligible_episodes", "sum"), split_across_traintest=("split_across_traintest", "sum"),
    ).reset_index()
    schema = {**schema, "subjects": int(len(mn)),
              "subject_ids_shared_across_sources": int(mn.groupby("subject_id").source.nunique().gt(1).sum()),
              "is_test_rows": int(mn.is_test_rows.sum()),
              "by_source": json.loads(by_source.to_json(orient="records"))}
    (out / "metabonet_schema.json").write_text(json.dumps(schema, indent=2) + "\n")

    cg_elig, mn_elig = int(cg.eligible_episodes.sum()), int(mn.eligible_episodes.sum())
    skipped = cg.attrs.get("skipped", [])
    summary = f"""# Data audit — organizer datasets

Generated by `python -m data.audit` (reproducible; source files are not committed).
Timestamps in both datasets are **date-shifted, offset-naive local wall clock**; dates
below are shifted dates, not real calendar dates.

## Datasets

| Dataset | Citation | Format | Distributed as |
|---|---|---|---|
| CGMacros v1.0.0 | Das A, Kerr D, Glantz N, et al. *CGMacros: a pilot scientific dataset for personalized nutrition and diet monitoring.* Sci Data 12, 1557 (2025). doi:10.1038/s41597-025-05851-7 | per-participant 1-min CSV + meal JPGs | organizer SharePoint (PhysioNet package layout) |
| MetaboNet (Type1Diabetes.parquet) | Wolff MK, Calhoun P, Aiello EM, Qin Y, Royston SF. *MetaboNet: The Largest Publicly Available Consolidated Data Set for Type 1 Diabetes Management.* J Diabetes Sci Technol. 2026. doi:10.1177/19322968261441637 | one Parquet file | organizer SharePoint |

**Licensing.** The CGMacros package's `LICENSE.txt` is **empty (0 bytes)**, so the terms are
not stated in the files we received; we rely on the organizers' permission for challenge
use and cite the paper. MetaboNet consolidates {schema['by_source'].__len__()} source studies, some of which
are distributed under their own data-use agreements (e.g. OhioT1DM); we use the file only
for this challenge, as provided by the organizers. **Neither dataset, no derived database
and no images are committed to git** (`data/official_raw/` and `*.db` are gitignored).

## Headline numbers

| | CGMacros | MetaboNet |
|---|---|---|
| Participants discovered | {len(cg)} (folders skipped: {len(skipped)}) | {len(mn)} subjects ({schema['subject_ids_shared_across_sources']} numeric ids reused across sources) |
| Rows | {int(cg.rows_1min.sum()):,} (1-min) | {schema['rows']:,} (5-min) |
| Native CGM readings | {int(cg.dexcom_native_readings.sum()):,} Dexcom G6 Pro + {int(cg.libre_native_readings.sum()):,} Libre Pro | {int(mn.cgm_readings.sum()):,} |
| Meal events | {int(cg.meal_events.sum()):,} ({int(cg.meals_complete_macros.sum()):,} with all 5 macros) | {int(mn.meal_events.sum()):,} carb entries (carbs only) |
| Meal photos linked | {int(cg.meal_photos_linked.sum()):,} | 0 (none in source) |
| Participants with HR | {int((cg.heart_rate_minutes > 0).sum())} | {int((mn.heart_rate_rows > 0).sum())} |
| Participants with insulin records | 0 (not in source) | {int((mn.bolus_events > 0).sum())} |
| **Eligible Day-8 episodes** | **{cg_elig}** ({int(cg.eligible_without_overlap.sum())} without overlap, {int(cg.eligible_with_complete_macros.sum())} with all 5 macros) across {int((cg.eligible_episodes > 0).sum())} participants | **{mn_elig:,}** ({int(mn.eligible_without_overlap.sum()):,} without overlap) across {int((mn.eligible_episodes > 0).sum())} subjects — **carbs only** |

Counts are computed from the raw sources **before** validation; 8 CGMacros meals with implausible fiber
are quarantined at ingestion, so the database holds slightly fewer eligible meals (see `data_quality.md`).

Eligibility (descriptive; the ML owner sets the real inclusion rule): ≥{HISTORY_DAYS} days of prior
CGM with ≥{MIN_HISTORY_COVERAGE:.0%} of 5-min readings present, a reading within {BASELINE_TOLERANCE_MIN} min before
the meal, and a reading within ±{HORIZON_TOLERANCE_MIN} min of t+30/60/90/120. "Overlap" = another meal within
{OVERLAP_WINDOW_MIN} min. Thresholds mirror `ml/config.py`.

## Recommendation

**Primary modeling dataset: CGMacros.** It is the only source with all five macronutrients
(which `POST /api/predict` requires), meal photos, heart rate and activity for every
participant, in a non-T1D population (healthy / prediabetes / T2D per the CGMacros paper; `bio.csv` carries A1c).
**MetaboNet is the scale and heterogeneity demonstration**: a second, very different
source (T1D, insulin, carbs only) ingested through the same pipeline and Protocol. Its
meals cannot be forecast under the current contract without protein/fat/fiber/kcal.

## Files

- `cgmacros_participants.csv` — per participant: period, sensors, de-interpolation, HR, activity, meals, photos, gaps, eligibility.
- `metabonet_schema.json` — file checksum, row groups, Arrow schema, timestamp semantics, per-source totals.
- `modality_coverage.csv` — per dataset/source: who has which modality.
- `day8_eligibility.csv` — per participant, both datasets: each eligibility criterion and the counts passing it.
- `data_quality.md` — issues, exclusions and transformation assumptions.

## Per-source coverage

{_md_table(cov)}
"""
    (out / "summary.md").write_text(summary)

    quality = f"""# Data quality, transformations and exclusions

## CGMacros

1. **Interpolated glucose (critical).** Both CGM columns are linearly interpolated to 1-minute
   rows between true readings (Dexcom every 5 min, Libre every 15 min). An interpolated
   minute is computed from the NEXT real reading, i.e. it leaks the future. The adapter
   keeps only the true readings ({int(cg.dexcom_native_readings.sum()):,} Dexcom, {int(cg.libre_native_readings.sum()):,} Libre) and drops
   {int(cg.dexcom_interpolated_rows_dropped.sum()):,} Dexcom and {int(cg.libre_interpolated_rows_dropped.sum()):,} Libre interpolated rows. Method: within each contiguous
   run, keep the sampling phase whose values are whole mg/dL.
2. **Two sensors.** Dexcom G6 Pro is the primary `cgm` series (5-min cadence matches the
   contract); Libre Pro goes to `cgm_secondary` with its sensor name. `--primary-sensor libre`
   switches them. Sensor identity is stored on every reading.
3. **Header variants.** 9 different headers across 45 files (`METs` vs `Intensity` vs `Steps`,
   an extra `Sugar` / `RecordIndex`, a trailing-space `Amount Consumed `, a leading index
   column, swapped Libre/Dexcom order). The adapter normalizes by name, not position.
4. **Meals vs photos.** {int(cg.end_of_meal_photos.sum())} photos sit on rows without a meal type (end-of-meal photos);
   they are not counted as meals. {int(cg.meal_photo_files_missing.sum())} referenced meal photos are missing on disk.
5. **Implausible fiber.** 8 meals (participants 016 and 019) report 1,020–2,830 g of fiber,
   probably milligrams. They are **quarantined** (`implausible_fiber_g`), not unit-guessed.
   Many meals report fiber = 0 (stored as 0; it may mean "not estimated").
6. **Fields without a canonical home (not stored):** `Meal Type`, `Amount Consumed` (% of
   the meal eaten; {int(cg.amount_consumed_below_100pct.sum())} meals < 100%, {int(cg.amount_consumed_above_100pct.sum())} > 100%; macros are NOT scaled by it),
   `Sugar`, `RecordIndex`, and participant-level `bio.csv` / gut-health / microbiome tables.
7. **Coverage.** Participant 007 has a fragmented Dexcom signal (~66% coverage); 018 has two
   recording periods ~9 days apart. Both are kept and reported.
8. **Medications.** None recorded. Absent ≠ "not taking any" (T2D participants may be medicated).
9. **Activity units.** METs are stored as MET (source is MET×10 per the data dictionary);
   Fitbit `Intensity` as `fitbit_intensity_level`; `Calories (Activity)` as kcal per minute.

## MetaboNet

1. **Identity collisions.** {schema['subject_ids_shared_across_sources']} numeric subject ids occur in more than one source study. Participant
   identity is `(source_file, id)` → `metabonet_<source>_<id>`.
2. **Missing modalities by source.** 6 source studies (DCLP3, DCLP5, Flair, IOBP2,
   ShanghaiT1DM, SIESTA) have **no carbohydrate entries**, so no meal episodes. HR exists only
   in OhioT1DM, BrisT1D, HUPA-UCM and SIESTA. See `modality_coverage.csv`.
3. **Meals are carbs only.** `carbs` is zero-filled; a meal = carbs > 0. Protein, fat, fiber,
   kcal and images do not exist; they stay null.
4. **Insulin.** Bolus events (> 0) become `medications` (`insulin_bolus`, unit `U`, inferred).
   {int(mn.negative_bolus.sum())} negative bolus values are quarantined. Basal delivery is reported but not ingested as
   events. No pharmacodynamic effect is assumed.
5. **Implausible CGM.** A handful of readings below 10 mg/dL are rejected; values 10–39 are
   kept and flagged.
6. **Train/test split.** All {schema['rows']:,} rows have `is_test = False` ({schema['is_test_rows']} test rows), i.e. we
   received the training portion. {int(mn.split_across_traintest.sum())} subjects are flagged `subject_split_across_traintest`
   (part of their data is in a held-out test split we do not have).
7. **Ingestion scope.** The full file is audited by streaming. Ingestion loads, per eligible
   subject, an 8-day window (7 days before the first eligible meal's day + that day).
   Subjects without an eligible episode are not ingested. Everything outside the window
   is excluded from the database (still counted here).

## Both datasets

- **Time.** Offset-naive, date-shifted local wall clock, no timezone given. Stored as
  UTC-labelled wall clock through an explicit `assume_timezone="UTC"` policy;
  `participant.timezone` is null (unknown). Within-participant ordering, durations and
  time-of-day are exact; cross-participant absolute instants are meaningless. With a null
  timezone, `ml/features.py` falls back to UTC = the source wall clock.
- **Provenance.** Every row carries `data_source="official"`, `ingestion_run_id`, and its
  participant's `source_dataset`; every run records the input SHA-256.
- **Nothing invented.** No macro, activity, medication or image value is imputed.
"""
    (out / "data_quality.md").write_text(quality)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cgmacros", default=str(cgmacros.DEFAULT_PATH))
    parser.add_argument("--metabonet", default=str(metabonet.DEFAULT_PATH))
    parser.add_argument("--metabonet-subjects", default=None, help="CSV from `metabonet audit` (skips the rescan)")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args(argv)
    cg = audit_cgmacros(Path(args.cgmacros))
    mn = pd.read_csv(args.metabonet_subjects, dtype={"subject_id": str}) if args.metabonet_subjects \
        else metabonet.audit(args.metabonet, progress=True)
    write_reports(cg, mn, metabonet.schema_report(args.metabonet), Path(args.out_dir))
    print(f"wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
