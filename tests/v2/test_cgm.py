"""Glucose ingestion: units, time, duplicates, corrections, freshness, file imports."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from backend.app.v2.db import tables as t
from backend.app.v2.providers import cgm_files
from backend.app.v2.services import glucose
from backend.app.v2.services.glucose import IncomingReading
from tests.v2.conftest import BASE_NOW, UTC


def push(h, headers, readings, source="manual_entry", device_id="meter-1"):
    return h.client.post("/api/v2/cgm/readings", headers=headers,
                         json={"source": source, "device_id": device_id, "readings": readings})


def reading(minutes_ago, value, unit="mg/dL", **extra):
    return {"observed_at": (BASE_NOW - dt.timedelta(minutes=minutes_ago)).isoformat(),
            "value": value, "unit": unit, **extra}


def test_mmol_l_is_converted_and_the_source_value_is_preserved(harness):
    headers = harness.register()
    assert push(harness, headers, [reading(5, 5.5, "mmol/L")]).json()["counts"]["inserted"] == 1
    stored = harness.client.get("/api/v2/cgm/readings", headers=headers).json()["readings"][0]
    assert stored["glucose_mg_dl"] == pytest.approx(5.5 * 18.016, abs=1e-3)
    assert stored["source"] == {"value": 5.5, "unit": "mmol/L", "conversion": "mmol/L*18.016",
                                "timestamp": stored["source"]["timestamp"], "timezone": None}
    in_mmol = harness.client.get("/api/v2/cgm/readings", params={"unit": "mmol/L"}, headers=headers).json()
    assert in_mmol["readings"][0]["glucose"] == 5.5 and in_mmol["unit"] == "mmol/L"


def test_the_same_reading_in_either_unit_is_one_observation(harness):
    headers = harness.register()
    push(harness, headers, [reading(5, 99.088)])
    again = push(harness, headers, [reading(5, 5.5, "mmol/L")]).json()["counts"]
    assert again == {"received": 1, "inserted": 0, "duplicates": 1, "corrected": 0,
                     "rejected": 0, "rejected_by_reason": {}}


@pytest.mark.parametrize("body", [
    {"observed_at": BASE_NOW.isoformat(), "value": 100},                       # unit missing
    {"observed_at": BASE_NOW.isoformat(), "value": 100, "unit": "mg/L"},       # unit unknown
    {"observed_at": "2026-09-20T12:00:00", "value": 100, "unit": "mg/dL"},     # naive timestamp
    {"observed_at": "yesterday", "value": 100, "unit": "mg/dL"},
    {"observed_at": BASE_NOW.isoformat(), "value": "high", "unit": "mg/dL"},
])
def test_malformed_readings_fail_validation(harness, body):
    assert push(harness, harness.register(), [body]).status_code == 422


def test_nonfinite_values_are_rejected_at_the_http_boundary(harness):
    headers = {**harness.register(), "Content-Type": "application/json"}
    for token in ("NaN", "Infinity", "-Infinity"):
        raw = ('{"source":"manual_entry","readings":[{"observed_at":"%s","value":%s,"unit":"mg/dL"}]}'
               % (BASE_NOW.isoformat(), token))
        assert harness.client.post("/api/v2/cgm/readings", headers=headers, content=raw).status_code == 422


def test_implausible_future_and_conflicting_readings_are_rejected_with_reasons(harness):
    headers = harness.register()
    summary = push(harness, headers, [
        reading(30, 100), reading(25, 12), reading(20, 601), reading(15, 1.0, "mmol/L"),
        reading(-30, 110),            # 30 minutes in the future
        reading(10, 120), reading(10, 150),   # two different values for one instant
        reading(10, 120),             # exact repeat of the first
    ]).json()
    assert summary["counts"]["inserted"] == 2
    assert summary["counts"]["duplicates"] == 1
    assert summary["counts"]["rejected_by_reason"] == {
        "out_of_range": 3, "future_timestamp": 1, "conflicting_duplicate_in_batch": 1}
    assert [r["index"] for r in summary["rejections"]] == [1, 2, 3, 4, 6]


def test_service_rejects_nonfinite_naive_and_unknown_inputs(harness):
    headers = harness.register()
    user_id = harness.client.get("/api/v2/me", headers=headers).json()["user_id"]
    ok = BASE_NOW - dt.timedelta(minutes=5)
    batch = [
        IncomingReading(value=float("nan"), unit="mg/dL", observed_at=ok),
        IncomingReading(value=float("inf"), unit="mg/dL", observed_at=ok),
        IncomingReading(value=100, unit="mg/dL", observed_at=ok.replace(tzinfo=None)),
        IncomingReading(value=100, unit=None, observed_at=ok),
        IncomingReading(value=100, unit="mg/dL", observed_at=None),
        IncomingReading(value=True, unit="mg/dL", observed_at=ok),
        IncomingReading(value=100, unit="mg/dL", observed_at=ok, record_kind="guess"),
    ]
    with harness.ctx.engine.begin() as conn:
        result = glucose.ingest_readings(conn, harness.ctx, user_id, "manual_entry", batch, source_format="test")
    assert result["counts"]["inserted"] == 0
    assert result["counts"]["rejected_by_reason"] == {
        "nonfinite_value": 3, "naive_timestamp": 1, "unknown_unit": 1,
        "missing_timestamp": 1, "unknown_record_kind": 1}


def test_out_of_order_and_overlapping_batches_store_each_instant_once(harness):
    headers = harness.register()
    push(harness, headers, [reading(m, 100 + m) for m in (10, 30, 20)])          # shuffled
    overlap = push(harness, headers, [reading(m, 100 + m) for m in (40, 30, 20, 0)]).json()["counts"]
    assert (overlap["inserted"], overlap["duplicates"]) == (2, 2)
    stored = harness.client.get("/api/v2/cgm/readings", headers=headers).json()["readings"]
    stamps = [r["observed_at"] for r in stored]
    assert stamps == sorted(stamps) and len(stamps) == 5   # returned ascending, no repeats


def test_a_changed_value_is_a_recorded_correction_not_a_silent_overwrite(harness):
    headers = harness.register()
    push(harness, headers, [reading(10, 140)])
    harness.clock.advance(minutes=3)
    corrected = push(harness, headers, [reading(10, 152)]).json()["counts"]
    assert corrected["corrected"] == 1 and corrected["inserted"] == 0

    stored = harness.client.get("/api/v2/cgm/readings", headers=headers).json()["readings"]
    assert len(stored) == 1 and stored[0]["glucose_mg_dl"] == 152 and stored[0]["corrected"] is True
    assert "corrected" in stored[0]["quality_flags"]
    with harness.ctx.engine.connect() as conn:
        history = conn.execute(select(t.glucose_corrections)).mappings().one()
    assert history["previous_glucose_mg_dl"] == 140
    assert history["previous_received_at"] == BASE_NOW          # when the old value arrived
    assert history["corrected_at"] == BASE_NOW + dt.timedelta(minutes=3)


def test_observed_at_and_received_at_are_distinct(harness):
    headers = harness.register()
    push(harness, headers, [reading(600, 100)])
    stored = harness.client.get("/api/v2/cgm/readings",
                                params={"start": (BASE_NOW - dt.timedelta(days=1)).isoformat()},
                                headers=headers).json()["readings"][0]
    assert stored["received_at"] == "2026-09-20T16:00:00Z"
    assert stored["observed_at"] == "2026-09-20T06:00:00Z"


def test_freshness_goes_stale_and_gaps_are_reported_not_filled(harness):
    headers = harness.register()
    assert harness.client.get("/api/v2/cgm/status", headers=headers).json()["freshness"] == "no_data"
    # A sensor that dropped out for an hour.
    harness.push_cgm(headers, BASE_NOW - dt.timedelta(hours=6), BASE_NOW - dt.timedelta(hours=3))
    harness.push_cgm(headers, BASE_NOW - dt.timedelta(hours=2), BASE_NOW)
    status = harness.client.get("/api/v2/cgm/status", headers=headers).json()
    assert status["freshness"] == "fresh" and status["forecast_ready"] is True
    assert status["last_24h"]["median_interval_minutes"] == 5.0
    middle = [g for g in status["last_24h"]["gaps"] if g["minutes"] == 60.0]
    assert len(middle) == 1
    count = len(harness.client.get("/api/v2/cgm/readings", headers=headers).json()["readings"])
    assert count == 37 + 25      # exactly what was sent; the hour is still empty

    harness.clock.advance(minutes=40)
    assert harness.client.get("/api/v2/cgm/status", headers=headers).json()["freshness"] == "delayed"
    harness.clock.advance(hours=3)
    stale = harness.client.get("/api/v2/cgm/status", headers=headers).json()
    assert stale["freshness"] == "stale" and stale["forecast_ready"] is False
    assert stale["latest_age_minutes"] == 220.0


def test_demo_data_is_labelled_and_never_marks_a_source_connected(harness):
    headers = harness.register()
    summary = harness.push_cgm(headers, BASE_NOW - dt.timedelta(hours=1), BASE_NOW, source="demo")
    assert summary["data_class"] == "demo"
    status = harness.client.get("/api/v2/cgm/status", headers=headers).json()
    assert status["data_class"] == "demo" and status["latest"]["data_class"] == "demo"
    connection = harness.client.get("/api/v2/devices", headers=headers).json()["connections"][0]
    assert connection["provider"] == "demo"
    assert connection["status"] == "pending" and connection["authentic_data_received"] is False

    push(harness, headers, [reading(5, 100)], source="manual_entry")
    by_provider = {c["provider"]: c for c in harness.client.get("/api/v2/devices", headers=headers).json()["connections"]}
    assert by_provider["manual_entry"]["status"] == "connected"
    assert by_provider["manual_entry"]["authentic_data_received"] is True


def test_two_sensors_are_never_blended_into_one_series(harness):
    headers = harness.register()
    start = BASE_NOW - dt.timedelta(hours=3)
    harness.push_cgm(headers, start, BASE_NOW - dt.timedelta(minutes=40), device_id="old-sensor", offset=30)
    harness.push_cgm(headers, start, BASE_NOW, device_id="new-sensor")
    status = harness.client.get("/api/v2/cgm/status", headers=headers).json()
    assert status["model_series"]["device"]["device_id"] == "new-sensor"
    assert status["model_series"]["multi_device_overlap"] is True
    assert status["model_series"]["other_devices"][0]["device_id"] == "old-sensor"

    body = harness.predict(headers, harness.meal(headers, BASE_NOW)).json()
    assert body["context_quality"]["cgm_device"]["device_id"] == "new-sensor"
    assert body["context_quality"]["multi_device_overlap"] is True
    # Minute 0 is the chosen sensor's reading, not a mix with the one reading 30 higher.
    newest = harness.client.get("/api/v2/cgm/readings", headers=headers).json()["readings"][-1]
    assert body["forecast"]["points"][0]["glucose_mg_dl"] == round(newest["glucose_mg_dl"], 1)


def test_scans_and_fingersticks_are_stored_but_kept_out_of_the_model_series(harness):
    headers = harness.register()
    push(harness, headers, [reading(3, 250, record_kind="fingerstick"), reading(2, 240, record_kind="scan")])
    status = harness.client.get("/api/v2/cgm/status", headers=headers).json()
    assert status["freshness"] == "no_data"
    assert len(harness.client.get("/api/v2/cgm/readings", headers=headers).json()["readings"]) == 2
    response = harness.predict(headers, harness.meal(headers, BASE_NOW))
    assert response.status_code == 422 and response.json()["code"] == "insufficient_premeal_context"


# --------------------------------------------------------------------------- #
# file imports
# --------------------------------------------------------------------------- #

CLARITY = """Index,Timestamp (YYYY-MM-DDThh:mm:ss),Event Type,Event Subtype,Patient Info,Device Info,Source Device ID,Glucose Value (mg/dL),Insulin Value (u),Carb Value (grams),Duration (hh:mm:ss),Glucose Rate of Change (mg/dL/min),Transmitter Time (Long Integer),Transmitter ID
1,,FirstName,,Test,,,,,,,,,
2,,Device,,,Dexcom G6 Mobile App,iPhone G6,,,,,,,
3,2026-09-19T08:00:10,EGV,,,,iPhone G6,112,,,,,7001,8ABC12
4,2026-09-19T08:05:10,EGV,,,,iPhone G6,118,,,,,7301,8ABC12
5,2026-09-19T08:10:10,EGV,,,,iPhone G6,Low,,,,,7601,8ABC12
6,2026-09-19T08:15:10,EGV,,,,iPhone G6,High,,,,,7901,8ABC12
7,2026-09-19T08:20:10,Carbs,,,,iPhone G6,,,45,,,,
8,2026-09-19T08:25:10,EGV,,,,iPhone G6,131,,,,,8501,8ABC12
"""


def test_dexcom_clarity_export_is_parsed_localized_and_range_markers_are_rejected(harness):
    headers = harness.register()   # account timezone: America/New_York (EDT, UTC-4)
    response = harness.client.post("/api/v2/cgm/import", params={"format": "dexcom_clarity_csv"},
                                   headers={**headers, "Content-Type": "text/csv"}, content=CLARITY.encode())
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["counts"]["inserted"] == 3
    assert summary["counts"]["rejected_by_reason"] == {"sensor_range_marker": 2}

    stored = harness.client.get("/api/v2/cgm/readings", params={
        "start": "2026-09-19T00:00:00Z", "end": "2026-09-20T00:00:00Z"}, headers=headers).json()["readings"]
    assert [r["glucose_mg_dl"] for r in stored] == [112, 118, 131]   # no 39 / 401 invented
    assert stored[0]["observed_at"] == "2026-09-19T12:00:10Z"        # 08:00 EDT
    assert stored[0]["source"]["timestamp"] == "2026-09-19T08:00:10"
    assert stored[0]["source"]["timezone"] == "America/New_York"
    assert stored[0]["provider"] == "dexcom_clarity_csv"

    replay = harness.client.post("/api/v2/cgm/import", params={"format": "dexcom_clarity_csv"},
                                 headers={**headers, "Content-Type": "text/csv"}, content=CLARITY.encode()).json()
    assert (replay["counts"]["inserted"], replay["counts"]["duplicates"]) == (0, 3)


def _libre(rows: str, unit: str = "mg/dL") -> bytes:
    head = ("Glucose Data,Generated on,20-09-2026 10:00 UTC,Generated by,Test User\n"
            f"Device,Serial Number,Device Timestamp,Record Type,Historic Glucose {unit},Scan Glucose {unit},Notes\n")
    return (head + rows).encode()


def test_libreview_export_mmol_historic_and_scan_rows():
    rows = ("FreeStyle LibreLink,ABC-123,19-09-2026 08:00,0,\"5,6\",,\n"
            "FreeStyle LibreLink,ABC-123,19-09-2026 08:15,0,6.1,,\n"
            "FreeStyle LibreLink,ABC-123,19-09-2026 08:17,1,,6.4,\n"
            "FreeStyle LibreLink,ABC-123,19-09-2026 08:20,6,,,note\n")
    readings = cgm_files.parse_libreview(_libre(rows, "mmol/L"), "Europe/London")
    assert [(r.record_kind, r.value, r.unit) for r in readings] == [
        ("cgm", 5.6, "mmol/L"), ("cgm", 6.1, "mmol/L"), ("scan", 6.4, "mmol/L")]
    assert readings[0].observed_at == dt.datetime(2026, 9, 19, 7, 0, tzinfo=UTC)   # BST = UTC+1
    assert readings[0].device_id == "ABC-123"


def test_libreview_date_order_is_never_guessed(harness):
    headers = {**harness.register(), "Content-Type": "text/csv"}
    ambiguous = _libre("FreeStyle LibreLink,ABC,03-04-2026 08:00,0,101,,\n")
    refused = harness.client.post("/api/v2/cgm/import", params={"format": "libreview_csv"},
                                  headers=headers, content=ambiguous)
    assert refused.status_code == 422 and refused.json()["code"] == "ambiguous_date_format"

    as_mdy = cgm_files.parse_libreview(ambiguous, "UTC", "MDY")[0].observed_at
    as_dmy = cgm_files.parse_libreview(ambiguous, "UTC", "DMY")[0].observed_at
    assert (as_mdy.month, as_mdy.day) == (3, 4) and (as_dmy.month, as_dmy.day) == (4, 3)
    # A date that can only be read one way settles it for the whole file.
    us = _libre("L,ABC,09-19-2026 08:00 AM,0,101,,\nL,ABC,03-04-2026 09:00 PM,0,102,,\n")
    parsed = cgm_files.parse_libreview(us, "UTC")
    assert (parsed[1].observed_at.month, parsed[1].observed_at.hour) == (3, 21)


def test_dst_ambiguous_and_nonexistent_local_times_are_rejected_not_guessed(harness):
    """America/New_York 2025: 02:30 on 9 March never happened; 01:30 on 2 November
    happened twice. Neither can be placed on the UTC timeline from the file alone."""
    header = CLARITY.splitlines()[0] + "\n"
    rows = ("1,2025-03-09T02:30:00,EGV,,,,G6,100,,,,,1,TX\n"
            "2,2025-11-02T01:30:00,EGV,,,,G6,101,,,,,2,TX\n"
            "3,2025-11-02T03:30:00,EGV,,,,G6,102,,,,,3,TX\n")
    readings = cgm_files.parse_dexcom_clarity((header + rows).encode(), "America/New_York")
    assert [r.reject_reason for r in readings] == ["nonexistent_local_time", "ambiguous_local_time", None]
    assert readings[2].observed_at == dt.datetime(2025, 11, 2, 8, 30, tzinfo=UTC)   # EST = UTC-5


def test_local_display_boundaries_across_dst_come_from_stored_utc_instants(harness):
    """Stored instants are UTC, so a day that is 23 or 25 local hours long still has
    every reading exactly once and in order."""
    from zoneinfo import ZoneInfo
    zone = ZoneInfo("America/New_York")
    start = dt.datetime(2025, 11, 2, 4, 0, tzinfo=UTC)       # 00:00 EDT on fall-back day
    instants = [start + dt.timedelta(minutes=30 * i) for i in range(50)]   # 25 local hours
    local = [i.astimezone(zone) for i in instants]
    assert local[0].date() == local[-1].date() == dt.date(2025, 11, 2)
    assert [l.strftime("%H:%M") for l in local].count("01:30") == 2       # the repeated hour
    assert sorted(instants) == instants and len(set(instants)) == 50


def test_unrecognized_or_empty_files_are_refused(harness):
    headers = {**harness.register(), "Content-Type": "text/csv"}
    for content, params in ((b"", {"format": "dexcom_clarity_csv"}),
                            (b"a,b,c\n1,2,3\n", {"format": "dexcom_clarity_csv"}),
                            (b"a,b,c\n1,2,3\n", {"format": "libreview_csv"}),
                            (b"\xff\xfe\x00\xd8\x00", {"format": "libreview_csv"}),
                            (CLARITY.encode(), {"format": "nightscout"})):
        assert harness.client.post("/api/v2/cgm/import", params=params, headers=headers,
                                   content=content).status_code == 422
