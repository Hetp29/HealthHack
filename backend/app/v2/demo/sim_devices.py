"""Simulated connected-health sources for the demonstration accounts.

Everything this module produces is INVENTED and says so. It demonstrates how
wearable data flows into GlucoTwin -- ingestion, storage, provenance, the timeline --
without contacting Apple, Fitbit, WHOOP or anyone else, and without a token, an OAuth
exchange or a callback of any kind.

How a simulated source is kept unmistakable:

  * its provider id starts `demo_` (`demo_apple_watch`), never a real provider's id;
  * its connection status is `demo_simulated` -- a status of its own, so `authorized`
    and `connected` keep meaning what they mean;
  * every observation is stored with `data_class: "demo"` and a `detail` block that
    says `simulated: true`, which profile produced it and under which generator version;
  * it can only be activated through the demo-only routes, by a demo replay account.

The glucose in the demonstration is NOT simulated: it is the CGMacros research
recording, replayed by `replay.py` and labelled `research_dataset`. This module never
creates, changes or reads a glucose value.

**None of this reaches the forecast.** The frozen model's inputs are assembled in
`services/history.py`, which does not read the wearable store. The values here come
from a fixed seed and the calendar. The only thing taken from the recording is the
TIME of each logged meal, used so that a simulated sleep or workout never sits on top
of a recorded meal; no glucose value, macronutrient or forecast is read.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.demo.source import Bundle
from backend.app.v2.deps import V2Context
from backend.app.v2.providers.base import WearableObservation
from backend.app.v2.services import wearables

SIMULATION_VERSION = "sim-1"
#: Fixed. Changing it changes every simulated value; bump SIMULATION_VERSION with it.
SEED = "glucotwin-demo-devices-2026"
STATUS_SIMULATED = "demo_simulated"
PROVIDER_PREFIX = "demo_"

DISCLAIMER = (
    "Simulated sample data for demonstration. No Apple, Fitbit or WHOOP account is "
    "connected and no real device produced these values. They are not inputs to the "
    "glucose forecast."
)

#: The profiles a demo account can switch on. `kind` is what the real thing is, so
#: the interface can describe it correctly: Apple Health is an aggregation platform,
#: not a sensor.
PROFILES: dict[str, dict] = {
    "apple_health": {
        "label": "Apple Health", "kind": "health_data_platform",
        "description": "Simulated Apple Health. Apple Health is a platform that aggregates "
                       "data from other devices and apps; it is not itself a sensor.",
        "metrics": ("steps_daily", "resting_heart_rate", "sleep_duration", "workout"),
        "notes": {"aggregation": "Its daily steps and workouts repeat the simulated Apple "
                                 "Watch's, as an aggregator would."},
    },
    "apple_watch": {
        "label": "Apple Watch", "kind": "wearable_device",
        "description": "Simulated Apple Watch.",
        "metrics": ("heart_rate", "steps_daily", "workout"),
        "notes": {"heart_rate": "One simulated sample every 15 minutes."},
    },
    "fitbit": {
        "label": "Fitbit", "kind": "wearable_device",
        "description": "Simulated Fitbit.",
        "metrics": ("heart_rate", "steps_daily", "sleep_duration", "resting_heart_rate"),
        "notes": {"heart_rate": "One simulated sample every 30 minutes."},
    },
    "whoop": {
        "label": "WHOOP", "kind": "wearable_device",
        "description": "Simulated WHOOP.",
        "metrics": ("recovery_score", "sleep_duration", "resting_heart_rate", "hrv_rmssd"),
        "notes": {"heart_rate": "None. WHOOP's developer API does not expose a continuous "
                                "heart-rate stream, so the simulation does not invent one."},
    },
}


def provider_of(profile: str) -> str:
    return PROVIDER_PREFIX + profile


def _rng(*parts: str) -> random.Random:
    digest = hashlib.sha256("|".join((SEED, SIMULATION_VERSION, *parts)).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _day_model(day: dt.date) -> dict:
    """One simulated person-day, shared by every profile so the sources agree with
    each other. A function of the calendar date and the fixed seed only."""
    rng = _rng("day", day.isoformat())
    weekend = day.weekday() >= 5
    return {
        "steps": int(rng.gauss(9200 if weekend else 7800, 1300)),
        "sleep_minutes": round(rng.gauss(445 if weekend else 415, 28)),
        "wake": dt.time(7 if weekend else 6, rng.choice((0, 15, 30, 45))),
        "resting_hr": round(rng.gauss(61, 2.0)),
        "hrv": round(rng.gauss(48, 6.0), 1),
        "recovery": int(max(20, min(98, rng.gauss(66, 13)))),
        "workout": None if rng.random() < 0.35 else {
            "start": dt.time(17 + rng.choice((0, 1)), rng.choice((0, 15, 30))),
            "minutes": rng.choice((25, 30, 40, 45)),
            "sport": rng.choice(("walking", "running", "cycling")),
        },
    }


#: Small, fixed per-device differences: two real trackers never agree exactly.
_DEVICE_BIAS = {
    "apple_watch": {"steps": 1.00, "sleep": 0, "rhr": 0},
    "apple_health": {"steps": 1.00, "sleep": -6, "rhr": 0},
    "fitbit": {"steps": 1.04, "sleep": 9, "rhr": 1},
    "whoop": {"steps": 1.00, "sleep": -12, "rhr": -1},
}


def generate(profile: str, bundle: Bundle) -> list[tuple[dt.datetime, WearableObservation]]:
    """Every simulated observation of one profile across the replay window, as
    `(available_at, observation)` in order.

    `available_at` is when the value could first exist: a heart-rate sample at its own
    instant, a daily total at the end of its day, a sleep session when it ends. The
    replay releases an observation only once its position has reached that instant.
    """
    zone = ZoneInfo(bundle.timezone)
    start, end = bundle.cgm[0][0], bundle.end
    bias = _DEVICE_BIAS[profile]
    wanted = set(PROFILES[profile]["metrics"])
    out: list[tuple[dt.datetime, WearableObservation]] = []

    def detail(**extra) -> dict:
        return {"simulated": True, "device_profile": profile, "source_label": PROFILES[profile]["label"],
                "simulation_version": SIMULATION_VERSION, **extra}

    def add(available: dt.datetime, metric: str, value: float, at: dt.datetime, key: str,
            end_at: dt.datetime | None = None, **extra) -> None:
        if metric in wanted and start <= at and available <= end:
            out.append((available, WearableObservation(
                metric=metric, value=float(value), unit=wearables.METRIC_UNITS[metric],
                observed_at=at, observed_end_at=end_at,
                source_record_id=f"sim:{profile}:{metric}:{key}", detail=detail(**extra))))

    def local(day: dt.date, clock: dt.time) -> dt.datetime:
        return dt.datetime.combine(day, clock, tzinfo=zone).astimezone(core.UTC)

    day = start.astimezone(zone).date()
    last_day = end.astimezone(zone).date()
    workouts: list[tuple[dt.datetime, dt.datetime]] = []
    sleeps: list[tuple[dt.datetime, dt.datetime]] = []
    wakes: dict[dt.date, dt.datetime] = {}
    meal_times = [meal.occurred_at for meal in bundle.meals]
    while day <= last_day:
        model = _day_model(day)
        key = day.isoformat()
        midnight, next_midnight = local(day, dt.time(0)), local(day + dt.timedelta(days=1), dt.time(0))
        wake = local(day, model["wake"])

        wakes[day] = wake
        add(next_midnight, "steps_daily", max(0, round(model["steps"] * bias["steps"])), midnight, key,
            end_at=next_midnight, local_date=key)
        # Coherence with the recording: nobody is asleep while logging a meal. If a
        # recorded meal falls in the planned night, sleep starts 45 minutes after it.
        planned_onset = wake - dt.timedelta(minutes=model["sleep_minutes"] + 22)
        night_meals = [m for m in meal_times if planned_onset - dt.timedelta(minutes=45) <= m < wake]
        onset = max([planned_onset] + [m + dt.timedelta(minutes=45) for m in night_meals])
        asleep = (wake - onset).total_seconds() / 60.0 - 22
        if asleep >= 120:
            sleeps.append((onset, wake))
            add(wake, "sleep_duration", round(asleep + bias["sleep"]), onset, key, end_at=wake)
        add(wake, "resting_heart_rate", model["resting_hr"] + bias["rhr"], wake, key, local_date=key)
        add(wake, "hrv_rmssd", model["hrv"], wake, key, measured_during="sleep")
        add(wake, "recovery_score", model["recovery"], wake, key)
        if model["workout"]:
            begin = local(day, model["workout"]["start"])
            finish = begin + dt.timedelta(minutes=model["workout"]["minutes"])
            # ...and nobody logs a meal in the middle of a run: skip that day's workout.
            if not any(begin - dt.timedelta(minutes=20) <= m <= finish for m in meal_times):
                workouts.append((begin, finish))
                add(finish, "workout", model["workout"]["minutes"], begin, key, end_at=finish,
                    sport=model["workout"]["sport"])
        day += dt.timedelta(days=1)

    if "heart_rate" in wanted:
        step = dt.timedelta(minutes=15 if profile == "apple_watch" else 30)
        rng = _rng("heart_rate", profile, bundle.scenario_id)
        moment = start.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        while moment <= end:
            clock = moment.astimezone(zone)
            hour = clock.hour + clock.minute / 60.0
            resting = _day_model(clock.date())["resting_hr"] + bias["rhr"]
            asleep = any(onset <= moment < wake for onset, wake in sleeps)
            value = resting + (2 if asleep else 14 + 6 * math.sin((hour - 9) / 14 * math.pi))
            if any(begin <= moment <= finish for begin, finish in workouts):
                value = resting + 62 + rng.gauss(0, 6)
            add(moment, "heart_rate", round(value + rng.gauss(0, 2.5)), moment,
                moment.strftime("%Y%m%dT%H%M"))
            moment += step

    out.sort(key=lambda item: (item[0], item[1].metric, item[1].source_record_id))
    return out


# --------------------------------------------------------------------------- #
# activation and release
# --------------------------------------------------------------------------- #


def _check(profile: str) -> str:
    if profile not in PROFILES:
        raise core.V2Error(404, "unknown_demo_device", "No such simulated device profile.",
                           {"allowed": sorted(PROFILES)})
    return profile


def active_profiles(ctx: V2Context, user_id: str) -> list[str]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(select(t.device_connections.c.provider).where(and_(
            t.device_connections.c.user_id == user_id,
            t.device_connections.c.status == STATUS_SIMULATED))).scalars().all()
    return sorted(p[len(PROVIDER_PREFIX):] for p in rows if p[len(PROVIDER_PREFIX):] in PROFILES)


def _ingest(ctx: V2Context, user_id: str, profile: str, bundle: Bundle,
            after: dt.datetime | None, upto: dt.datetime) -> int:
    due = [obs for available, obs in generate(profile, bundle)
           if available <= upto and (after is None or available > after)]
    if not due:
        return 0
    # Through the ordinary wearable ingestion service: same validation, same units,
    # same de-duplication as any real source. `received_at` is the replay position.
    with core.replay_clock(upto), ctx.engine.begin() as conn:
        counts = wearables.ingest(conn, ctx, user_id, provider_of(profile), due, data_class="demo")
    if counts["rejected"]:
        raise core.V2Error(500, "demo_device_release_failed",
                           "Simulated observations were rejected by ingestion.",
                           {"profile": profile, "rejected": counts["rejected_by_reason"]})
    return counts["inserted"]


def activate(ctx: V2Context, user_id: str, profile: str, bundle: Bundle, position: dt.datetime) -> dict:
    """Switch a simulated source on for a demo account. Idempotent.

    Creates a connection row with status `demo_simulated` -- no token, no scope, no
    external account -- and makes that source's simulated history up to the current
    replay position visible. Later observations appear as the replay advances.
    """
    _check(profile)
    provider = provider_of(profile)
    with core.replay_clock(position), ctx.engine.begin() as conn:
        existing = conn.execute(select(t.device_connections.c.status).where(and_(
            t.device_connections.c.user_id == user_id,
            t.device_connections.c.provider == provider))).scalar()
        now = ctx.now()
        if existing is None:
            conn.execute(t.device_connections.insert().values(
                connection_id=core.new_id("dev"), user_id=user_id, provider=provider,
                kind="wearable", status=STATUS_SIMULATED, scopes=[],
                last_sync_at=now, created_at=now, updated_at=now))
        elif existing != STATUS_SIMULATED:
            conn.execute(t.device_connections.update().where(and_(
                t.device_connections.c.user_id == user_id,
                t.device_connections.c.provider == provider))
                .values(status=STATUS_SIMULATED, updated_at=now))
    released = _ingest(ctx, user_id, profile, bundle, None, position)
    return {"profile": profile, "already_active": existing == STATUS_SIMULATED,
            "observations_released": released}


def release(ctx: V2Context, user_id: str, bundle: Bundle, after: dt.datetime, upto: dt.datetime) -> int:
    """Called whenever replay time moves: reveal what active sources recorded since."""
    return sum(_ingest(ctx, user_id, profile, bundle, after, upto)
               for profile in active_profiles(ctx, user_id))


# --------------------------------------------------------------------------- #
# read models
# --------------------------------------------------------------------------- #


def listing(ctx: V2Context, user_id: str, bundle: Bundle, position: dt.datetime) -> dict:
    active = set(active_profiles(ctx, user_id))
    w = t.wearable_observations
    with ctx.engine.connect() as conn:
        stored = {(provider, metric): (count, core.rfc3339(latest)) for provider, metric, count, latest in
                  conn.execute(select(w.c.provider, w.c.metric, func.count(), func.max(w.c.observed_at))
                               .where(w.c.user_id == user_id).group_by(w.c.provider, w.c.metric))}
        glucose_count, glucose_latest = conn.execute(
            select(func.count(), func.max(t.glucose_observations.c.observed_at))
            .where(t.glucose_observations.c.user_id == user_id)).one()
    profiles = []
    for name, spec in PROFILES.items():
        provider = provider_of(name)
        profiles.append({
            "profile": name, "provider": provider, "label": spec["label"], "kind": spec["kind"],
            "description": spec["description"],
            "status": STATUS_SIMULATED if name in active else "inactive",
            "simulated": True, "data_class": "demo",
            "display_label": f"{spec['label']} (simulated sample)",
            "authorization": "none - no account was linked and no provider was contacted",
            "metrics": [{
                "metric": metric, "unit": wearables.METRIC_UNITS[metric],
                "observations_visible": stored.get((provider, metric), (0, None))[0],
                "latest_observed_at": stored.get((provider, metric), (0, None))[1],
            } for metric in spec["metrics"]],
            "notes": spec["notes"],
        })
    research = bundle.data_class == "research_dataset"
    return {
        "position": core.rfc3339(position),
        "simulation_version": SIMULATION_VERSION,
        "disclaimer": DISCLAIMER,
        "profiles": profiles,
        "glucose_source": {
            "provider": bundle.store_labels[0], "label": (
                "CGMacros research recording (Dexcom G6 Pro)" if research else "Generated test fixture"),
            "kind": "historical_replay", "simulated": False if research else True,
            "data_class": bundle.store_labels[1],
            "status": "replaying",
            "description": (
                "Genuine historical glucose readings from a research dataset, released in "
                "order as the replay advances. Not a live sensor and not a connected Dexcom "
                "account." if research else "Generated arithmetic. Not real data."),
            "readings_visible": glucose_count, "latest_observed_at": core.rfc3339(glucose_latest),
        },
        "used_by_forecast_model": {
            "glucose_source": True,
            "simulated_profiles": False,
            "explanation": "The frozen fs-1.3 forecast uses the glucose history, the confirmed "
                           "meal's macronutrients, meal timing and the account's own completed "
                           "meal responses. It does not use heart rate, steps, sleep, recovery, "
                           "workouts or medication records.",
        },
    }


def timeline(ctx: V2Context, user_id: str, start: dt.datetime, end: dt.datetime, limit: int) -> list[dict]:
    """Meals, forecasts' alerts, dose actions and wearable summaries in one ordered
    list, each tagged with where it came from. Heart-rate samples and glucose
    readings are series, not events: read them from their own endpoints."""
    items: list[dict] = []
    w, m, a, d = t.wearable_observations, t.meals, t.alert_events, t.medication_doses
    with ctx.engine.connect() as conn:
        for row in conn.execute(select(w).where(and_(
                w.c.user_id == user_id, w.c.metric != "heart_rate",
                w.c.received_at >= start, w.c.received_at <= end))).mappings():
            items.append({
                "at": row["observed_end_at"] or row["observed_at"], "type": "wearable",
                "data_class": row["data_class"], "simulated": bool(row["detail"].get("simulated")),
                "source": row["detail"].get("source_label", row["provider"]), "provider": row["provider"],
                "metric": row["metric"], "value": row["value"], "unit": row["unit"],
                "observed_at": core.rfc3339(row["observed_at"]),
                "observed_end_at": core.rfc3339(row["observed_end_at"]),
                "detail": {k: v for k, v in row["detail"].items() if k in ("sport", "local_date")},
                "used_by_forecast_model": False})
        for row in conn.execute(select(m).where(and_(
                m.c.user_id == user_id, m.c.status != "voided",
                m.c.occurred_at >= start, m.c.occurred_at <= end))).mappings():
            research = (row["note"] or "").startswith("Research dataset")
            items.append({"at": row["occurred_at"], "type": "meal", "meal_id": row["meal_id"],
                          "data_class": "research_dataset" if research else "demo",
                          "simulated": False, "source": row["note"], "used_by_forecast_model": True})
        for row in conn.execute(select(a).where(and_(
                a.c.user_id == user_id, a.c.created_at >= start, a.c.created_at <= end))).mappings():
            items.append({"at": row["created_at"], "type": "alert", "alert_id": row["alert_id"],
                          "status": row["status"], "data_class": "model_output", "simulated": False,
                          "source": "GlucoTwin forecast alert (informational)"})
        for row in conn.execute(select(d).where(and_(
                d.c.user_id == user_id, d.c.action != "scheduled",
                d.c.recorded_at >= start, d.c.recorded_at <= end))).mappings():
            items.append({"at": row["recorded_at"], "type": "medication_dose", "dose_id": row["dose_id"],
                          "action": row["action"], "data_class": "fictional", "simulated": True,
                          "source": "Fictional medication record", "used_by_forecast_model": False})
    items.sort(key=lambda item: (item["at"], item["type"], item.get("metric", "")))
    items = items[-limit:]
    for item in items:
        item["at"] = core.rfc3339(item["at"])
    return items


def digest(ctx: V2Context, user_id: str) -> str:
    """Fingerprint of an account's simulated wearable data, for determinism checks."""
    w = t.wearable_observations
    with ctx.engine.connect() as conn:
        rows = [[provider, metric, source_id, value, unit, core.rfc3339(at), core.rfc3339(end_at),
                 core.rfc3339(received), data_class]
                for provider, metric, source_id, value, unit, at, end_at, received, data_class in
                conn.execute(select(w.c.provider, w.c.metric, w.c.source_record_id, w.c.value, w.c.unit,
                                    w.c.observed_at, w.c.observed_end_at, w.c.received_at, w.c.data_class)
                             .where(w.c.user_id == user_id)
                             .order_by(w.c.provider, w.c.metric, w.c.source_record_id))]
    return hashlib.sha256(repr(rows).encode()).hexdigest()
