"""Data-quality summary over canonical records (per participant and per modality).

These are descriptive data-quality statistics only. They define no prediction target
and choose no inclusion rule; that belongs to the ML owner. Usable on an ingestion
batch or on the whole database:

    python -m data.quality [--db-url ...] [--out data/reports/quality.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from data.schema import MACRO_FIELDS
from data.timeutil import to_rfc3339

CGM_NOMINAL_MINUTES = 5
GAP_THRESHOLD_MINUTES = 15  # > 3 nominal intervals without a reading counts as a gap
NEAR_MEAL_PRE_MINUTES = 30
NEAR_MEAL_POST_MINUTES = 120


def _by_participant(records: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[record["participant_id"]].append(record)
    for rows in grouped.values():
        rows.sort(key=lambda r: r["timestamp"])
    return grouped


def _gaps(times: list[datetime]) -> list[float]:
    return [
        (b - a).total_seconds() / 60
        for a, b in zip(times, times[1:])
        if (b - a) > timedelta(minutes=GAP_THRESHOLD_MINUTES)
    ]


def _window_count(times: list[datetime], start: datetime, end: datetime, *, include_start: bool) -> int:
    lo = bisect_left(times, start) if include_start else bisect_right(times, start)
    return bisect_right(times, end) - lo


def cgm_summary(rows: list[dict]) -> dict:
    if not rows:
        return {"count": 0}
    times = [r["timestamp"] for r in rows]
    values = [r["glucose_mg_dl"] for r in rows]
    span_min = (times[-1] - times[0]).total_seconds() / 60
    expected = int(span_min // CGM_NOMINAL_MINUTES) + 1
    gaps = _gaps(times)
    return {
        "count": len(rows),
        "first": to_rfc3339(times[0]),
        "last": to_rfc3339(times[-1]),
        "span_days": round(span_min / 1440, 2),
        "coverage_ratio_vs_5min_grid": round(min(len(rows) / expected, 1.0), 4),
        "gaps_over_15min": len(gaps),
        "longest_gap_minutes": round(max(gaps), 1) if gaps else 0.0,
        "glucose_min": min(values),
        "glucose_median": statistics.median(values),
        "glucose_max": max(values),
    }


def meal_summary(meals: list[dict], cgm_rows: list[dict]) -> dict:
    if not meals:
        return {"count": 0}
    times = [r["timestamp"] for r in cgm_rows]
    pre_cov, post_cov = [], []
    next_within_120 = 0
    for i, meal in enumerate(meals):
        t = meal["timestamp"]
        pre = _window_count(times, t - timedelta(minutes=NEAR_MEAL_PRE_MINUTES), t, include_start=True)
        post = _window_count(times, t, t + timedelta(minutes=NEAR_MEAL_POST_MINUTES), include_start=False)
        pre_cov.append(min(pre / (NEAR_MEAL_PRE_MINUTES // CGM_NOMINAL_MINUTES), 1.0))
        post_cov.append(min(post / (NEAR_MEAL_POST_MINUTES // CGM_NOMINAL_MINUTES), 1.0))
        if i + 1 < len(meals) and meals[i + 1]["timestamp"] - t <= timedelta(minutes=NEAR_MEAL_POST_MINUTES):
            next_within_120 += 1
    complete = sum(all(m.get(f) is not None for f in MACRO_FIELDS) for m in meals)
    return {
        "count": len(meals),
        "complete_nutrition": complete,
        "nutrition_completeness_ratio": round(complete / len(meals), 4),
        "missing_by_field": {f: sum(m.get(f) is None for m in meals) for f in MACRO_FIELDS},
        "with_image_ref": sum(m.get("image_ref") is not None for m in meals),
        "mean_premeal_cgm_coverage_30min": round(statistics.fmean(pre_cov), 4),
        "mean_postmeal_cgm_coverage_120min": round(statistics.fmean(post_cov), 4),
        "followed_by_another_meal_within_120min": next_within_120,
    }


def quality_summary(
    participants: list[dict],
    cgm: list[dict],
    heart_rate: list[dict],
    meals: list[dict],
    medications: list[dict],
    activity: list[dict],
) -> dict:
    by = {
        "cgm": _by_participant(cgm),
        "heart_rate": _by_participant(heart_rate),
        "meals": _by_participant(meals),
        "medications": _by_participant(medications),
        "activity": _by_participant(activity),
    }
    ids = sorted({p["participant_id"] for p in participants} | {pid for g in by.values() for pid in g})
    per_participant = {}
    for pid in ids:
        hr_rows = by["heart_rate"].get(pid, [])
        hr_gaps = _gaps([r["timestamp"] for r in hr_rows])
        per_participant[pid] = {
            "cgm": cgm_summary(by["cgm"].get(pid, [])),
            "heart_rate": {"count": len(hr_rows), "gaps_over_15min": len(hr_gaps)},
            "meals": meal_summary(by["meals"].get(pid, []), by["cgm"].get(pid, [])),
            "medications": {
                "count": len(by["medications"].get(pid, [])),
                "classes": sorted({m["medication_class"] for m in by["medications"].get(pid, [])}),
            },
            "activity": {
                "count": len(by["activity"].get(pid, [])),
                "units": sorted({a["activity_unit"] for a in by["activity"].get(pid, []) if a["activity_unit"]}),
            },
            "absent_modalities": sorted(name for name, g in by.items() if not g.get(pid)),
        }
    return {
        "participant_count": len(ids),
        "record_counts": {
            "participants": len(participants),
            "cgm": len(cgm),
            "heart_rate": len(heart_rate),
            "meals": len(meals),
            "medications": len(medications),
            "activity": len(activity),
        },
        "participants": per_participant,
    }


def main(argv: list[str] | None = None) -> int:
    from data.repository import DataRepository

    parser = argparse.ArgumentParser(description="Data-quality report over the canonical database.")
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--out", default=None, help="write JSON here (default: print)")
    args = parser.parse_args(argv)
    repo = DataRepository(args.db_url)
    report = repo.quality_report()
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
