"""Demonstration replay: chronology, isolation, determinism and safety.

Run on the labelled test-fixture bundle (generated arithmetic) so they execute on any
machine. `test_demo_real_data.py` repeats the essentials on the genuine recording
with the frozen model when both are present.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging

import pytest
from sqlalchemy import func, select

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.demo import fixture, profile, replay, source
from tests.v2.conftest import BASE_NOW, MEAL, PASSWORD, build_harness

DEMO_PASSWORD = "demo-account-password"


@pytest.fixture()
def demo(tmp_path, v1_settings):
    h = build_harness(tmp_path, v1_settings, demo_mode=True, demo_password=DEMO_PASSWORD)
    h.ctx.demo_bundle = fixture.fixture_bundle()
    replay.initialize(h.v2_app, h.ctx)
    return h


def login(h) -> dict:
    response = h.client.post("/api/v2/auth/login", json={
        "email": h.ctx.settings.demo_email, "password": DEMO_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['session']['token']}"}


def advance(h, headers, **body):
    response = h.client.post("/api/v2/demo/replay/advance", headers=headers, json=body or None)
    assert response.status_code == 200, response.text
    return response.json()


def latest_reading(h):
    with h.ctx.engine.connect() as conn:
        return conn.execute(select(func.max(t.glucose_observations.c.observed_at))).scalar()


# --------------------------------------------------------------------------- #
# gating and authorization
# --------------------------------------------------------------------------- #


def test_demo_routes_do_not_exist_unless_demo_mode_is_enabled(harness):
    headers = harness.register()
    for method, path in (("GET", "/api/v2/demo/replay"), ("POST", "/api/v2/demo/replay/advance"),
                         ("POST", "/api/v2/demo/replay/reset")):
        assert harness.client.request(method, path, headers=headers).status_code == 404
    assert "/demo/replay" not in harness.client.get("/api/v2/openapi.json").json()["paths"]
    assert harness.client.get("/api/v2/status").json()["demo_mode"] is False
    harness.ctx.demo_bundle = fixture.fixture_bundle()
    for call in (lambda: replay.initialize(harness.v2_app, harness.ctx),
                 lambda: replay.reset(harness.v2_app, harness.ctx)):
        with pytest.raises(core.V2Error) as caught:
            call()
        assert caught.value.code == "demo_mode_disabled"


def test_replay_controls_require_authentication_and_the_demo_account(demo):
    assert demo.client.get("/api/v2/demo/replay").status_code == 401
    ordinary = demo.register("ordinary@example.com")
    for method, path in (("GET", "/api/v2/demo/replay"), ("POST", "/api/v2/demo/replay/advance"),
                         ("POST", "/api/v2/demo/replay/reset")):
        response = demo.client.request(method, path, headers=ordinary)
        assert response.status_code == 403 and response.json()["code"] == "not_demo_account"
    # ...and the refusal changed nothing.
    assert demo.client.get("/api/v2/demo/replay", headers=login(demo)).json()["event_index"] == 0


def test_a_password_is_required_and_the_account_is_not_created_twice(tmp_path, v1_settings):
    h = build_harness(tmp_path, v1_settings, demo_mode=True)
    h.ctx.demo_bundle = fixture.fixture_bundle()
    with pytest.raises(core.V2Error) as caught:
        replay.initialize(h.v2_app, h.ctx)
    assert caught.value.code == "demo_not_configured"
    h.ctx.settings = dataclasses.replace(h.ctx.settings, demo_password=DEMO_PASSWORD)
    replay.initialize(h.v2_app, h.ctx)
    with pytest.raises(core.V2Error) as again:
        replay.initialize(h.v2_app, h.ctx)
    assert again.value.code == "demo_already_initialized"


def test_research_data_is_only_replayed_through_the_frozen_model(tmp_path, v1_settings):
    """The test artifact is a synthetic development model. A bundle labelled as
    research data must be refused on it -- there is no synthetic fallback."""
    h = build_harness(tmp_path, v1_settings, demo_mode=True, demo_password=DEMO_PASSWORD)
    payload = fixture.fixture_payload()
    payload["data_class"] = "research_dataset"
    h.ctx.demo_bundle = source.bundle_from_payload(payload)
    with pytest.raises(core.V2Error) as caught:
        replay.initialize(h.v2_app, h.ctx)
    assert caught.value.code == "demo_requires_frozen_model"
    with h.ctx.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(t.users)).scalar() == 0


def test_a_missing_or_wrong_bundle_fails_with_setup_instructions(tmp_path, v1_settings):
    h = build_harness(tmp_path, v1_settings, demo_mode=True, demo_password=DEMO_PASSWORD,
                      demo_bundle_path=tmp_path / "absent.json")
    with pytest.raises(core.V2Error) as caught:
        replay.initialize(h.v2_app, h.ctx)
    assert caught.value.code == "demo_data_unavailable"
    assert "No substitute data will be generated" in caught.value.message

    import json
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps(fixture.fixture_payload()))
    with pytest.raises(source.BundleError, match="checksum mismatch"):
        source.load_bundle(wrong, expected_sha256="0" * 64)
    with pytest.raises(source.BundleError, match="not found"):
        source.load_bundle(tmp_path / "absent.json", expected_sha256=None)


# --------------------------------------------------------------------------- #
# initial state
# --------------------------------------------------------------------------- #


def test_initial_state_holds_only_pre_start_data_and_labelled_fictional_records(demo):
    headers = login(demo)
    bundle = demo.ctx.demo_bundle
    state = demo.client.get("/api/v2/demo/replay", headers=headers).json()
    assert state["position"] == core.rfc3339(bundle.start) and state["event_index"] == 0
    assert state["next_checkpoint"]["id"] == "meal_01_forecast"
    assert state["next_checkpoint"]["meal_record"]["source"] == "research_dataset_meal_record"
    assert state["provenance"]["labels"] == ["DEMO", "HISTORICAL REPLAY", "NOT LIVE"]
    assert state["provenance"]["profile_and_medications"]["data_class"] == "fictional"
    assert "No model parameter is retrained" in state["personalization_progress"]["description"]

    assert latest_reading(demo) <= bundle.start
    assert demo.client.get("/api/v2/meals", headers=headers).json()["meals"] == []
    assert demo.client.get("/api/v2/predictions", headers=headers).json()["predictions"] == []

    me = demo.client.get("/api/v2/me", headers=headers).json()
    assert me["display_name"] == profile.DISPLAY_NAME and me["participant_id"].startswith("gtp_")
    assert {c["consent_type"]: c["state"] for c in me["consents"]} == {
        "necessary_processing": "granted", "personalization_history": "granted"}
    onboarding = demo.client.get("/api/v2/onboarding", headers=headers).json()
    assert onboarding["completed"] is True and onboarding["answers"]["hba1c"] == "unknown"
    regimens = demo.client.get("/api/v2/medications", headers=headers).json()["regimens"]
    assert sorted((r["name"], r["status"]) for r in regimens) == [
        ("Demozide (fictional)", "current"), ("Samplitol (fictional)", "discontinued")]
    assert all(r["prescriber_note"] == profile.FICTIONAL_NOTICE for r in regimens)
    assert demo.client.get("/api/v2/alert-settings", headers=headers).json()["enabled"] is True

    devices = demo.client.get("/api/v2/devices", headers=headers).json()["connections"]
    assert [d["status"] for d in devices] == ["pending"]          # never "connected"
    assert devices[0]["authentic_data_received"] is False
    status = demo.client.get("/api/v2/cgm/status", headers=headers).json()
    assert status["data_class"] == "demo" and status["freshness"] == "fresh"   # fresh on the REPLAY clock


# --------------------------------------------------------------------------- #
# chronology
# --------------------------------------------------------------------------- #


def test_each_step_releases_data_only_up_to_its_instant_and_forecasts_prospectively(demo):
    headers = login(demo)
    bundle = demo.ctx.demo_bundle
    state = advance(demo, headers)
    first = bundle.meals[0]
    assert state["advanced"] is True and state["position"] == core.rfc3339(first.occurred_at)
    assert latest_reading(demo) == first.occurred_at             # nothing after the meal instant

    meal = demo.client.get("/api/v2/meals", headers=headers).json()["meals"][0]
    assert meal["occurred_at"] == core.rfc3339(first.occurred_at)   # historical time kept, not re-dated
    assert meal["confirmation"]["carbs_g"] == first.macros["carbs_g"]
    assert "fixture_meal_01" in meal["note"]
    prediction = demo.client.get(f"/api/v2/predictions/{meal['prediction_id']}", headers=headers).json()
    assert prediction["prediction_kind"] == "prospective"
    assert prediction["created_at"] == core.rfc3339(first.occurred_at)
    assert prediction["context_quality"]["latest_cgm_age_minutes"] == 0.0
    assert prediction["outcome_status"] == "pending"

    outcome = demo.client.get(f"/api/v2/meals/{meal['meal_id']}/outcome", headers=headers).json()
    assert outcome["status"] == "pending" and outcome["observed"] is None   # future not yet revealed

    revealed = advance(demo, headers)
    assert revealed["checkpoints"][1]["result"]["outcome"] == "scored"
    assert latest_reading(demo) == first.occurred_at + source.REVEAL_AFTER
    scored = demo.client.get(f"/api/v2/meals/{meal['meal_id']}/outcome", headers=headers).json()
    assert scored["status"] == "scored" and len(scored["observed"]["curve"]) == 25


def test_later_readings_and_meals_cannot_change_an_earlier_forecast(tmp_path, v1_settings):
    """Two replays whose source data is identical up to meal 5 and wildly different
    afterwards must produce identical forecasts for meals 1 to 5."""
    cut = fixture.FIRST_MEAL + dt.timedelta(hours=fixture.MEAL_OFFSETS_HOURS[4])
    forecasts = []
    for name, kwargs in (("clean", {}), ("poisoned", {"shift_after": cut, "shift_mg_dl": 140.0})):
        h = build_harness(tmp_path / name, v1_settings, demo_mode=True, demo_password=DEMO_PASSWORD)
        h.ctx.demo_bundle = fixture.fixture_bundle(**kwargs)
        replay.initialize(h.v2_app, h.ctx)
        headers = login(h)
        advance(h, headers, to="meal_05_forecast")
        rows = h.client.get("/api/v2/predictions", headers=headers, params={"limit": 50}).json()["predictions"]
        forecasts.append([(p["forecast"]["points"], p["personalization"]) for p in reversed(rows)])
    assert len(forecasts[0]) == 5 and forecasts[0] == forecasts[1]


def test_the_replay_never_holds_anything_from_its_future(demo):
    headers = login(demo)
    timeline = replay.build_timeline(demo.ctx.demo_bundle)
    for event in [e for e in timeline if e["checkpoint"]]:
        state = advance(demo, headers, to=event["id"])
        assert state["position"] == core.rfc3339(event["at"])
        assert latest_reading(demo) <= event["at"]
        with demo.ctx.engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(t.meals).where(
                t.meals.c.occurred_at > event["at"])).scalar() == 0
            assert conn.execute(select(func.count()).select_from(t.response_episodes).where(
                t.response_episodes.c.scored_at > event["at"])).scalar() == 0
    assert state["status"] == "complete" and state["next_checkpoint"] is None


def test_replay_moves_forward_only_and_retries_are_no_ops(demo):
    headers = login(demo)
    advance(demo, headers, to="meal_03_forecast")
    before = demo.client.get("/api/v2/demo/replay", headers=headers).json()

    stale = advance(demo, headers, expected_event_index=0)          # a retried request
    assert stale["advanced"] is False and stale["event_index"] == before["event_index"]
    back = advance(demo, headers, to="meal_01_forecast")            # an earlier checkpoint
    assert back["advanced"] is False and back["position"] == before["position"]
    with demo.ctx.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(t.meals)).scalar() == 3

    unknown = demo.client.post("/api/v2/demo/replay/advance", headers=headers, json={"to": "meal_99_forecast"})
    assert unknown.status_code == 422 and unknown.json()["code"] == "unknown_checkpoint"
    both = demo.client.post("/api/v2/demo/replay/advance", headers=headers, json={"to": "next", "minutes": 5})
    assert both.status_code == 422


def test_time_can_pass_without_executing_a_checkpoint_and_cannot_skip_one(demo):
    headers = login(demo)
    bundle = demo.ctx.demo_bundle
    state = advance(demo, headers, minutes=10)
    assert state["position"] == core.rfc3339(bundle.start + dt.timedelta(minutes=10))
    assert state["event_index"] == 0 and latest_reading(demo) == bundle.start + dt.timedelta(minutes=10)
    capped = advance(demo, headers, minutes=600)                    # far past the first meal
    assert capped["position"] == core.rfc3339(bundle.meals[0].occurred_at)
    assert capped["event_index"] == 0                               # reached it, did not execute it
    assert demo.client.get("/api/v2/meals", headers=headers).json()["meals"] == []


# --------------------------------------------------------------------------- #
# personalization
# --------------------------------------------------------------------------- #


def test_history_personalization_progresses_and_activates_at_exactly_five(demo):
    headers = login(demo)
    state = advance(demo, headers, to="end")
    results = [c["result"] for c in state["checkpoints"] if c["kind"] == "meal_forecast"]
    counts = [r["admissible_prior_meals"] for r in results]
    assert counts == [0, 1, 1, 2, 3, 4, 5, 6, 7]
    assert [r["retrieval_active"] for r in results] == [c >= 5 for c in counts]
    assert state["aliases"]["retrieval_activation"] == "meal_07_forecast"

    outcomes = {c["meal"]: c["result"] for c in state["checkpoints"] if c["kind"] == "outcome_reveal"}
    assert outcomes[2]["eligible_for_personalization"] is False
    assert outcomes[2]["ineligibility_reasons"] == ["intervening_meal"]     # excluded, never counted
    assert all(outcomes[m]["eligible_for_personalization"] for m in (1, 3, 4, 5, 6, 7, 8, 9))

    # The backend's metadata, not a count kept by the replay.
    rows = demo.client.get("/api/v2/predictions", headers=headers, params={"limit": 50}).json()["predictions"]
    assert [p["personalization"]["admissible_prior_meals"] for p in reversed(rows)] == counts
    assert all(p["personalization"]["calibration_applied"] is False
               and p["personalization"]["policy"] == "history_only"
               and p["forecast"]["prediction_status"] == "generalized_fallback" for p in rows)


def test_a_completed_meal_counts_only_after_its_whole_window_has_ended(demo):
    headers = login(demo)
    state = advance(demo, headers, to="meal_03_forecast")
    results = {c["meal"]: c["result"] for c in state["checkpoints"] if c["kind"] == "meal_forecast" and c["done"]}
    # Meal 2 was eaten 20 minutes before meal 3: its window is still open, so it is
    # not history for meal 3 -- only meal 1 is.
    assert results[3]["admissible_prior_meals"] == 1


def test_selection_rules_are_strict_and_deterministic():
    start = dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.timezone.utc)
    times = [start + dt.timedelta(minutes=5 * i) for i in range(12 * 24 * 3)]
    macros = dict(MEAL)

    def meals(offsets):
        return [(f"m{i}", start + dt.timedelta(minutes=m), macros) for i, m in enumerate(offsets)]

    spaced = meals([60, 360, 500, 700, 900, 1200, 1500, 1800, 2100])
    assessed = source.assess_meals(times, spaced, [m[1] for m in spaced])
    assert [a["expected_eligible"] for a in assessed] == [True] * 9
    # Meal 3 is 140 minutes after meal 2, whose window ended 20 minutes earlier.
    assert source.expected_admissible(assessed, 2) == 2
    # A meal EXACTLY 120 minutes later: the earlier window ends at that instant. That
    # is inside the window (so it is an intervening meal) and is not strictly before
    # the later meal (so it would not be admissible to it either).
    exact = meals([60, 180, 400])
    exact_assessed = source.assess_meals(times, exact, [m[1] for m in exact])
    assert exact_assessed[0]["expected_ineligibility_reasons"] == ["intervening_meal"]
    assert source.expected_admissible(exact_assessed, 1) == 0
    one_minute_later = meals([60, 181, 400])
    later_assessed = source.assess_meals(times, one_minute_later, [m[1] for m in one_minute_later])
    assert later_assessed[0]["expected_eligible"] is True
    assert source.expected_admissible(later_assessed, 1) == 1

    plan = source.plan_window(assessed)
    assert plan == source.plan_window(list(assessed))
    assert source.rank_key("p2", plan) > source.rank_key("p1", plan)
    # A meal whose macros cannot be logged makes the window unusable, not patched.
    broken = [(i, at, None if n == 2 else m) for n, (i, at, m) in enumerate(spaced)]
    assert source.plan_window(source.assess_meals(times, broken, [m[1] for m in broken])) is None
    # Too few readings: reported, and nothing is invented to fill them.
    sparse = source.assess_meals(times[::6], spaced, [m[1] for m in spaced])
    assert all("fewer_than_20_readings_in_window" in a["expected_ineligibility_reasons"] for a in sparse)


# --------------------------------------------------------------------------- #
# alerts and medications
# --------------------------------------------------------------------------- #


def test_alerts_come_from_the_existing_server_side_engine(demo):
    headers = login(demo)
    advance(demo, headers, to="end")
    alerts = demo.client.get("/api/v2/alerts", headers=headers).json()["alerts"]
    assert alerts and {a["kind"] for a in alerts} == {"forecast_above_threshold"}
    assert {a["status"] for a in alerts} <= {"active", "suppressed_cooldown", "suppressed_quiet_hours"}
    predictions = {p["prediction_id"]: p for p in demo.client.get(
        "/api/v2/predictions", headers=headers, params={"limit": 50}).json()["predictions"]}
    for alert in alerts:
        payload, forecast = alert["payload"], predictions[alert["prediction_id"]]["forecast"]
        crossing = next(p["minute"] for p in forecast["points"]
                        if p["minute"] > 0 and p["glucose_mg_dl"] >= payload["threshold"]["mg_dl"])
        assert payload["predicted_crossing_minute"] == crossing      # derived from the stored forecast
        assert payload["uncertainty_status"] == "not_calibrated" and payload["informational_only"] is True
        assert "does not replace your CGM" in payload["disclaimer"]
    # Forecasts that stay below the threshold raised nothing.
    alerted = {a["prediction_id"] for a in alerts}
    for prediction_id, p in predictions.items():
        peak = max(pt["glucose_mg_dl"] for pt in p["forecast"]["points"][1:])
        assert (prediction_id in alerted) == (peak >= 130.0)


def test_fictional_doses_are_deliberate_actions_and_one_reminder_stays_unanswered(demo):
    headers = login(demo)
    advance(demo, headers, to="end")
    doses = demo.client.get("/api/v2/medication-doses", headers=headers).json()["doses"]
    answered = sorted((d["scheduled_at"], d["action"]) for d in doses if d["action"] != "scheduled")
    assert [a for _, a in answered] == ["taken", "skipped", "taken"]
    for dose in doses:
        assert dose["actor"] == ("system" if dose["action"] == "scheduled" else "user")
        assert (dose["actual_at"] is not None) == (dose["action"] == "taken")
    due = demo.client.get("/api/v2/medication-reminders/due", headers=headers).json()["due"]
    assert len(due) >= 1 and all(d["action"] == "scheduled" and d["actual_at"] is None for d in due)
    assert all(d["regimen"]["name"] == "Demozide (fictional)" for d in due)

    # Acting on it live is an ordinary, idempotent API call.
    body = {"regimen_id": due[0]["regimen_id"], "action": "taken", "scheduled_at": due[0]["scheduled_at"],
            "client_event_id": "live-demo-tap-0001"}
    first = demo.client.post("/api/v2/medication-doses", headers=headers, json=body).json()
    again = demo.client.post("/api/v2/medication-doses", headers=headers, json=body).json()
    assert first["action"] == "taken" and again["replayed"] is True and again["dose_id"] == first["dose_id"]


def test_fictional_medication_never_changes_a_forecast(tmp_path, v1_settings):
    """The same replay with and without the scripted doses gives the same forecasts."""
    def run(name, skip_doses):
        h = build_harness(tmp_path / name, v1_settings, demo_mode=True, demo_password=DEMO_PASSWORD)
        h.ctx.demo_bundle = fixture.fixture_bundle()
        if skip_doses:
            original = replay._run_dose
            replay._run_dose = lambda api, event: {"dose": "not_run"}
        try:
            replay.initialize(h.v2_app, h.ctx)
            headers = login(h)
            advance(h, headers, to="end")
        finally:
            if skip_doses:
                replay._run_dose = original
        rows = h.client.get("/api/v2/predictions", headers=headers, params={"limit": 50}).json()["predictions"]
        return [p["forecast"]["points"] for p in reversed(rows)]

    assert run("with", False) == run("without", True)


# --------------------------------------------------------------------------- #
# reset, determinism, isolation
# --------------------------------------------------------------------------- #


def test_seed_then_replay_is_deterministic_and_reset_restores_the_same_state(demo, tmp_path, v1_settings):
    headers = login(demo)
    user_id = replay._demo_user_id(demo.ctx)
    initial = replay.digest(demo.ctx, user_id)
    advance(demo, headers, to="final_meal")
    reached = replay.digest(demo.ctx, user_id)
    assert reached["digest"] != initial["digest"]

    response = demo.client.post("/api/v2/demo/replay/reset", headers=headers)
    assert response.status_code == 200 and response.json()["event_index"] == 0
    assert demo.client.get("/api/v2/me", headers=headers).status_code == 401      # old token is dead
    fresh_headers = login(demo)
    new_user = replay._demo_user_id(demo.ctx)
    assert new_user != user_id
    assert replay.digest(demo.ctx, new_user)["digest"] == initial["digest"]       # identical initial state
    advance(demo, fresh_headers, to="final_meal")
    assert replay.digest(demo.ctx, new_user)["digest"] == reached["digest"]       # identical journey

    other = build_harness(tmp_path / "second", v1_settings, demo_mode=True, demo_password=DEMO_PASSWORD)
    other.ctx.demo_bundle = fixture.fixture_bundle()
    replay.initialize(other.v2_app, other.ctx)
    advance(other, login(other), to="final_meal")
    assert replay.digest(other.ctx, replay._demo_user_id(other.ctx))["digest"] == reached["digest"]


def test_reset_and_replay_never_touch_another_account(demo):
    ordinary = demo.register("real.user@example.com")
    demo.push_cgm(ordinary, BASE_NOW - dt.timedelta(hours=3), BASE_NOW, source="manual_entry")
    meal_id = demo.meal(ordinary, BASE_NOW)
    forecast = demo.predict(ordinary, meal_id).json()

    headers = login(demo)
    advance(demo, headers, to="meal_04_forecast")
    demo.client.post("/api/v2/demo/replay/reset", headers=headers)
    advance(demo, login(demo), to="end")

    assert demo.client.get(f"/api/v2/predictions/{forecast['prediction_id']}", headers=ordinary).json()["forecast"] \
        == forecast["forecast"]
    readings = demo.client.get("/api/v2/cgm/readings", headers=ordinary).json()["readings"]
    assert len(readings) == 37 and {r["provider"] for r in readings} == {"manual_entry"}
    assert {r["data_class"] for r in readings} == {"user_device"}
    assert len(demo.client.get("/api/v2/meals", headers=ordinary).json()["meals"]) == 1
    # No demo meal or reading leaked into the ordinary account, and none of theirs into the demo's.
    demo_meals = demo.client.get("/api/v2/meals", headers=login(demo), params={"limit": 200}).json()["meals"]
    assert len(demo_meals) == 9 and meal_id not in {m["meal_id"] for m in demo_meals}


def test_reset_refuses_to_delete_an_ordinary_account_holding_the_demo_email(tmp_path, v1_settings):
    h = build_harness(tmp_path, v1_settings, demo_mode=True, demo_password=DEMO_PASSWORD)
    h.ctx.demo_bundle = fixture.fixture_bundle()
    h.client.post("/api/v2/auth/register", json={
        "email": h.ctx.settings.demo_email, "password": PASSWORD, "timezone": "UTC"})
    with pytest.raises(core.V2Error) as caught:
        replay.reset(h.v2_app, h.ctx)
    assert caught.value.code == "demo_email_in_use"
    with h.ctx.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(t.users)).scalar() == 1


def test_the_replay_clock_applies_to_the_demo_account_only(demo):
    """An ordinary account in the same process stays on the wall clock."""
    ordinary = demo.register("clock@example.com")
    demo.push_cgm(ordinary, BASE_NOW - dt.timedelta(hours=2), BASE_NOW, source="manual_entry")
    assert demo.client.get("/api/v2/cgm/status", headers=ordinary).json()["as_of"] == core.rfc3339(BASE_NOW)
    assert demo.predict(ordinary, demo.meal(ordinary, BASE_NOW)).json()["prediction_kind"] == "prospective"

    headers = login(demo)
    advance(demo, headers)
    bundle = demo.ctx.demo_bundle
    assert demo.client.get("/api/v2/cgm/status", headers=headers).json()["as_of"] == \
        core.rfc3339(bundle.meals[0].occurred_at)
    # Interleaved requests do not bleed one account's clock into the other's.
    assert demo.client.get("/api/v2/cgm/status", headers=ordinary).json()["as_of"] == core.rfc3339(BASE_NOW)
    # A historical meal is acceptable for the demo account and far too old for anyone else.
    old = demo.client.post("/api/v2/meals", headers=ordinary, json={
        "occurred_at": (BASE_NOW - dt.timedelta(days=400)).isoformat(), "macros": MEAL})
    assert old.status_code == 422
    state = demo.client.get("/api/v2/demo/replay", headers=headers).json()
    assert state["wall_clock"] == core.rfc3339(BASE_NOW) and state["position"] != state["wall_clock"]


def test_wall_clock_workers_leave_the_demo_account_alone(demo):
    from workers import outcome_scoring, reminders

    headers = login(demo)
    advance(demo, headers)                       # one forecast, outcome still in the replay's future
    assert outcome_scoring.run_once(demo.ctx)["examined"] == 0
    assert reminders.run_once(demo.ctx) == 0
    with demo.ctx.engine.connect() as conn:
        assert conn.execute(select(t.response_episodes.c.status)).scalars().all() == ["pending"]
        assert conn.execute(select(func.count()).select_from(t.medication_doses)).scalar() == 0


def test_the_manual_meal_path_works_in_the_demo_without_an_openai_key(demo):
    demo.ctx.vision = None
    headers = login(demo)
    advance(demo, headers, minutes=20)
    position = dt.datetime.fromisoformat(
        demo.client.get("/api/v2/demo/replay", headers=headers).json()["position"].replace("Z", "+00:00"))
    created = demo.client.post("/api/v2/meals", headers=headers, json={"occurred_at": position.isoformat()})
    meal_id = created.json()["meal_id"]
    analysis = demo.client.post(f"/api/v2/meals/{meal_id}/analyze", headers=headers)
    assert analysis.status_code == 409                                    # no photo: manual entry
    demo.client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=headers, json=MEAL)
    forecast = demo.predict(headers, meal_id)
    assert forecast.status_code == 200 and forecast.json()["prediction_kind"] == "prospective"


def test_replay_logs_carry_no_health_values(demo):
    handler = _Capture()
    target = logging.getLogger("glucotwin")
    target.addHandler(handler)
    try:
        advance(demo, login(demo), to="meal_02_forecast")
        replay.reset(demo.v2_app, demo.ctx)
    finally:
        target.removeHandler(handler)
    text = "\n".join(handler.lines)
    assert "demo_initialized" in text
    bundle = demo.ctx.demo_bundle
    for value in {f"{v}" for _, v in bundle.cgm[-600:]} | {str(bundle.meals[0].macros["calories_kcal"])}:
        assert value not in text
    assert DEMO_PASSWORD not in text and bundle.meals[0].source_meal_id not in text


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())
