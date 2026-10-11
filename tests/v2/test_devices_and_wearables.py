"""Provider connections, OAuth token custody, sync, and wearable normalization.

No provider is contacted. Every HTTP exchange goes through `httpx.MockTransport`
returning payloads in the provider's documented shape, which tests our handling of
those shapes -- it is not evidence that any provider integration works live.
"""

from __future__ import annotations

import datetime as dt
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from backend.app.v2.db import tables as t
from backend.app.v2.providers import dexcom, fitbit, whoop
from backend.app.v2.settings import OAuthClient
from tests.v2.conftest import BASE_NOW, UTC, build_harness

KEY = Fernet.generate_key().decode()
CONFIGURED = dict(
    token_encryption_key=KEY, public_api_base_url="https://api.example.test",
    dexcom=OAuthClient("dex-id", "dex-secret"), dexcom_environment="production",
    fitbit=OAuthClient("fit-id", "fit-secret"), whoop=OAuthClient("whoop-id", "whoop-secret"),
)


class Provider:
    """A scripted provider. Records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_responses: list[tuple[int, dict]] = []
        self.api: dict[str, tuple[int, dict, dict]] = {}
        self.access_tokens_seen: list[str] = []

    def token(self, status: int = 200, **payload) -> None:
        self.token_responses.append((status, payload))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/token"):
            status, payload = self.token_responses.pop(0)
            return httpx.Response(status, json=payload)
        if path.endswith("/revoke") or path.endswith("/user/access"):
            return httpx.Response(204)
        self.access_tokens_seen.append(request.headers.get("authorization", ""))
        for suffix, (status, payload, headers) in self.api.items():
            if suffix in path:
                return httpx.Response(status, json=payload, headers=headers)
        return httpx.Response(200, json={})

    def on(self, suffix: str, payload: dict, status: int = 200, headers: dict | None = None) -> None:
        self.api[suffix] = (status, payload, headers or {})


@pytest.fixture()
def connected(tmp_path, v1_settings):
    h = build_harness(tmp_path, v1_settings, **CONFIGURED)
    provider = Provider()
    h.ctx.http_client_factory = lambda: httpx.Client(transport=httpx.MockTransport(provider.handler))
    return h, provider


def authorize(h, provider, headers, name, **token):
    start = h.client.post(f"/api/v2/devices/{name}/connect", headers=headers).json()
    state = parse_qs(urlparse(start["authorization_url"]).query)["state"][0]
    provider.token(**{"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600, **token})
    callback = h.client.get(f"/api/v2/devices/{name}/callback", params={"state": state, "code": "auth-code"})
    return start, state, callback


def connection(h, headers, name):
    rows = h.client.get("/api/v2/devices", headers=headers).json()["connections"]
    return next(r for r in rows if r["provider"] == name)


def egvs(*values):
    return {"recordType": "egv", "userId": "dexcom-user-1", "records": [
        {"recordId": f"rec-{i}", "systemTime": (BASE_NOW - dt.timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "displayTime": "2026-09-20T12:00:00-04:00", "transmitterId": "tx-9", "value": v,
         "unit": "mg/dL", "status": None, "trend": "flat"} for i, v in enumerate(values)]}


# --------------------------------------------------------------------------- #
# configuration and capability honesty
# --------------------------------------------------------------------------- #


def test_an_unconfigured_provider_cannot_be_connected_and_says_what_is_missing(harness):
    headers = harness.register()
    response = harness.client.post("/api/v2/devices/fitbit/connect", headers=headers)
    assert response.status_code == 503 and response.json()["code"] == "provider_not_configured"
    assert response.json()["details"]["missing_configuration"] == [
        "FITBIT_CLIENT_ID", "FITBIT_CLIENT_SECRET", "GLIMPSE_TOKEN_ENCRYPTION_KEY",
        "GLIMPSE_PUBLIC_API_BASE_URL"]
    assert harness.client.post("/api/v2/devices/garmin/connect", headers=headers).status_code == 404
    assert harness.client.get("/api/v2/devices", headers=headers).json()["connections"] == []


def test_capability_matrix_states_limits_and_that_nothing_is_live_verified(harness):
    matrix = {p["provider"]: p for p in harness.client.get("/api/v2/devices", headers=harness.register()).json()["providers"]}
    assert matrix["healthkit_bridge"]["integration"] == "native_bridge"
    assert matrix["healthkit_bridge"]["configured"] is False
    assert "cannot be read from a web browser" in matrix["healthkit_bridge"]["not_available"]["browser_access"]
    assert "heart_rate_continuous" in matrix["whoop"]["not_available"]
    assert "heart_rate_intraday" in matrix["fitbit"]["not_available"]
    assert "real_time_glucose" in matrix["dexcom_api"]["not_available"]
    assert "provider_api" in matrix["libreview_csv"]["not_available"]
    for name in ("dexcom_api", "fitbit", "whoop"):
        assert matrix[name]["configured"] is False
        assert matrix[name]["verification"] == "mock_tested_never_run_against_provider"


# --------------------------------------------------------------------------- #
# OAuth
# --------------------------------------------------------------------------- #


def test_oauth_round_trip_ends_authorized_not_connected(connected):
    h, provider = connected
    headers = h.register()
    start, state, callback = authorize(h, provider, headers, "fitbit", scope="activity heartrate sleep profile")

    query = parse_qs(urlparse(start["authorization_url"]).query)
    assert query["client_id"] == ["fit-id"] and query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["https://api.example.test/api/v2/devices/fitbit/callback"]
    assert query["code_challenge_method"] == ["S256"] and len(query["code_challenge"][0]) == 43
    assert "client_secret" not in start["authorization_url"]

    assert callback.status_code == 200 and callback.json()["status"] == "authorized"
    exchange = provider.requests[0]
    form = parse_qs(exchange.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["auth-code"]
    assert "code_verifier" in form and exchange.headers["authorization"].startswith("Basic ")

    row = connection(h, headers, "fitbit")
    # Linked, but not one observation received: this is not a data connection yet.
    assert row["status"] == "authorized" and row["authentic_data_received"] is False


def test_tokens_are_encrypted_at_rest_and_never_returned(connected):
    h, provider = connected
    headers = h.register()
    authorize(h, provider, headers, "whoop", access_token="very-secret-access", refresh_token="very-secret-refresh")
    with h.ctx.engine.connect() as conn:
        row = conn.execute(select(t.device_connections)).mappings().one()
    assert "very-secret" not in (row["access_token_enc"] + row["refresh_token_enc"])
    assert Fernet(KEY.encode()).decrypt(row["access_token_enc"].encode()) == b"very-secret-access"
    for path in ("/api/v2/devices", "/api/v2/status"):
        text = h.client.get(path, headers=headers).text
        assert "very-secret" not in text and "token_enc" not in text and "whoop-secret" not in text


def test_the_oauth_state_is_single_use_expiring_and_bound_to_its_provider(connected):
    h, provider = connected
    headers = h.register()
    _, state, first = authorize(h, provider, headers, "fitbit")
    assert first.status_code == 200
    reuse = h.client.get("/api/v2/devices/fitbit/callback", params={"state": state, "code": "again"})
    assert reuse.status_code == 400 and reuse.json()["code"] == "invalid_oauth_state"

    start = h.client.post("/api/v2/devices/whoop/connect", headers=headers).json()
    whoop_state = parse_qs(urlparse(start["authorization_url"]).query)["state"][0]
    wrong_provider = h.client.get("/api/v2/devices/fitbit/callback", params={"state": whoop_state, "code": "c"})
    assert wrong_provider.status_code == 400
    h.clock.advance(minutes=11)
    expired = h.client.get("/api/v2/devices/whoop/callback", params={"state": whoop_state, "code": "c"})
    assert expired.status_code == 400
    forged = h.client.get("/api/v2/devices/whoop/callback", params={"state": "forged", "code": "c"})
    assert forged.status_code == 400
    with h.ctx.engine.connect() as conn:      # the state itself is stored only as a hash
        assert state not in [r[0] for r in conn.execute(select(t.oauth_states.c.state_hash))]


def test_scope_denial_and_token_exchange_failure_are_recorded_states(connected):
    h, provider = connected
    headers = h.register()
    start = h.client.post("/api/v2/devices/fitbit/connect", headers=headers).json()
    state = parse_qs(urlparse(start["authorization_url"]).query)["state"][0]
    denied = h.client.get("/api/v2/devices/fitbit/callback", params={"state": state, "error": "access_denied"})
    assert denied.json() == {"provider": "fitbit", "status": "error", "error_code": "authorization_denied"}
    assert connection(h, headers, "fitbit")["last_error_code"] == "authorization_denied"

    start = h.client.post("/api/v2/devices/fitbit/connect", headers=headers).json()
    state = parse_qs(urlparse(start["authorization_url"]).query)["state"][0]
    provider.token(status=500, error="server_error")
    failed = h.client.get("/api/v2/devices/fitbit/callback", params={"state": state, "code": "c"})
    assert failed.json()["error_code"] == "token_exchange_failed"

    partial = authorize(h, provider, headers, "fitbit", scope="activity profile")[2].json()
    assert partial["status"] == "authorized" and partial["scopes_not_granted"] == ["heartrate", "sleep"]


# --------------------------------------------------------------------------- #
# sync
# --------------------------------------------------------------------------- #


def test_dexcom_sync_stores_readings_and_only_then_reports_connected(connected):
    h, provider = connected
    headers = h.register()
    authorize(h, provider, headers, "dexcom_api")
    provider.on("/v3/users/self/egvs", egvs(118, 121, 125))
    result = h.client.post("/api/v2/devices/dexcom_api/sync", headers=headers).json()
    assert result["outcome"] == "ok" and result["summary"]["glucose"]["inserted"] == 3
    assert result["connection"]["status"] == "connected"
    assert result["connection"]["authentic_data_received"] is True
    stored = h.client.get("/api/v2/cgm/readings", headers=headers).json()["readings"]
    assert [r["glucose_mg_dl"] for r in stored] == [125, 121, 118]
    assert {r["provider"] for r in stored} == {"dexcom_api"} and stored[0]["device_id"] == "tx-9"

    replay = h.client.post("/api/v2/devices/dexcom_api/sync", headers=headers).json()
    assert replay["summary"]["glucose"] == {**replay["summary"]["glucose"], "inserted": 0, "duplicates": 3}


def test_dexcom_sandbox_data_is_demo_and_never_marks_the_account_connected(tmp_path, v1_settings):
    h = build_harness(tmp_path, v1_settings, **{**CONFIGURED, "dexcom_environment": "sandbox"})
    provider = Provider()
    h.ctx.http_client_factory = lambda: httpx.Client(transport=httpx.MockTransport(provider.handler))
    headers = h.register()
    start, _, _ = authorize(h, provider, headers, "dexcom_api")
    assert start["authorization_url"].startswith("https://sandbox-api.dexcom.com/")
    provider.on("/v3/users/self/egvs", egvs(110, 112))
    result = h.client.post("/api/v2/devices/dexcom_api/sync", headers=headers).json()
    assert result["data_class"] == "demo"
    assert result["connection"]["status"] == "authorized"
    assert result["connection"]["authentic_data_received"] is False
    assert {r["data_class"] for r in h.client.get("/api/v2/cgm/readings", headers=headers).json()["readings"]} == {"demo"}


def test_an_empty_sync_does_not_claim_a_connection(connected):
    h, provider = connected
    headers = h.register()
    authorize(h, provider, headers, "dexcom_api")
    provider.on("/v3/users/self/egvs", {"records": []})
    result = h.client.post("/api/v2/devices/dexcom_api/sync", headers=headers).json()
    assert result["outcome"] == "ok" and result["summary"] == {}
    assert result["connection"]["status"] == "authorized"


def test_an_expired_access_token_is_refreshed_before_use(connected):
    h, provider = connected
    headers = h.register()
    authorize(h, provider, headers, "dexcom_api", expires_in=600)
    h.clock.advance(minutes=30)
    provider.token(access_token="access-2", refresh_token="refresh-2", expires_in=3600)
    provider.on("/v3/users/self/egvs", {"records": []})
    assert h.client.post("/api/v2/devices/dexcom_api/sync", headers=headers).json()["outcome"] == "ok"

    refresh = next(r for r in provider.requests[1:] if r.url.path.endswith("/token"))
    assert parse_qs(refresh.content.decode())["refresh_token"] == ["refresh-1"]
    assert provider.access_tokens_seen == ["Bearer access-2"]
    with h.ctx.engine.connect() as conn:
        row = conn.execute(select(t.device_connections)).mappings().one()
    assert Fernet(KEY.encode()).decrypt(row["refresh_token_enc"].encode()) == b"refresh-2"   # rotated


def test_a_rejected_token_is_refreshed_once_and_retried(connected):
    h, provider = connected
    headers = h.register()
    authorize(h, provider, headers, "dexcom_api")
    calls = {"n": 0}
    original = provider.handler

    def flaky(request):
        if "/egvs" in request.url.path:
            calls["n"] += 1
            if calls["n"] == 1:
                provider.requests.append(request)
                return httpx.Response(401, json={})
        return original(request)

    h.ctx.http_client_factory = lambda: httpx.Client(transport=httpx.MockTransport(flaky))
    provider.token(access_token="access-2", expires_in=3600)       # provider omits the refresh token
    provider.on("/v3/users/self/egvs", egvs(101))
    result = h.client.post("/api/v2/devices/dexcom_api/sync", headers=headers).json()
    assert result["outcome"] == "ok" and calls["n"] == 2
    with h.ctx.engine.connect() as conn:
        row = conn.execute(select(t.device_connections)).mappings().one()
    assert Fernet(KEY.encode()).decrypt(row["refresh_token_enc"].encode()) == b"refresh-1"   # kept


def test_a_revoked_grant_clears_tokens_and_is_reported(connected):
    h, provider = connected
    headers = h.register()
    authorize(h, provider, headers, "whoop", expires_in=60)
    h.clock.advance(minutes=5)
    provider.token(status=400, error="invalid_grant")
    result = h.client.post("/api/v2/devices/whoop/sync", headers=headers)
    assert result.status_code == 200
    assert result.json()["outcome"] == "authorization_revoked" and result.json()["synced"] is False
    row = connection(h, headers, "whoop")
    assert row["status"] == "revoked" and row["last_error_code"] == "authorization_revoked"
    with h.ctx.engine.connect() as conn:
        stored = conn.execute(select(t.device_connections)).mappings().one()
    assert stored["access_token_enc"] is None and stored["refresh_token_enc"] is None
    again = h.client.post("/api/v2/devices/whoop/sync", headers=headers)
    assert again.status_code == 409 and again.json()["code"] == "authorization_revoked"


def test_rate_limits_and_provider_errors_are_recorded_and_recoverable(connected):
    h, provider = connected
    headers = h.register()
    authorize(h, provider, headers, "dexcom_api")
    provider.on("/v3/users/self/egvs", {}, status=429, headers={"Retry-After": "120"})
    limited = h.client.post("/api/v2/devices/dexcom_api/sync", headers=headers).json()
    assert limited["outcome"] == "rate_limited" and limited["retry_after_seconds"] == 120
    assert connection(h, headers, "dexcom_api")["last_error_code"] == "rate_limited"

    provider.on("/v3/users/self/egvs", {}, status=502)
    assert h.client.post("/api/v2/devices/dexcom_api/sync", headers=headers).json()["outcome"] == "provider_error"

    provider.on("/v3/users/self/egvs", egvs(104))
    recovered = h.client.post("/api/v2/devices/dexcom_api/sync", headers=headers).json()
    assert recovered["outcome"] == "ok" and recovered["connection"]["last_error_code"] is None
    assert recovered["connection"]["status"] == "connected"


def test_sync_requires_an_authorized_connection(connected):
    h, _ = connected
    response = h.client.post("/api/v2/devices/fitbit/sync", headers=h.register())
    assert response.status_code == 409 and response.json()["code"] == "not_authorized"


def test_disconnect_destroys_tokens_and_revokes_upstream_where_supported(connected):
    h, provider = connected
    headers = h.register()
    authorize(h, provider, headers, "fitbit")
    result = h.client.delete("/api/v2/devices/fitbit", headers=headers).json()
    assert result == {"provider": "fitbit", "status": "disconnected", "revoked_upstream": True,
                      "upstream_revocation_supported": True, "stored_data_retained": True}
    assert provider.requests[-1].url.path.endswith("/oauth2/revoke")
    with h.ctx.engine.connect() as conn:
        stored = conn.execute(select(t.device_connections)).mappings().one()
    assert stored["status"] == "disconnected" and stored["access_token_enc"] is None

    authorize(h, provider, headers, "dexcom_api")
    dex = h.client.delete("/api/v2/devices/dexcom_api", headers=headers).json()
    assert dex["revoked_upstream"] is False and dex["upstream_revocation_supported"] is False
    assert h.client.delete("/api/v2/devices/whoop", headers=headers).status_code == 404


def test_the_sync_worker_isolates_failures_between_connections(connected):
    from workers import device_sync

    h, provider = connected
    good, bad = h.register("good@example.com"), h.register("bad@example.com")
    authorize(h, provider, good, "dexcom_api")
    authorize(h, provider, bad, "whoop", expires_in=1)
    h.clock.advance(minutes=5)
    provider.token(status=400, error="invalid_grant")                 # consumed by the whoop refresh
    provider.on("/v3/users/self/egvs", egvs(99))
    tally = device_sync.run_once(h.ctx)
    assert tally == {"ok": 1, "authorization_revoked": 1} or tally == {"authorization_revoked": 1, "ok": 1}
    assert connection(h, good, "dexcom_api")["status"] == "connected"
    assert connection(h, bad, "whoop")["status"] == "revoked"


# --------------------------------------------------------------------------- #
# normalization (pure functions over documented payload shapes)
# --------------------------------------------------------------------------- #


def test_dexcom_range_markers_and_missing_values_are_rejected_not_invented():
    payload = egvs(120, 39, 401)
    payload["records"][1].update(status="low", value=None)
    payload["records"][2]["status"] = "high"
    payload["records"].append({"recordId": "bad", "systemTime": "2026-09-20T10:00:00", "value": 100, "unit": "mg/dL"})
    readings = dexcom.normalize_egvs(payload)
    assert [r.reject_reason for r in readings] == [None, "sensor_range_marker", "sensor_range_marker", None]
    assert readings[0].observed_at == BASE_NOW and readings[0].source_record_id == "rec-0"
    assert readings[3].observed_at is None          # naive systemTime is not assumed to be UTC


def test_fitbit_local_times_are_placed_with_the_profile_timezone():
    tz = "America/Los_Angeles"
    steps = fitbit.normalize_daily_activity({"summary": {"steps": 8421}}, "2026-09-19", tz)[0]
    assert (steps.metric, steps.value, steps.unit) == ("steps_daily", 8421.0, "count")
    assert steps.observed_at == dt.datetime(2026, 9, 19, 7, 0, tzinfo=UTC)          # local midnight, PDT
    assert steps.observed_end_at - steps.observed_at == dt.timedelta(days=1)

    sleep = fitbit.normalize_sleep({"sleep": [
        {"logId": 42, "startTime": "2026-09-18T23:10:00.000", "endTime": "2026-09-19T06:40:00.000",
         "minutesAsleep": 401, "efficiency": 91, "isMainSleep": True},
        {"logId": None, "minutesAsleep": 30}]}, tz)
    assert len(sleep) == 1 and sleep[0].value == 401.0 and sleep[0].source_record_id == "sleep:42"
    assert sleep[0].observed_at == dt.datetime(2026, 9, 19, 6, 10, tzinfo=UTC)

    rhr = fitbit.normalize_resting_heart_rate({"activities-heart": [
        {"dateTime": "2026-09-19", "value": {"restingHeartRate": 58}},
        {"dateTime": "2026-09-20", "value": {}}]}, tz)
    assert [(o.metric, o.value) for o in rhr] == [("resting_heart_rate", 58.0)]
    hrv = fitbit.normalize_hrv({"hrv": [{"dateTime": "2026-09-19", "value": {"dailyRmssd": 41.5}}]}, tz)
    assert (hrv[0].metric, hrv[0].unit, hrv[0].value) == ("hrv_rmssd", "ms", 41.5)


def test_whoop_only_scored_records_are_used():
    recovery = whoop.normalize_recovery({"records": [
        {"cycle_id": 7, "created_at": "2026-09-20T11:00:00.000Z", "score_state": "SCORED",
         "score": {"recovery_score": 64, "resting_heart_rate": 52, "hrv_rmssd_milli": 48.2}},
        {"cycle_id": 8, "created_at": "2026-09-21T11:00:00.000Z", "score_state": "PENDING_SCORE", "score": None}]})
    assert {(o.metric, o.value, o.unit) for o in recovery} == {
        ("recovery_score", 64.0, "percent"), ("resting_heart_rate", 52.0, "bpm"), ("hrv_rmssd", 48.2, "ms")}
    assert len({o.source_record_id for o in recovery}) == 3

    sleep = whoop.normalize_sleep({"records": [{
        "id": "s-1", "start": "2026-09-19T03:00:00.000Z", "end": "2026-09-19T10:30:00.000Z", "nap": False,
        "score_state": "SCORED", "score": {"stage_summary": {
            "total_in_bed_time_milli": 27_000_000, "total_awake_time_milli": 1_800_000},
            "sleep_efficiency_percentage": 93.3}}]})
    assert sleep[0].value == 420.0 and sleep[0].unit == "min"
    workout = whoop.normalize_workouts({"records": [{
        "id": "w-1", "start": "2026-09-19T15:00:00.000Z", "end": "2026-09-19T15:45:00.000Z",
        "sport_name": "running", "score_state": "SCORED", "score": {"strain": 11.2, "average_heart_rate": 151}}]})
    assert workout[0].value == 45.0 and workout[0].detail["average_heart_rate_bpm"] == 151


def test_fitbit_sync_collects_daily_metrics_and_skips_a_declined_scope(connected):
    h, provider = connected
    headers = h.register()
    authorize(h, provider, headers, "fitbit")
    provider.on("/profile.json", {"user": {"encodedId": "FB1", "timezone": "America/New_York"}})
    provider.on("/activities/date/", {"summary": {"steps": 7000}})
    provider.on("/activities/heart/date/", {"activities-heart": [
        {"dateTime": "2026-09-20", "value": {"restingHeartRate": 60}}]})
    provider.on("/sleep/date/", {}, status=403)                 # the user declined the sleep scope
    provider.on("/hrv/date/", {"hrv": []})
    result = h.client.post("/api/v2/devices/fitbit/sync", headers=headers).json()
    assert result["outcome"] == "ok"
    assert "sleep_duration" in result["unavailable_metrics"]
    assert result["summary"]["wearable"]["inserted"] > 0
    assert result["connection"]["status"] == "connected"
    stored = h.client.get("/api/v2/wearables/observations", headers=headers).json()
    assert {o["metric"] for o in stored["observations"]} == {"steps_daily", "resting_heart_rate"}
    assert all(o["used_by_forecast_model"] is False for o in stored["observations"])


# --------------------------------------------------------------------------- #
# native bridge upload (Apple Health)
# --------------------------------------------------------------------------- #


def sample(metric, value, unit, minutes_ago, record_id, **extra):
    return {"metric": metric, "value": value, "unit": unit, "source_record_id": record_id,
            "observed_at": (BASE_NOW - dt.timedelta(minutes=minutes_ago)).isoformat(), **extra}


def test_bridge_upload_is_idempotent_and_unit_checked(harness):
    headers = harness.register()
    body = {"source": "healthkit_bridge", "observations": [
        sample("heart_rate", 72, "bpm", 10, "hk-uuid-1"),
        sample("steps_daily", 5400, "count", 600, "hk-steps-2026-09-20"),
        sample("heart_rate", 72, "count/min", 9, "hk-uuid-2"),       # wrong unit
        sample("heart_rate", 75, "bpm", -60, "hk-uuid-3"),           # in the future
    ]}
    first = harness.client.post("/api/v2/wearables/observations", headers=headers, json=body).json()
    assert first["counts"]["inserted"] == 2
    assert first["counts"]["rejected_by_reason"] == {"unexpected_unit": 1, "future_timestamp": 1}

    body["observations"][1]["value"] = 6100          # the day's step count grew
    second = harness.client.post("/api/v2/wearables/observations", headers=headers, json=body).json()
    assert (second["counts"]["inserted"], second["counts"]["duplicates"], second["counts"]["updated"]) == (0, 1, 1)
    stored = harness.client.get("/api/v2/wearables/observations", params={"metric": "steps_daily"}, headers=headers).json()
    assert [o["value"] for o in stored["observations"]] == [6100.0]

    for bad in ({"metric": "blood_pressure"}, {"value": -1}, {"observed_at": "2026-09-20T10:00:00"}, {"source_record_id": ""}):
        payload = {"source": "healthkit_bridge", "observations": [{**sample("heart_rate", 70, "bpm", 5, "x"), **bad}]}
        assert harness.client.post("/api/v2/wearables/observations", headers=headers, json=payload).status_code == 422
    assert json.dumps(stored)
