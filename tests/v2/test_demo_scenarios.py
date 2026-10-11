"""Two demonstration modes -- NEW USER and RETURNING USER -- and their isolation.

On the labelled test-fixture bundles. The real recordings are covered by
`test_demo_real_data.py`.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.demo import fixture, profile, replay, source
from tests.v2.conftest import BASE_NOW, build_harness

DEMO_PASSWORD = "demo-account-password"
FIRST_DISPLAY = 8          # fixture: retrieval activates at meal 7, so meal 8 starts with it on


def make(tmp_path, v1_settings, *, modes=("new_user", "returning_user")):
    h = build_harness(tmp_path, v1_settings, demo_mode=True, demo_password=DEMO_PASSWORD)
    h.ctx.demo_bundle = {"new_user": fixture.fixture_bundle(),
                         "returning_user": fixture.fixture_bundle(display_from_meal=FIRST_DISPLAY)}
    for mode in modes:
        replay.initialize(h.v2_app, h.ctx, mode)
    return h


def login(h, mode) -> dict:
    response = h.client.post("/api/v2/auth/login", json={
        "email": replay.demo_email(h.ctx, mode), "password": DEMO_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['session']['token']}"}


def digest(h, mode) -> str:
    return replay.digest(h.ctx, replay._demo_user_id(h.ctx, mode))["digest"]


def test_returning_user_starts_with_history_built_by_the_ordinary_workflow(tmp_path, v1_settings):
    h = make(tmp_path, v1_settings, modes=("returning_user",))
    headers = login(h, "returning_user")
    state = h.client.get("/api/v2/demo/replay", headers=headers).json()
    assert (state["mode"], state["mode_label"], state["display_from_meal"]) == (
        "returning_user", "RETURNING USER", FIRST_DISPLAY)
    assert state["next_checkpoint"]["id"] == f"meal_{FIRST_DISPLAY:02d}_forecast"
    assert state["aliases"]["first_display_meal"] == f"meal_{FIRST_DISPLAY:02d}_forecast"
    assert state["provenance"]["backfilled_history"]["meals"] == FIRST_DISPLAY - 1
    phases = {c["id"]: c["phase"] for c in state["checkpoints"]}
    assert phases["meal_07_outcome"] == "backfill" and phases["meal_08_forecast"] == "display"
    assert all(c["done"] == (c["phase"] == "backfill") for c in state["checkpoints"])

    user_id = replay._demo_user_id(h.ctx, "returning_user")
    with h.ctx.engine.connect() as conn:
        # Nothing was inserted as a finished record: every backfilled meal has a real
        # forecast, with its own snapshot, made prospectively at the meal's instant,
        # and an outcome that was scored only after its window.
        predictions = conn.execute(select(t.predictions).where(t.predictions.c.user_id == user_id)
                                   .order_by(t.predictions.c.created_at)).mappings().all()
        episodes = conn.execute(select(t.response_episodes).where(
            t.response_episodes.c.user_id == user_id)).mappings().all()
        snapshots = conn.execute(select(func.count()).select_from(t.feature_snapshots).where(
            t.feature_snapshots.c.user_id == user_id)).scalar()
        position = conn.execute(select(t.demo_replay_state.c.position)).scalar()
        latest = conn.execute(select(func.max(t.glucose_observations.c.observed_at))).scalar()
    assert len(predictions) == len(episodes) == snapshots == FIRST_DISPLAY - 1
    assert all(p["prediction_kind"] == "prospective" for p in predictions)
    assert [p["personalization"]["admissible_prior_meals"] for p in predictions] == [0, 1, 1, 2, 3, 4, 5]
    assert all(e["status"] == "scored" and e["scored_at"] >= e["window_end_at"] for e in episodes)
    assert sum(e["eligible_for_personalization"] for e in episodes) == 6       # meal 2 excluded
    assert latest <= position                                                   # no future reading

    first = h.client.post("/api/v2/demo/replay/advance", headers=headers).json()
    result = first["checkpoints"][[c["id"] for c in first["checkpoints"]].index("meal_08_forecast")]["result"]
    assert result["retrieval_active"] is True and result["admissible_prior_meals"] == 6
    reproduced = h.client.post(f"/api/v2/predictions/{result['prediction_id']}/reproduce", headers=headers).json()
    assert reproduced["reproduced"] is True


def test_new_user_mode_is_unchanged_by_the_existence_of_the_other_mode(tmp_path, v1_settings):
    alone = make(tmp_path / "alone", v1_settings, modes=("new_user",))
    both = make(tmp_path / "both", v1_settings)
    assert digest(alone, "new_user") == digest(both, "new_user")
    for h in (alone, both):
        replay.advance(h.v2_app, h.ctx, replay._demo_user_id(h.ctx, "new_user"), to="end")
    assert digest(alone, "new_user") == digest(both, "new_user")

    state = both.client.get("/api/v2/demo/replay", headers=login(both, "new_user")).json()
    assert (state["mode"], state["mode_label"], state["display_from_meal"]) == ("new_user", "NEW USER", 1)
    assert "backfilled_history" not in state["provenance"]
    assert {c["phase"] for c in state["checkpoints"]} == {"display"}
    assert "backfill_complete" not in state["aliases"]


def test_the_two_modes_share_no_history(tmp_path, v1_settings):
    """Same recording, two accounts: the new user's first forecast sees none of the
    returning user's completed meals."""
    h = make(tmp_path, v1_settings)
    new_headers = login(h, "new_user")
    state = h.client.post("/api/v2/demo/replay/advance", headers=new_headers).json()
    assert state["checkpoints"][0]["result"]["admissible_prior_meals"] == 0
    assert len(h.client.get("/api/v2/meals", headers=new_headers, params={"limit": 200}).json()["meals"]) == 1
    returning_meals = h.client.get("/api/v2/meals", headers=login(h, "returning_user"),
                                   params={"limit": 200}).json()["meals"]
    assert len(returning_meals) == FIRST_DISPLAY - 1
    ids = replay._demo_user_id(h.ctx, "new_user"), replay._demo_user_id(h.ctx, "returning_user")
    with h.ctx.engine.connect() as conn:
        bindings = conn.execute(select(t.participant_bindings.c.participant_id).where(
            t.participant_bindings.c.user_id.in_(ids))).scalars().all()
    assert len(set(bindings)) == 2 and all(b.startswith("gtp_") for b in bindings)


@pytest.mark.parametrize("reset_mode,other", [("new_user", "returning_user"), ("returning_user", "new_user")])
def test_resetting_one_mode_never_touches_the_other_or_an_ordinary_account(tmp_path, v1_settings, reset_mode, other):
    h = make(tmp_path, v1_settings)
    ordinary = h.register("ordinary@example.com")
    h.push_cgm(ordinary, BASE_NOW - dt.timedelta(hours=3), BASE_NOW, source="manual_entry")
    forecast = h.predict(ordinary, h.meal(ordinary, BASE_NOW)).json()

    for mode in ("new_user", "returning_user"):
        h.client.post("/api/v2/demo/replay/advance", headers=login(h, mode))
    other_headers = login(h, other)
    other_before, other_user = digest(h, other), replay._demo_user_id(h.ctx, other)
    initial = digest(make(tmp_path / "fresh", v1_settings, modes=(reset_mode,)), reset_mode)

    # Through the API, as that mode's own account.
    response = h.client.post("/api/v2/demo/replay/reset", headers=login(h, reset_mode))
    assert response.status_code == 200 and response.json()["mode"] == reset_mode

    assert digest(h, reset_mode) == initial                                 # back to its own start
    assert replay._demo_user_id(h.ctx, other) == other_user                 # same account, not re-created
    assert digest(h, other) == other_before                                 # byte-for-byte untouched
    assert h.client.get("/api/v2/me", headers=other_headers).status_code == 200   # its session survives
    assert h.client.get(f"/api/v2/predictions/{forecast['prediction_id']}", headers=ordinary).json()["forecast"] \
        == forecast["forecast"]
    with h.ctx.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(t.users)).scalar() == 3


def test_each_mode_is_reproducible_on_its_own(tmp_path, v1_settings):
    runs = []
    for name in ("one", "two"):
        h = make(tmp_path / name, v1_settings, modes=("returning_user",))
        start = digest(h, "returning_user")
        replay.advance(h.v2_app, h.ctx, replay._demo_user_id(h.ctx, "returning_user"), to="end")
        runs.append((start, digest(h, "returning_user")))
    assert runs[0] == runs[1]
    replay.reset(h.v2_app, h.ctx, "returning_user")
    assert digest(h, "returning_user") == runs[0][0]


def test_modes_are_named_and_unknown_or_unavailable_ones_fail_clearly(tmp_path, v1_settings):
    h = build_harness(tmp_path, v1_settings, demo_mode=True, demo_password=DEMO_PASSWORD,
                      demo_returning_bundle_path=tmp_path / "absent.json")
    h.ctx.demo_bundle = {"new_user": fixture.fixture_bundle()}     # returning bundle not installed
    assert sorted(source.MODES) == ["new_user", "returning_user"]
    assert profile.DISPLAY_NAMES["returning_user"] == "Returning Demo User (fictional)"
    assert replay.demo_email(h.ctx, "new_user") != replay.demo_email(h.ctx, "returning_user")

    with pytest.raises(core.V2Error) as unknown:
        replay.initialize(h.v2_app, h.ctx, "power_user")
    assert unknown.value.code == "unknown_demo_scenario"
    with pytest.raises(core.V2Error) as missing:
        replay.initialize(h.v2_app, h.ctx, "returning_user")
    assert missing.value.code == "demo_data_unavailable"
    assert "demo_select --scenario returning_user" in missing.value.message
    # The default demonstration is unaffected by the optional one being absent.
    assert replay.initialize(h.v2_app, h.ctx)["mode"] == "new_user"
    with h.ctx.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(t.users)).scalar() == 1


def test_returning_user_backfill_obeys_the_same_strict_eligibility(tmp_path, v1_settings):
    h = make(tmp_path, v1_settings, modes=("returning_user",))
    headers = login(h, "returning_user")
    state = h.client.get("/api/v2/demo/replay", headers=headers).json()
    outcomes = {c["meal"]: c["result"] for c in state["checkpoints"]
                if c["kind"] == "outcome_reveal" and c["done"]}
    assert outcomes[2]["eligible_for_personalization"] is False
    assert outcomes[2]["ineligibility_reasons"] == ["intervening_meal"]
    rows = h.client.get("/api/v2/predictions", headers=headers, params={"limit": 50}).json()["predictions"]
    assert all(p["personalization"]["policy"] == "history_only"
               and p["personalization"]["calibration_applied"] is False
               and p["personalization"]["retrieval_active"] == (p["personalization"]["admissible_prior_meals"] >= 5)
               for p in rows)
