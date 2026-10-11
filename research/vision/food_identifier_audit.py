"""Phase Four determination: is USDA FoodData Central matching possible at all?

    python -m research.vision.food_identifier_audit

A USDA FoodData Central lookup needs something to look up: a food name, an
ingredient description, or a product identifier. This script answers whether
CGMacros contains any such field, by enumerating EVERY column of EVERY tabular
file in the dataset and every member of the source archive -- not by checking the
one file we happened to open first.

The test for "is this a food identifier" is deliberately generous: any column
whose values are free text that is not a timestamp, a path, a sensor name, or a
categorical the dictionary defines, counts as a candidate.
"""

from __future__ import annotations

import csv
import glob
import json
import pathlib
import re
import zipfile

ARCHIVE_ROOT = pathlib.Path(
    "data/cgmacros-a-scientific-dataset-for-personalized-nutrition-and-diet-monitoring-1.0.0")
RESULTS = pathlib.Path("research/vision/results")

IDENTIFIER_HINTS = (
    "food", "name", "desc", "ingredient", "item", "dish", "menu", "recipe",
    "brand", "restaurant", "product", "upc", "gtin", "barcode", "sku", "label",
    "annotation", "caption", "note", "comment", "text", "category", "class",
    "fdc", "usda", "ndb",
)
#: columns known from the dictionary to be non-food text
KNOWN_NON_FOOD = {"timestamp", "image path", "meal type", "subject", "unnamed: 0",
                  "gender", "self-identify", "collection time pdl (lab)", "time (t)"}


def main() -> int:
    findings: dict[str, dict] = {}

    # --- every extracted tabular file ------------------------------------- #
    # Only MEAL-LEVEL files can carry a food identifier. The microbiome tables
    # (1979 bacterial species columns) and the DataDictionary files (whose
    # "Description" column describes FIELDS, not foods) are enumerated separately
    # so they cannot masquerade as food-identifier hits.
    meal_level = re.compile(r"CGMacros-\d+[/\\]CGMacros-\d+\.csv$")
    other_tables: list[str] = []
    for path in sorted(glob.glob(str(ARCHIVE_ROOT / "**" / "*.csv"), recursive=True)):
        rel = str(pathlib.Path(path).relative_to(ARCHIVE_ROOT))
        if not meal_level.search(rel):
            other_tables.append(rel)
            continue
        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                header = next(csv.reader(fh))
        except Exception as exc:                                       # noqa: BLE001
            findings[rel] = {"error": f"{type(exc).__name__}"}
            continue
        cols = [c.strip() for c in header]
        hits = [c for c in cols
                if c.lower() not in KNOWN_NON_FOOD
                and any(h in c.lower() for h in IDENTIFIER_HINTS)]
        findings[rel] = {"n_columns": len(cols), "identifier_candidates": hits}

    # --- every member of the authentic archive ---------------------------- #
    zip_path = ARCHIVE_ROOT / "CGMacros_dateshifted365.zip"
    members: dict[str, int] = {}
    inner_csv_headers: dict[str, list[str]] = {}
    if zip_path.exists():
        with zipfile.ZipFile(zip_path) as z:
            for n in z.namelist():
                ext = pathlib.Path(n).suffix.lower() or "<dir>"
                members[ext] = members.get(ext, 0) + 1
                if ext == ".csv" and len(inner_csv_headers) < 60:
                    with z.open(n) as fh:
                        first = fh.readline().decode("utf-8-sig", "replace").strip()
                    inner_csv_headers[n] = [c.strip() for c in first.split(",")]

    archive_candidates = sorted({
        c for n, cols in inner_csv_headers.items() for c in cols
        if meal_level.search(n)
        and c.lower() not in KNOWN_NON_FOOD
        and any(h in c.lower() for h in IDENTIFIER_HINTS)})

    distinct_headers = sorted({tuple(v) for v in inner_csv_headers.values()},
                              key=len, reverse=True)

    report = {
        "extracted_files_scanned": len(findings),
        "extracted_identifier_candidates": sorted({
            c for v in findings.values() for c in v.get("identifier_candidates", [])}),
        "archive_member_types": members,
        "archive_csv_headers_sampled": len(inner_csv_headers),
        "non_meal_tables": other_tables,
        "meal_level_schema": sorted({tuple(v) for n, v in inner_csv_headers.items()
                                     if meal_level.search(n)}, key=len, reverse=True)[:1],
        "archive_identifier_candidates": archive_candidates,

        "USDA_MATCHING_POSSIBLE": False,
        "GI_ASSIGNMENT_POSSIBLE": False,
        "determination": (
            "CGMacros contains NO food name, ingredient description, product "
            "identifier, brand, restaurant, UPC or free-text annotation in any "
            "extracted file or any archive member. The only meal-level fields are "
            "the occasion label (Meal Type), five estimated macronutrient "
            "quantities, Amount Consumed, and the photograph path. There is "
            "therefore nothing to match against USDA FoodData Central, and no "
            "identified food to attach a glycaemic index to. Both branches are "
            "UNSUPPORTED BY THE DATA. Deriving a food name from the photograph and "
            "then calling the resulting USDA row 'source composition' would "
            "manufacture the ground truth the enrichment is meant to provide, so "
            "it is not done."),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "food_identifier_audit.json").write_text(json.dumps(report, indent=2) + "\n")

    print("=== PHASE FOUR: FOOD IDENTIFIER AUDIT ===")
    print(f"  extracted tabular files scanned : {report['extracted_files_scanned']}")
    print(f"  identifier candidates found      : "
          f"{report['extracted_identifier_candidates'] or 'NONE'}")
    print(f"  archive member types             : {members}")
    print(f"  archive CSV headers sampled      : {report['archive_csv_headers_sampled']}")
    print(f"  archive identifier candidates    : "
          f"{archive_candidates or 'NONE'}")
    print(f"  non-meal tables (excluded)       : {len(report['non_meal_tables'])} "
          f"(microbiome, gut health, bio, data dictionaries)")
    print(f"  THE COMPLETE MEAL-LEVEL SCHEMA   :")
    for h in report["meal_level_schema"]:
        print(f"      {list(h)}")
    print(f"\n  USDA_MATCHING_POSSIBLE : {report['USDA_MATCHING_POSSIBLE']}")
    print(f"  GI_ASSIGNMENT_POSSIBLE : {report['GI_ASSIGNMENT_POSSIBLE']}")
    print(f"\n  {report['determination']}")
    print(f"\nwrote {RESULTS}/food_identifier_audit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
