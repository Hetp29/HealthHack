"""Row-level validation rules for canonical records.

Every rule yields a machine-readable code. A row ends up in exactly one bucket:

* **rejected** — one or more ``reasons``; the row goes to quarantine, never to storage.
* **accepted with warnings** — stored, with ``warnings`` counted in the run report.
* **accepted** — stored, no findings.

Philosophy: reject what is *impossible or unusable* (non-finite numbers, negative
glucose, missing keys, unparseable or ambiguous timestamps, a dose with no unit);
*flag* what is merely unusual but physiologically possible (e.g. glucose of 35 or 450
mg/dL). Plausible extremes are never silently deleted. All thresholds live in
``Thresholds`` below and are documented in ``docs/data_provenance.md``.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Callable

from data.timeutil import TimestampError, is_valid_timezone, parse_timestamp, to_storage


@dataclass(frozen=True)
class Thresholds:
    # Glucose (mg/dL). Reject <= 0 (impossible) and > hard max (sensor/sentinel/unit
    # error). Commercial CGMs report roughly 40-400 (some 500); values outside that are
    # kept but flagged, because lab-measured extremes beyond it do occur.
    glucose_hard_min: float = 10.0
    glucose_hard_max: float = 1500.0
    glucose_typical_min: float = 40.0
    glucose_typical_max: float = 400.0
    # Heart rate (bpm). Outside 20-300 is not a plausible human reading -> reject.
    # 35-220 is the "unremarkable for a wearable" band; outside it is flagged.
    hr_hard_min: float = 20.0
    hr_hard_max: float = 300.0
    hr_typical_min: float = 35.0
    hr_typical_max: float = 220.0
    resting_hr_typical_max: float = 120.0
    # Meals. Per-field ceiling matches the predict-request MealInput maximum (1000 g).
    macro_hard_max_g: float = 1000.0
    calories_hard_max_kcal: float = 10000.0
    carbs_flag_g: float = 250.0
    calories_flag_kcal: float = 3000.0
    # Atwater cross-check: |kcal - (4C + 4P + 9F)| beyond both limits -> warning.
    energy_rel_tolerance: float = 0.35
    energy_abs_tolerance_kcal: float = 75.0


DEFAULT_THRESHOLDS = Thresholds()


@dataclass
class RowResult:
    record: dict[str, Any] | None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return bool(self.reasons)


@dataclass
class ValidationContext:
    """Per-run knobs, normally built from a mapping config."""

    data_source: str
    null_tokens: frozenset[str] = frozenset({"", "NA", "N/A", "n/a", "null", "NULL", "None"})
    timestamp_format: str | None = None
    # None -> reject naive timestamps; "participant" -> participant's timezone; else IANA.
    assume_timezone: str | None = None
    participant_timezones: dict[str, str | None] = field(default_factory=dict)
    # Used by the "participant" policy for participants with no known timezone.
    default_participant_timezone: str | None = None
    # canonical field -> multiplicative factor into canonical units (declared in mapping).
    unit_factors: dict[str, float] = field(default_factory=dict)
    participant_id_prefix: str = ""
    thresholds: Thresholds = DEFAULT_THRESHOLDS


# ------------------------------------------------------------------ coercion helpers


def _is_null(value: Any, ctx: ValidationContext) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() in ctx.null_tokens:
        return True
    return False


def _text(value: Any, ctx: ValidationContext) -> str | None:
    if _is_null(value, ctx):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # e.g. a numeric ID that a spreadsheet turned into 7.0
    text = str(value).strip()
    return text or None


def _number(value: Any, name: str, ctx: ValidationContext, reasons: list[str]) -> float | None:
    if _is_null(value, ctx):
        return None
    if isinstance(value, bool):
        reasons.append(f"non_numeric_{name}")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        reasons.append(f"non_numeric_{name}")
        return None
    if not math.isfinite(number):
        reasons.append(f"nonfinite_{name}")
        return None
    return number * ctx.unit_factors.get(name, 1.0)


def _participant_id(row: dict, ctx: ValidationContext, reasons: list[str]) -> str | None:
    pid = _text(row.get("participant_id"), ctx)
    if pid is None:
        reasons.append("missing_participant_id")
        return None
    return ctx.participant_id_prefix + pid


def _timestamp(row: dict, pid: str | None, ctx: ValidationContext, reasons: list[str]):
    assume = ctx.assume_timezone
    if assume == "participant":
        assume = (ctx.participant_timezones.get(pid) if pid else None) or ctx.default_participant_timezone
    raw = row.get("timestamp")
    if _is_null(raw, ctx):
        reasons.append("missing_timestamp")
        return None
    try:
        return parse_timestamp(raw, fmt=ctx.timestamp_format, assume_tz=assume)
    except TimestampError as exc:
        reasons.append(exc.code)
        return None


def stable_id(prefix: str, *parts: Any) -> str:
    """Deterministic identifier from a natural key (used when a source has no ID)."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]
    return f"{prefix}_{digest}"


# ------------------------------------------------------------------- modality rules


def validate_participant(row: dict, ctx: ValidationContext) -> RowResult:
    reasons: list[str] = []
    warnings: list[str] = []
    pid = _participant_id(row, ctx, reasons)
    display_name = _text(row.get("display_name"), ctx)
    if display_name is None:
        reasons.append("missing_display_name")
    tz = _text(row.get("timezone"), ctx)
    if tz is not None and not is_valid_timezone(tz):
        warnings.append("invalid_timezone_set_null")
        tz = None
    if reasons:
        return RowResult(None, reasons, warnings)
    return RowResult(
        {
            "participant_id": pid,
            "display_name": display_name,
            "timezone": tz,
            "source_dataset": _text(row.get("source_dataset"), ctx),
            "data_source": ctx.data_source,
        },
        reasons,
        warnings,
    )


def validate_cgm(row: dict, ctx: ValidationContext) -> RowResult:
    t = ctx.thresholds
    reasons: list[str] = []
    warnings: list[str] = []
    pid = _participant_id(row, ctx, reasons)
    ts = _timestamp(row, pid, ctx, reasons)
    glucose = _number(row.get("glucose_mg_dl"), "glucose_mg_dl", ctx, reasons)
    if glucose is None and not any(r.endswith("glucose_mg_dl") for r in reasons):
        reasons.append("missing_glucose_mg_dl")
    elif glucose is not None:
        if glucose < 0:
            reasons.append("negative_glucose")
        elif glucose == 0:
            reasons.append("zero_glucose")
        elif glucose < t.glucose_hard_min:
            reasons.append("implausible_glucose_low")
        elif glucose > t.glucose_hard_max:
            reasons.append("implausible_glucose_high")
        elif glucose < t.glucose_typical_min:
            warnings.append("glucose_below_typical_cgm_range")
        elif glucose > t.glucose_typical_max:
            warnings.append("glucose_above_typical_cgm_range")
    if reasons:
        return RowResult(None, reasons, warnings)
    return RowResult(
        {
            "participant_id": pid,
            "timestamp": ts,
            "glucose_mg_dl": glucose,
            "sensor": _text(row.get("sensor"), ctx),
            "data_source": ctx.data_source,
        },
        reasons,
        warnings,
    )


def validate_cgm_secondary(row: dict, ctx: ValidationContext) -> RowResult:
    result = validate_cgm(row, ctx)
    if not result.rejected and result.record["sensor"] is None:
        return RowResult(None, ["missing_sensor"], result.warnings)
    return result


def validate_heart_rate(row: dict, ctx: ValidationContext) -> RowResult:
    t = ctx.thresholds
    reasons: list[str] = []
    warnings: list[str] = []
    pid = _participant_id(row, ctx, reasons)
    ts = _timestamp(row, pid, ctx, reasons)
    hr = _number(row.get("heart_rate_bpm"), "heart_rate_bpm", ctx, reasons)
    resting = _number(row.get("resting_heart_rate_bpm"), "resting_heart_rate_bpm", ctx, reasons)
    if hr is None and not any(r.endswith("heart_rate_bpm") and "resting" not in r for r in reasons):
        reasons.append("missing_heart_rate_bpm")
    elif hr is not None:
        if hr < t.hr_hard_min or hr > t.hr_hard_max:
            reasons.append("implausible_heart_rate")
        elif hr < t.hr_typical_min or hr > t.hr_typical_max:
            warnings.append("heart_rate_outside_typical_range")
    if resting is not None:
        if resting < t.hr_hard_min or resting > t.hr_hard_max:
            reasons.append("implausible_resting_heart_rate")
        elif resting > t.resting_hr_typical_max:
            warnings.append("resting_heart_rate_high")
    if reasons:
        return RowResult(None, reasons, warnings)
    return RowResult(
        {
            "participant_id": pid,
            "timestamp": ts,
            "heart_rate_bpm": hr,
            "resting_heart_rate_bpm": resting,
            "data_source": ctx.data_source,
        },
        reasons,
        warnings,
    )


def _safe_image_ref(ref: str | None, warnings: list[str]) -> str | None:
    """image_ref is an opaque handle. Absolute paths or '..' segments are not stored."""
    if ref is None:
        return None
    normalized = ref.replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/") or ":" in normalized.split("/")[0]:
        if "://" not in normalized:  # allow URI-style handles such as s3://bucket/key
            warnings.append("unsafe_image_ref_dropped")
            return None
    return ref


def validate_meal(row: dict, ctx: ValidationContext) -> RowResult:
    t = ctx.thresholds
    reasons: list[str] = []
    warnings: list[str] = []
    pid = _participant_id(row, ctx, reasons)
    ts = _timestamp(row, pid, ctx, reasons)
    meal_id = _text(row.get("meal_id"), ctx)
    if meal_id is None:
        reasons.append("missing_meal_id")

    values: dict[str, float | None] = {}
    for name in ("carbs_g", "protein_g", "fat_g", "fiber_g"):
        v = _number(row.get(name), name, ctx, reasons)
        if v is not None:
            if v < 0:
                reasons.append(f"negative_{name}")
            elif v > t.macro_hard_max_g:
                reasons.append(f"implausible_{name}")
        values[name] = v
    kcal = _number(row.get("calories_kcal"), "calories_kcal", ctx, reasons)
    if kcal is not None:
        if kcal < 0:
            reasons.append("negative_calories_kcal")
        elif kcal > t.calories_hard_max_kcal:
            reasons.append("implausible_calories_kcal")
    values["calories_kcal"] = kcal

    if not reasons:
        if all(v is None for v in values.values()):
            warnings.append("meal_has_no_nutrition")
        elif any(v is None for v in values.values()):
            warnings.append("meal_nutrition_incomplete")
        c, p, f = values["carbs_g"], values["protein_g"], values["fat_g"]
        if c is not None and c > t.carbs_flag_g:
            warnings.append("carbs_unusually_high")
        if kcal is not None and kcal > t.calories_flag_kcal:
            warnings.append("calories_unusually_high")
        fiber = values["fiber_g"]
        if fiber is not None and c is not None and fiber > c:
            warnings.append("fiber_exceeds_carbs")
        if None not in (c, p, f, kcal):
            expected = 4 * c + 4 * p + 9 * f
            diff = abs(kcal - expected)
            if diff > t.energy_abs_tolerance_kcal and diff > t.energy_rel_tolerance * max(expected, 1.0):
                warnings.append("calories_inconsistent_with_macros")

    image_ref = _safe_image_ref(_text(row.get("image_ref"), ctx), warnings)
    if reasons:
        return RowResult(None, reasons, warnings)
    return RowResult(
        {
            "meal_id": meal_id,
            "participant_id": pid,
            "timestamp": ts,
            **values,
            "image_ref": image_ref,
            "data_source": ctx.data_source,
        },
        reasons,
        warnings,
    )


def validate_medication(row: dict, ctx: ValidationContext) -> RowResult:
    reasons: list[str] = []
    warnings: list[str] = []
    pid = _participant_id(row, ctx, reasons)
    ts = _timestamp(row, pid, ctx, reasons)
    med_class = _text(row.get("medication_class"), ctx)
    if med_class is None:
        reasons.append("missing_medication_class")
    # Doses are NEVER unit-converted: original magnitude + explicit unit.
    dose = _number(row.get("dose"), "dose", ctx, reasons)
    unit = _text(row.get("dose_unit"), ctx)
    if dose is not None:
        if dose < 0:
            reasons.append("negative_dose")
        elif unit is None:
            reasons.append("dose_without_unit")
        elif dose == 0:
            warnings.append("zero_dose")
    elif unit is not None and not any(r.endswith("_dose") for r in reasons):
        warnings.append("dose_unit_without_dose")
    if reasons:
        return RowResult(None, reasons, warnings)
    med_id = _text(row.get("id"), ctx) or stable_id("med", pid, to_storage(ts), med_class)
    return RowResult(
        {
            "id": med_id,
            "participant_id": pid,
            "timestamp": ts,
            "medication_class": med_class,
            "dose": dose,
            "dose_unit": unit,
            "data_source": ctx.data_source,
        },
        reasons,
        warnings,
    )


def validate_activity(row: dict, ctx: ValidationContext) -> RowResult:
    reasons: list[str] = []
    warnings: list[str] = []
    pid = _participant_id(row, ctx, reasons)
    ts = _timestamp(row, pid, ctx, reasons)
    value = _number(row.get("activity_value"), "activity_value", ctx, reasons)
    unit = _text(row.get("activity_unit"), ctx)
    if value is None and unit is None and not reasons:
        reasons.append("empty_activity_record")
    if value is not None and value < 0:
        reasons.append("negative_activity_value")
    if value is not None and unit is None:
        reasons.append("activity_value_without_unit")
    if value is None and unit is not None and not reasons:
        warnings.append("activity_unit_without_value")
    if reasons:
        return RowResult(None, reasons, warnings)
    return RowResult(
        {
            "id": stable_id("act", pid, to_storage(ts), unit),
            "participant_id": pid,
            "timestamp": ts,
            "activity_value": value,
            "activity_unit": unit,
            "data_source": ctx.data_source,
        },
        reasons,
        warnings,
    )


VALIDATORS: dict[str, Callable[[dict, ValidationContext], RowResult]] = {
    "participants": validate_participant,
    "cgm": validate_cgm,
    "cgm_secondary": validate_cgm_secondary,
    "heart_rate": validate_heart_rate,
    "meals": validate_meal,
    "medications": validate_medication,
    "activity": validate_activity,
}

# Natural key per modality: duplicates are detected on these, and the DB upserts on them.
NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "participants": ("participant_id",),
    "cgm": ("participant_id", "timestamp"),
    "cgm_secondary": ("participant_id", "timestamp", "sensor"),
    "heart_rate": ("participant_id", "timestamp"),
    "meals": ("meal_id",),
    "medications": ("participant_id", "timestamp", "medication_class"),
    "activity": ("participant_id", "timestamp", "activity_unit"),
}

# Storage primary key per modality (what an UPDATE targets).
PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "participants": ("participant_id",),
    "cgm": ("participant_id", "timestamp"),
    "cgm_secondary": ("participant_id", "timestamp", "sensor"),
    "heart_rate": ("participant_id", "timestamp"),
    "meals": ("meal_id",),
    "medications": ("id",),
    "activity": ("id",),
}
