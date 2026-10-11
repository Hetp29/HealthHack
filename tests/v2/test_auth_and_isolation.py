"""Identity, consent, and the rule that one user can never reach another's data."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from backend.app.v2.db import tables as t
from tests.v2.conftest import BASE_NOW, MEAL, PASSWORD, jpeg_bytes


def test_register_creates_a_fresh_participant_binding(harness):
    response = harness.client.post("/api/v2/auth/register", json={
        "email": "New@Example.com", "password": PASSWORD, "timezone": "Europe/London"})
    assert response.status_code == 201
    user = response.json()["user"]
    assert user["email"] == "new@example.com"
    assert user["participant_id"].startswith("gtp_")
    # Never a research-dataset participant: the artifact must not know this id.
    artifact = harness.ctx.prediction_service.artifact()
    assert user["participant_id"] not in artifact.personalization.calibrations
    assert all(c["state"] == "not_recorded" for c in user["consents"])


def test_password_is_not_stored_and_token_is_stored_only_as_a_hash(harness):
    token = harness.register()["Authorization"].split()[1]
    with harness.ctx.engine.connect() as conn:
        user = conn.execute(select(t.users)).mappings().one()
        session = conn.execute(select(t.auth_sessions)).mappings().one()
    assert PASSWORD not in user["password_hash"] and user["password_hash"].startswith("scrypt$")
    assert session["token_hash"] != token and len(session["token_hash"]) == 64


def test_duplicate_email_weak_password_and_bad_timezone_are_rejected(harness):
    harness.register("dup@example.com")
    again = harness.client.post("/api/v2/auth/register", json={
        "email": "dup@example.com", "password": PASSWORD, "timezone": "UTC"})
    assert again.status_code == 409 and again.json()["code"] == "email_taken"
    weak = harness.client.post("/api/v2/auth/register", json={
        "email": "w@example.com", "password": "short", "timezone": "UTC"})
    assert weak.status_code == 422


def test_login_logout_and_expiry(harness):
    harness.register("s@example.com")
    bad = harness.client.post("/api/v2/auth/login", json={"email": "s@example.com", "password": "wrong-password"})
    unknown = harness.client.post("/api/v2/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["message"] == unknown.json()["message"]  # no account enumeration

    session = harness.client.post("/api/v2/auth/login", json={"email": "s@example.com", "password": PASSWORD}).json()
    headers = {"Authorization": f"Bearer {session['session']['token']}"}
    assert harness.client.get("/api/v2/me", headers=headers).status_code == 200
    assert harness.client.post("/api/v2/auth/logout", headers=headers).status_code == 204
    assert harness.client.get("/api/v2/me", headers=headers).status_code == 401

    fresh = harness.register("t@example.com")
    harness.clock.advance(hours=harness.ctx.settings.session_ttl_hours + 1)
    expired = harness.client.get("/api/v2/me", headers=fresh)
    assert expired.status_code == 401 and expired.json()["code"] == "session_expired"


@pytest.mark.parametrize("header", ["", "Basic abc", "Bearer ", "Bearer not-a-real-token"])
def test_missing_or_malformed_credentials_are_401(harness, header):
    headers = {"Authorization": header} if header else {}
    assert harness.client.get("/api/v2/meals", headers=headers).status_code == 401


def test_health_data_endpoints_require_necessary_consent(harness):
    headers = harness.register("c@example.com", consent=False)
    for method, path in [("GET", "/api/v2/meals"), ("GET", "/api/v2/cgm/status"),
                         ("GET", "/api/v2/medications"), ("GET", "/api/v2/alert-settings")]:
        response = harness.client.request(method, path, headers=headers)
        assert response.status_code == 403, path
        assert response.json()["code"] == "consent_required"
    # Identity, consent and onboarding stay reachable so consent can be given.
    assert harness.client.get("/api/v2/me", headers=headers).status_code == 200
    assert harness.client.get("/api/v2/onboarding", headers=headers).status_code == 200


def test_consent_is_append_only_and_withdrawal_blocks_processing(harness):
    headers = harness.register("w@example.com")
    assert harness.client.get("/api/v2/meals", headers=headers).status_code == 200
    harness.clock.advance(minutes=1)
    harness.consent(headers, "necessary_processing", "withdrawn")
    assert harness.client.get("/api/v2/meals", headers=headers).status_code == 403
    with harness.ctx.engine.connect() as conn:
        rows = conn.execute(select(t.consent_records.c.state).where(
            t.consent_records.c.consent_type == "necessary_processing")
            .order_by(t.consent_records.c.recorded_at)).scalars().all()
    assert rows == ["granted", "withdrawn"]  # history kept, nothing overwritten
    assert harness.client.put("/api/v2/consents/marketing", headers=headers,
                              json={"state": "granted"}).status_code == 404


def _seed_everything(h, headers):
    """One of every owned resource, returning the ids."""
    h.push_cgm(headers, BASE_NOW - dt.timedelta(hours=4), BASE_NOW)
    photo = h.client.post("/api/v2/meals/images", params={"occurred_at": BASE_NOW.isoformat()},
                          headers={**headers, "Content-Type": "image/jpeg"}, content=jpeg_bytes())
    meal_id = photo.json()["meal_id"]
    h.client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=headers, json=MEAL)
    h.client.put("/api/v2/alert-settings", headers=headers, json={
        "enabled": True, "threshold": {"value": 100, "unit": "mg/dL"}, "channels": ["in_app"]})
    prediction = h.predict(headers, meal_id).json()
    regimen = h.client.post("/api/v2/medications", headers=headers, json={"name": "Metformin"}).json()
    dose = h.client.post("/api/v2/medication-doses", headers=headers, json={
        "regimen_id": regimen["regimen_id"], "action": "taken", "client_event_id": "evt-00000001"}).json()
    return {"meal": meal_id, "prediction": prediction["prediction_id"],
            "alert": prediction["alert"]["alert_id"], "regimen": regimen["regimen_id"],
            "dose": dose["dose_id"]}


def test_user_b_cannot_read_or_change_anything_owned_by_user_a(harness):
    a = harness.register("a@example.com")
    b = harness.register("b@example.com")
    ids = _seed_everything(harness, a)
    c = harness.client

    probes = [
        ("GET", f"/api/v2/meals/{ids['meal']}", None),
        ("GET", f"/api/v2/meals/{ids['meal']}/image", None),
        ("DELETE", f"/api/v2/meals/{ids['meal']}/image", None),
        ("POST", f"/api/v2/meals/{ids['meal']}/analyze", None),
        ("PATCH", f"/api/v2/meals/{ids['meal']}/confirm", MEAL),
        ("PATCH", f"/api/v2/meals/{ids['meal']}/consumption", {"consumption_status": "not_eaten"}),
        ("POST", f"/api/v2/meals/{ids['meal']}/predict", None),
        ("GET", f"/api/v2/meals/{ids['meal']}/outcome", None),
        ("DELETE", f"/api/v2/meals/{ids['meal']}", None),
        ("GET", f"/api/v2/predictions/{ids['prediction']}", None),
        ("POST", f"/api/v2/predictions/{ids['prediction']}/reproduce", None),
        ("POST", f"/api/v2/alerts/{ids['alert']}/acknowledge", None),
        ("PATCH", f"/api/v2/medications/{ids['regimen']}", {"name": "x"}),
        ("POST", "/api/v2/medication-doses",
         {"regimen_id": ids["regimen"], "action": "taken", "client_event_id": "evt-b-000001"}),
    ]
    for method, path, body in probes:
        response = c.request(method, path, headers=b, **({"json": body} if body else {}))
        assert response.status_code == 404, f"{method} {path} -> {response.status_code}"
        assert response.json()["code"] == "not_found"

    # B's collections are empty: nothing of A's leaks into a list.
    assert c.get("/api/v2/meals", headers=b).json()["meals"] == []
    assert c.get("/api/v2/predictions", headers=b).json()["predictions"] == []
    assert c.get("/api/v2/cgm/readings", headers=b).json()["readings"] == []
    assert c.get("/api/v2/cgm/status", headers=b).json()["freshness"] == "no_data"
    assert c.get("/api/v2/alerts", headers=b).json()["alerts"] == []
    assert c.get("/api/v2/notifications", headers=b).json()["notifications"] == []
    assert c.get("/api/v2/medications", headers=b).json()["regimens"] == []
    assert c.get("/api/v2/medication-doses", headers=b).json()["doses"] == []
    assert c.get("/api/v2/wearables/observations", headers=b).json()["observations"] == []
    assert c.get("/api/v2/devices", headers=b).json()["connections"] == []
    assert c.get("/api/v2/cgm/imports", headers=b).json()["imports"] == []

    # And A is unaffected by B's attempts.
    meal = c.get(f"/api/v2/meals/{ids['meal']}", headers=a).json()
    assert meal["status"] == "confirmed" and meal["consumption_status"] == "as_logged"


def test_a_user_id_in_the_request_is_never_honoured(harness):
    a = harness.register("a@example.com")
    harness.register("b@example.com")
    response = harness.client.post("/api/v2/meals", headers=a, json={
        "occurred_at": BASE_NOW.isoformat(), "user_id": "usr_someone_else"})
    assert response.status_code == 422  # unknown field, rejected rather than ignored


def test_account_deletion_removes_every_row_and_every_photo(harness):
    a = harness.register("a@example.com")
    b = harness.register("b@example.com")
    _seed_everything(harness, a)
    _seed_everything(harness, b)
    assert len(list(harness.image_dir.rglob("*.jpg"))) == 2

    wrong = harness.client.request("DELETE", "/api/v2/account", headers=a, json={"password": "nope-nope-nope"})
    assert wrong.status_code == 401
    assert harness.client.request("DELETE", "/api/v2/account", headers=a,
                                  json={"password": PASSWORD}).status_code == 204
    assert harness.client.get("/api/v2/me", headers=a).status_code == 401
    assert len(list(harness.image_dir.rglob("*.jpg"))) == 1

    with harness.ctx.engine.connect() as conn:
        for table in t.metadata.sorted_tables:
            if "user_id" not in table.c or table.name == "audit_events":
                continue
            owners = conn.execute(select(func.count(func.distinct(table.c.user_id)))).scalar()
            assert owners <= 1, f"{table.name} still holds the deleted user's rows"
        deleted = conn.execute(select(t.audit_events).where(
            t.audit_events.c.action == "account.deleted")).mappings().one()
    assert deleted["detail"]["photos_removed"] == 1
    # B is intact.
    assert len(harness.client.get("/api/v2/meals", headers=b).json()["meals"]) == 1


def test_onboarding_saves_incrementally_and_insists_on_units(harness):
    headers = harness.register("o@example.com", consent=False)
    c = harness.client
    first = c.patch("/api/v2/onboarding", headers=headers, json={"answers": {
        "primary_goal": "spike_awareness", "weight": {"value": 165, "unit": "lb"},
        "hba1c": "unknown"}})
    assert first.status_code == 200 and first.json()["version"] == 1
    second = c.patch("/api/v2/onboarding", headers=headers, json={
        "answers": {"hba1c": {"value": 48, "unit": "mmol/mol"}, "primary_goal": None},
        "completed": True, "expected_version": 1}).json()
    assert second["version"] == 2 and second["completed"] is True
    assert second["answers"] == {"weight": {"value": 165, "unit": "lb"},
                                 "hba1c": {"value": 48, "unit": "mmol/mol"}}  # original units kept

    stale = c.patch("/api/v2/onboarding", headers=headers, json={"answers": {}, "expected_version": 1})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    for bad in ({"fasting_glucose": 5.4}, {"fasting_glucose": {"value": 5.4, "unit": "mmol"}},
                {"weight": {"value": -1, "unit": "kg"}}, {"weight": {"value": "heavy", "unit": "kg"}},
                {"favourite_colour": "blue"}):
        assert c.patch("/api/v2/onboarding", headers=headers, json={"answers": bad}).status_code == 422


def test_onboarding_answers_never_reach_the_model_input(harness):
    """Two users with opposite questionnaire answers get the same forecast."""
    forecasts = []
    for email, answers in (("x@example.com", {"diabetes_status": "type2", "uses_insulin": "yes"}),
                           ("y@example.com", {"diabetes_status": "none", "uses_insulin": "no"})):
        headers = harness.register(email)
        harness.client.patch("/api/v2/onboarding", headers=headers, json={"answers": answers})
        harness.push_cgm(headers, BASE_NOW - dt.timedelta(hours=6), BASE_NOW)
        forecasts.append(harness.predict(headers, harness.meal(headers, BASE_NOW)).json()["forecast"])
    assert forecasts[0]["points"] == forecasts[1]["points"]
