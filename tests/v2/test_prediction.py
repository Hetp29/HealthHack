"""Orchestration around the frozen model: inputs, persistence, leakage, history."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import pathlib

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import func, select

from backend.app.v2.db import tables as t
from ml.features import FEATURE_NAMES, MealComposition
from ml.inference import forecast as frozen_forecast
from ml.schema import CgmPoint, ParticipantHistory
from tests.v2.conftest import (
    BASE_NOW, MEAL, seed_scored_meals, snapshot_features, synthetic_glucose,
)

SCHEMA = json.loads(pathlib.Path("contracts/schemas/predict_response.schema.json").read_text())
NY_OFFSET = dt.timedelta(hours=-4)   # America/New_York on the test date (EDT)


def ready_user(h, email="a@example.com", hours=6, **kwargs):
    headers = h.register(email, **kwargs)
    h.push_cgm(headers, BASE_NOW - dt.timedelta(hours=hours), BASE_NOW)
    return headers


def test_forecast_is_the_frozen_contract_shape_and_is_persisted_with_its_inputs(harness):
    headers = ready_user(harness)
    meal_id = harness.meal(headers, BASE_NOW)
    body = harness.predict(headers, meal_id).json()

    errors = list(Draft202012Validator(SCHEMA).iter_errors(body["forecast"]))
    assert errors == [], [e.message for e in errors]
    assert [p["minute"] for p in body["forecast"]["points"]] == list(range(0, 121, 5))
    assert body["forecast"]["meal_timestamp"] == "2026-09-20T16:00:00Z"   # the true instant
    assert body["forecast"]["uncertainty_status"] == "not_calibrated"
    assert body["forecast"]["prediction_status"] == "generalized_fallback"

    me = harness.client.get("/api/v2/me", headers=headers).json()
    assert body["forecast"]["participant_id"] == me["participant_id"]
    artifact_file = pathlib.Path(harness.ctx.prediction_service.settings.model_artifact)
    assert body["model"] == {
        "model_id": "glucotwin-fs13-blend-personalized", "model_version": "v0.1.0",
        "feature_set_version": "fs-1.3",
        "artifact_sha256": hashlib.sha256(artifact_file.read_bytes()).hexdigest()}

    with harness.ctx.engine.connect() as conn:
        snapshot = conn.execute(select(t.feature_snapshots)).mappings().one()
        prediction = conn.execute(select(t.predictions)).mappings().one()
        episode = conn.execute(select(t.response_episodes)).mappings().one()
    assert tuple(snapshot["feature_names"]) == FEATURE_NAMES          # all 39, frozen order
    assert len(snapshot["feature_values"]) == 39
    assert snapshot["as_of"] == BASE_NOW
    assert prediction["forecast"] == body["forecast"]
    assert prediction["user_id"] == me["user_id"] and prediction["inputs_confirmed"] is True
    assert episode["status"] == "pending"
    assert episode["window_end_at"] == BASE_NOW + dt.timedelta(minutes=120)
    assert episode["eligible_after"] == BASE_NOW + dt.timedelta(minutes=150)


def test_the_orchestrator_adds_nothing_to_the_frozen_engine_output(harness):
    """Calling the frozen engine directly on the same observations gives the same curve."""
    headers = ready_user(harness)
    body = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()

    start = BASE_NOW - dt.timedelta(hours=6)
    points, moment = [], start
    while moment <= BASE_NOW:
        points.append(CgmPoint(timestamp=moment + NY_OFFSET, glucose_mg_dl=round(synthetic_glucose(moment, []), 3)))
        moment += dt.timedelta(minutes=5)
    direct = frozen_forecast(
        harness.ctx.prediction_service.artifact(),
        ParticipantHistory(participant_id=body["forecast"]["participant_id"], data_source="unknown", cgm=points),
        BASE_NOW + NY_OFFSET, MealComposition(**MEAL), time_basis="source_local_unknown_tz",
    ).to_contract_dict()
    for key in ("points", "glucose_at_minutes", "metrics", "drivers", "prediction_status"):
        assert body["forecast"][key] == direct[key], key


def test_repeat_requests_return_the_stored_forecast(harness):
    headers = ready_user(harness)
    meal_id = harness.meal(headers, BASE_NOW)
    first = harness.predict(headers, meal_id).json()
    harness.clock.advance(minutes=30)
    harness.push_cgm(headers, BASE_NOW, harness.clock.now, offset=80)   # the world has moved on
    second = harness.predict(headers, meal_id).json()
    assert second["replayed"] is True and first["replayed"] is False
    assert second["prediction_id"] == first["prediction_id"]
    assert second["forecast"] == first["forecast"]
    with harness.ctx.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(t.predictions)).scalar() == 1


def test_a_corrected_meal_gets_a_new_forecast_and_the_old_one_is_kept_unchanged(harness):
    headers = ready_user(harness)
    meal_id = harness.meal(headers, BASE_NOW)
    first = harness.predict(headers, meal_id).json()
    harness.client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=headers, json={**MEAL, "carbs_g": 120.0})
    second = harness.predict(headers, meal_id).json()
    assert second["prediction_id"] != first["prediction_id"]
    assert second["forecast"]["points"] != first["forecast"]["points"]
    kept = harness.client.get(f"/api/v2/predictions/{first['prediction_id']}", headers=headers).json()
    assert kept["forecast"] == first["forecast"]


def test_a_stored_prediction_can_be_reproduced_exactly_from_its_snapshot(harness):
    headers = harness.register()
    seed_scored_meals(harness, headers, 6)
    body = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()
    assert body["personalization"]["retrieval_active"] is True      # the non-trivial path
    result = harness.client.post(f"/api/v2/predictions/{body['prediction_id']}/reproduce", headers=headers).json()
    assert result == {
        "prediction_id": body["prediction_id"], "reproduced": True, "forecast_identical": True,
        "features_identical": True, "feature_count": 39,
        "artifact_sha256": body["model"]["artifact_sha256"],
        "history_sha256": body["snapshot"]["history_sha256"], "history_sha256_verified": True}

    harness.ctx.model_integrity["artifact_sha256"] = "f" * 64       # a different artifact loaded
    differs = harness.client.post(f"/api/v2/predictions/{body['prediction_id']}/reproduce", headers=headers).json()
    assert differs["reproduced"] is False and differs["reason"] == "artifact_differs"


@pytest.mark.parametrize("age_minutes,expected", [(0, 200), (15, 200), (16, 422), (45, 422)])
def test_the_frozen_baseline_recency_rule_gates_forecasting(harness, age_minutes, expected):
    headers = harness.register()
    last = BASE_NOW - dt.timedelta(minutes=age_minutes)
    harness.push_cgm(headers, last - dt.timedelta(hours=3), last, step_minutes=1 if age_minutes == 16 else 5)
    response = harness.predict(headers, harness.meal(headers, BASE_NOW))
    assert response.status_code == expected
    if expected == 422:
        body = response.json()
        assert body["code"] == "insufficient_premeal_context"
        assert body["details"]["latest_cgm_age_minutes"] == age_minutes
        assert body["details"]["baseline_tolerance_minutes"] == 15.0
        with harness.ctx.engine.connect() as conn:   # nothing half-written
            assert conn.execute(select(func.count()).select_from(t.predictions)).scalar() == 0
            assert conn.execute(select(func.count()).select_from(t.feature_snapshots)).scalar() == 0


def test_zero_history_is_an_explicit_refusal(harness):
    headers = harness.register()
    response = harness.predict(headers, harness.meal(headers, BASE_NOW))
    assert response.status_code == 422
    assert response.json()["details"]["cgm_readings_in_lookback"] == 0


def test_observations_after_the_meal_cannot_change_the_forecast(harness):
    """Two accounts identical up to the meal instant. One also holds, already stored
    at prediction time, extreme post-meal glucose and a huge later meal."""
    meal_time = BASE_NOW - dt.timedelta(hours=4)
    results = []
    for email, poisoned in (("clean@example.com", False), ("poisoned@example.com", True)):
        headers = harness.register(email)
        harness.push_cgm(headers, meal_time - dt.timedelta(hours=8), meal_time)
        if poisoned:
            later = [{"observed_at": (meal_time + dt.timedelta(minutes=5 * i)).isoformat(),
                      "value": 390, "unit": "mg/dL"} for i in range(1, 37)]
            harness.client.post("/api/v2/cgm/readings", headers=headers,
                                json={"source": "demo", "device_id": "sim-1", "readings": later})
            harness.meal(headers, meal_time + dt.timedelta(minutes=45),
                         {**MEAL, "carbs_g": 250.0, "calories_kcal": 1500.0})
        prediction = harness.predict(headers, harness.meal(headers, meal_time)).json()
        results.append((prediction, snapshot_features(harness, prediction["prediction_id"])))

    (clean, clean_features), (poisoned, poisoned_features) = results
    assert poisoned_features == clean_features                       # all 39, bit for bit
    for key in ("points", "glucose_at_minutes", "metrics", "drivers"):
        assert poisoned["forecast"][key] == clean["forecast"][key]
    assert clean["prediction_kind"] == "retrospective"


def test_time_of_day_is_read_on_the_account_local_clock(harness):
    """Same UTC observations, three timezones. Clock features follow the local hour;
    every elapsed-time feature is untouched by the projection."""
    features = {}
    for email, zone in (("ny@example.com", "America/New_York"), ("utc@example.com", "UTC"),
                        ("tokyo@example.com", "Asia/Tokyo")):
        headers = harness.register(email, timezone=zone)
        harness.push_cgm(headers, BASE_NOW - dt.timedelta(days=2), BASE_NOW)
        body = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()
        features[zone] = snapshot_features(harness, body["prediction_id"])
        assert body["forecast"]["meal_timestamp"] == "2026-09-20T16:00:00Z"

    def expected(local_hour):
        angle = 2 * math.pi * (local_hour * 60) / 1440.0
        return pytest.approx(math.sin(angle), abs=1e-9), pytest.approx(math.cos(angle), abs=1e-9)

    assert (features["America/New_York"]["tod_sin"], features["America/New_York"]["tod_cos"]) == expected(12)
    assert (features["UTC"]["tod_sin"], features["UTC"]["tod_cos"]) == expected(16)
    assert (features["Asia/Tokyo"]["tod_sin"], features["Asia/Tokyo"]["tod_cos"]) == expected(1)

    clock_dependent = {"tod_sin", "tod_cos", "g_dev_from_night"}
    for name in FEATURE_NAMES:
        if name not in clock_dependent:
            assert features["America/New_York"][name] == features["Asia/Tokyo"][name] == features["UTC"][name], name
    # The overnight floor is taken from 02:00-06:00 LOCAL, so it differs by zone.
    assert features["America/New_York"]["g_dev_from_night"] != features["Asia/Tokyo"]["g_dev_from_night"]


def test_the_live_clock_note_replaces_the_research_dataset_caveat(harness):
    headers = ready_user(harness)
    limitations = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()["forecast"]["limitations"]
    assert any("local clock" in note for note in limitations)
    assert not any("dates are shifted" in note for note in limitations)
    assert any("not medical advice" in note for note in limitations)


@pytest.mark.parametrize("prior_meals", [0, 1, 2, 3, 4, 5, 6, 8])
def test_history_count_drives_the_frozen_retrieval_gate(harness, prior_meals):
    headers = harness.register()
    if prior_meals:
        seed_scored_meals(harness, headers, prior_meals)
    else:
        harness.push_cgm(headers, BASE_NOW - dt.timedelta(days=3), BASE_NOW)
    body = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()
    features = snapshot_features(harness, body["prediction_id"])

    assert body["personalization"]["admissible_prior_meals"] == prior_meals
    assert features["r_hist_n"] == prior_meals
    assert body["personalization"]["retrieval_min_history"] == 5
    if prior_meals < 5:
        # Below the gate: value features absent, and q_retr_n reports the true count.
        assert body["personalization"]["retrieval_active"] is False
        assert features["q_retr_n"] == prior_meals
        assert features["q_retr_delta_60"] is None
        assert "q_retr_delta_60" in body["context_quality"]["missing_features"]
    else:
        # At or above: exactly retrieval_k = 5 neighbours are used.
        assert body["personalization"]["retrieval_active"] is True
        assert features["q_retr_n"] == 5
        assert features["q_retr_delta_60"] is not None
    assert (features["r_hist_peak_delta"] is None) == (prior_meals == 0)


@pytest.mark.parametrize("minutes_before,admissible", [(119, 0), (120, 0), (121, 1), (240, 1)])
def test_a_prior_window_ending_exactly_at_the_new_meal_is_excluded(harness, minutes_before, admissible):
    """Strict inequality: a meal 120 minutes ago finishes its window AT the query
    instant and is excluded. One minute earlier and it is admissible."""
    headers = harness.register()
    prior_time = BASE_NOW - dt.timedelta(minutes=minutes_before)
    harness.push_cgm(headers, BASE_NOW - dt.timedelta(days=1), BASE_NOW, step_minutes=1, meals=[prior_time])
    assert harness.predict(headers, harness.meal(headers, prior_time)).status_code == 200

    body = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()
    features = snapshot_features(harness, body["prediction_id"])
    assert features["r_hist_n"] == admissible
    assert body["personalization"]["admissible_prior_meals"] == admissible
    # Either way the previous meal still supplies "time since previous meal".
    assert features["minutes_since_prior_meal"] == minutes_before


def test_another_users_history_never_contaminates_a_forecast(harness):
    rich = harness.register("rich@example.com")
    seed_scored_meals(harness, rich, 6)
    assert harness.predict(rich, harness.meal(rich, BASE_NOW)).json()["personalization"]["admissible_prior_meals"] == 6

    fresh = harness.register("fresh@example.com")
    harness.push_cgm(fresh, BASE_NOW - dt.timedelta(hours=3), BASE_NOW)
    body = harness.predict(fresh, harness.meal(fresh, BASE_NOW)).json()
    assert body["personalization"]["admissible_prior_meals"] == 0
    with harness.ctx.engine.connect() as conn:
        user_id = harness.client.get("/api/v2/me", headers=fresh).json()["user_id"]
        context = conn.execute(select(t.feature_snapshots.c.context).where(
            t.feature_snapshots.c.user_id == user_id)).scalar()
    assert context["prior_meals"]["included_meal_ids"] == []
    assert context["cgm"]["readings_in_lookback"] == 37      # exactly this user's 3 hours


def test_without_personalization_consent_completed_meals_are_not_used_as_history(harness):
    headers = harness.register("np@example.com", personalization=False)
    seeded = seed_scored_meals(harness, headers, 6)
    body = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()
    features = snapshot_features(harness, body["prediction_id"])
    assert body["personalization"] == {
        "policy": "history_only", "consent": False, "admissible_prior_meals": 0,
        "retrieval_active": False, "retrieval_min_history": 5, "calibration_applied": False}
    assert features["r_hist_n"] == 0
    # Timing of the previous meal is still context, supplied without exposing the meal.
    assert features["minutes_since_prior_meal"] == 300.0
    assert features["prior_meal_carbs_g"] is None

    outcome = harness.client.get(f"/api/v2/meals/{seeded[0][0]}/outcome", headers=headers).json()
    assert outcome["status"] == "scored" and outcome["eligible_for_personalization"] is False
    assert outcome["ineligibility_reasons"] == ["personalization_consent_absent"]

    # Granting consent makes the same completed meals available from the next forecast on.
    harness.clock.advance(minutes=1)
    harness.consent(headers, "personalization_history")
    harness.push_cgm(headers, BASE_NOW, harness.clock.now)
    with harness.ctx.engine.begin() as conn:
        conn.execute(t.response_episodes.update().values(status="pending"))
    later = harness.predict(headers, harness.meal(headers, harness.clock.now)).json()
    assert later["personalization"]["admissible_prior_meals"] == 6


def test_medications_and_wearables_do_not_reach_the_model(harness):
    results = []
    for email, with_extras in (("plain@example.com", False), ("extras@example.com", True)):
        headers = ready_user(harness, email)
        if with_extras:
            regimen = harness.client.post("/api/v2/medications", headers=headers, json={
                "name": "Insulin lispro", "medication_class": "insulin"}).json()
            taken = harness.client.post("/api/v2/medication-doses", headers=headers, json={
                "regimen_id": regimen["regimen_id"], "action": "taken", "client_event_id": "evt-insulin-1"})
            assert taken.status_code == 201
            pushed = harness.client.post("/api/v2/wearables/observations", headers=headers, json={
                "source": "healthkit_bridge", "observations": [
                    {"metric": "heart_rate", "value": 148, "unit": "bpm",
                     "observed_at": (BASE_NOW - dt.timedelta(minutes=m)).isoformat(),
                     "source_record_id": f"hk-{m}"} for m in range(1, 30)]})
            assert pushed.json()["counts"]["inserted"] == 29
        body = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()
        results.append((body, snapshot_features(harness, body["prediction_id"])))

    (plain, plain_features), (extras, extras_features) = results
    assert extras_features == plain_features
    assert extras["forecast"]["points"] == plain["forecast"]["points"]
    assert extras_features["medication_exposure_recent"] == 0.0
    assert extras_features["hr_mean_30min_bpm"] is None
    assert "hr_mean_30min_bpm" in extras["context_quality"]["missing_features"]


def test_prediction_logs_carry_policy_and_ids_but_no_health_values(harness, caplog):
    headers = ready_user(harness)
    import logging
    # The "glucotwin" logger does not propagate, so the capture handler is attached
    # to it directly.
    target = logging.getLogger("glucotwin.v2.orchestrator")
    target.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="glucotwin.v2.orchestrator"):
            body = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()
    finally:
        target.removeHandler(caplog.handler)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("prediction_created"))
    assert body["prediction_id"] in line and "policy=history_only" in line
    for value in (body["forecast"]["metrics"]["peak_mg_dl"], body["forecast"]["points"][0]["glucose_mg_dl"], 55.0, 460.0):
        assert str(value) not in line
