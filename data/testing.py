"""Test helpers and shared pytest fixtures for data-layer tests.

Every fixture uses a temporary database under pytest's tmp dirs; nothing touches
data/glucotwin.db or the committed fixture files.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from data.adapters import import_fixture
from data.adapters.mapping import parse_mapping
from data.repository import DataRepository
from data.storage.db import init_db, make_engine

CANONICAL_HEADERS = {
    "participants": ["participant_id", "display_name", "timezone"],
    "cgm": ["participant_id", "timestamp", "glucose_mg_dl"],
    "heart_rate": ["participant_id", "timestamp", "heart_rate_bpm", "resting_heart_rate_bpm"],
    "meals": ["meal_id", "participant_id", "timestamp", "carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal",
              "image_ref"],
    "medications": ["id", "participant_id", "timestamp", "medication_class", "dose", "dose_unit"],
    "activity": ["participant_id", "timestamp", "activity_value", "activity_unit"],
}


def write_csv(path: Path, header: list[str], rows: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(["" if v is None else v for v in row])
    return path


def canonical_mapping(modalities: tuple[str, ...] = tuple(CANONICAL_HEADERS), **overrides):
    """Identity mapping over ``<modality>.csv`` files, for hand-built test sources."""
    raw = {
        "name": "test_canonical",
        "data_source": overrides.pop("data_source", "synthetic_dev"),
        "modalities": {
            m: {"files": [f"{m}.csv"], "columns": {c: c for c in CANONICAL_HEADERS[m]}} for m in modalities
        },
        **overrides,
    }
    return parse_mapping(raw)


def temp_engine(tmp_path: Path, name: str = "test.db"):
    engine = make_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    return engine


@pytest.fixture(scope="session")
def fixture_repo(tmp_path_factory) -> DataRepository:
    """The committed synthetic_dev fixture set loaded into a throwaway database."""
    tmp = tmp_path_factory.mktemp("fixture_db")
    engine = temp_engine(tmp)
    import_fixture(engine=engine, report_dir=tmp / "reports")
    return DataRepository(engine=engine)
