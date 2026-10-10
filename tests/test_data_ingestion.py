import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from data.adapters import IngestionError, import_official
from data.adapters.mapping import MappingError, load_mapping, parse_mapping
from data.adapters.pipeline import run_ingestion
from data.repository import DataRepository
from data.storage import tables
from data.storage.db import SchemaVersionMismatch, init_db, is_initialized, make_engine
from data.testing import CANONICAL_HEADERS, canonical_mapping, temp_engine, write_csv

P1 = ["p1", "Participant 001", "America/New_York"]
P2 = ["p2", "Participant 002", "UTC"]


def count(engine, table):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def make_source(root, **rows):
    for modality, data in rows.items():
        write_csv(root / f"{modality}.csv", CANONICAL_HEADERS[modality], data)
    return root


def ingest(src, engine, mapping=None, tmp=None, **kw):
    mapping = mapping or canonical_mapping(tuple(m for m in CANONICAL_HEADERS if (src / f"{m}.csv").exists()))
    return run_ingestion(src, mapping, engine, report_dir=(tmp or src) / "reports", **kw)


# ------------------------------------------------------------------- database


def test_init_creates_all_tables_and_never_drops_by_default(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'x.db'}")
    assert not is_initialized(engine)
    init_db(engine)
    for name in ("participants", "cgm", "heart_rate", "meals", "medications", "activity", "ingestion_runs", "quarantine"):
        assert name in tables.metadata.tables
    src = make_source(tmp_path / "src", participants=[P1], cgm=[["p1", "2026-10-01T00:00:00Z", 100]])
    ingest(src, engine)
    init_db(engine)  # second init: no drop
    assert count(engine, tables.cgm) == 1
    init_db(engine, reset=True)
    assert count(engine, tables.cgm) == 0


def test_schema_version_mismatch_is_detected(tmp_path):
    engine = temp_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(tables.schema_meta.update().values(value="0.9"))
    with pytest.raises(SchemaVersionMismatch):
        init_db(engine)


def test_database_enforces_foreign_keys_and_checks(tmp_path):
    from datetime import datetime, timezone

    engine = temp_engine(tmp_path)
    ts = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.cgm.insert().values(participant_id="ghost", timestamp=ts, glucose_mg_dl=100,
                                                    data_source="synthetic_dev"))
    with engine.begin() as conn:
        conn.execute(tables.participants.insert().values(participant_id="p1", display_name="P", data_source="synthetic_dev"))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.cgm.insert().values(participant_id="p1", timestamp=ts, glucose_mg_dl=-4,
                                                    data_source="synthetic_dev"))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.cgm.insert().values(participant_id="p1", timestamp=ts, glucose_mg_dl=100,
                                                    data_source="made_up"))


# -------------------------------------------------------------------- idempotency


def test_rerun_is_idempotent_and_changes_are_updates(tmp_path):
    engine = temp_engine(tmp_path)
    src = make_source(
        tmp_path / "src",
        participants=[P1],
        cgm=[["p1", "2026-10-01T00:00:00Z", 100], ["p1", "2026-10-01T00:05:00Z", 104]],
        meals=[["m1", "p1", "2026-10-01T00:02:00Z", 40, 10, 5, 3, 245, None]],
    )
    first = ingest(src, engine)
    assert first.report["totals"]["inserted"] == 4
    second = ingest(src, engine)
    assert second.report["totals"]["inserted"] == 0 and second.report["totals"]["unchanged"] == 4
    assert count(engine, tables.cgm) == 2 and count(engine, tables.meals) == 1
    assert count(engine, tables.ingestion_runs) == 2  # every run is logged

    write_csv(src / "cgm.csv", CANONICAL_HEADERS["cgm"],
              [["p1", "2026-10-01T00:00:00Z", 100], ["p1", "2026-10-01T00:05:00Z", 111]])
    third = ingest(src, engine)
    assert third.report["modalities"]["cgm"]["updated"] == 1
    assert count(engine, tables.cgm) == 2
    with engine.connect() as conn:
        run_ids = set(conn.execute(select(tables.cgm.c.ingestion_run_id)).scalars())
    assert run_ids == {first.run_id, third.run_id}  # provenance points at the last writer


def test_equivalent_timestamps_in_other_offsets_do_not_duplicate(tmp_path):
    engine = temp_engine(tmp_path)
    src = make_source(tmp_path / "src", participants=[P1], cgm=[["p1", "2026-10-01T00:00:00Z", 100]])
    ingest(src, engine)
    write_csv(src / "cgm.csv", CANONICAL_HEADERS["cgm"], [["p1", "2026-09-30T20:00:00-04:00", 100]])
    result = ingest(src, engine)
    assert result.report["totals"]["unchanged"] == 2
    assert count(engine, tables.cgm) == 1


def test_in_source_duplicates_collapse_and_conflicts_are_quarantined(tmp_path):
    engine = temp_engine(tmp_path)
    src = make_source(
        tmp_path / "src",
        participants=[P1],
        cgm=[
            ["p1", "2026-10-01T00:00:00Z", 100],
            ["p1", "2026-10-01T00:00:00Z", 100],  # exact repeat -> collapsed
            ["p1", "2026-10-01T00:05:00Z", 104],
            ["p1", "2026-10-01T00:05:00Z", 140],  # same key, different value -> both quarantined
        ],
    )
    r = ingest(src, engine)
    cgm = r.report["modalities"]["cgm"]
    assert cgm["duplicates_dropped"] == 1
    assert cgm["rejection_reasons"] == {"conflicting_duplicate_in_source": 2}
    assert count(engine, tables.cgm) == 1
    assert count(engine, tables.quarantine) == 2


# ------------------------------------------------------- rejection, quarantine, report


def test_invalid_rows_are_quarantined_with_reasons_and_report(tmp_path):
    engine = temp_engine(tmp_path)
    src = make_source(
        tmp_path / "src",
        participants=[P1],
        cgm=[
            ["p1", "2026-10-01T00:00:00Z", 100],
            ["p1", "2026-10-01T00:05:00Z", -3],
            ["p1", "2026-10-01T00:10:00", 100],  # naive
            ["ghost", "2026-10-01T00:15:00Z", 100],  # unknown participant
            ["", "2026-10-01T00:20:00Z", 100],
            ["p1", "2026-10-01T00:25:00Z", "nan"],
            ["p1", "2026-10-01T00:30:00Z", 450],  # kept, flagged
        ],
        meals=[["m1", "p1", "2026-10-01T00:02:00Z", -5, 10, 5, 3, 200, None]],
        medications=[["", "p1", "2026-10-01T08:00:00Z", "biguanide", 500, None]],
    )
    r = ingest(src, engine)
    cgm = r.report["modalities"]["cgm"]
    assert cgm["accepted"] == 2 and cgm["accepted_with_warnings"] == 1 and cgm["rejected"] == 5
    assert cgm["rejection_reasons"] == {
        "negative_glucose": 1, "naive_timestamp": 1, "unknown_participant": 1,
        "missing_participant_id": 1, "nonfinite_glucose_mg_dl": 1,
    }
    assert r.report["modalities"]["meals"]["rejection_reasons"] == {"negative_carbs_g": 1}
    assert r.report["modalities"]["medications"]["rejection_reasons"] == {"dose_without_unit": 1}
    assert r.status == "completed_with_rejections"
    assert count(engine, tables.quarantine) == 7

    saved = json.loads(r.report_path.read_text())  # machine-readable report on disk
    assert saved["run_id"] == r.run_id and saved["quarantine"]["count"] == 7
    with engine.connect() as conn:
        run = conn.execute(select(tables.ingestion_runs)).mappings().one()
        q = conn.execute(select(tables.quarantine).where(tables.quarantine.c.modality == "cgm")).mappings().all()
    assert run["rejected"] == 7 and run["records"] == 3 and len(run["sha256"]) == 64
    assert "negative_glucose=1" in run["error_summary"]
    assert {row["source_file"] for row in q} == {"cgm.csv"} and all(row["source_row"] for row in q)


def test_out_of_order_rows_are_counted_and_stored_sorted(tmp_path):
    engine = temp_engine(tmp_path)
    src = make_source(
        tmp_path / "src",
        participants=[P1],
        cgm=[["p1", "2026-10-01T00:10:00Z", 110], ["p1", "2026-10-01T00:00:00Z", 100], ["p1", "2026-10-01T00:05:00Z", 105]],
    )
    r = ingest(src, engine)
    assert r.report["modalities"]["cgm"]["out_of_order_rows_in_source"] == 2
    values = [p.glucose_mg_dl for p in DataRepository(engine=engine).get_cgm("p1")]
    assert values == [100, 105, 110]


def test_dry_run_writes_nothing(tmp_path):
    engine = temp_engine(tmp_path)
    src = make_source(tmp_path / "src", participants=[P1], cgm=[["p1", "2026-10-01T00:00:00Z", 100]])
    r = ingest(src, engine, dry_run=True)
    assert r.status == "dry_run" and r.report["totals"]["accepted"] == 2
    assert count(engine, tables.cgm) == 0 and count(engine, tables.ingestion_runs) == 0


# -------------------------------------------------------------- optional modalities


def test_missing_optional_modalities_are_tolerated(tmp_path):
    engine = temp_engine(tmp_path)
    src = make_source(tmp_path / "src", participants=[P1], cgm=[["p1", "2026-10-01T00:00:00Z", 100]])
    r = ingest(src, engine)
    assert r.report["totals"]["rejected"] == 0
    codes = {w["code"] for w in r.report["batch_warnings"]}
    assert "modality_not_mapped" in codes
    repo = DataRepository(engine=engine)
    assert repo.get_heart_rate("p1") == [] and repo.get_medications("p1") == [] and repo.get_activity("p1") == []
    assert repo.get_history("p1").heart_rate == []


def test_declared_optional_file_that_is_absent_is_reported_not_fatal(tmp_path):
    engine = temp_engine(tmp_path)
    src = make_source(tmp_path / "src", participants=[P1], cgm=[["p1", "2026-10-01T00:00:00Z", 100]])
    r = ingest(src, engine, mapping=canonical_mapping(("participants", "cgm", "heart_rate")))
    assert {"code": "modality_files_not_found", "modality": "heart_rate"} in r.report["batch_warnings"]


def test_missing_required_cgm_file_fails_cleanly(tmp_path):
    engine = temp_engine(tmp_path)
    src = make_source(tmp_path / "src", participants=[P1])
    with pytest.raises(IngestionError):
        ingest(src, engine, mapping=canonical_mapping(("participants", "cgm")))


# ------------------------------------------------------------ official-style mapping


def test_official_style_mapping_end_to_end(tmp_path):
    """Organizer-like wide file: other column names, mmol/L, naive local times, no roster."""
    engine = temp_engine(tmp_path)
    src = tmp_path / "official"
    write_csv(
        src / "export.csv",
        ["Subject", "Time", "Glucose_mmol", "HR", "Carbs", "Kcal"],
        [
            ["7", "2026-10-01 08:00:00", "5.5", "70", "", ""],
            ["7", "2026-10-01 08:05:00", "6.1", "", "45", "300"],
            ["7", "2026-10-01 08:10:00", "", "72", "", ""],
            ["8", "2026-11-01 01:30:00", "5.0", "", "", ""],  # DST-ambiguous local time
        ],
    )
    raw = {
        "name": "official_test",
        "data_source": "official",
        "assume_timezone": "participant",
        "derive_participants": True,
        "derived_participant_timezone": "America/New_York",
        "participant_id_prefix": "official_",
        "timestamp_format": "%Y-%m-%d %H:%M:%S",
        "modalities": {
            "cgm": {"files": ["export.csv"], "columns": {"participant_id": "Subject", "timestamp": "Time",
                                                         "glucose_mg_dl": "Glucose_mmol"},
                    "units": {"glucose_mg_dl": "mmol/L"}, "skip_if_all_null": ["glucose_mg_dl"]},
            "heart_rate": {"files": ["export.csv"], "columns": {"participant_id": "Subject", "timestamp": "Time",
                                                               "heart_rate_bpm": "HR"},
                           "skip_if_all_null": ["heart_rate_bpm"]},
            "meals": {"files": ["export.csv"], "columns": {"participant_id": "Subject", "timestamp": "Time",
                                                          "carbs_g": "Carbs", "calories_kcal": "Kcal"},
                      "generate_meal_id": True, "skip_if_all_null": ["carbs_g", "calories_kcal"]},
        },
    }
    r = import_official(src, parse_mapping(raw), engine=engine, report_dir=tmp_path / "reports")
    assert r.report["modalities"]["cgm"]["accepted"] == 2
    assert r.report["modalities"]["cgm"]["rejection_reasons"] == {"ambiguous_local_time": 1}
    assert r.report["modalities"]["heart_rate"]["skipped_empty"] == 2
    repo = DataRepository(engine=engine)
    # official_8's only row was rejected, so no participant is derived for it.
    assert [p.participant_id for p in repo.list_participants()] == ["official_7"]
    assert repo.get_participant("official_7").data_source == "official"
    cgm = repo.get_cgm("official_7")
    assert cgm[0].timestamp.isoformat() == "2026-10-01T12:00:00+00:00"  # 08:00 EDT -> 12:00Z
    assert cgm[0].glucose_mg_dl == pytest.approx(5.5 * 18.0182)
    meals = repo.get_meals("official_7")
    assert len(meals) == 1 and meals[0].meal_id == "official_7_meal_20261001T120500"
    assert meals[0].protein_g is None  # absent column stays null, never 0


def test_mapping_validation_errors():
    with pytest.raises(MappingError):
        parse_mapping({"name": "x", "modalities": {"cgm": {"files": ["a.csv"], "columns": {"participant_id": "a"}}}})
    with pytest.raises(MappingError):  # unsupported unit
        parse_mapping({"name": "x", "derive_participants": True, "modalities": {"cgm": {
            "files": ["a.csv"], "columns": {"participant_id": "a", "timestamp": "b", "glucose_mg_dl": "c"},
            "units": {"glucose_mg_dl": "g/L"}}}})
    with pytest.raises(MappingError):  # not a canonical field
        parse_mapping({"name": "x", "derive_participants": True, "modalities": {"cgm": {
            "files": ["a.csv"], "columns": {"participant_id": "a", "timestamp": "b", "glucose_mg_dl": "c", "sugar": "d"}}}})
    with pytest.raises(MappingError):  # no roster and no derivation
        parse_mapping({"name": "x", "modalities": {"cgm": {
            "files": ["a.csv"], "columns": {"participant_id": "a", "timestamp": "b", "glucose_mg_dl": "c"}}}})


def test_committed_mapping_files_parse():
    from data.storage.db import REPO_ROOT

    assert load_mapping(REPO_ROOT / "data/mapping/synthetic_dev.toml").data_source == "synthetic_dev"
    assert load_mapping(REPO_ROOT / "data/mapping/official_template.toml").data_source == "official"


def test_schema_profile_without_and_with_mapping(tmp_path):
    from data.adapters.profile import profile, to_markdown
    from data.storage.db import REPO_ROOT

    src = tmp_path / "drop"
    write_csv(src / "glucose.csv", ["Subject", "Time", "Glucose", "post_meal_peak"],
              [["7", "2026-10-01 08:00:00", "5.5", "9"], ["7", "2026-10-01 08:05:00", "nan", "9"]])
    (src / "img").mkdir()
    (src / "img" / "meal1.jpg").write_bytes(b"\xff\xd8")
    p = profile(src)
    f = p["inventory"]["files"][0]
    assert f["rows"] == 2 and f["columns"]["Glucose"]["nan_tokens"] == 1
    assert f["columns"]["Time"]["inferred_type"] == "timestamp" and f["columns"]["Time"]["without_utc_offset"] == 2
    assert "glucose.csv:post_meal_peak" in p["leakage_review"]
    assert p["inventory"]["image_files"]["count"] == 1
    assert "Dataset schema profile" in to_markdown(p)

    mapped = profile(REPO_ROOT / "data/fixtures/synthetic_dev", REPO_ROOT / "data/mapping/synthetic_dev.toml")
    assert mapped["mapped"]["participant_count"] == 6 and mapped["mapped"]["totals"]["rejected"] == 0
