"""Config-driven translation from source columns to canonical fields.

A mapping config is a TOML file. It is the *only* place an organizer-specific column
name may appear; nothing downstream of the adapter ever sees one. See
``data/mapping/official_template.toml`` for an annotated example.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from data.schema import DATA_SOURCES
from data.timeutil import is_valid_timezone

MODALITIES: tuple[str, ...] = (
    "participants", "cgm", "cgm_secondary", "heart_rate", "meals", "medications", "activity", "source_attributes",
)

CANONICAL_FIELDS: dict[str, tuple[str, ...]] = {
    "participants": ("participant_id", "display_name", "timezone", "source_dataset", "time_basis"),
    "cgm": ("participant_id", "timestamp", "glucose_mg_dl", "sensor"),
    "cgm_secondary": ("participant_id", "timestamp", "glucose_mg_dl", "sensor"),
    "heart_rate": ("participant_id", "timestamp", "heart_rate_bpm", "resting_heart_rate_bpm"),
    "meals": (
        "meal_id",
        "participant_id",
        "timestamp",
        "carbs_g",
        "protein_g",
        "fat_g",
        "fiber_g",
        "calories_kcal",
        "image_ref",
    ),
    "medications": ("id", "participant_id", "timestamp", "medication_class", "dose", "dose_unit"),
    "activity": ("participant_id", "timestamp", "activity_value", "activity_unit"),
    "source_attributes": ("participant_id", "entity_type", "entity_id", "attribute", "value_num", "value_text", "unit"),
}

# Explicit, declared conversions into canonical units. Doses are deliberately absent:
# the contract keeps the original dose magnitude and unit.
UNIT_FACTORS: dict[str, dict[str, float]] = {
    "glucose_mg_dl": {"mg/dL": 1.0, "mmol/L": 18.0182},  # 1 mmol/L glucose = 18.0182 mg/dL
    "heart_rate_bpm": {"bpm": 1.0},
    "resting_heart_rate_bpm": {"bpm": 1.0},
    "carbs_g": {"g": 1.0, "mg": 0.001},
    "protein_g": {"g": 1.0, "mg": 0.001},
    "fat_g": {"g": 1.0, "mg": 0.001},
    "fiber_g": {"g": 1.0, "mg": 0.001},
    "calories_kcal": {"kcal": 1.0, "kJ": 1.0 / 4.184},
}


class MappingError(ValueError):
    pass


@dataclass(frozen=True)
class ModalityMap:
    modality: str
    files: tuple[str, ...]
    columns: dict[str, str]  # canonical field -> source column
    constants: dict[str, Any] = field(default_factory=dict)  # canonical field -> fixed value
    units: dict[str, str] = field(default_factory=dict)  # canonical field -> declared source unit
    timestamp_format: str | None = None
    # Wide files: a row where ALL of these canonical fields are null is "not this
    # modality" and is skipped (counted), not rejected.
    skip_if_all_null: tuple[str, ...] = ()
    generate_meal_id: bool = False

    def unit_factors(self) -> dict[str, float]:
        return {name: UNIT_FACTORS[name][unit] for name, unit in self.units.items()}


@dataclass(frozen=True)
class MappingConfig:
    name: str
    data_source: str
    modalities: dict[str, ModalityMap]
    null_tokens: frozenset[str]
    timestamp_format: str | None = None
    assume_timezone: str | None = None
    participant_id_prefix: str = ""
    derive_participants: bool = False
    derived_timezone: str | None = None
    sha256: str = ""
    path: str | None = None


def _require_table(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise MappingError(f"{where} must be a table")
    return value


def parse_mapping(raw: dict, *, sha256: str = "", path: str | None = None) -> MappingConfig:
    name = raw.get("name")
    if not name:
        raise MappingError("mapping config needs a 'name'")
    data_source = raw.get("data_source", "official")
    if data_source not in DATA_SOURCES:
        raise MappingError(f"data_source must be one of {DATA_SOURCES}, got {data_source!r}")
    assume_tz = raw.get("assume_timezone") or None
    if assume_tz not in (None, "participant", "floating") and not is_valid_timezone(assume_tz):
        raise MappingError(f"assume_timezone {assume_tz!r} is not an IANA timezone, 'participant' or 'floating'")
    derived_tz = raw.get("derived_participant_timezone") or None
    if derived_tz is not None and not is_valid_timezone(derived_tz):
        raise MappingError(f"derived_participant_timezone {derived_tz!r} is not an IANA timezone")

    modalities: dict[str, ModalityMap] = {}
    for modality, spec in _require_table(raw.get("modalities", {}), "modalities").items():
        if modality not in MODALITIES:
            raise MappingError(f"unknown modality {modality!r}; expected one of {MODALITIES}")
        spec = _require_table(spec, f"modalities.{modality}")
        files = spec.get("files")
        if isinstance(files, str):
            files = [files]
        if not files:
            raise MappingError(f"modalities.{modality}.files is required")
        columns = dict(_require_table(spec.get("columns", {}), f"modalities.{modality}.columns"))
        constants = dict(_require_table(spec.get("constants", {}), f"modalities.{modality}.constants"))
        units = dict(_require_table(spec.get("units", {}), f"modalities.{modality}.units"))
        allowed = CANONICAL_FIELDS[modality]
        for fname in list(columns) + list(constants) + list(units):
            if fname not in allowed:
                raise MappingError(f"modalities.{modality}: {fname!r} is not a canonical field ({allowed})")
        for fname, unit in units.items():
            options = UNIT_FACTORS.get(fname)
            if options is None:
                raise MappingError(f"modalities.{modality}.units: {fname!r} has no convertible unit")
            if unit not in options:
                raise MappingError(f"modalities.{modality}.units.{fname}: unsupported unit {unit!r} ({list(options)})")
        generate_meal_id = bool(spec.get("generate_meal_id", False))
        required = [f for f in ("participant_id", "timestamp") if f in allowed]
        if modality in ("participants", "source_attributes"):
            required = ["participant_id"]
        missing = [f for f in required if f not in columns and f not in constants]
        if modality == "meals" and "meal_id" not in columns and not generate_meal_id:
            missing.append("meal_id (or generate_meal_id = true)")
        if missing:
            raise MappingError(f"modalities.{modality}: missing required mapping for {missing}")
        modalities[modality] = ModalityMap(
            modality=modality,
            files=tuple(files),
            columns=columns,
            constants=constants,
            units=units,
            timestamp_format=spec.get("timestamp_format") or None,
            skip_if_all_null=tuple(spec.get("skip_if_all_null", ())),
            generate_meal_id=generate_meal_id,
        )

    if "cgm" not in modalities:
        raise MappingError("a mapping must define the cgm modality")
    derive = bool(raw.get("derive_participants", False))
    if "participants" not in modalities and not derive:
        raise MappingError("no participants modality: set derive_participants = true to build them from event IDs")

    return MappingConfig(
        name=name,
        data_source=data_source,
        modalities=modalities,
        null_tokens=frozenset(raw.get("null_tokens", ["", "NA", "N/A", "n/a", "null", "NULL", "None"])),
        timestamp_format=raw.get("timestamp_format") or None,
        assume_timezone=assume_tz,
        participant_id_prefix=raw.get("participant_id_prefix", ""),
        derive_participants=derive,
        derived_timezone=derived_tz,
        sha256=sha256,
        path=path,
    )


def load_mapping(path: str | Path) -> MappingConfig:
    path = Path(path)
    content = path.read_bytes()
    try:
        raw = tomllib.loads(content.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise MappingError(f"{path}: invalid TOML: {exc}") from exc
    return parse_mapping(raw, sha256=hashlib.sha256(content).hexdigest(), path=str(path))
