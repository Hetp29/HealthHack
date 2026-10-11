"""Phase Zero: authentic meal-photograph inventory, linkage and prediction-time audit.

    python -m research.vision.inventory

Nothing here trains anything. It answers, from the data rather than from the
publication, whether meal photographs exist, which meal each one belongs to, and
-- the question that decides whether they may be used at all -- whether each
photograph existed before the prediction it would inform.

THE LINKAGE IS AUTHORITATIVE, NOT INFERRED
------------------------------------------
Each per-participant CSV has an `Image path` column, so image-to-meal linkage is
read from the dataset rather than reconstructed from filenames. Filenames do
encode a capture time (`00000005-PHOTO-2020-5-1-14-23-0.jpg`) and that is used
ONLY to corroborate the timestamp evidence for the availability audit. It is
never used as a model feature: a filename sequence number correlates with study
day and would be a pure shortcut predictor.

THE LEAKAGE THAT MATTERS
------------------------
CGMacros photographs are not all pre-meal. Many rows carry an `Image path` while
having NO `Meal Type`, and they cluster shortly after a logged meal -- these are
the follow-up photographs from which `Amount Consumed` was derived, i.e. pictures
of what was left on the plate. A leftovers photograph taken 30 minutes after the
meal is not available at prediction time, and worse, it encodes how much was
actually eaten, which is downstream of the quantity the model is trying to use.
Those images are classified as post-prediction and excluded from the prospective
evaluation.
"""

from __future__ import annotations

import collections
import csv
import datetime as dt
import glob
import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(
    "data/cgmacros-a-scientific-dataset-for-personalized-nutrition-and-diet-monitoring-1.0.0/CGMacros"
)
WORKSPACE = pathlib.Path("data/local/vision")
RESULTS = pathlib.Path("research/vision/results")

#: `00000005-PHOTO-2020-5-1-14-23-0.jpg` -> (2020, 5, 1, 14, 23)
NAME_RE = re.compile(r"^(\d+)-PHOTO-(\d{4})-(\d{1,2})-(\d{1,2})-(\d{1,2})-(\d{1,2})-(\d+)\.jpg$",
                     re.IGNORECASE)

#: Meal Type is written inconsistently in the source ('dinner', 'Dinner',
#: 'Snacks', 'snack 1'). Normalised here; the raw value is kept in the audit.
def normalize_meal_type(value: str) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    if v.startswith("break"):
        return "breakfast"
    if v.startswith("lunch"):
        return "lunch"
    if v.startswith("dinner"):
        return "dinner"
    if v.startswith("snack"):
        return "snack"
    return "other"


def filename_timestamp(name: str) -> dt.datetime | None:
    m = NAME_RE.match(name)
    if not m:
        return None
    _, y, mo, d, h, mi, _ = m.groups()
    try:
        return dt.datetime(int(y), int(mo), int(d), int(h), int(mi))
    except ValueError:
        return None


def participant_id(folder: str) -> str:
    return f"cgmacros_{int(folder.split('-')[-1]):03d}"


def scan() -> dict:
    rows_by_participant: dict[str, list[dict]] = {}
    for csv_path in sorted(glob.glob(str(ROOT / "CGMacros-*" / "CGMacros-*.csv"))):
        folder = pathlib.Path(csv_path).parent.name
        pid = participant_id(folder)
        out = []
        for r in csv.DictReader(open(csv_path)):
            image = (r.get("Image path") or "").strip()
            meal = normalize_meal_type(r.get("Meal Type", ""))
            if not image and not meal:
                continue
            try:
                stamp = dt.datetime.strptime(r["Timestamp"].strip(), "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
            out.append({
                "participant_id": pid, "folder": folder, "timestamp": stamp,
                "meal_type_raw": (r.get("Meal Type") or "").strip(), "meal_type": meal,
                "image": image,
                "carbs": r.get("Carbs", ""), "protein": r.get("Protein", ""),
                "fat": r.get("Fat", ""), "fiber": r.get("Fiber", ""),
                "calories": r.get("Calories", ""),
                "amount_consumed": (r.get("Amount Consumed ") or r.get("Amount Consumed") or ""),
            })
        rows_by_participant[pid] = out
    return rows_by_participant


def main() -> int:
    from PIL import Image

    rows_by_participant = scan()
    disk = {p: sorted(f.name for f in (ROOT / p / "photos").glob("*.jpg"))
            for p in sorted(d.name for d in ROOT.glob("CGMacros-*") if d.is_dir())}
    total_disk = sum(len(v) for v in disk.values())

    meal_rows = [r for rs in rows_by_participant.values() for r in rs if r["meal_type"]]
    img_rows = [r for rs in rows_by_participant.values() for r in rs if r["image"]]
    meal_with_img = [r for r in meal_rows if r["image"]]
    orphan_img = [r for r in img_rows if not r["meal_type"]]

    # --- availability classification ------------------------------------- #
    # An orphan image is attributed to the most recent logged meal of the same
    # participant; its offset decides its class.
    classes = collections.Counter()
    offsets: list[float] = []
    detail: list[dict] = []
    for pid, rs in rows_by_participant.items():
        meals = sorted([r for r in rs if r["meal_type"]], key=lambda r: r["timestamp"])
        for r in sorted(rs, key=lambda r: r["timestamp"]):
            if not r["image"]:
                continue
            if r["meal_type"]:
                # the image sits on the meal row itself
                name = pathlib.Path(r["image"]).name
                ft = filename_timestamp(name)
                agree = ft is not None and abs((ft - r["timestamp"]).total_seconds()) <= 60
                cls = "verified_at_meal_time" if agree else "uncertain_timing"
                offsets.append(0.0)
            else:
                prior = [m for m in meals if m["timestamp"] <= r["timestamp"]]
                if not prior:
                    cls = "uncertain_timing"
                else:
                    gap = (r["timestamp"] - prior[-1]["timestamp"]).total_seconds() / 60.0
                    offsets.append(gap)
                    cls = "post_prediction_followup" if gap > 0 else "uncertain_timing"
            classes[cls] += 1
            detail.append({"participant_id": pid, "image": r["image"], "class": cls,
                           "timestamp": r["timestamp"].isoformat(),
                           "meal_type": r["meal_type"]})

    # --- image quality, duplicates, corruption ---------------------------- #
    sizes = collections.Counter()
    modes = collections.Counter()
    hashes: dict[str, list[str]] = {}
    corrupt: list[str] = []
    bytes_total = 0
    for folder, names in disk.items():
        for n in names:
            p = ROOT / folder / "photos" / n
            bytes_total += p.stat().st_size
            try:
                with Image.open(p) as im:
                    im.verify()
                with Image.open(p) as im:
                    sizes[im.size] += 1
                    modes[im.mode] += 1
            except Exception as exc:                                  # noqa: BLE001
                corrupt.append(f"{folder}/{n}: {type(exc).__name__}")
                continue
            hashes.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), []).append(
                f"{folder}/{n}")
    duplicate_groups = {h: v for h, v in hashes.items() if len(v) > 1}

    # linkage integrity: does every referenced image exist on disk?
    missing_files = []
    for r in img_rows:
        p = ROOT / r["folder"] / r["image"]
        if not p.exists():
            missing_files.append(f"{r['folder']}/{r['image']}")
    referenced = {f"{r['folder']}/{pathlib.Path(r['image']).name}" for r in img_rows}
    on_disk = {f"{f}/{n}" for f, ns in disk.items() for n in ns}
    unreferenced = sorted(on_disk - referenced)

    # multiple images per meal?
    per_meal = collections.Counter(
        (r["participant_id"], r["timestamp"]) for r in meal_with_img)
    multi = {k: v for k, v in per_meal.items() if v > 1}

    report = {
        "IMAGE_COUNT": total_disk,
        "IMAGE_BYTES": bytes_total,
        "LOGGED_MEALS": len(meal_rows),
        "MEALS_WITH_IMAGES": len(meal_with_img),
        "MEALS_WITHOUT_IMAGES": len(meal_rows) - len(meal_with_img),
        "PARTICIPANTS_WITH_IMAGES": len([p for p, v in disk.items() if v]),
        "UNIQUE_IMAGE_MEAL_LINKS": len({(r["participant_id"], r["timestamp"],
                                         r["image"]) for r in meal_with_img}),
        "ORPHAN_IMAGES_NO_MEAL_ROW": len(orphan_img),
        "IMAGES_REFERENCED_BY_CSV": len(img_rows),
        "IMAGES_ON_DISK_NOT_REFERENCED": len(unreferenced),
        "REFERENCED_BUT_MISSING_ON_DISK": len(missing_files),
        "VERIFIED_PREDICTION_TIME_IMAGES": classes["verified_at_meal_time"],
        "UNCERTAIN_IMAGES": classes["uncertain_timing"],
        "POST_PREDICTION_FOLLOWUP_IMAGES": classes["post_prediction_followup"],
        "MULTI_IMAGE_MEALS": len(multi),
        "CORRUPT_FILES": len(corrupt),
        "EXACT_DUPLICATE_GROUPS": len(duplicate_groups),
        "EXACT_DUPLICATE_FILES": sum(len(v) for v in duplicate_groups.values()),
        "TOP_RESOLUTIONS": [[list(k), v] for k, v in sizes.most_common(6)],
        "COLOR_MODES": dict(modes),
        "FOLLOWUP_OFFSET_MINUTES": {
            "n": len([o for o in offsets if o > 0]),
            "median": sorted(o for o in offsets if o > 0)[len([o for o in offsets if o > 0]) // 2]
            if any(o > 0 for o in offsets) else None,
        },
        "MEAL_TYPE_RAW_VALUES": dict(collections.Counter(
            r["meal_type_raw"] for r in meal_rows)),
        "MEAL_TYPE_NORMALIZED": dict(collections.Counter(r["meal_type"] for r in meal_rows)),
        "FOOD_TEXT_FIELDS": [],
        "COMPOSITION_FIELDS": ["Calories", "Carbs", "Protein", "Fat", "Fiber",
                               "Amount Consumed"],
    }

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "image_inventory.json").write_text(json.dumps(report, indent=2) + "\n")
    (RESULTS / "image_availability.json").write_text(json.dumps(detail[:50], indent=2) + "\n")

    print("=== PHASE ZERO INVENTORY CHECKPOINT ===")
    for k in ("IMAGE_COUNT", "LOGGED_MEALS", "MEALS_WITH_IMAGES", "MEALS_WITHOUT_IMAGES",
              "PARTICIPANTS_WITH_IMAGES", "UNIQUE_IMAGE_MEAL_LINKS",
              "ORPHAN_IMAGES_NO_MEAL_ROW", "IMAGES_ON_DISK_NOT_REFERENCED",
              "REFERENCED_BUT_MISSING_ON_DISK", "VERIFIED_PREDICTION_TIME_IMAGES",
              "UNCERTAIN_IMAGES", "POST_PREDICTION_FOLLOWUP_IMAGES",
              "MULTI_IMAGE_MEALS", "CORRUPT_FILES", "EXACT_DUPLICATE_GROUPS",
              "EXACT_DUPLICATE_FILES"):
        print(f"  {k}: {report[k]}")
    print(f"  IMAGE_QUALITY: resolutions {report['TOP_RESOLUTIONS'][:3]}, "
          f"modes {report['COLOR_MODES']}, {bytes_total/1e9:.2f} GB")
    print(f"  FOOD_TEXT_FIELDS: NONE -- no food name, description, ingredient, brand,")
    print(f"                    restaurant, UPC, category or annotation column exists")
    print(f"  COMPOSITION_FIELDS: {report['COMPOSITION_FIELDS']}")
    print(f"  MEAL_TYPE raw values: {report['MEAL_TYPE_RAW_VALUES']}")
    print(f"  followup offsets: n={report['FOLLOWUP_OFFSET_MINUTES']['n']}, "
          f"median {report['FOLLOWUP_OFFSET_MINUTES']['median']} min after the meal")
    if corrupt:
        print(f"  corrupt: {corrupt[:5]}")
    print(f"\nwrote {RESULTS}/image_inventory.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
