"""CGMacros + MetaboNet adapters, on small hand-built inputs shaped like the real files.

The real datasets are not in git, so these tests build miniature sources that reproduce
the properties that matter: interpolated 1-min CGM, header variants, end-of-meal photos,
missing modalities, a numeric id shared by two MetaboNet studies, zero-filled carbs,
negative bolus, multiple Parquet row groups.
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.adapters import cgmacros, metabonet
from data.adapters.readers import sha256_file
from data.audit import audit_cgmacros, write_reports
from data.eligibility import meal_episodes
from data.storage.repository import SqliteDataRepository
from data.testing import temp_engine

UTC = dt.timezone.utc


# ------------------------------------------------------------------ CGMacros fixture


def _interpolated(minutes: np.ndarray, cadence: int, phase: int, rng) -> np.ndarray:
    """Integer readings every `cadence` min at `phase`, linearly interpolated per minute."""
    knots = minutes[(minutes % cadence) == phase]
    readings = 100 + np.round(20 * np.sin(knots / 90.0) + rng.integers(-3, 4, len(knots)))
    return np.interp(minutes, knots, readings, left=np.nan, right=np.nan)


def make_cgmacros(root, *, days=9):
    rng = np.random.default_rng(7)
    base = root / "CGMacros"
    start = pd.Timestamp("2020-05-01 00:00:00")
    n = days * 1440
    ts = start + pd.to_timedelta(np.arange(n), unit="min")
    minutes = (ts.astype("int64") // 60_000_000_000).to_numpy()
    for number, variant in (("001", "mets"), ("002", "intensity_no_hr")):
        folder = base / f"CGMacros-{number}"
        (folder / "photos").mkdir(parents=True)
        frame = pd.DataFrame({
            "Timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "Libre GL": _interpolated(minutes, 15, 3, rng),
            "Dexcom GL": _interpolated(minutes, 5, 2, rng),
            "HR": np.where(variant == "mets", rng.integers(55, 90, n), np.nan),
        })
        if variant == "mets":
            frame["METs"] = 10.0
        else:
            frame["Intensity"] = 0.0
        for col in ("Meal Type", "Calories", "Carbs", "Protein", "Fat", "Fiber", "Amount Consumed ", "Image path"):
            frame[col] = np.nan
        frame = frame.astype({"Meal Type": object, "Image path": object})
        for day in range(days):
            for hour, kind in ((8, "Breakfast"), (13, "lunch"), (19, "Dinner")):
                i = day * 1440 + hour * 60 + 7
                photo = f"photos/{number}_{day}_{hour}.jpg"
                (folder / photo).write_bytes(b"\xff\xd8")
                frame.loc[i, ["Meal Type", "Calories", "Carbs", "Protein", "Fat", "Fiber", "Amount Consumed ", "Image path"]] = \
                    [kind, 450.0, 55.0, 20.0, 15.0, 5.0, 100.0, photo]
                frame.loc[i + 25, "Image path"] = f"photos/{number}_{day}_{hour}_end.jpg"  # end-of-meal photo
        if number == "002":
            frame.insert(0, "Unnamed: 0", range(len(frame)))
        frame.to_csv(folder / f"CGMacros-{number}.csv", index=False)
    (base / "CGMacros-003").mkdir()  # folder without a participant file
    return base


@pytest.fixture(scope="module")
def cgm_source(tmp_path_factory):
    return make_cgmacros(tmp_path_factory.mktemp("cgmacros"))


@pytest.fixture(scope="module")
def cgm_repo(cgm_source, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cgm_db")
    engine = temp_engine(tmp)
    results = cgmacros.import_cgmacros(cgm_source, engine=engine, report_dir=tmp / "reports")
    repo = SqliteDataRepository(engine=engine)
    repo._results = results
    repo._tmp = tmp
    return repo


def test_cgmacros_discovery_skips_folders_without_files(cgm_source):
    files, skipped = cgmacros.discover(cgm_source)
    assert [f.parent.name for f in files] == ["CGMacros-001", "CGMacros-002"]
    assert skipped == [{"folder": "CGMacros-003", "reason": "no participant csv"}]


def test_cgmacros_interpolated_minutes_are_never_stored(cgm_repo):
    cgm = cgm_repo.get_history("cgmacros_001").cgm
    assert cgm and all(float(p.glucose_mg_dl).is_integer() for p in cgm)
    steps = {(b.timestamp - a.timestamp).total_seconds() / 60 for a, b in zip(cgm, cgm[1:])}
    assert steps == {5.0}
    assert all(p.timestamp.minute % 5 == 2 for p in cgm)  # the true sampling phase
    libre = cgm_repo.get_cgm_secondary("cgmacros_001")
    assert libre and {s for _, s, _ in libre} == {"libre_pro"}
    assert all(t.minute % 15 == 3 for t, _, _ in libre)


def test_cgmacros_sensor_identity_and_provenance(cgm_repo, cgm_source):
    meta = cgm_repo.get_participant_metadata("cgmacros_001")
    assert meta["cgm_sensors"] == ["dexcom_g6_pro"] and meta["source_dataset"] == "cgmacros"
    assert meta["data_source"] == "official" and meta["timezone"] is None
    report = cgm_repo._results[0].report
    assert report["input_sha256"] == sha256_file(cgm_source / "CGMacros-001" / "CGMacros-001.csv")
    codes = {w["code"] for w in report["batch_warnings"]}
    assert {"cgm_deinterpolated", "time_basis_source_local_unknown_tz", "medications_absent_in_source",
            "end_of_meal_photos_not_counted_as_meals"} <= codes


def test_cgmacros_meals_photos_and_ids(cgm_repo):
    meals = cgm_repo.get_meals("cgmacros_001")
    assert len(meals) == 27  # 3/day x 9 days; end-of-meal photos are not meals
    assert meals[0].meal_id == "cgmacros_001_meal_20200501T0807"
    assert meals[0].image_ref == "cgmacros://CGMacros-001/photos/001_0_8.jpg"
    assert meals[0].has_complete_macros
    assert meals[0].timestamp == dt.datetime(2020, 5, 1, 8, 7, tzinfo=UTC)  # wall clock kept


def test_cgmacros_missing_modalities_and_header_variants(cgm_repo):
    h = cgm_repo.get_history("cgmacros_002")
    assert h.heart_rate == [] and h.medications == []
    assert {a.activity_unit for a in h.activity} == {"fitbit_intensity_level"}
    mets = {a.activity_value for a in cgm_repo.get_history("cgmacros_001").activity if a.activity_unit == "MET"}
    assert mets == {1.0}  # source MET x10 -> MET


def test_cgmacros_reimport_is_idempotent(cgm_repo, cgm_source):
    again = cgmacros.import_cgmacros(cgm_source, engine=cgm_repo.engine, report_dir=cgm_repo._tmp / "r2")
    assert all(r.report["totals"]["inserted"] == 0 and r.report["totals"]["updated"] == 0 for r in again)


def test_cgmacros_as_of_access_has_no_future(cgm_repo):
    meal = cgm_repo.get_meals("cgmacros_001")[-1]
    h = cgm_repo.get_history_as_of("cgmacros_001", meal.timestamp, lookback=dt.timedelta(days=7))
    obs = [*h.cgm, *h.heart_rate, *h.activity, *h.meals]
    assert max(o.timestamp for o in obs) <= meal.timestamp
    assert min(o.timestamp for o in obs) >= meal.timestamp - dt.timedelta(days=7)


# ------------------------------------------------------------------ MetaboNet fixture


def make_metabonet(path):
    frames = []

    def subject(source, sid, days, carbs=True, device="Dexcom G6"):
        dates = pd.date_range("2027-04-01 00:00", periods=days * 288, freq="5min")
        n = len(dates)
        f = pd.DataFrame({"source_file": source, "id": sid, "date": dates,
                          "CGM": 120.0 + 10 * np.sin(np.arange(n) / 30), "cgm_device": device})
        f["carbs"] = 0.0
        f["bolus"] = np.nan
        f["basal"] = 0.05
        if carbs:
            for d in range(days):
                f.loc[d * 288 + 96, "carbs"] = 45.0  # 08:00
                f.loc[d * 288 + 96, "bolus"] = 4.0
        f.loc[5, "CGM"] = 3.0  # implausible, must be rejected
        f.loc[7, "bolus"] = -1.0  # negative dose, must be quarantined
        f["heartrate"] = 70.0 if source == "OhioT1DM" else np.nan
        f["steps"] = np.nan
        f["calories_burned"] = np.nan
        f["is_test"] = False
        f["subject_split_across_traintest"] = False
        return f

    frames.append(subject("OhioT1DM", "540", 9))
    frames.append(subject("Loop", "540", 9))  # same numeric id, different person
    frames.append(subject("Flair", "9", 9, carbs=False))  # no carb data at all
    frames.append(subject("Loop", "77", 5))  # too short for 7-day history
    table = pa.Table.from_pandas(pd.concat(frames, ignore_index=True), preserve_index=False)
    pq.write_table(table, path, row_group_size=1000)  # many row groups; subjects span them
    return path


@pytest.fixture(scope="module")
def mn_path(tmp_path_factory):
    return make_metabonet(tmp_path_factory.mktemp("mn") / "mini.parquet")


def test_metabonet_streaming_groups_subjects_across_row_groups(mn_path):
    assert pq.ParquetFile(mn_path).num_row_groups >= 5
    seen = [(s, i, len(g)) for s, i, g in metabonet.iter_subjects(mn_path, metabonet.AUDIT_COLUMNS)]
    assert [(s, i) for s, i, _ in seen] == [("OhioT1DM", "540"), ("Loop", "540"), ("Flair", "9"), ("Loop", "77")]
    assert [n for *_, n in seen] == [9 * 288, 9 * 288, 9 * 288, 5 * 288]


def test_metabonet_audit_identity_and_eligibility(mn_path):
    a = metabonet.audit(mn_path).set_index("participant_id")
    assert {"metabonet_ohiot1dm_540", "metabonet_loop_540"} <= set(a.index)  # no collision
    assert a.loc["metabonet_flair_9", "eligible_episodes"] == 0  # no carbs -> no episodes
    assert a.loc["metabonet_loop_77", "eligible_episodes"] == 0  # < 7 days of history
    assert a.loc["metabonet_ohiot1dm_540", "eligible_episodes"] == 2  # meals on days 8 and 9
    assert a.loc["metabonet_loop_540", "negative_bolus"] == 1


def test_metabonet_ingests_bounded_window_with_canonical_rules(mn_path, tmp_path):
    engine = temp_engine(tmp_path)
    results = metabonet.import_metabonet(mn_path, engine=engine, report_dir=tmp_path / "reports")
    repo = SqliteDataRepository(engine=engine)
    ids = [p.participant_id for p in repo.list_participants()]
    assert ids == ["metabonet_loop_540", "metabonet_ohiot1dm_540"]  # only eligible subjects
    h = repo.get_history("metabonet_ohiot1dm_540")
    assert h.cgm[0].timestamp == dt.datetime(2027, 4, 1, tzinfo=UTC)
    assert h.cgm[-1].timestamp < dt.datetime(2027, 4, 9, tzinfo=UTC)  # 7 days + day 8 only
    assert all(m.carbs_g == 45.0 and m.protein_g is None and m.calories_kcal is None for m in h.meals)
    assert {m.medication_class for m in h.medications} == {"insulin_bolus"}
    assert all(m.dose > 0 and m.dose_unit == "U" for m in h.medications)
    assert h.heart_rate and repo.get_history("metabonet_loop_540").heart_rate == []
    assert repo.get_participant_metadata("metabonet_loop_540")["cgm_sensors"] == ["Dexcom G6"]
    reasons = {}
    for r in results:
        for m, d in r.report["modalities"].items():
            for k, v in d["rejection_reasons"].items():
                reasons[(m, k)] = reasons.get((m, k), 0) + v
    assert reasons == {("cgm", "implausible_glucose_low"): 2, ("medications", "negative_dose"): 2}
    again = metabonet.import_metabonet(mn_path, engine=engine, report_dir=tmp_path / "r2")
    assert sum(r.report["totals"]["inserted"] for r in again) == 0


# ------------------------------------------------------------------ eligibility + audit


def test_eligibility_rules_on_hand_built_series():
    t0 = pd.Timestamp("2026-01-01")
    cgm = pd.date_range(t0, t0 + pd.Timedelta(days=9), freq="5min")
    meals = [t0 + pd.Timedelta(days=3), t0 + pd.Timedelta(days=8, hours=8), t0 + pd.Timedelta(days=8, hours=9)]
    ep = meal_episodes(cgm, meals)
    assert ep["has_7d_history"].tolist() == [False, True, True]
    assert ep["eligible"].tolist() == [False, True, True]
    assert ep["overlap"].tolist() == [False, True, False]
    holed = cgm[(cgm < meals[1] + pd.Timedelta(minutes=50)) | (cgm > meals[1] + pd.Timedelta(minutes=70))]
    assert meal_episodes(holed, meals[1:2])["horizons_ok"].tolist() == [False]  # no reading near t+60


def test_audit_reports_are_reproducible(cgm_source, mn_path, tmp_path):
    cg = audit_cgmacros(cgm_source)
    mn = metabonet.audit(mn_path)
    schema = metabonet.schema_report(mn_path)
    write_reports(cg, mn, schema, tmp_path / "a")
    write_reports(audit_cgmacros(cgm_source), metabonet.audit(mn_path), metabonet.schema_report(mn_path), tmp_path / "b")
    names = sorted(p.name for p in (tmp_path / "a").iterdir())
    assert names == ["cgmacros_participants.csv", "data_quality.md", "day8_eligibility.csv",
                     "metabonet_schema.json", "modality_coverage.csv", "summary.md"]
    for name in names:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    assert json.loads((tmp_path / "a" / "metabonet_schema.json").read_text())["subject_ids_shared_across_sources"] == 1
    d8 = pd.read_csv(tmp_path / "a" / "day8_eligibility.csv")
    assert set(d8.dataset) == {"cgmacros", "metabonet"}


# ------------------------------------------------------------------ time basis + attributes


def test_unknown_timezone_is_explicit_and_original_text_preserved(cgm_repo):
    from data.storage import tables
    from sqlalchemy import select

    basis = cgm_repo.get_time_basis("cgmacros_001")
    assert basis == {"time_basis": "source_local_unknown_tz", "timezone": None}
    with cgm_repo.engine.connect() as conn:
        ts, src = conn.execute(
            select(tables.meals.c.timestamp, tables.meals.c.source_timestamp)
            .where(tables.meals.c.participant_id == "cgmacros_001").order_by(tables.meals.c.timestamp)
        ).first()
    assert src == "2020-05-01 08:07:00"  # exactly as written in the source file
    assert cgm_repo.local_minutes_of_day("cgmacros_001", ts) == 8 * 60 + 7  # local clock, not a UTC shift


def test_local_time_of_day_rules():
    from data.timeutil import local_minutes_of_day

    t = dt.datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
    assert local_minutes_of_day(t, "source_local_unknown_tz", None) == 750
    assert local_minutes_of_day(t, "utc", "America/New_York") == 8 * 60 + 30
    assert local_minutes_of_day(t, "utc", None) is None  # real UTC, unknown zone: not guessed


def test_amount_consumed_and_a1c_are_preserved_outside_the_api(cgm_source, tmp_path):
    (cgm_source / "bio.csv").write_text("subject,Age,A1c PDL (Lab)\n1,30,5.4\n2,41,6.1\n")
    engine = temp_engine(tmp_path)
    cgmacros.import_cgmacros(cgm_source, engine=engine, report_dir=tmp_path / "reports")
    repo = SqliteDataRepository(engine=engine)
    attrs = repo.get_source_attributes("cgmacros_001")
    a1c = [a for a in attrs if a["attribute"] == "a1c_lab"]
    assert len(a1c) == 1 and a1c[0]["value_num"] == 5.4 and a1c[0]["entity_type"] == "participant"
    consumed = [a for a in attrs if a["attribute"] == "amount_consumed_pct"]
    assert len(consumed) == 27 and {a["value_num"] for a in consumed} == {100.0} and consumed[0]["unit"] == "%"
    assert "Age" not in {a["attribute"] for a in attrs}  # only the approved fields
    meal = repo.get_meals("cgmacros_001")[0]
    assert not hasattr(meal, "amount_consumed_pct")  # API record types unchanged


def test_metabonet_time_basis(mn_path, tmp_path):
    engine = temp_engine(tmp_path)
    metabonet.import_metabonet(mn_path, engine=engine, report_dir=tmp_path / "reports", max_subjects=1)
    repo = SqliteDataRepository(engine=engine)
    pid = repo.list_participants()[0].participant_id
    assert repo.get_time_basis(pid) == {"time_basis": "source_local_unknown_tz", "timezone": None}
