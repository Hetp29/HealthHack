"""D-006 as ruled: IANA, observation-time-specific local-clock conversion.

America/New_York in 2026: spring forward at 2026-03-08T07:00Z (EST -5 -> EDT -4),
fall back at 2026-11-01T06:00Z (EDT -4 -> EST -5).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import zlib

import pytest
from sqlalchemy import select

from backend.app.v2.db import tables as t
from backend.app.v2.services import history
from ml.features import FEATURE_NAMES
from tests.v2.conftest import UTC, snapshot_features

NY = "America/New_York"
FALL_BACK = dt.datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
SPRING_FORWARD = dt.datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
CLOCK_FEATURES = {"tod_sin", "tod_cos", "g_dev_from_night"}


@pytest.mark.parametrize("zone,start,end,expected", [
    (NY, FALL_BACK - dt.timedelta(days=3), FALL_BACK + dt.timedelta(days=3), FALL_BACK),
    (NY, SPRING_FORWARD - dt.timedelta(days=3), SPRING_FORWARD + dt.timedelta(days=3), SPRING_FORWARD),
    (NY, FALL_BACK, FALL_BACK + dt.timedelta(days=8), None),                  # change is not inside (start, end]
    (NY, FALL_BACK - dt.timedelta(days=8), FALL_BACK - dt.timedelta(seconds=1), None),
    (NY, FALL_BACK - dt.timedelta(hours=1), FALL_BACK, FALL_BACK),            # exactly at the change
    ("Asia/Tokyo", SPRING_FORWARD - dt.timedelta(days=8), SPRING_FORWARD + dt.timedelta(days=8), None),
    ("UTC", FALL_BACK - dt.timedelta(days=8), FALL_BACK + dt.timedelta(days=8), None),
    ("Australia/Lord_Howe", dt.datetime(2026, 4, 1, tzinfo=UTC), dt.datetime(2026, 4, 8, tzinfo=UTC),
     dt.datetime(2026, 4, 4, 15, 0, tzinfo=UTC)),                             # a 30-minute shift
])
def test_offset_transitions_are_located_to_the_second(zone, start, end, expected):
    assert history.last_offset_transition(zone, start, end) == expected


def test_each_observation_is_converted_with_its_own_offset():
    before, after = FALL_BACK - dt.timedelta(minutes=30), FALL_BACK + dt.timedelta(minutes=30)
    assert history.clock_offset(NY, before) == dt.timedelta(hours=-4)
    assert history.clock_offset(NY, after) == dt.timedelta(hours=-5)
    # 05:30Z is 01:30 EDT; 06:30Z is 01:30 EST -- the repeated local hour.
    assert history.project_local_clock(before, NY).strftime("%H:%M") == "01:30"
    assert history.project_local_clock(after, NY).strftime("%H:%M") == "01:30"
    # Which is exactly why a history may not span the change: local time went backwards.
    assert history.project_local_clock(after, NY) == history.project_local_clock(before, NY)


def _snapshot(h, prediction_id):
    with h.ctx.engine.connect() as conn:
        row = conn.execute(
            select(t.feature_snapshots.c.history_blob, t.feature_snapshots.c.context)
            .select_from(t.feature_snapshots.join(t.predictions))
            .where(t.predictions.c.prediction_id == prediction_id)).one()
    return json.loads(zlib.decompress(row[0])), row[1]


def _forecast(h, email, zone, now, cgm_start):
    h.clock.now = now
    headers = h.register(email, timezone=zone)
    h.push_cgm(headers, cgm_start, now)
    response = h.predict(headers, h.meal(headers, now))
    assert response.status_code == 200, response.text
    return headers, response.json()


@pytest.mark.parametrize("transition,local_offset_after", [(FALL_BACK, -5), (SPRING_FORWARD, -4)])
def test_history_is_cut_at_the_offset_change_and_stays_a_true_timeline(harness, transition, local_offset_after):
    now = transition + dt.timedelta(days=3, hours=11)
    _, body = _forecast(harness, "dst@example.com", NY, now, now - dt.timedelta(days=6))
    canonical, context = _snapshot(harness, body["prediction_id"])

    assert context["clock_policy"] == "iana_per_observation"
    assert context["lookback_truncated_at_offset_change"] is True
    assert context["offset_change_at"] == transition.isoformat().replace("+00:00", "Z")
    assert body["context_quality"]["history_truncated_at_offset_change"] is True
    assert any("changed its UTC offset" in n for n in body["forecast"]["limitations"])

    stamps = [dt.datetime.fromisoformat(ts) for ts, _ in canonical["cgm"]]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)      # ascending, no repeats
    shift = dt.timedelta(hours=local_offset_after)
    assert stamps[0] - shift >= transition                                    # nothing from before
    assert stamps[0] - shift < transition + dt.timedelta(minutes=5)           # and nothing dropped after
    # Every difference is true elapsed time: 5-minute cadence throughout.
    assert {(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])} == {300.0}
    assert body["forecast"]["meal_timestamp"] == now.isoformat().replace("+00:00", "Z")

    features = snapshot_features(harness, body["prediction_id"])
    local = now + shift
    angle = 2 * math.pi * (local.hour * 60 + local.minute) / 1440.0
    assert features["tod_sin"] == pytest.approx(math.sin(angle), abs=1e-9)
    assert features["tod_cos"] == pytest.approx(math.cos(angle), abs=1e-9)


def test_truncated_history_equals_a_fresh_account_holding_only_the_later_data(harness):
    """The cut changes WHICH observations are shown and nothing about how they are read."""
    now = FALL_BACK + dt.timedelta(days=2, hours=12)
    _, cut = _forecast(harness, "cut@example.com", NY, now, now - dt.timedelta(days=6))
    _, fresh = _forecast(harness, "fresh@example.com", NY, now, FALL_BACK)
    a = snapshot_features(harness, cut["prediction_id"])
    b = snapshot_features(harness, fresh["prediction_id"])
    assert a == b
    assert cut["forecast"]["points"] == fresh["forecast"]["points"]


def test_elapsed_time_features_do_not_depend_on_the_zone_across_a_change(harness):
    now = FALL_BACK + dt.timedelta(days=1, hours=12)
    _, ny = _forecast(harness, "ny@example.com", NY, now, FALL_BACK)
    _, tokyo = _forecast(harness, "tokyo@example.com", "Asia/Tokyo", now, FALL_BACK)
    a, b = snapshot_features(harness, ny["prediction_id"]), snapshot_features(harness, tokyo["prediction_id"])
    for name in FEATURE_NAMES:
        if name not in CLOCK_FEATURES:
            assert a[name] == b[name], name
    assert tokyo["context_quality"]["history_truncated_at_offset_change"] is False


@pytest.mark.parametrize("delta,truncated", [
    (dt.timedelta(days=-1), False),             # the day before: old offset throughout
    (dt.timedelta(days=7, hours=23), True),     # still inside the 8-day lookback
    (dt.timedelta(days=8), False),              # the change is no longer inside (start, end]
    (dt.timedelta(days=30), False),
])
def test_truncation_applies_only_while_the_change_is_inside_the_lookback(harness, delta, truncated):
    now = FALL_BACK + delta
    _, body = _forecast(harness, "w@example.com", NY, now, now - dt.timedelta(days=9))
    assert body["context_quality"]["history_truncated_at_offset_change"] is truncated
    canonical, _ = _snapshot(harness, body["prediction_id"])
    if not truncated:
        assert len(canonical["cgm"]) == 8 * 288 + 1     # the full 8 days
    else:
        assert len(canonical["cgm"]) < 8 * 288 + 1


def test_minutes_after_a_change_a_forecast_uses_only_post_change_readings(harness):
    now = FALL_BACK + dt.timedelta(minutes=12)
    _, body = _forecast(harness, "soon@example.com", NY, now, now - dt.timedelta(days=2))
    canonical, _ = _snapshot(harness, body["prediction_id"])
    assert len(canonical["cgm"]) == 3            # 06:02, 06:07, 06:12
    # Short-window inputs that need more data are reported missing, not invented.
    assert "cgm_std_60min_mg_dl" not in body["context_quality"]["missing_features"]
    assert "g_dev_from_night" in body["context_quality"]["missing_features"]


def test_no_post_change_reading_means_an_explicit_refusal(harness):
    harness.clock.now = FALL_BACK + dt.timedelta(minutes=4)
    headers = harness.register("gap@example.com", timezone=NY)
    harness.push_cgm(headers, FALL_BACK - dt.timedelta(hours=3), FALL_BACK - dt.timedelta(minutes=1))
    response = harness.predict(headers, harness.meal(headers, harness.clock.now))
    assert response.status_code == 422
    assert response.json()["code"] == "insufficient_premeal_context"


def test_a_meal_from_before_the_change_still_supplies_true_elapsed_timing(harness):
    now = FALL_BACK + dt.timedelta(hours=3)
    harness.clock.now = now
    headers = harness.register("meal@example.com", timezone=NY)
    earlier = FALL_BACK - dt.timedelta(hours=2)            # 5 real hours before `now`
    harness.push_cgm(headers, now - dt.timedelta(days=2), now, meals=[earlier])
    assert harness.predict(headers, harness.meal(headers, earlier)).status_code == 200
    body = harness.predict(headers, harness.meal(headers, now)).json()
    features = snapshot_features(harness, body["prediction_id"])
    assert features["minutes_since_prior_meal"] == 300.0   # true elapsed, not wall-clock 240 or 360
    assert features["r_hist_n"] == 0                        # not shown to the engine as history
    canonical, context = _snapshot(harness, body["prediction_id"])
    assert canonical["meals"] == [] and context["prior_meals"]["prior_meal_timing_from_override"] is True


def test_storage_keeps_true_utc_regardless_of_projection(harness):
    now = FALL_BACK + dt.timedelta(hours=2)
    headers, body = _forecast(harness, "store@example.com", NY, now, FALL_BACK - dt.timedelta(hours=2))
    readings = harness.client.get("/api/v2/cgm/readings", headers=headers, params={
        "start": (FALL_BACK - dt.timedelta(hours=2)).isoformat(), "end": now.isoformat()}).json()["readings"]
    stamps = [r["observed_at"] for r in readings]
    assert len(stamps) == 49 and stamps == sorted(stamps) and len(set(stamps)) == 49
    assert stamps[0] == "2026-11-01T04:00:00Z" and stamps[-1] == "2026-11-01T08:00:00Z"
    with harness.ctx.engine.connect() as conn:
        as_of = conn.execute(select(t.feature_snapshots.c.as_of)).scalar()
        meal = conn.execute(select(t.meals.c.occurred_at)).scalar()
    assert as_of == now and meal == now


def test_dst_window_forecasts_reproduce_exactly(harness):
    now = SPRING_FORWARD + dt.timedelta(days=2, hours=9)
    headers, body = _forecast(harness, "repro@example.com", NY, now, now - dt.timedelta(days=5))
    result = harness.client.post(f"/api/v2/predictions/{body['prediction_id']}/reproduce", headers=headers).json()
    assert result["reproduced"] is True and result["history_sha256_verified"] is True
