"""Completed-episode scoring and the future-only personalization cycle."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from backend.app.v2.db import tables as t
from backend.app.v2.services import outcomes
from tests.v2.conftest import (
    BASE_NOW, MEAL, build_harness, seed_scored_meals, snapshot_features, synthetic_glucose,
)

WINDOW = dt.timedelta(minutes=120)


def forecast_now(h, headers, hours=6):
    h.push_cgm(headers, BASE_NOW - dt.timedelta(hours=hours), BASE_NOW)
    meal_id = h.meal(headers, BASE_NOW)
    return meal_id, h.predict(headers, meal_id).json()


def outcome(h, headers, meal_id):
    return h.client.get(f"/api/v2/meals/{meal_id}/outcome", headers=headers).json()


def test_an_episode_is_scored_only_after_its_window_and_the_grace_period(harness):
    headers = harness.register()
    meal_id, prediction = forecast_now(harness, headers)
    assert outcome(harness, headers, meal_id)["status"] == "pending"

    # The full response arrives, but the grace period has not elapsed.
    harness.clock.advance(minutes=125)
    harness.push_cgm(headers, BASE_NOW + dt.timedelta(minutes=5), harness.clock.now, meals=[BASE_NOW])
    early = outcome(harness, headers, meal_id)
    assert early["status"] == "pending" and early["errors"] is None
    assert early["scoring_not_before"] == "2026-09-20T18:30:00Z"

    harness.clock.advance(minutes=30)
    scored = outcome(harness, headers, meal_id)
    assert scored["status"] == "scored" and scored["eligible_for_personalization"] is True
    assert scored["ineligibility_reasons"] == []
    assert scored["coverage"]["readings_in_window"] == 24
    assert scored["coverage"]["horizons_missing"] == []

    # Errors are predicted minus what the sensor actually read at each horizon.
    predicted = prediction["forecast"]["glucose_at_minutes"]
    for horizon in (30, 60, 90, 120):
        observed = round(synthetic_glucose(BASE_NOW + dt.timedelta(minutes=horizon), [BASE_NOW]), 3)
        row = scored["errors"]["by_horizon"][str(horizon)]
        assert row["observed_mg_dl"] == round(observed, 1)
        assert row["error_mg_dl"] == pytest.approx(predicted[str(horizon)] - observed, abs=0.06)
    signed = [scored["errors"]["by_horizon"][h]["error_mg_dl"] for h in ("30", "60", "90", "120")]
    assert scored["errors"]["mae_mg_dl"] == pytest.approx(sum(abs(e) for e in signed) / 4, abs=0.06)
    assert scored["errors"]["bias_mg_dl"] == pytest.approx(sum(signed) / 4, abs=0.06)
    assert scored["observed"]["time_to_peak_minutes"] == 55
    assert "residual_vs_cohort" not in scored["errors"]     # internal, not for display


def test_scoring_never_modifies_the_stored_prediction(harness):
    headers = harness.register()
    meal_id, prediction = forecast_now(harness, headers)
    harness.clock.advance(minutes=200)
    harness.push_cgm(headers, BASE_NOW + dt.timedelta(minutes=5), harness.clock.now, meals=[BASE_NOW], offset=60)
    assert outcome(harness, headers, meal_id)["status"] == "scored"
    stored = harness.client.get(f"/api/v2/predictions/{prediction['prediction_id']}", headers=headers).json()
    assert stored["forecast"] == prediction["forecast"]
    assert stored["created_at"] == prediction["created_at"]
    assert stored["outcome_status"] == "scored"


def test_delayed_sensor_upload_is_scored_when_it_arrives(harness):
    headers = harness.register()
    meal_id, _ = forecast_now(harness, headers)

    harness.clock.advance(minutes=160)         # window and grace over, sensor silent
    missing = outcome(harness, headers, meal_id)
    assert missing["status"] == "insufficient_data"
    assert missing["ineligibility_reasons"][0] == "missing_horizon_targets"
    assert missing["coverage"]["horizons_missing"] == [30, 60, 90, 120]
    assert missing["eligible_for_personalization"] is False

    harness.clock.advance(hours=5)             # the phone syncs hours later
    harness.push_cgm(headers, BASE_NOW + dt.timedelta(minutes=5), BASE_NOW + dt.timedelta(hours=3), meals=[BASE_NOW])
    late = outcome(harness, headers, meal_id)
    assert late["status"] == "scored" and late["eligible_for_personalization"] is True
    assert late["score_revision"] == 2
    readings = harness.client.get("/api/v2/cgm/readings", headers=headers, params={
        "start": (BASE_NOW + dt.timedelta(minutes=5)).isoformat(),
        "end": (BASE_NOW + dt.timedelta(minutes=10)).isoformat()}).json()["readings"]
    assert readings[0]["observed_at"] == "2026-09-20T16:05:00Z"
    assert readings[0]["received_at"] == "2026-09-20T23:40:00Z"    # arrival is recorded as late


def test_a_sensor_gap_inside_the_window_makes_the_episode_ineligible(harness):
    headers = harness.register()
    meal_id, _ = forecast_now(harness, headers)
    harness.clock.advance(minutes=200)
    # Readings only around the four horizons: 11 points where the frozen rule needs 20.
    keep = [m for m in range(5, 125, 5) if min(abs(m - h) for h in (30, 60, 90, 120)) <= 5]
    harness.client.post("/api/v2/cgm/readings", headers=headers, json={
        "source": "demo", "device_id": "sim-1", "readings": [
            {"observed_at": (BASE_NOW + dt.timedelta(minutes=m)).isoformat(),
             "value": round(synthetic_glucose(BASE_NOW + dt.timedelta(minutes=m), [BASE_NOW]), 3),
             "unit": "mg/dL"} for m in keep]})
    result = outcome(harness, headers, meal_id)
    assert result["status"] == "scored"                        # error is still measurable
    assert result["coverage"]["readings_in_window"] == 11
    assert result["coverage"]["max_gap_minutes"] == 25.0   # meal start to the first reading
    assert result["eligible_for_personalization"] is False
    assert result["ineligibility_reasons"] == ["insufficient_curve_points"]


def test_another_meal_inside_the_window_makes_the_episode_ineligible(harness):
    headers = harness.register()
    meal_id, _ = forecast_now(harness, headers)
    harness.clock.advance(minutes=45)
    harness.push_cgm(headers, BASE_NOW + dt.timedelta(minutes=5), harness.clock.now)
    snack = harness.meal(headers, harness.clock.now, {**MEAL, "carbs_g": 20.0})
    harness.clock.advance(minutes=200)
    harness.push_cgm(headers, BASE_NOW + dt.timedelta(minutes=50), harness.clock.now)
    result = outcome(harness, headers, meal_id)
    assert result["ineligibility_reasons"] == ["intervening_meal"]
    assert result["coverage"]["intervening_meal_ids"] == [snack]
    assert result["eligible_for_personalization"] is False


def test_a_sensor_correction_reopens_and_disqualifies_a_scored_episode(harness):
    headers = harness.register()
    meal_id, _ = forecast_now(harness, headers)
    harness.clock.advance(minutes=200)
    harness.push_cgm(headers, BASE_NOW + dt.timedelta(minutes=5), harness.clock.now, meals=[BASE_NOW])
    first = outcome(harness, headers, meal_id)
    assert first["eligible_for_personalization"] is True

    harness.clock.advance(minutes=10)
    at_60 = BASE_NOW + dt.timedelta(minutes=60)
    harness.client.post("/api/v2/cgm/readings", headers=headers, json={
        "source": "demo", "device_id": "sim-1",
        "readings": [{"observed_at": at_60.isoformat(), "value": 199.0, "unit": "mg/dL"}]})
    second = outcome(harness, headers, meal_id)
    assert second["score_revision"] == first["score_revision"] + 1
    assert second["errors"]["by_horizon"]["60"]["observed_mg_dl"] == 199.0   # rescored on new data
    assert second["ineligibility_reasons"] == ["sensor_correction_in_window"]
    assert second["eligible_for_personalization"] is False


@pytest.mark.parametrize("status,reason", [("partial", "meal_partially_eaten"), ("not_eaten", "meal_not_eaten")])
def test_what_was_actually_eaten_decides_eligibility(harness, status, reason):
    headers = harness.register()
    meal_id, _ = forecast_now(harness, headers)
    harness.clock.advance(minutes=200)
    harness.push_cgm(headers, BASE_NOW + dt.timedelta(minutes=5), harness.clock.now, meals=[BASE_NOW])
    assert outcome(harness, headers, meal_id)["eligible_for_personalization"] is True
    harness.client.patch(f"/api/v2/meals/{meal_id}/consumption", headers=headers,
                         json={"consumption_status": status})
    result = outcome(harness, headers, meal_id)
    assert result["ineligibility_reasons"] == [reason] and result["eligible_for_personalization"] is False


def test_unreviewed_ai_inputs_and_revised_inputs_are_never_personal_history(harness):
    from tests.v2.conftest import jpeg_bytes
    headers = harness.register()
    harness.push_cgm(headers, BASE_NOW - dt.timedelta(hours=6), BASE_NOW)
    photo = harness.client.post("/api/v2/meals/images", params={"occurred_at": BASE_NOW.isoformat()},
                                headers={**headers, "Content-Type": "image/jpeg"}, content=jpeg_bytes()).json()
    harness.client.post(f"/api/v2/meals/{photo['meal_id']}/analyze", headers=headers)
    harness.predict(headers, photo["meal_id"], allow_unconfirmed_estimate=True)
    harness.clock.advance(minutes=200)
    harness.push_cgm(headers, BASE_NOW + dt.timedelta(minutes=5), harness.clock.now, meals=[BASE_NOW])
    unreviewed = outcome(harness, headers, photo["meal_id"])
    assert unreviewed["ineligibility_reasons"] == ["inputs_unconfirmed"]

    # The user later corrects the meal: the old forecast's inputs are no longer current.
    harness.client.patch(f"/api/v2/meals/{photo['meal_id']}/confirm", headers=headers, json=MEAL)
    with harness.ctx.engine.begin() as conn:
        conn.execute(t.response_episodes.update().values(status="pending"))
    revised = outcome(harness, headers, photo["meal_id"])
    assert set(revised["ineligibility_reasons"]) == {"inputs_unconfirmed", "meal_inputs_revised"}


def test_a_deleted_meal_voids_its_outcome_and_leaves_history(harness):
    headers = harness.register()
    seeded = seed_scored_meals(harness, headers, 3)
    before = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()
    assert before["personalization"]["admissible_prior_meals"] == 3
    harness.client.delete(f"/api/v2/meals/{seeded[1][0]}", headers=headers)
    harness.clock.advance(minutes=5)
    harness.push_cgm(headers, BASE_NOW, harness.clock.now)
    after = harness.predict(headers, harness.meal(headers, harness.clock.now)).json()
    assert after["personalization"]["admissible_prior_meals"] == 2


def test_repeated_identical_meals_are_separate_episodes(harness):
    headers = harness.register()
    now = harness.clock.now
    times = [now - dt.timedelta(hours=4 * (i + 1)) for i in range(5)][::-1]
    harness.push_cgm(headers, now - dt.timedelta(days=2), now, meals=times)
    ids = []
    for moment in times:                       # the same meal, five times
        meal_id = harness.meal(headers, moment, MEAL)
        harness.predict(headers, meal_id)
        ids.append(meal_id)
    body = harness.predict(headers, harness.meal(headers, now, MEAL)).json()
    assert body["personalization"]["admissible_prior_meals"] == 5
    assert body["personalization"]["retrieval_active"] is True
    with harness.ctx.engine.connect() as conn:
        episodes = conn.execute(select(t.response_episodes.c.meal_id).where(
            t.response_episodes.c.eligible_for_personalization.is_(True))).scalars().all()
    assert sorted(episodes) == sorted(ids) and len(set(episodes)) == 5


def test_completed_history_changes_future_forecasts_only(harness):
    """The loop the product exists for: yesterday's measured responses reach
    tomorrow's forecast, and yesterday's forecasts stay exactly as they were made."""
    headers = harness.register()
    seeded = seed_scored_meals(harness, headers, 6)
    stored_before = {
        p["prediction_id"]: p["forecast"]
        for p in harness.client.get("/api/v2/predictions", headers=headers).json()["predictions"]}

    with_history = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()
    features = snapshot_features(harness, with_history["prediction_id"])
    assert features["r_hist_n"] == 6 and features["r_hist_peak_delta"] > 0

    # The same meal for someone with the same sensor trace but no logged history.
    other = harness.register("nohistory@example.com")
    harness.push_cgm(other, BASE_NOW - dt.timedelta(days=3), BASE_NOW, meals=[m for _, m in seeded])
    without = harness.predict(other, harness.meal(other, BASE_NOW)).json()
    assert without["forecast"]["points"][0] == with_history["forecast"]["points"][0]   # same baseline
    assert without["forecast"]["points"] != with_history["forecast"]["points"]         # history matters

    stored_after = {
        p["prediction_id"]: p["forecast"]
        for p in harness.client.get("/api/v2/predictions", headers=headers, params={"limit": 50}).json()["predictions"]}
    for prediction_id, forecast in stored_before.items():
        assert stored_after[prediction_id] == forecast


def test_the_scoring_worker_processes_every_user(harness):
    from workers import outcome_scoring

    meals = {}
    for email in ("w1@example.com", "w2@example.com"):
        headers = harness.register(email)
        meals[email] = (headers, forecast_now(harness, headers)[0])
    harness.clock.advance(minutes=200)
    for headers, _ in meals.values():
        harness.push_cgm(headers, BASE_NOW + dt.timedelta(minutes=5), harness.clock.now, meals=[BASE_NOW])
    tally = outcome_scoring.run_once(harness.ctx)
    assert tally["scored"] == 2 and tally["examined"] == 2
    assert outcome_scoring.run_once(harness.ctx)["examined"] == 0     # nothing left to do
    with harness.ctx.engine.connect() as conn:
        statuses = conn.execute(select(t.response_episodes.c.status)).scalars().all()
    assert statuses == ["scored", "scored"]


# --------------------------------------------------------------------------- #
# Level B: the frozen calibration policy, behind its switch
# --------------------------------------------------------------------------- #


def test_default_policy_never_applies_a_calibration_offset(harness):
    headers = harness.register()
    seed_scored_meals(harness, headers, 8)
    body = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()
    assert body["personalization"]["policy"] == "history_only"
    assert body["personalization"]["calibration_applied"] is False
    assert body["forecast"]["prediction_status"] == "generalized_fallback"
    with harness.ctx.engine.connect() as conn:
        offsets = conn.execute(select(t.predictions.c.applied_offsets)).scalars().all()
    assert all(set(o.values()) == {0.0} for o in offsets)


@pytest.mark.parametrize("episodes,personalized", [(3, False), (4, True), (7, True)])
def test_frozen_calibration_policy_uses_the_frozen_shrinkage_and_minimum(tmp_path, v1_settings, episodes, personalized):
    h = build_harness(tmp_path, v1_settings, personalization_policy="history_plus_frozen_calibration")
    headers = h.register()
    seed_scored_meals(h, headers, episodes)
    body = h.predict(headers, h.meal(headers, BASE_NOW)).json()
    assert body["personalization"]["policy"] == "history_plus_frozen_calibration"
    assert body["personalization"]["calibration_applied"] is personalized
    assert body["forecast"]["prediction_status"] == ("personalized" if personalized else "generalized_fallback")

    with h.ctx.engine.connect() as conn:
        prediction = conn.execute(select(t.predictions).where(
            t.predictions.c.prediction_id == body["prediction_id"])).mappings().one()
        residuals = [e["residual_vs_cohort"] for e in conn.execute(
            select(t.response_episodes.c.errors).where(
                t.response_episodes.c.eligible_for_personalization.is_(True))).scalars()]
    assert len(residuals) == episodes
    record = prediction["personalization"]["calibration"]
    for horizon in ("30", "60", "90", "120"):
        raw = sum(r[horizon] for r in residuals) / episodes
        expected = max(-60.0, min(60.0, raw * episodes / (episodes + 3.0)))     # n/(n+k), k=3, cap 60
        assert record["offsets_mg_dl"][horizon] == pytest.approx(expected, abs=1e-9)
        assert prediction["applied_offsets"][horizon] == pytest.approx(expected if personalized else 0.0, abs=1e-9)
        # The stored cohort delta is the engine's output before the offset.
        total = body["forecast"]["glucose_at_minutes"][horizon] - prediction["baseline_glucose_mg_dl"]
        assert prediction["cohort_deltas"][horizon] + prediction["applied_offsets"][horizon] == pytest.approx(total, abs=0.06)

    # The loaded artifact itself was not given this participant.
    assert prediction["participant_id"] not in h.ctx.prediction_service.artifact().personalization.calibrations
    reproduced = h.client.post(f"/api/v2/predictions/{body['prediction_id']}/reproduce", headers=headers).json()
    assert reproduced["reproduced"] is True


def test_frozen_calibration_policy_requires_consent(tmp_path, v1_settings):
    h = build_harness(tmp_path, v1_settings, personalization_policy="history_plus_frozen_calibration")
    headers = h.register("nc@example.com", personalization=False)
    seed_scored_meals(h, headers, 6)
    body = h.predict(headers, h.meal(headers, BASE_NOW)).json()
    assert body["personalization"]["calibration_applied"] is False
    assert body["forecast"]["prediction_status"] == "generalized_fallback"


def test_calibration_excludes_an_episode_whose_window_ends_at_the_query_instant(tmp_path, v1_settings):
    from backend.app.v2.services import personalization

    h = build_harness(tmp_path, v1_settings, personalization_policy="history_plus_frozen_calibration")
    headers = h.register()
    seeded = seed_scored_meals(h, headers, 5)
    h.predict(headers, h.meal(headers, BASE_NOW))          # scores the five
    user_id = h.client.get("/api/v2/me", headers=headers).json()["user_id"]
    last_window_end = seeded[-1][1] + WINDOW
    artifact = h.ctx.prediction_service.artifact()
    with h.ctx.engine.connect() as conn:
        _, at_boundary = personalization.fit_user_calibration(conn, artifact, user_id, "p", last_window_end)
        _, just_after = personalization.fit_user_calibration(
            conn, artifact, user_id, "p", last_window_end + dt.timedelta(seconds=1))
    assert len(at_boundary) == 4 and len(just_after) == 5


def test_reopening_only_touches_episodes_whose_window_overlaps_the_new_data(harness):
    headers = harness.register()
    seed_scored_meals(harness, headers, 3)
    harness.predict(headers, harness.meal(headers, BASE_NOW))
    user_id = harness.client.get("/api/v2/me", headers=headers).json()["user_id"]
    moment = BASE_NOW - dt.timedelta(hours=9, minutes=30)      # inside the middle meal's window
    with harness.ctx.engine.begin() as conn:
        assert outcomes.reopen_affected_episodes(conn, user_id, moment, moment) == 1
