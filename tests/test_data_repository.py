import json
from datetime import datetime, timedelta, timezone

import pytest
from jsonschema import Draft202012Validator

from data.repository import DataLayerUnavailableError, DataRepository, ParticipantNotFoundError
from data.storage.db import REPO_ROOT, make_engine
from data.testing import fixture_repo  # noqa: F401  (pytest fixture)

UTC = timezone.utc
SCHEMAS = REPO_ROOT / "contracts" / "schemas"


def validator(name):
    return Draft202012Validator(json.loads((SCHEMAS / f"{name}.schema.json").read_text()))


def test_uninitialized_database_is_reported(tmp_path):
    with pytest.raises(DataLayerUnavailableError):
        DataRepository(engine=make_engine(f"sqlite:///{tmp_path / 'empty.db'}"))


def test_list_participants_matches_contract(fixture_repo):
    people = fixture_repo.list_participants()
    assert [p.participant_id for p in people] == [f"synthetic_p00{i}" for i in range(1, 7)]
    assert all(p.data_source == "synthetic_dev" for p in people)
    assert all(6.5 <= p.days_available <= 7.0 for p in people)
    payload = {"participants": [p.model_dump(mode="json") for p in people]}
    validator("participants_response").validate(payload)


def test_get_participant_unknown_returns_none_but_readers_raise(fixture_repo):
    assert fixture_repo.get_participant("nope_p999") is None
    assert fixture_repo.get_participant("synthetic_p001").display_name == "Participant 001"
    assert fixture_repo.get_participant_record("synthetic_p001").timezone == "America/New_York"
    with pytest.raises(ParticipantNotFoundError):
        fixture_repo.get_history("nope_p999")
    with pytest.raises(ParticipantNotFoundError):
        fixture_repo.get_cgm("nope_p999")


def test_history_is_sorted_isolated_and_contract_valid(fixture_repo):
    hist = fixture_repo.get_history("synthetic_p002")
    for series in (hist.cgm, hist.heart_rate, hist.meals):
        times = [x.timestamp for x in series]
        assert times == sorted(times) and len(times) == len(set(times))
    assert all(m.participant_id == "synthetic_p002" for m in hist.meals)
    assert all(t.timestamp.utcoffset() == timedelta(0) for t in hist.cgm)
    payload = hist.model_dump(mode="json")
    assert payload["cgm"][0]["timestamp"].endswith("Z")
    validator("history_response").validate(payload)


def test_participant_isolation(fixture_repo):
    """No series ever contains another participant's data."""
    seen = {}
    for p in fixture_repo.list_participants():
        cgm = fixture_repo.get_cgm(p.participant_id)
        seen[p.participant_id] = {(c.timestamp, c.glucose_mg_dl) for c in cgm}
        meals = fixture_repo.get_meals(p.participant_id)
        assert {m.participant_id for m in meals} == {p.participant_id}
        assert all(m.meal_id.startswith(p.participant_id) for m in meals)
        meds = fixture_repo.get_medications(p.participant_id)
        assert {m.participant_id for m in meds} <= {p.participant_id}
    total = sum(len(v) for v in seen.values())
    assert total == fixture_repo.quality_report()["record_counts"]["cgm"]


def test_inclusive_start_end_filters(fixture_repo):
    cgm = fixture_repo.get_cgm("synthetic_p001")
    start, end = cgm[10].timestamp, cgm[20].timestamp
    window = fixture_repo.get_cgm("synthetic_p001", start, end)
    assert window[0].timestamp == start and window[-1].timestamp == end and len(window) == 11
    with pytest.raises(ValueError):
        fixture_repo.get_cgm("synthetic_p001", start=datetime(2026, 10, 2))  # naive bound


def test_prediction_context_never_contains_future_observations(fixture_repo):
    meal = fixture_repo.get_meals("synthetic_p003")[12]
    ctx = fixture_repo.get_prediction_context("synthetic_p003", meal.timestamp)
    everything = ctx.cgm + ctx.heart_rate + ctx.meals + ctx.medications + ctx.activity
    assert everything and max(x.timestamp for x in everything) <= meal.timestamp
    assert ctx.meals[-1].meal_id == meal.meal_id  # the meal itself (metadata at t) is allowed
    full = fixture_repo.get_cgm("synthetic_p003")
    assert len(ctx.cgm) == sum(1 for c in full if c.timestamp <= meal.timestamp)
    # The display history for a window crossing the meal DOES include post-meal glucose,
    # which is exactly why prediction code must use get_prediction_context.
    hist = fixture_repo.get_history("synthetic_p003", meal.timestamp - timedelta(hours=1), meal.timestamp + timedelta(hours=2))
    assert max(c.timestamp for c in hist.cgm) > meal.timestamp


def test_prediction_context_lookback_and_naive_guard(fixture_repo):
    as_of = datetime(2026, 10, 5, 17, 0, tzinfo=UTC)
    ctx = fixture_repo.get_prediction_context("synthetic_p001", as_of, lookback=timedelta(hours=6))
    assert ctx.lookback_start == as_of - timedelta(hours=6)
    assert all(as_of - timedelta(hours=6) <= c.timestamp <= as_of for c in ctx.cgm)
    assert 60 <= len(ctx.cgm) <= 73
    with pytest.raises(ValueError):
        fixture_repo.get_prediction_context("synthetic_p001", datetime(2026, 10, 5, 17, 0))


def test_missing_modality_returns_empty_lists(fixture_repo):
    assert fixture_repo.get_heart_rate("synthetic_p005") == []
    assert fixture_repo.get_activity("synthetic_p005") == []
    assert fixture_repo.get_medications("synthetic_p001") == []
    assert len(fixture_repo.get_meals("synthetic_p006")) == 3  # sparse logger
    ctx = fixture_repo.get_prediction_context("synthetic_p005", datetime(2026, 10, 7, 12, tzinfo=UTC))
    assert ctx.heart_rate == [] and ctx.cgm


def test_meals_keep_missing_macros_as_null(fixture_repo):
    meals = [m for p in fixture_repo.list_participants() for m in fixture_repo.get_meals(p.participant_id)]
    incomplete = [m for m in meals if not m.has_complete_macros]
    assert incomplete, "fixtures include incompletely logged meals"
    for m in incomplete:
        dumped = m.model_dump()
        # A genuine 0 g (e.g. fat in an apple) is a value; a missing label is None, never 0.
        assert all(v is None or v >= 0 for k, v in dumped.items() if k.endswith(("_g", "_kcal")))
        assert any(v is None for k, v in dumped.items() if k.endswith(("_g", "_kcal")))


def test_export_model_examples_ordering_and_cutoff(fixture_repo):
    every = fixture_repo.export_model_examples()
    keys = [(m.timestamp, m.meal_id) for m in every]
    assert keys == sorted(keys) and len(every) == fixture_repo.quality_report()["record_counts"]["meals"]
    cutoff = datetime(2026, 10, 4, 0, 0, tzinfo=UTC)
    early = fixture_repo.export_model_examples(cutoff)
    assert early and all(m.timestamp <= cutoff for m in early)
    assert early == [m for m in every if m.timestamp <= cutoff]
    assert fixture_repo.export_model_examples() == every  # deterministic


def test_export_tables_writes_canonical_csvs(fixture_repo, tmp_path):
    manifest = fixture_repo.export_tables(tmp_path / "export", cutoff=datetime(2026, 10, 3, tzinfo=UTC))
    assert manifest["files"]["cgm.csv"]["rows"] > 0
    header = (tmp_path / "export" / "cgm.csv").read_text().splitlines()[0]
    assert header == "participant_id,timestamp,glucose_mg_dl,sensor,data_source"
    assert manifest["runs"] and manifest["runs"][0]["sha256"]
