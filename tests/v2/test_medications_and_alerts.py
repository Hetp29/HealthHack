"""Medication regimens, dose events, reminders, forecast alerts and notifications."""

from __future__ import annotations

import datetime as dt
import json

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.services import alerts as alert_service
from backend.app.v2.services import medications as medication_service
from tests.v2.conftest import BASE_NOW, MEAL, UTC


def regimen(h, headers, **overrides):
    body = {"name": "Metformin", "medication_class": "metformin", "dose_amount": 500,
            "dose_unit": "mg", "frequency": "twice daily", **overrides}
    response = h.client.post("/api/v2/medications", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def dose(h, headers, regimen_id, action, event_id, **extra):
    return h.client.post("/api/v2/medication-doses", headers=headers, json={
        "regimen_id": regimen_id, "action": action, "client_event_id": event_id, **extra})


# --------------------------------------------------------------------------- #
# regimens and doses
# --------------------------------------------------------------------------- #


def test_a_regimen_is_not_a_dose(harness):
    headers = harness.register()
    created = regimen(harness, headers)
    assert created["status"] == "current" and created["timezone"] == "America/New_York"
    assert created["dose"] == {"amount": 500.0, "unit": "mg"}
    # Prescribing something records no dose at all.
    assert harness.client.get("/api/v2/medication-doses", headers=headers).json()["doses"] == []


def test_current_and_previous_regimens_are_separated(harness):
    headers = harness.register()
    a = regimen(harness, headers)
    b = regimen(harness, headers, name="Glipizide", medication_class="sulfonylurea")
    stopped = harness.client.patch(f"/api/v2/medications/{b['regimen_id']}", headers=headers,
                                   json={"status": "discontinued", "end_date": "2026-09-19"}).json()
    assert stopped["status"] == "discontinued"
    current = harness.client.get("/api/v2/medications", params={"status": "current"}, headers=headers).json()
    previous = harness.client.get("/api/v2/medications", params={"status": "discontinued"}, headers=headers).json()
    assert [r["regimen_id"] for r in current["regimens"]] == [a["regimen_id"]]
    assert [r["regimen_id"] for r in previous["regimens"]] == [b["regimen_id"]]


@pytest.mark.parametrize("body", [
    {"name": ""}, {"name": "X", "dose_amount": 500}, {"name": "X", "dose_unit": "mg"},
    {"name": "X", "dose_amount": -5, "dose_unit": "mg"}, {"name": "X", "schedule_times": ["8am"]},
    {"name": "X", "schedule_times": ["25:00"]}, {"name": "X", "timezone": "Nowhere/Land"},
    {"name": "X", "start_date": "2026-13-40"}, {"name": "X", "medication_class": "miracle_cure"},
])
def test_invalid_regimens_are_rejected(harness, body):
    assert harness.client.post("/api/v2/medications", headers=harness.register(), json=body).status_code == 422


def test_taken_is_recorded_only_by_an_explicit_user_action(harness):
    headers = harness.register()
    rid = regimen(harness, headers)["regimen_id"]
    taken = dose(harness, headers, rid, "taken", "evt-taken-001")
    assert taken.status_code == 201
    body = taken.json()
    assert body["action"] == "taken" and body["actor"] == "user"
    assert body["actual_at"] == "2026-09-20T16:00:00Z"

    skipped = dose(harness, headers, rid, "skipped", "evt-skip-0001").json()
    unknown = dose(harness, headers, rid, "unknown", "evt-unknown-1").json()
    assert skipped["actual_at"] is None and unknown["actual_at"] is None


def test_dose_events_are_idempotent_on_the_client_event_id(harness):
    headers = harness.register()
    rid = regimen(harness, headers)["regimen_id"]
    first = dose(harness, headers, rid, "taken", "evt-retry-001")
    harness.clock.advance(minutes=7)
    retry = dose(harness, headers, rid, "taken", "evt-retry-001")
    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json()["replayed"] is True
    assert retry.json()["dose_id"] == first.json()["dose_id"]
    assert retry.json()["actual_at"] == first.json()["actual_at"]     # not re-stamped
    assert len(harness.client.get("/api/v2/medication-doses", headers=headers).json()["doses"]) == 1


@pytest.mark.parametrize("extra", [
    {"action": "skipped", "actual_at": BASE_NOW.isoformat()},
    {"action": "snoozed"},
    {"action": "snoozed", "snoozed_until": (BASE_NOW - dt.timedelta(minutes=1)).isoformat()},
    {"action": "taken", "snoozed_until": (BASE_NOW + dt.timedelta(minutes=10)).isoformat()},
    {"action": "taken", "actual_at": (BASE_NOW + dt.timedelta(hours=2)).isoformat()},
    {"action": "scheduled"},
])
def test_inconsistent_dose_events_are_rejected(harness, extra):
    headers = harness.register()
    rid = regimen(harness, headers)["regimen_id"]
    action = extra.pop("action")
    assert dose(harness, headers, rid, action, "evt-bad-00001", **extra).status_code == 422


def test_the_database_itself_refuses_an_actual_time_on_a_dose_not_taken(harness):
    headers = harness.register()
    rid = regimen(harness, headers)["regimen_id"]
    user_id = harness.client.get("/api/v2/me", headers=headers).json()["user_id"]
    with pytest.raises(IntegrityError), harness.ctx.engine.begin() as conn:
        conn.execute(t.medication_doses.insert().values(
            dose_id="dos_x", regimen_id=rid, user_id=user_id, scheduled_at=BASE_NOW,
            action="scheduled", actual_at=BASE_NOW, actor="system", provenance="reminder_schedule",
            recorded_at=BASE_NOW))


# --------------------------------------------------------------------------- #
# reminders
# --------------------------------------------------------------------------- #


def test_a_reminder_slot_never_becomes_taken_on_its_own(harness):
    headers = harness.register()
    # BASE_NOW is 12:00 in New York.
    rid = regimen(harness, headers, schedule_times=["12:30", "20:00"], reminders_enabled=True)["regimen_id"]
    assert harness.client.get("/api/v2/medication-reminders/due", headers=headers).json()["due"] == []

    harness.clock.advance(minutes=45)       # 12:45 local: the 12:30 slot has passed
    due = harness.client.get("/api/v2/medication-reminders/due", headers=headers).json()["due"]
    assert len(due) == 1
    slot = due[0]
    assert slot["scheduled_at"] == "2026-09-20T16:30:00Z"
    assert slot["action"] == "scheduled" and slot["actor"] == "system" and slot["actual_at"] is None
    assert slot["regimen"]["name"] == "Metformin"

    # Ignored for a day: still not taken, and no duplicate slot is created.
    harness.clock.advance(hours=10)
    again = harness.client.get("/api/v2/medication-reminders/due", headers=headers).json()["due"]
    assert [d["scheduled_at"] for d in again] == ["2026-09-20T16:30:00Z", "2026-09-21T00:00:00Z"]
    assert all(d["action"] == "scheduled" and d["actual_at"] is None for d in again)
    history = harness.client.get("/api/v2/medication-doses", headers=headers).json()["doses"]
    assert {d["action"] for d in history} == {"scheduled"}

    delivered = harness.client.get("/api/v2/notifications", headers=headers).json()["notifications"]
    assert [(n["source_type"], n["channel"], n["status"]) for n in delivered] == [("reminder", "in_app", "delivered")] * 2
    assert rid


def test_snooze_skip_and_taken_resolve_the_slot_they_answer(harness):
    headers = harness.register()
    rid = regimen(harness, headers, schedule_times=["12:30"], reminders_enabled=True)["regimen_id"]
    harness.clock.advance(minutes=45)
    slot = harness.client.get("/api/v2/medication-reminders/due", headers=headers).json()["due"][0]

    until = harness.clock.now + dt.timedelta(minutes=20)
    snoozed = dose(harness, headers, rid, "snoozed", "evt-snooze-01",
                   scheduled_at=slot["scheduled_at"], snoozed_until=until.isoformat())
    assert snoozed.status_code == 200 and snoozed.json()["dose_id"] == slot["dose_id"]
    assert snoozed.json()["actual_at"] is None
    assert harness.client.get("/api/v2/medication-reminders/due", headers=headers).json()["due"] == []

    harness.clock.advance(minutes=25)       # the snooze expires: due again, still not taken
    back = harness.client.get("/api/v2/medication-reminders/due", headers=headers).json()["due"]
    assert [d["action"] for d in back] == ["snoozed"]

    taken = dose(harness, headers, rid, "taken", "evt-taken-002", scheduled_at=slot["scheduled_at"]).json()
    assert taken["dose_id"] == slot["dose_id"] and taken["action"] == "taken"
    assert taken["actual_at"] == core.rfc3339(harness.clock.now)
    assert harness.client.get("/api/v2/medication-reminders/due", headers=headers).json()["due"] == []


def test_reminder_slots_follow_local_time_across_dst():
    from zoneinfo import ZoneInfo
    zone = ZoneInfo("America/New_York")
    slot = medication_service._slot_instant
    # 08:00 local is 12:00Z in summer and 13:00Z in winter.
    assert slot(dt.date(2025, 7, 1), "08:00", zone) == dt.datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
    assert slot(dt.date(2025, 12, 1), "08:00", zone) == dt.datetime(2025, 12, 1, 13, 0, tzinfo=UTC)
    # 02:30 does not exist on spring-forward day; it resolves to one instant, not zero or two.
    assert slot(dt.date(2025, 3, 9), "02:30", zone) == dt.datetime(2025, 3, 9, 7, 30, tzinfo=UTC)
    # 01:30 occurs twice on fall-back day; the first occurrence is used.
    assert slot(dt.date(2025, 11, 2), "01:30", zone) == dt.datetime(2025, 11, 2, 5, 30, tzinfo=UTC)


def test_reminders_respect_regimen_dates_and_discontinuation(harness):
    headers = harness.register()
    future = regimen(harness, headers, name="Later", schedule_times=["12:30"], reminders_enabled=True,
                     start_date="2026-09-25")
    stopped = regimen(harness, headers, name="Stopped", schedule_times=["12:30"], reminders_enabled=True)
    harness.client.patch(f"/api/v2/medications/{stopped['regimen_id']}", headers=headers, json={"status": "discontinued"})
    regimen(harness, headers, name="Silent", schedule_times=["12:30"], reminders_enabled=False)
    harness.clock.advance(hours=1)
    assert harness.client.get("/api/v2/medication-reminders/due", headers=headers).json()["due"] == []
    assert future["start_date"] == "2026-09-25"


def test_the_reminder_worker_is_idempotent(harness):
    from workers import reminders

    headers = harness.register()
    regimen(harness, headers, schedule_times=["12:30"], reminders_enabled=True)
    harness.clock.advance(minutes=45)
    assert reminders.run_once(harness.ctx) == 1
    assert reminders.run_once(harness.ctx) == 0


def test_education_is_sourced_general_and_makes_no_individual_claim(harness):
    body = harness.client.get("/api/v2/medication-education", headers=harness.register()).json()
    assert body["review_status"] == "not_clinically_reviewed"
    assert body["individualized_effect_forecast_available"] is False
    assert "do not have a validated individualized medication-effect forecast" in body["limitation"]
    for entry in body["entries"]:
        assert entry["sources"] and all(s["url"].startswith("https://") for s in entry["sources"])
    text = json.dumps(body["entries"]).lower()
    for phrase in ("you should", "your dose", "increase your", "reduce your", "units of"):
        assert phrase not in text


# --------------------------------------------------------------------------- #
# alerts
# --------------------------------------------------------------------------- #

SETTINGS = {"enabled": True, "threshold": {"value": 100, "unit": "mg/dL"}, "channels": ["in_app"]}


def put_settings(h, headers, **overrides):
    return h.client.put("/api/v2/alert-settings", headers=headers, json={**SETTINGS, **overrides})


def forecast(h, headers, when=None, carbs=90.0):
    when = when or h.clock.now
    h.push_cgm(headers, when - dt.timedelta(hours=3), when)
    return h.predict(headers, h.meal(headers, when, {**MEAL, "carbs_g": carbs})).json()


def test_alert_settings_default_off_and_keep_the_users_unit(harness):
    headers = harness.register()
    default = harness.client.get("/api/v2/alert-settings", headers=headers).json()
    assert default["enabled"] is False and default["informational_only"] is True

    saved = put_settings(harness, headers, threshold={"value": 10.0, "unit": "mmol/L"},
                         channels=["in_app", "push"], quiet_hours_start="22:00", quiet_hours_end="07:00").json()
    assert saved["threshold"] == {"value": 10.0, "unit": "mmol/L", "mg_dl": 180.16}
    # Push is accepted as a preference and reported honestly as not available.
    assert saved["channels"] == [{"channel": "in_app", "available": True},
                                 {"channel": "push", "available": False}]


@pytest.mark.parametrize("overrides", [
    {"threshold": {"value": 180}}, {"threshold": {"value": 50, "unit": "mg/dL"}},
    {"threshold": {"value": 30, "unit": "mmol/L"}}, {"channels": []}, {"channels": ["sms"]},
    {"quiet_hours_start": "22:00"}, {"quiet_hours_start": "10pm", "quiet_hours_end": "07:00"},
    {"cooldown_minutes": -1},
])
def test_invalid_alert_settings_are_rejected(harness, overrides):
    assert put_settings(harness, harness.register(), **overrides).status_code == 422


def test_the_server_raises_an_informational_alert_from_the_stored_forecast(harness):
    headers = harness.register()
    put_settings(harness, headers)
    body = forecast(harness, headers)
    alert = body["alert"]
    assert alert["status"] == "active" and alert["kind"] == "forecast_above_threshold"
    payload = alert["payload"]
    points = body["forecast"]["points"]
    first_over = next(p["minute"] for p in points if p["minute"] > 0 and p["glucose_mg_dl"] >= 100)
    assert payload["predicted_crossing_minute"] == first_over
    assert payload["predicted_peak"]["mg_dl"] == max(p["glucose_mg_dl"] for p in points)
    assert payload["uncertainty_status"] == "not_calibrated" and payload["informational_only"] is True
    assert payload["latest_cgm_age_minutes"] == 0.0
    assert "does not replace your CGM" in payload["disclaimer"]

    listed = harness.client.get("/api/v2/alerts", headers=headers).json()["alerts"]
    assert [a["alert_id"] for a in listed] == [alert["alert_id"]]
    notes = harness.client.get("/api/v2/notifications", headers=headers).json()["notifications"]
    assert [(n["source_id"], n["channel"], n["status"]) for n in notes] == [(alert["alert_id"], "in_app", "delivered")]

    acknowledged = harness.client.post(f"/api/v2/alerts/{alert['alert_id']}/acknowledge", headers=headers).json()
    assert acknowledged["acknowledged_at"] == "2026-09-20T16:00:00Z"


def test_no_alert_when_disabled_or_when_the_forecast_stays_below(harness):
    headers = harness.register()
    assert forecast(harness, headers)["alert"] is None            # rule off by default
    put_settings(harness, headers, threshold={"value": 390, "unit": "mg/dL"})
    harness.clock.advance(minutes=10)
    assert forecast(harness, headers)["alert"] is None
    assert harness.client.get("/api/v2/alerts", headers=headers).json()["alerts"] == []


def test_alerts_are_deduplicated_per_forecast_and_rate_limited_by_cooldown(harness):
    headers = harness.register()
    put_settings(harness, headers, cooldown_minutes=60)
    first = forecast(harness, headers)
    meal_id = first["meal_id"]
    replay = harness.predict(headers, meal_id).json()
    assert replay["alert"]["alert_id"] == first["alert"]["alert_id"]      # same forecast, same alert

    harness.clock.advance(minutes=20)
    second = forecast(harness, headers)
    assert second["alert"]["status"] == "suppressed_cooldown"
    harness.clock.advance(minutes=50)
    third = forecast(harness, headers)
    assert third["alert"]["status"] == "active"
    notes = harness.client.get("/api/v2/notifications", headers=headers).json()["notifications"]
    assert len(notes) == 2                                               # only active alerts notify


def test_quiet_hours_suppress_alerts_in_the_users_local_time(harness):
    headers = harness.register()            # New York; BASE_NOW is 12:00 local
    put_settings(harness, headers, quiet_hours_start="11:00", quiet_hours_end="13:00")
    assert forecast(harness, headers)["alert"]["status"] == "suppressed_quiet_hours"
    assert harness.client.get("/api/v2/notifications", headers=headers).json()["notifications"] == []
    harness.clock.advance(minutes=90)       # 13:30 local
    assert forecast(harness, headers)["alert"]["status"] == "active"


@pytest.mark.parametrize("start,end,local,quiet", [
    ("22:00", "07:00", "23:30", True), ("22:00", "07:00", "06:59", True),
    ("22:00", "07:00", "07:00", False), ("22:00", "07:00", "12:00", False),
    ("09:00", "17:00", "09:00", True), ("09:00", "17:00", "17:00", False),
    (None, None, "03:00", False), ("08:00", "08:00", "08:00", False),
])
def test_quiet_hours_window_including_midnight_wrap(start, end, local, quiet):
    hour, minute = (int(x) for x in local.split(":"))
    moment = dt.datetime(2026, 9, 20, hour, minute, tzinfo=UTC)
    rule = {"quiet_hours_start": start, "quiet_hours_end": end}
    assert alert_service.in_quiet_hours(rule, moment, "UTC") is quiet


def test_a_retrospective_forecast_never_raises_a_live_alert(harness):
    headers = harness.register()
    put_settings(harness, headers)
    body = forecast(harness, headers, when=BASE_NOW - dt.timedelta(hours=5))
    assert body["prediction_kind"] == "retrospective"
    assert body["alert"]["status"] == "suppressed_retrospective"


def test_unavailable_channels_are_recorded_as_unavailable_not_sent(harness):
    headers = harness.register()
    put_settings(harness, headers, channels=["in_app", "push", "email"])
    forecast(harness, headers)
    notes = {n["channel"]: n for n in harness.client.get("/api/v2/notifications", headers=headers).json()["notifications"]}
    assert notes["in_app"]["status"] == "delivered"
    assert notes["push"]["status"] == notes["email"]["status"] == "channel_unavailable"
    assert "no push service" in notes["push"]["detail"]["reason"]


def test_medication_records_do_not_change_alert_evaluation(harness):
    plain = harness.register("p@example.com")
    medicated = harness.register("m@example.com")
    rid = regimen(harness, medicated, name="Insulin", medication_class="insulin")["regimen_id"]
    dose(harness, medicated, rid, "taken", "evt-insulin-9")
    outcomes = []
    for headers in (plain, medicated):
        put_settings(harness, headers)
        outcomes.append(forecast(harness, headers)["alert"]["payload"]["predicted_peak"])
    assert outcomes[0] == outcomes[1]
    with harness.ctx.engine.connect() as conn:
        assert conn.execute(select(t.medication_doses.c.action)).scalars().all() == ["taken"]
