"""What the user confirmed is what gets forecast, and what every reader sees.

The chain under test: photo -> AI estimate -> user edit -> confirmation -> stored
record -> model inputs -> forecast -> the lists a dashboard reads. Each link is checked
against the stored record, not against what the previous link claimed.

The model here is the test suite's stand-in (the frozen artifact is not in git); the
binding it proves -- which numbers reach the engine -- is the application's own code
and is the same with the frozen model. `e2e/journeys/meal-to-home.mjs` repeats the
chain in a browser against the frozen fs-1.3 artifact.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from backend.app.v2.db import tables as t
from tests.v2.conftest import BASE_NOW, MEAL, jpeg_bytes, snapshot_features

NUTRIENTS = ("carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal")
PROFILE_A = {"carbs_g": 30.0, "protein_g": 20.0, "fat_g": 12.0, "fiber_g": 5.0, "calories_kcal": 308.0}
PROFILE_B = {"carbs_g": 90.0, "protein_g": 20.0, "fat_g": 12.0, "fiber_g": 5.0, "calories_kcal": 548.0}


def user_with_glucose(h, email="a@example.com"):
    headers = h.register(email)
    h.push_cgm(headers, BASE_NOW - dt.timedelta(days=2), BASE_NOW)
    return headers


def photo_meal(h, headers, when=BASE_NOW) -> str:
    response = h.client.post("/api/v2/meals/images", headers={**headers, "Content-Type": "image/jpeg"},
                             params={"occurred_at": when.isoformat()}, content=jpeg_bytes())
    assert response.status_code == 201, response.text
    return response.json()["meal_id"]


def confirmed_in_database(h, meal_id) -> dict:
    with h.ctx.engine.connect() as conn:
        row = conn.execute(
            select(t.meal_confirmations)
            .select_from(t.meal_confirmations.join(
                t.meals, t.meals.c.current_confirmation_id == t.meal_confirmations.c.confirmation_id))
            .where(t.meals.c.meal_id == meal_id)).mappings().one()
    return {k: row[k] for k in NUTRIENTS} | {"confirmation_id": row["confirmation_id"]}


def test_the_edited_values_not_the_ai_estimate_are_stored_returned_and_forecast(harness):
    headers = user_with_glucose(harness)
    meal_id = photo_meal(harness, headers)
    estimate = harness.client.post(f"/api/v2/meals/{meal_id}/analyze", headers=headers).json()["estimate"]
    ai = estimate["totals"]
    edited = {**ai, "carbs_g": ai["carbs_g"] + 41.0, "fiber_g": 2.0}
    assert edited != ai

    confirmed = harness.client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=headers, json=edited)
    assert confirmed.status_code == 200 and confirmed.json()["confirmation"]["source"] == "ai_edited"

    stored = confirmed_in_database(harness, meal_id)                       # the database
    assert {k: stored[k] for k in NUTRIENTS} == edited
    fetched = harness.client.get(f"/api/v2/meals/{meal_id}", headers=headers).json()   # the API
    assert {k: fetched["confirmation"][k] for k in NUTRIENTS} == edited
    assert fetched["estimate"]["totals"] == ai                              # the AI's numbers are kept, separately

    prediction = harness.predict(headers, meal_id).json()
    assert prediction["meal_id"] == meal_id
    assert prediction["confirmation_id"] == stored["confirmation_id"]       # forecast names the confirmation it used
    assert prediction["inputs_confirmed"] is True
    features = snapshot_features(harness, prediction["prediction_id"])      # what the engine was given
    assert {k: features[k] for k in NUTRIENTS} == edited
    assert features["carbs_g"] != ai["carbs_g"]


def test_two_meals_with_different_carbohydrates_get_their_own_inputs_and_forecasts(harness):
    """Same glucose history, same instant, only the carbohydrates (and so calories) differ."""
    headers = user_with_glucose(harness)
    meal_a = harness.meal(headers, BASE_NOW, PROFILE_A)
    forecast_a = harness.predict(headers, meal_a).json()
    # Remove A so that B is forecast from an identical context, not with A as a prior meal.
    features_a = snapshot_features(harness, forecast_a["prediction_id"])
    assert harness.client.delete(f"/api/v2/meals/{meal_a}", headers=headers).status_code == 204
    meal_b = harness.meal(headers, BASE_NOW, PROFILE_B)
    forecast_b = harness.predict(headers, meal_b).json()
    features_b = snapshot_features(harness, forecast_b["prediction_id"])

    assert {k: features_a[k] for k in NUTRIENTS} == PROFILE_A
    assert {k: features_b[k] for k in NUTRIENTS} == PROFILE_B
    same_context = [k for k in features_a if k not in ("carbs_g", "calories_kcal")]
    assert all(features_a[k] == features_b[k] or (features_a[k] != features_a[k] and features_b[k] != features_b[k])
               for k in same_context), "only the edited nutrients may differ between the two requests"

    curve_a = [p["glucose_mg_dl"] for p in forecast_a["forecast"]["points"]]
    curve_b = [p["glucose_mg_dl"] for p in forecast_b["forecast"]["points"]]
    assert len(curve_a) == len(curve_b) == 25
    assert curve_a[0] == curve_b[0]                       # both start from the same measured glucose
    assert curve_a != curve_b, "three times the carbohydrate must not produce an identical curve"
    assert forecast_b["forecast"]["metrics"]["peak_mg_dl"] > forecast_a["forecast"]["metrics"]["peak_mg_dl"]


def test_a_forecast_keeps_the_values_it_was_made_from_when_the_meal_is_edited_later(harness):
    headers = user_with_glucose(harness)
    meal_id = harness.meal(headers, BASE_NOW, PROFILE_A)
    first = harness.predict(headers, meal_id).json()

    harness.client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=headers, json=PROFILE_B)
    second = harness.predict(headers, meal_id).json()

    assert second["prediction_id"] != first["prediction_id"]
    assert snapshot_features(harness, first["prediction_id"])["carbs_g"] == 30.0     # never rewritten
    assert snapshot_features(harness, second["prediction_id"])["carbs_g"] == 90.0
    stored_first = harness.client.get(f"/api/v2/predictions/{first['prediction_id']}", headers=headers).json()
    assert stored_first["forecast"] == first["forecast"]
    meal = harness.client.get(f"/api/v2/meals/{meal_id}", headers=headers).json()
    assert meal["prediction_id"] == second["prediction_id"]        # the meal points at the current forecast
    assert meal["confirmation"]["carbs_g"] == 90.0


def test_repeating_the_forecast_request_does_not_duplicate_anything(harness):
    headers = user_with_glucose(harness)
    meal_id = harness.meal(headers, BASE_NOW, PROFILE_A)
    first = harness.predict(headers, meal_id).json()
    again = harness.predict(headers, meal_id).json()
    assert again["prediction_id"] == first["prediction_id"] and again["replayed"] is True
    assert again["forecast"] == first["forecast"]
    listed = harness.client.get("/api/v2/predictions", headers=headers).json()["predictions"]
    assert [p["prediction_id"] for p in listed] == [first["prediction_id"]]
    assert len(harness.client.get("/api/v2/meals", headers=headers).json()["meals"]) == 1


def test_what_a_dashboard_reads_is_the_stored_meal_and_its_forecast(harness):
    """`GET /meals` and `GET /predictions` are the Home screen's sources."""
    headers = user_with_glucose(harness)
    older = harness.meal(headers, BASE_NOW - dt.timedelta(hours=6), MEAL)
    harness.predict(headers, older)
    before = harness.client.get("/api/v2/cgm/readings", headers=headers).json()["readings"]

    draft = photo_meal(harness, headers)                                    # uploaded, nothing confirmed
    listed = {m["meal_id"]: m for m in harness.client.get("/api/v2/meals", headers=headers).json()["meals"]}
    assert listed[draft]["status"] == "draft" and listed[draft]["confirmation"] is None
    assert listed[draft]["prediction_id"] is None
    assert harness.predict(headers, draft).status_code == 409               # and it cannot be forecast

    harness.client.patch(f"/api/v2/meals/{draft}/confirm", headers=headers, json=PROFILE_B)
    forecast = harness.predict(headers, draft).json()

    meals = harness.client.get("/api/v2/meals", headers=headers).json()["meals"]
    assert [m["meal_id"] for m in meals] == [draft, older]                   # newest first
    assert {k: meals[0]["confirmation"][k] for k in NUTRIENTS} == PROFILE_B
    assert meals[0]["prediction_id"] == forecast["prediction_id"]
    predictions = {p["prediction_id"]: p for p in
                   harness.client.get("/api/v2/predictions", headers=headers).json()["predictions"]}
    assert len(predictions) == 2
    listed_forecast = predictions[forecast["prediction_id"]]
    assert listed_forecast["meal_id"] == draft and listed_forecast["forecast"] == forecast["forecast"]
    # Logging a meal never changes a measured glucose reading.
    assert harness.client.get("/api/v2/cgm/readings", headers=headers).json()["readings"] == before


def test_one_accounts_meals_and_forecasts_are_invisible_to_another(harness):
    mine = user_with_glucose(harness, "mine@example.com")
    meal_id = harness.meal(mine, BASE_NOW, PROFILE_B)
    prediction_id = harness.predict(mine, meal_id).json()["prediction_id"]

    theirs = user_with_glucose(harness, "theirs@example.com")
    assert harness.client.get("/api/v2/meals", headers=theirs).json()["meals"] == []
    assert harness.client.get("/api/v2/predictions", headers=theirs).json()["predictions"] == []
    for path in (f"/meals/{meal_id}", f"/meals/{meal_id}/outcome", f"/meals/{meal_id}/image",
                 f"/predictions/{prediction_id}"):
        assert harness.client.get(f"/api/v2{path}", headers=theirs).status_code == 404, path
    assert harness.client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=theirs, json=PROFILE_A).status_code == 404
    assert harness.predict(theirs, meal_id).status_code == 404
    assert harness.client.get(f"/api/v2/meals/{meal_id}", headers=mine).json()["confirmation"]["carbs_g"] == 90.0


def test_a_refused_forecast_stores_nothing_and_leaves_the_meal_as_confirmed(harness):
    headers = harness.register()                                            # no glucose at all
    meal_id = harness.meal(headers, BASE_NOW, PROFILE_A)
    refused = harness.predict(headers, meal_id)
    assert refused.status_code == 422 and refused.json()["code"] == "insufficient_premeal_context"
    assert harness.client.get("/api/v2/predictions", headers=headers).json()["predictions"] == []
    meal = harness.client.get(f"/api/v2/meals/{meal_id}", headers=headers).json()
    assert meal["status"] == "confirmed" and meal["prediction_id"] is None
    assert meal["confirmation"]["carbs_g"] == 30.0
