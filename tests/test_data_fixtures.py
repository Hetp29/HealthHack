import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from data.fixtures.generator import DEFAULT_OUT, DEFAULT_TRUTH_OUT, generate


def read(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def test_generation_is_deterministic(tmp_path):
    a = generate(tmp_path / "a", tmp_path / "a_truth", seed=123)
    b = generate(tmp_path / "b", tmp_path / "b_truth", seed=123)
    c = generate(tmp_path / "c", tmp_path / "c_truth", seed=124)
    assert a["files"] == b["files"]
    assert a["files"]["cgm.csv"]["sha256"] != c["files"]["cgm.csv"]["sha256"]


def test_committed_fixtures_match_the_generator(tmp_path):
    """Committed fixtures are reproducible: regenerating gives byte-identical files."""
    manifest = generate(tmp_path / "out", tmp_path / "truth")
    committed = json.loads((DEFAULT_OUT / "manifest.json").read_text())
    assert committed["files"] == manifest["files"], "run `python -m data.fixtures.generator` and commit"


def test_fixture_shape_and_labels():
    manifest = json.loads((DEFAULT_OUT / "manifest.json").read_text())
    assert manifest["data_source"] == "synthetic_dev" and "SYNTHETIC" in manifest["label"]
    people = read(DEFAULT_OUT / "participants.csv")
    assert len(people) >= 6
    assert all(p["participant_id"].startswith("synthetic_") for p in people)
    cgm = defaultdict(list)
    for row in read(DEFAULT_OUT / "cgm.csv"):
        cgm[row["participant_id"]].append(datetime.fromisoformat(row["timestamp"]))
    for pid, times in cgm.items():
        assert times[-1] - times[0] >= timedelta(days=6, hours=23)
        steps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]
        assert statistics.median(steps) == 5.0  # nominal 5-min cadence
        assert max(steps) > 15  # realistic gaps exist
    meals = read(DEFAULT_OUT / "meals.csv")
    assert all(m["image_ref"] == "" for m in meals)  # no images exist; never invented
    assert any(m["fiber_g"] == "" for m in meals)  # missing labels stay missing


def test_modality_coverage_edge_cases():
    hr_ids = {r["participant_id"] for r in read(DEFAULT_OUT / "heart_rate.csv")}
    assert "synthetic_p005" not in hr_ids and len(hr_ids) == 5
    med_ids = {r["participant_id"] for r in read(DEFAULT_OUT / "medications.csv")}
    assert med_ids == {"synthetic_p002", "synthetic_p004"}


def test_overlapping_meals_exist():
    by = defaultdict(list)
    for m in read(DEFAULT_OUT / "meals.csv"):
        by[m["participant_id"]].append(datetime.fromisoformat(m["timestamp"]))
    close = sum(1 for times in by.values() for a, b in zip(times, times[1:]) if b - a <= timedelta(minutes=120))
    assert close >= 5


def test_simulator_truth_is_separate_and_individuals_differ():
    assert DEFAULT_TRUTH_OUT.resolve() != DEFAULT_OUT.resolve()
    assert not (DEFAULT_OUT / "simulator_truth.json").exists()
    truth = json.loads((DEFAULT_TRUTH_OUT / "simulator_truth.json").read_text())
    assert "NOT A MODEL INPUT" in truth["warning"]
    params = [p["params"] for p in truth["participants"].values()]
    assert len({p["carb_sensitivity"] for p in params}) == len(params)
    assert len({p["baseline_mg_dl"] for p in params}) == len(params)
    # Hidden simulator fields never appear as columns in model-readable files.
    hidden = {"carb_sensitivity", "baseline_mg_dl", "time_to_peak_min", "true_time", "true_macros"}
    for path in DEFAULT_OUT.glob("*.csv"):
        header = path.read_text().splitlines()[0].split(",")
        assert not hidden & set(header)


def test_observed_glucose_distinguishes_participants():
    """Sanity check that the fixture lets a model tell individuals apart."""
    values = defaultdict(list)
    for row in read(DEFAULT_OUT / "cgm.csv"):
        values[row["participant_id"]].append(float(row["glucose_mg_dl"]))
    medians = sorted(statistics.median(v) for v in values.values())
    assert medians[-1] - medians[0] > 30
