"""A meal entered into a demonstration is forecast, and is otherwise inert.

The demonstration replays one participant's recorded glucose. Someone exploring it may
add a meal of their own. The participant never ate that meal, so the recording that
follows is not its response (DECISIONS D-019). These tests hold the line: a
hypothetical meal gets a genuine forecast and nothing else, and no authentic meal's
forecast, score or eligibility moves because of it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select, update

from backend.app.v2.db import tables as t
from backend.app.v2.demo import fixture, replay
from backend.app.v2.services import history, meal_provenance, outcomes
from tests.v2.conftest import MEAL, build_harness, jpeg_bytes
from tests.v2.test_demo_replay import DEMO_PASSWORD, advance, login

HYPOTHETICAL = {"carbs_g": 120.0, "protein_g": 5.0, "fat_g": 5.0, "fiber_g": 1.0, "calories_kcal": 545.0}


def make_demo(tmp_path, v1_settings):
    h = build_harness(tmp_path, v1_settings, demo_mode=True, demo_password=DEMO_PASSWORD)
    h.ctx.demo_bundle = fixture.fixture_bundle()
    replay.initialize(h.v2_app, h.ctx)
    return h


@pytest.fixture()
def demo(tmp_path, v1_settings):
    return make_demo(tmp_path, v1_settings)


def position(h, headers) -> dt.datetime:
    raw = h.client.get("/api/v2/demo/replay", headers=headers).json()["position"]
    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))


def add_hypothetical(h, headers, *, forecast: bool = True) -> dict:
    """Log a meal 'now' on the replay clock, as a person exploring the demo would."""
    created = h.client.post("/api/v2/meals", headers=headers,
                            json={"occurred_at": position(h, headers).isoformat(), "macros": HYPOTHETICAL})
    assert created.status_code == 201, created.text
    meal = created.json()
    if forecast:
        response = h.predict(headers, meal["meal_id"])
        meal["forecast_status"] = response.status_code
        meal["prediction"] = response.json() if response.status_code == 200 else None
    return meal


def essentials(state: dict) -> list:
    """Everything the replay reports about each event, minus generated ids."""
    drop = {"meal_id", "prediction_id", "alert_id"}
    return [(c["id"], {k: v for k, v in (c["result"] or {}).items() if k not in drop})
            for c in state["checkpoints"]]


def forecasts(h, headers) -> list:
    """Every authentic meal's stored forecast, oldest first."""
    rows = h.client.get("/api/v2/predictions", headers=headers, params={"limit": 100}).json()["predictions"]
    notes = {m["meal_id"]: m["note"] for m in
             h.client.get("/api/v2/meals", headers=headers, params={"limit": 200}).json()["meals"]}
    return [(p["forecast"]["meal_timestamp"], p["forecast"]["points"], p["personalization"])
            for p in reversed(rows) if meal_provenance.has_reserved_prefix(notes[p["meal_id"]])]


# --------------------------------------------------------------------------- #
# what a hypothetical meal gets
# --------------------------------------------------------------------------- #


def test_a_hypothetical_meal_is_still_genuinely_forecast(demo):
    headers = login(demo)
    advance(demo, headers, minutes=20)
    meal = add_hypothetical(demo, headers)
    assert meal["forecast_status"] == 200
    prediction = meal["prediction"]
    assert len(prediction["forecast"]["points"]) == 25
    assert prediction["inputs_confirmed"] is True
    assert prediction["outcome_status"] == "not_applicable"
    assert prediction["outcome_not_before"] is None


def test_it_has_no_outcome_and_says_so_rather_than_pending_or_scored(demo):
    headers = login(demo)
    advance(demo, headers, minutes=20)
    meal = add_hypothetical(demo, headers)
    advance(demo, headers, to="end")              # the recording now covers its whole window

    outcome = demo.client.get(f"/api/v2/meals/{meal['meal_id']}/outcome", headers=headers)
    assert outcome.status_code == 200
    body = outcome.json()
    assert body["status"] == "not_applicable"
    assert body["reason"] == "hypothetical_demo_meal"
    assert body["observed"] is None and body["errors"] is None and body["coverage"] is None
    assert body["eligible_for_personalization"] is False
    assert body["ineligibility_reasons"] == ["hypothetical_demo_meal"]
    assert body["predicted"]["metrics"]["peak_mg_dl"] > 0     # the forecast itself is still reported

    with demo.ctx.engine.connect() as conn:
        episodes = conn.execute(select(t.response_episodes).where(
            t.response_episodes.c.meal_id == meal["meal_id"])).all()
    assert episodes == []                          # nothing was ever scored against the recording
    listed = demo.client.get(f"/api/v2/predictions/{meal['prediction']['prediction_id']}", headers=headers).json()
    assert listed["outcome_status"] == "not_applicable"


def test_a_photo_meal_in_the_demo_is_hypothetical_too(demo):
    headers = login(demo)
    advance(demo, headers, minutes=20)
    uploaded = demo.client.post(
        "/api/v2/meals/images", headers={**headers, "Content-Type": "image/jpeg"},
        params={"occurred_at": position(demo, headers).isoformat()}, content=jpeg_bytes())
    assert uploaded.status_code == 201, uploaded.text
    meal_id = uploaded.json()["meal_id"]
    demo.client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=headers, json=MEAL)
    assert demo.predict(headers, meal_id).status_code == 200
    advance(demo, headers, to="end")
    assert demo.client.get(f"/api/v2/meals/{meal_id}/outcome", headers=headers).json()["status"] == "not_applicable"


def test_the_recorded_meal_label_cannot_be_forged(demo, harness):
    headers = login(demo)
    advance(demo, headers, minutes=20)
    when = position(demo, headers).isoformat()
    for note in ("Research dataset meal record (CGMacros v1.0.0): x", "Generated test-fixture meal record (not real data): x"):
        refused = demo.client.post("/api/v2/meals", headers=headers,
                                   json={"occurred_at": when, "macros": HYPOTHETICAL, "note": note})
        assert refused.status_code == 422 and refused.json()["details"]["field"] == "note"
        photo = demo.client.post("/api/v2/meals/images", headers={**headers, "Content-Type": "image/jpeg"},
                                 params={"occurred_at": when, "note": note}, content=jpeg_bytes())
        assert photo.status_code == 422
    # A guessed header is not the replay's.
    forged = demo.client.post(
        "/api/v2/meals", headers={**headers, meal_provenance.REPLAY_HEADER: "0" * 64},
        json={"occurred_at": when, "macros": HYPOTHETICAL, "note": "Research dataset meal record: x"})
    assert forged.status_code == 422
    # An ordinary note is fine, and an ordinary account is refused the reserved one as well.
    assert demo.client.post("/api/v2/meals", headers=headers,
                            json={"occurred_at": when, "note": "Lunch"}).status_code == 201
    other = harness.register()
    assert harness.client.post("/api/v2/meals", headers=other, json={
        "occurred_at": "2026-09-20T15:00:00Z", "note": "Research dataset meal record: x"}).status_code == 422


# --------------------------------------------------------------------------- #
# what it must never touch
# --------------------------------------------------------------------------- #


def test_hypothetical_meals_change_no_authentic_forecast_score_or_eligibility(tmp_path, v1_settings):
    """The whole replay, run clean and run with a hypothetical meal dropped in before
    every event, must report exactly the same thing for every recorded meal."""
    clean = make_demo(tmp_path / "clean", v1_settings)
    clean_headers = login(clean)
    clean_state = advance(clean, clean_headers, to="end")

    dirty = make_demo(tmp_path / "dirty", v1_settings)
    headers = login(dirty)
    added = 0
    state = dirty.client.get("/api/v2/demo/replay", headers=headers).json()
    while state["next_checkpoint"] is not None:
        # A large-carbohydrate meal right now, and another a few minutes on: inside
        # recorded meals' two-hour windows and immediately before the next one.
        add_hypothetical(dirty, headers)
        advance(dirty, headers, minutes=7)
        add_hypothetical(dirty, headers, forecast=False)
        added += 2
        state = advance(dirty, headers)
    assert added >= 20

    assert essentials(state) == essentials(clean_state)
    assert forecasts(dirty, headers) == forecasts(clean, clean_headers)
    assert state["personalization_progress"] == clean_state["personalization_progress"]

    with dirty.ctx.engine.connect() as conn:
        user_id = conn.execute(select(t.demo_replay_state.c.user_id)).scalar()
        eligible = history.eligible_meal_ids(conn, user_id)
        notes = dict(conn.execute(select(t.meals.c.meal_id, t.meals.c.note)).all())
        scored = conn.execute(select(t.response_episodes.c.meal_id)).scalars().all()
    assert eligible and all(meal_provenance.has_reserved_prefix(notes[m]) for m in eligible)
    assert all(meal_provenance.has_reserved_prefix(notes[m]) for m in scored)   # no episode for a hypothetical meal


def test_a_hypothetical_meal_does_not_make_a_recorded_meal_an_intervening_meal_case(demo):
    headers = login(demo)
    advance(demo, headers)                                   # recorded meal 1 logged and forecast
    advance(demo, headers, minutes=30)
    add_hypothetical(demo, headers)                          # inside meal 1's two-hour window
    state = advance(demo, headers, to="meal_01_outcome")
    result = next(c["result"] for c in state["checkpoints"] if c["id"] == "meal_01_outcome")
    assert result["outcome"] == "scored"
    assert result["eligible_for_personalization"] is True
    assert "intervening_meal" not in result["ineligibility_reasons"]


def test_an_episode_scored_before_this_rule_can_no_longer_count(demo):
    """A database that already holds a scored hypothetical episode stays safe."""
    headers = login(demo)
    advance(demo, headers, minutes=20)
    meal = add_hypothetical(demo, headers)
    with demo.ctx.engine.begin() as conn:
        user_id = conn.execute(select(t.demo_replay_state.c.user_id)).scalar()
        stored_meal = dict(conn.execute(select(t.meals).where(t.meals.c.meal_id == meal["meal_id"])).mappings().one())
        # What the old code would have written: an episode, later marked scored and eligible.
        values = outcomes.new_episode_values(meal["prediction"]["prediction_id"], stored_meal, user_id, 0)
        conn.execute(t.response_episodes.insert().values(**{
            **values, "status": "scored", "eligible_for_personalization": True,
            "observed": {"curve": [{"minute": 30, "glucose_mg_dl": 111.0}]}, "errors": {"mae_mg_dl": 1.0}}))
        assert meal["meal_id"] not in history.eligible_meal_ids(conn, user_id)

    body = demo.client.get(f"/api/v2/meals/{meal['meal_id']}/outcome", headers=headers).json()
    assert body["status"] == "not_applicable" and body["observed"] is None and body["errors"] is None

    moment = position(demo, headers) - dt.timedelta(minutes=1)
    with demo.ctx.engine.begin() as conn:                    # and a rescore voids it for good
        conn.execute(update(t.response_episodes).where(t.response_episodes.c.meal_id == meal["meal_id"])
                     .values(status="pending", eligible_after=moment, window_end_at=moment))
    demo.client.get(f"/api/v2/meals/{meal['meal_id']}/outcome", headers=headers)
    with demo.ctx.engine.connect() as conn:
        row = conn.execute(select(t.response_episodes).where(
            t.response_episodes.c.meal_id == meal["meal_id"])).mappings().one()
    assert row["status"] == "voided" and row["eligible_for_personalization"] is False
    assert row["ineligibility_reasons"] == ["hypothetical_demo_meal"] and row["observed"] == {}


def test_ordinary_accounts_are_untouched_by_the_rule(harness):
    """Outside a demonstration every meal is the user's own and is scored as before."""
    from tests.v2.conftest import seed_scored_meals

    headers = harness.register()
    meal_ids = seed_scored_meals(harness, headers, 2)
    for meal_id, _ in meal_ids:
        body = harness.client.get(f"/api/v2/meals/{meal_id}/outcome", headers=headers).json()
        assert body["status"] == "scored" and body["eligible_for_personalization"] is True
        assert "reason" not in body
