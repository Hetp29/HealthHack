"""Simulated connected-health sources in the demonstration accounts."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.demo import fixture, replay, sim_devices
from backend.app.v2.services import wearables
from tests.v2.conftest import BASE_NOW, build_harness

DEMO_PASSWORD = "demo-account-password"
ALL = ("apple_health", "apple_watch", "fitbit", "whoop")


def make(tmp_path, v1_settings, modes=("new_user",)):
    h = build_harness(tmp_path, v1_settings, demo_mode=True, demo_password=DEMO_PASSWORD)
    h.ctx.demo_bundle = {"new_user": fixture.fixture_bundle(),
                         "returning_user": fixture.fixture_bundle(display_from_meal=8)}
    for mode in modes:
        replay.initialize(h.v2_app, h.ctx, mode)
    return h


@pytest.fixture()
def demo(tmp_path, v1_settings):
    return make(tmp_path, v1_settings)


def login(h, mode="new_user") -> dict:
    token = h.client.post("/api/v2/auth/login", json={
        "email": replay.demo_email(h.ctx, mode), "password": DEMO_PASSWORD}).json()["session"]["token"]
    return {"Authorization": f"Bearer {token}"}


def activate(h, headers, profile):
    response = h.client.post(f"/api/v2/demo/devices/{profile}/activate", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def observations(h, headers, **params):
    params = {"start": "2000-01-01T00:00:00Z", "end": "2100-01-01T00:00:00Z", "limit": 5000, **params}
    return h.client.get("/api/v2/wearables/observations", headers=headers, params=params).json()["observations"]


def position(h, headers) -> str:
    return h.client.get("/api/v2/demo/replay", headers=headers).json()["position"]


# --------------------------------------------------------------------------- #
# access
# --------------------------------------------------------------------------- #


def test_simulation_routes_are_demo_only(harness, demo):
    ordinary_off = harness.register()
    for method, path in (("GET", "/api/v2/demo/devices"), ("POST", "/api/v2/demo/devices/fitbit/activate"),
                         ("GET", "/api/v2/demo/health-timeline")):
        assert harness.client.request(method, path, headers=ordinary_off).status_code == 404   # demo mode off
        assert demo.client.request(method, path).status_code == 401                            # no session
    ordinary = demo.register("ordinary@example.com")
    for method, path in (("GET", "/api/v2/demo/devices"), ("POST", "/api/v2/demo/devices/fitbit/activate"),
                         ("GET", "/api/v2/demo/health-timeline")):
        response = demo.client.request(method, path, headers=ordinary)
        assert response.status_code == 403 and response.json()["code"] == "not_demo_account"
    # The refused activation left the ordinary account with nothing simulated.
    assert demo.client.get("/api/v2/devices", headers=ordinary).json()["connections"] == []
    assert observations(demo, ordinary) == []


def test_everything_starts_inactive_and_the_glucose_source_is_not_called_simulated_research(demo):
    headers = login(demo)
    listing = demo.client.get("/api/v2/demo/devices", headers=headers).json()
    assert [p["profile"] for p in listing["profiles"]] == list(ALL)
    assert all(p["status"] == "inactive" and p["simulated"] is True and p["data_class"] == "demo"
               for p in listing["profiles"])
    assert all(m["observations_visible"] == 0 for p in listing["profiles"] for m in p["metrics"])
    kinds = {p["profile"]: p["kind"] for p in listing["profiles"]}
    assert kinds["apple_health"] == "health_data_platform"          # an aggregator, not a sensor
    assert kinds["apple_watch"] == kinds["fitbit"] == kinds["whoop"] == "wearable_device"
    whoop = next(p for p in listing["profiles"] if p["profile"] == "whoop")
    assert "heart_rate" not in [m["metric"] for m in whoop["metrics"]]
    assert "does not expose a continuous" in whoop["notes"]["heart_rate"]
    assert "No Apple, Fitbit or WHOOP account is connected" in listing["disclaimer"]
    assert listing["used_by_forecast_model"]["simulated_profiles"] is False
    # This fixture's glucose is generated, and is labelled so; it is never "research".
    assert listing["glucose_source"]["data_class"] == "demo" and listing["glucose_source"]["kind"] == "historical_replay"
    assert observations(demo, headers) == []


# --------------------------------------------------------------------------- #
# activation, provenance, no fake authorization
# --------------------------------------------------------------------------- #


def test_activation_shows_history_with_explicit_simulated_provenance(demo):
    headers = login(demo)
    result = activate(demo, headers, "apple_watch")
    assert result["already_active"] is False and result["observations_released"] > 0
    profile = next(p for p in result["profiles"] if p["profile"] == "apple_watch")
    assert profile["status"] == "demo_simulated" and profile["display_label"] == "Apple Watch (simulated sample)"
    assert profile["authorization"].startswith("none")

    rows = observations(demo, headers)
    assert len(rows) == result["observations_released"]
    now = position(demo, headers)
    for row in rows:
        assert row["provider"] == "demo_apple_watch" and row["data_class"] == "demo"
        assert row["detail"]["simulated"] is True and row["detail"]["device_profile"] == "apple_watch"
        assert row["detail"]["simulation_version"] == sim_devices.SIMULATION_VERSION
        assert row["unit"] == wearables.METRIC_UNITS[row["metric"]]
        assert row["used_by_forecast_model"] is False
        assert (row["observed_end_at"] or row["observed_at"]) <= now      # nothing from the future
        assert row["received_at"] == now                                  # ingestion time is the replay position
    assert {r["metric"] for r in rows} <= {"heart_rate", "steps_daily", "workout"}
    assert observations(demo, headers, provider="demo_fitbit") == []
    assert len(observations(demo, headers, provider="demo_apple_watch", metric="heart_rate")) > 50


def test_a_simulated_source_is_never_authorized_connected_or_given_a_token(demo):
    headers = login(demo)
    for profile in ALL:
        activate(demo, headers, profile)
    connections = {c["provider"]: c for c in demo.client.get("/api/v2/devices", headers=headers).json()["connections"]}
    for profile in ALL:
        row = connections[f"demo_{profile}"]
        assert row["status"] == "demo_simulated"
        assert row["authentic_data_received"] is False and row["scopes"] == []
    assert not {c["status"] for c in connections.values()} & {"authorized", "connected"}
    assert "demo_simulated" in demo.client.get("/api/v2/devices", headers=headers).json()["status_meanings"]
    with demo.ctx.engine.connect() as conn:
        stored = conn.execute(select(t.device_connections)).mappings().all()
        assert all(r["access_token_enc"] is None and r["refresh_token_enc"] is None
                   and r["external_user_id"] is None and r["authentic_data_received_at"] is None for r in stored)
        assert conn.execute(select(func.count()).select_from(t.oauth_states)).scalar() == 0   # no OAuth round trip
    # The real-provider machinery does not know these ids at all.
    for provider in ("demo_fitbit", "demo_whoop", "demo_apple_health"):
        assert demo.client.post(f"/api/v2/devices/{provider}/connect", headers=headers).status_code == 404
        assert demo.client.post(f"/api/v2/devices/{provider}/sync", headers=headers).status_code == 404
    # And the real provider ids were not touched: Fitbit and WHOOP are still not configured.
    real = demo.client.post("/api/v2/devices/fitbit/connect", headers=headers)
    assert real.status_code == 503 and real.json()["code"] == "provider_not_configured"
    assert "fitbit" not in connections and "whoop" not in connections


def test_activation_is_idempotent(demo):
    headers = login(demo)
    first = activate(demo, headers, "whoop")
    again = activate(demo, headers, "whoop")
    assert again["already_active"] is True and again["observations_released"] == 0
    assert len(observations(demo, headers)) == first["observations_released"]
    with demo.ctx.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(t.device_connections).where(
            t.device_connections.c.provider == "demo_whoop")).scalar() == 1
    unknown = demo.client.post("/api/v2/demo/devices/garmin/activate", headers=headers)
    assert unknown.status_code == 404 and unknown.json()["code"] == "unknown_demo_device"
    assert unknown.json()["details"]["allowed"] == sorted(ALL)


def test_each_profile_supplies_only_its_own_metrics_in_canonical_units(demo):
    headers = login(demo)
    for profile in ALL:
        activate(demo, headers, profile)
    replay.advance(demo.v2_app, demo.ctx, replay._demo_user_id(demo.ctx), to="end")
    by_provider: dict[str, set] = {}
    for row in observations(demo, headers):
        by_provider.setdefault(row["provider"], set()).add((row["metric"], row["unit"]))
    for profile in ALL:
        expected = {(m, wearables.METRIC_UNITS[m]) for m in sim_devices.PROFILES[profile]["metrics"]}
        assert by_provider[f"demo_{profile}"] == expected, profile
    values = {r["metric"]: [] for r in observations(demo, headers)}
    for row in observations(demo, headers):
        values[row["metric"]].append(row["value"])
    assert 40 <= min(values["heart_rate"]) and max(values["heart_rate"]) <= 190
    assert all(45 <= v <= 80 for v in values["resting_heart_rate"])
    assert all(120 <= v <= 600 for v in values["sleep_duration"])
    assert all(0 <= v <= 100 for v in values["recovery_score"])
    assert all(2000 <= v <= 20000 for v in values["steps_daily"])


# --------------------------------------------------------------------------- #
# chronology
# --------------------------------------------------------------------------- #


def test_simulated_observations_are_released_only_as_replay_time_passes(demo):
    headers = login(demo)
    activate(demo, headers, "fitbit")
    activate(demo, headers, "apple_health")
    seen = len(observations(demo, headers))
    timeline = replay.build_timeline(demo.ctx.demo_bundle["new_user"])
    for event in [e for e in timeline if e["checkpoint"]][:8]:
        demo.client.post("/api/v2/demo/replay/advance", headers=headers, json={"to": event["id"]})
        now = position(demo, headers)
        rows = observations(demo, headers)
        assert all((r["observed_end_at"] or r["observed_at"]) <= now for r in rows)
        assert all(r["received_at"] <= now for r in rows)
        assert len(rows) >= seen
        seen = len(rows)
    with demo.ctx.engine.connect() as conn:
        late = conn.execute(select(func.count()).select_from(t.wearable_observations).where(
            t.wearable_observations.c.received_at < t.wearable_observations.c.observed_at)).scalar()
    assert late == 0                                           # never stored before it "happened"
    # A daily total is not visible until its day has ended.
    steps = observations(demo, headers, metric="steps_daily")
    assert steps and all(r["observed_end_at"] <= position(demo, headers) for r in steps)


def test_generation_is_a_pure_function_within_the_replay_window():
    bundle = fixture.fixture_bundle()
    for profile in ALL:
        first, second = sim_devices.generate(profile, bundle), sim_devices.generate(profile, bundle)
        assert first == second and len(first) > 0
        assert [a for a, _ in first] == sorted(a for a, _ in first)
        for available, obs in first:
            assert bundle.cgm[0][0] <= obs.observed_at <= bundle.end      # inside the observation period
            assert obs.observed_at <= available <= bundle.end
            assert obs.source_record_id.startswith(f"sim:{profile}:")
            if obs.metric in ("sleep_duration", "workout"):
                assert not any(obs.observed_at <= m.occurred_at <= obs.observed_end_at for m in bundle.meals)
    ids = [o.source_record_id for p in ALL for _, o in sim_devices.generate(p, bundle)]
    assert len(ids) == len(set(ids))
    # Apple Health aggregates the watch: same daily steps, never a contradiction.
    watch = {o.detail["local_date"]: o.value for _, o in sim_devices.generate("apple_watch", bundle) if o.metric == "steps_daily"}
    health = {o.detail["local_date"]: o.value for _, o in sim_devices.generate("apple_health", bundle) if o.metric == "steps_daily"}
    assert watch == health


# --------------------------------------------------------------------------- #
# the forecast is untouched
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["new_user", "returning_user"])
def test_forecasts_outcomes_and_glucose_are_byte_identical_with_devices_on(tmp_path, v1_settings, mode):
    fingerprints = []
    for name, devices_on in (("plain", False), ("devices", True)):
        h = make(tmp_path / name, v1_settings, modes=(mode,))
        user_id = replay._demo_user_id(h.ctx, mode)
        if devices_on:
            headers = login(h, mode)
            for profile in ALL:
                activate(h, headers, profile)
        replay.advance(h.v2_app, h.ctx, user_id, to="end")
        fingerprints.append(replay.digest(h.ctx, user_id))
        if devices_on:
            assert len(observations(h, login(h, mode))) > 500
            with h.ctx.engine.connect() as conn:
                features = conn.execute(select(t.feature_snapshots.c.feature_names,
                                               t.feature_snapshots.c.feature_values)).all()
            for names, values in features:      # the model's wearable-shaped input stayed empty
                assert dict(zip(names, values))["hr_mean_30min_bpm"] is None
                assert dict(zip(names, values))["medication_exposure_recent"] == 0.0
    assert fingerprints[0]["digest"] == fingerprints[1]["digest"]
    assert fingerprints[0]["sections"] == fingerprints[1]["sections"]


# --------------------------------------------------------------------------- #
# reset, determinism, isolation
# --------------------------------------------------------------------------- #


def test_reset_returns_devices_to_inactive_and_the_same_steps_give_the_same_data(demo):
    def journey(headers):
        activate(demo, headers, "apple_health")
        demo.client.post("/api/v2/demo/replay/advance", headers=headers, json={"to": "meal_02_forecast"})
        activate(demo, headers, "whoop")
        demo.client.post("/api/v2/demo/replay/advance", headers=headers, json={"to": "meal_04_outcome"})
        return sim_devices.digest(demo.ctx, replay._demo_user_id(demo.ctx))

    headers = login(demo)
    first = journey(headers)
    demo.client.post("/api/v2/demo/replay/reset", headers=headers)
    headers = login(demo)
    listing = demo.client.get("/api/v2/demo/devices", headers=headers).json()
    assert all(p["status"] == "inactive" for p in listing["profiles"])
    assert observations(demo, headers) == []
    assert [c["provider"] for c in demo.client.get("/api/v2/devices", headers=headers).json()["connections"]] == ["demo"]
    assert journey(headers) == first


def test_simulation_is_per_account_and_never_reaches_another_one(tmp_path, v1_settings):
    h = make(tmp_path, v1_settings, modes=("new_user", "returning_user"))
    ordinary = h.register("real@example.com")
    h.client.post("/api/v2/wearables/observations", headers=ordinary, json={
        "source": "healthkit_bridge", "observations": [
            {"metric": "heart_rate", "value": 70, "unit": "bpm",
             "observed_at": (BASE_NOW - dt.timedelta(minutes=5)).isoformat(), "source_record_id": "real-1"}]})
    new_headers, returning_headers = login(h, "new_user"), login(h, "returning_user")
    for profile in ALL:
        activate(h, new_headers, profile)

    returning = h.client.get("/api/v2/demo/devices", headers=returning_headers).json()
    assert all(p["status"] == "inactive" for p in returning["profiles"])
    assert observations(h, returning_headers) == []
    mine = observations(h, ordinary)
    assert [(r["provider"], r["data_class"]) for r in mine] == [("healthkit_bridge", "user_device")]
    assert all(r["data_class"] == "demo" for r in observations(h, new_headers))
    with h.ctx.engine.connect() as conn:
        owners = conn.execute(select(func.count(func.distinct(t.wearable_observations.c.user_id))).where(
            t.wearable_observations.c.provider.like("demo\\_%", escape="\\"))).scalar()
    assert owners == 1


def test_health_timeline_is_ordered_tagged_and_ends_at_the_replay_position(demo):
    headers = login(demo)
    activate(demo, headers, "apple_health")
    activate(demo, headers, "whoop")
    demo.client.post("/api/v2/demo/replay/advance", headers=headers, json={"to": "meal_05_outcome"})
    body = demo.client.get("/api/v2/demo/health-timeline", headers=headers, params={"hours": 48}).json()
    assert body["end"] == body["position"] == position(demo, headers)
    stamps = [item["at"] for item in body["items"]]
    assert stamps == sorted(stamps) and all(stamp <= body["position"] for stamp in stamps)
    types = {item["type"] for item in body["items"]}
    assert {"wearable", "meal", "medication_dose"} <= types
    for item in body["items"]:
        assert {"at", "type", "data_class", "simulated", "source"} <= set(item)
        if item["type"] == "wearable":
            assert item["simulated"] is True and item["data_class"] == "demo"
            assert item["metric"] != "heart_rate" and item["used_by_forecast_model"] is False
            assert item["source"] in ("Apple Health", "WHOOP")
        if item["type"] == "medication_dose":
            assert item["data_class"] == "fictional" and item["used_by_forecast_model"] is False
        if item["type"] == "meal":
            assert item["simulated"] is False and item["used_by_forecast_model"] is True
    assert "not inputs to the glucose forecast" in body["disclaimer"]


def test_a_non_demo_deployment_cannot_produce_the_simulated_status(harness):
    """With demo mode off there is no route that sets `demo_simulated`."""
    headers = harness.register()
    spec = harness.client.get("/api/v2/openapi.json").json()
    assert not [p for p in spec["paths"] if p.startswith("/demo")]
    with pytest.raises(core.V2Error) as caught:
        replay.activate_device(harness.ctx, "usr_anyone", "fitbit")
    assert caught.value.code == "demo_mode_disabled"
    pushed = harness.client.post("/api/v2/wearables/observations", headers=headers, json={
        "source": "demo_fitbit", "observations": [
            {"metric": "heart_rate", "value": 70, "unit": "bpm",
             "observed_at": BASE_NOW.isoformat(), "source_record_id": "x"}]})
    assert pushed.status_code == 422           # the public upload route does not accept these ids
