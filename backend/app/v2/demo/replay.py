"""Deterministic chronological replay of a research recording through the real API.

Design, and why (DECISIONS D-014):

**Future data is not in the database.** A demo account's store holds only what has
been *released*: observations at or before its replay position. Everything later
stays in the bundle file. So no v2 query -- forecast, status, readings, outcome --
can see the future, without any of them needing to know a replay exists. The
alternative, loading everything and filtering by a replay time in every query, was
rejected: one forgotten filter would be a silent leak.

**Historical timestamps are kept.** Nothing is re-dated to today. For a demo account
"now" is its replay position (`core.clock_override`), so a 2023 meal is forecast at
its 2023 instant, prospectively, exactly as a live meal would be.

**Everything goes through the public API.** Each step calls the same endpoints a
client would -- register, consent, meals, predict, outcome, medications -- through an
in-process client. The only direct writes are the staged-glucose release (there is no
public route for a research source) and the replay-state row. No forecast, outcome or
alert is written by this module; they are produced by the application.

Four clocks, never conflated:
  * `observed_at` / `occurred_at` -- when the data was really recorded (historical);
  * `position`                    -- the simulated instant the replay has reached;
  * wall clock                    -- when the demo is actually being run;
  * `received_at`                 -- the replay position at which a reading was released.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import threading
from zoneinfo import ZoneInfo

from sqlalchemy import select
from starlette.testclient import TestClient

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.demo import profile, source
from backend.app.v2.demo.source import Bundle
from backend.app.v2.deps import V2Context
from backend.app.v2.services import glucose
from backend.app.v2.services.glucose import IncomingReading

logger = logging.getLogger("glucotwin.v2.demo")

#: Re-entrant: initializing the returning-user mode advances through its backfill.
_LOCK = threading.RLock()
_BUNDLE_CACHE: dict[tuple, Bundle] = {}

KIND_ORDER = {"outcome_reveal": 0, "dose_action": 1, "meal_forecast": 2}


def demo_error(status: int, code: str, message: str, details: dict | None = None) -> core.V2Error:
    return core.V2Error(status, code, message, details)


DEFAULT_MODE = "new_user"


def _check_mode(mode: str) -> str:
    if mode not in source.MODES:
        raise demo_error(422, "unknown_demo_scenario", "No such demo scenario.",
                         {"allowed": sorted(source.MODES)})
    return mode


def demo_email(ctx: V2Context, mode: str) -> str:
    return getattr(ctx.settings, source.MODES[mode]["email_setting"])


def get_bundle(ctx: V2Context, mode: str = DEFAULT_MODE) -> Bundle:
    """The bundle one demo mode replays, verified against its committed manifest."""
    _check_mode(mode)
    override = getattr(ctx, "demo_bundle", None)
    if isinstance(override, dict):
        if mode in override:
            return override[mode]
    elif override is not None and mode == DEFAULT_MODE:
        return override
    path = getattr(ctx.settings, source.MODES[mode]["bundle_setting"])
    try:
        manifest = source.load_manifest(source.manifest_path(mode))
        key = (mode, str(path), path.stat().st_mtime_ns if path.exists() else None)
        if key not in _BUNDLE_CACHE:
            for stale in [k for k in _BUNDLE_CACHE if k[0] == mode]:
                del _BUNDLE_CACHE[stale]
            _BUNDLE_CACHE[key] = source.load_bundle(path, expected_sha256=manifest["bundle_sha256"])
        return _BUNDLE_CACHE[key]
    except source.BundleError as error:
        raise demo_error(503, "demo_data_unavailable",
                         f"[{mode}] {error} (for this scenario: `{source.MODES[mode]['select_hint']}`)"
                         if mode != DEFAULT_MODE else str(error)) from error


def mode_of(ctx: V2Context, user_id: str) -> str:
    """Which demo mode an account belongs to, from its email. Defaults to new_user."""
    with ctx.engine.connect() as conn:
        email = conn.execute(select(t.users.c.email).where(t.users.c.user_id == user_id)).scalar()
    return next((m for m in source.MODES if m != DEFAULT_MODE and demo_email(ctx, m) == email), DEFAULT_MODE)


def display_from(bundle: Bundle) -> int:
    """The first meal that is part of the presented journey (1 = no backfill)."""
    return int((bundle.source.get("plan") or {}).get("display_from_meal", 1))


# --------------------------------------------------------------------------- #
# timeline
# --------------------------------------------------------------------------- #


def build_timeline(bundle: Bundle) -> list[dict]:
    """Every event of the replay, in order. A pure function of the bundle."""
    events: list[dict] = []
    for number, meal in enumerate(bundle.meals, start=1):
        events.append({"id": f"meal_{number:02d}_forecast", "kind": "meal_forecast",
                       "at": meal.occurred_at, "meal": number, "checkpoint": True})
        reveal = meal.occurred_at + source.REVEAL_AFTER
        if reveal <= bundle.end:
            events.append({"id": f"meal_{number:02d}_outcome", "kind": "outcome_reveal",
                           "at": reveal, "meal": number, "checkpoint": True})

    zone = ZoneInfo(bundle.timezone)
    final_meal = bundle.meals[-1].occurred_at
    last_scripted = final_meal - dt.timedelta(hours=profile.LEAVE_UNANSWERED_HOURS)
    day = bundle.start.astimezone(zone).date()
    slot_number = 0
    while True:
        slots = [dt.datetime.combine(day, dt.time.fromisoformat(hhmm), tzinfo=zone).astimezone(core.UTC)
                 for hhmm in profile.CURRENT_REGIMEN["schedule_times"]]
        if min(slots) > last_scripted:
            break
        for slot in sorted(slots):
            if bundle.start < slot <= last_scripted:
                slot_number += 1
                events.append({
                    "id": f"dose_{slot_number:02d}", "kind": "dose_action",
                    "at": slot + dt.timedelta(minutes=profile.DOSE_ACTION_DELAY_MINUTES),
                    "slot": slot, "checkpoint": False,
                    "action": ("skipped" if slot_number % profile.SKIP_MODULUS == profile.SKIP_REMAINDER
                               else "taken")})
        day += dt.timedelta(days=1)

    events.sort(key=lambda e: (e["at"], KIND_ORDER[e["kind"]], e["id"]))
    for index, event in enumerate(events):
        event["index"] = index
    return events


def _aliases(bundle: Bundle, timeline: list[dict]) -> dict[str, str]:
    """Named checkpoints, resolved to event ids."""
    final = len(bundle.meals)
    out = {"final_meal": f"meal_{final:02d}_forecast", "end": timeline[-1]["id"]}
    activation = (bundle.source.get("plan") or {}).get("activation_meal")
    if activation:
        out["retrieval_activation"] = f"meal_{activation:02d}_forecast"
    first = display_from(bundle)
    if first > 1:
        first_id = f"meal_{first:02d}_forecast"
        first_index = next(e["index"] for e in timeline if e["id"] == first_id)
        out["first_display_meal"] = first_id
        # The last checkpoint of the backfill: everything before the presented journey.
        out["backfill_complete"] = next(
            e["id"] for e in reversed(timeline[:first_index]) if e["checkpoint"])
    return out


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #


class _Api:
    """The public v2 API, called in-process as the demo user."""

    def __init__(self, app, ctx: V2Context, user_id: str) -> None:
        self._client = TestClient(app, raise_server_exceptions=True)
        self._ctx, self._user_id = ctx, user_id
        self._token = core.new_token()
        now = ctx.wall_now()
        with ctx.engine.begin() as conn:
            conn.execute(t.auth_sessions.insert().values(
                session_id=core.new_id("ses"), user_id=user_id,
                token_hash=core.sha256_hex(self._token), created_at=now,
                expires_at=now + dt.timedelta(minutes=30)))

    def call(self, method: str, path: str, **kwargs):
        return self._client.request(
            method, path, headers={"Authorization": f"Bearer {self._token}"}, **kwargs)

    def ok(self, method: str, path: str, **kwargs) -> dict:
        response = self.call(method, path, **kwargs)
        if response.status_code >= 400:
            raise demo_error(500, "demo_step_failed", "A demo setup step failed.",
                             {"request": f"{method} {path}", "status": response.status_code,
                              "code": response.json().get("code")})
        return response.json() if response.content else {}

    def close(self) -> None:
        with self._ctx.engine.begin() as conn:
            conn.execute(t.auth_sessions.delete().where(
                t.auth_sessions.c.token_hash == core.sha256_hex(self._token)))


def _state_row(ctx: V2Context, user_id: str) -> dict | None:
    with ctx.engine.connect() as conn:
        row = conn.execute(select(t.demo_replay_state).where(
            t.demo_replay_state.c.user_id == user_id)).mappings().first()
    return dict(row) if row else None


def _save(ctx: V2Context, user_id: str, **values) -> None:
    with ctx.engine.begin() as conn:
        conn.execute(t.demo_replay_state.update()
                     .where(t.demo_replay_state.c.user_id == user_id)
                     .values(**values, updated_at=ctx.wall_now()))


def _release(ctx: V2Context, user_id: str, bundle: Bundle, after: dt.datetime | None,
             upto: dt.datetime) -> int:
    """Move readings with `after < observed_at <= upto` from the bundle into the store.

    Values, units and timestamps are passed through untouched. The ordinary ingestion
    path stores them, so validation and de-duplication apply as for any source.
    """
    readings = bundle.readings_between(after, upto)
    if not readings:
        return 0
    provider, data_class = bundle.store_labels
    with core.replay_clock(upto), ctx.engine.begin() as conn:
        summary = glucose.ingest_readings(
            conn, ctx, user_id, provider,
            [IncomingReading(value=value, unit="mg/dL", observed_at=at, device_id=bundle.device_id,
                             source_timestamp=at.isoformat()) for at, value in readings],
            source_format="demo_replay_release", data_class=data_class,
            source_sha256=bundle.sha256)
    counts = summary["counts"]
    if counts["rejected"] or counts["corrected"]:
        raise demo_error(500, "demo_release_failed", "Research readings were not stored cleanly.",
                         {"rejected": counts["rejected_by_reason"], "corrected": counts["corrected"]})
    return counts["inserted"]


def _demo_user_id(ctx: V2Context, mode: str = DEFAULT_MODE) -> str | None:
    with ctx.engine.connect() as conn:
        return conn.execute(
            select(t.users.c.user_id)
            .select_from(t.users.join(t.demo_replay_state))
            .where(t.users.c.email == demo_email(ctx, mode))).scalar()


def _require_enabled(ctx: V2Context) -> None:
    if not ctx.settings.demo_mode:
        raise demo_error(403, "demo_mode_disabled", "Demo mode is not enabled in this environment.")
    if not ctx.settings.demo_password:
        raise demo_error(503, "demo_not_configured", "GLUCOTWIN_DEMO_PASSWORD is not set.")


#: The only artifact a research-data demonstration may run on.
FROZEN_ARTIFACT_SHA256 = "12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe"


def _require_genuine_model(ctx: V2Context, bundle: Bundle) -> None:
    """Real research data is only ever replayed through the real frozen model, with
    its checksum enforced. There is no synthetic fallback. (A labelled test-fixture
    bundle may run on a development artifact; it can never be stored as research data.)"""
    if bundle.data_class != "research_dataset":
        return
    integrity = ctx.model_integrity
    if integrity.get("artifact_sha256") != FROZEN_ARTIFACT_SHA256:
        raise demo_error(
            503, "demo_requires_frozen_model",
            "The demonstration replays research data and requires the frozen fs-1.3 artifact. "
            "Set GLUCOTWIN_MODEL_ARTIFACT to ml/artifacts_cgmacros_fs13/model.joblib.",
            {"loaded_artifact_sha256": integrity.get("artifact_sha256")})
    if not (integrity.get("enforced") and integrity.get("verified")):
        raise demo_error(
            503, "demo_requires_frozen_model",
            "Set GLUCOTWIN_EXPECTED_MODEL_SHA256 so the artifact checksum is enforced.")
    if ctx.settings.personalization_policy != "history_only":
        raise demo_error(503, "demo_requires_level_a_only",
                         "Level B calibration is not approved; the demo runs with history_only.")


# --------------------------------------------------------------------------- #
# initialize / reset
# --------------------------------------------------------------------------- #


def initialize(app, ctx: V2Context, mode: str = DEFAULT_MODE) -> dict:
    """Create one demo mode's account at the start of its presented journey.

    `new_user` starts with no meals. `returning_user` first replays the recording's
    earlier meals -- each through the ordinary forecast, outcome and eligibility
    workflow, in order -- and stops just before its first presented meal. Fails if
    that mode's account already exists.
    """
    _require_enabled(ctx)
    bundle = get_bundle(ctx, mode)
    _require_genuine_model(ctx, bundle)
    with _LOCK:
        return _initialize(app, ctx, bundle, mode)


def _initialize(app, ctx: V2Context, bundle: Bundle, mode: str) -> dict:
    email = demo_email(ctx, mode)
    client = TestClient(app, raise_server_exceptions=True)
    registered = client.post("/auth/register", json={
        "email": email, "password": ctx.settings.demo_password,
        "timezone": bundle.timezone, "display_name": profile.DISPLAY_NAMES[mode]})
    if registered.status_code == 409:
        raise demo_error(409, "demo_already_initialized",
                         "The demo account already exists. Reset it instead.", {"scenario": mode})
    if registered.status_code != 201:
        raise demo_error(500, "demo_step_failed", "Could not create the demo account.",
                         {"status": registered.status_code, "code": registered.json().get("code")})
    user_id = registered.json()["user"]["user_id"]
    now = ctx.wall_now()
    with ctx.engine.begin() as conn:
        conn.execute(t.demo_replay_state.insert().values(
            user_id=user_id, scenario_id=bundle.scenario_id, bundle_sha256=bundle.sha256,
            position=bundle.start, event_index=0, status="ready", log=[],
            initialized_at=now, updated_at=now))

    # From here every call runs on the replay clock: the state row exists.
    api = _Api(app, ctx, user_id)
    try:
        for consent in ("necessary_processing", "personalization_history"):
            api.ok("PUT", f"/consents/{consent}", json={"state": "granted"})
        api.ok("PATCH", "/onboarding", json={"answers": profile.ONBOARDING_ANSWERS, "completed": True})
        threshold = float((bundle.source.get("plan") or {}).get("alert_threshold_mg_dl", 180.0))
        api.ok("PUT", "/alert-settings", json={
            "enabled": True, "threshold": {"value": threshold, "unit": "mg/dL"},
            "channels": ["in_app"], "cooldown_minutes": profile.ALERT_COOLDOWN_MINUTES})
        api.ok("POST", "/medications", json=profile.CURRENT_REGIMEN)
        stopped = api.ok("POST", "/medications", json=profile.DISCONTINUED_REGIMEN)
        api.ok("PATCH", f"/medications/{stopped['regimen_id']}", json={"status": "discontinued"})
    finally:
        api.close()

    released = _release(ctx, user_id, bundle, None, bundle.start)
    logger.info("demo_initialized mode=%s scenario=%s readings_released=%d",
                mode, bundle.scenario_id, released)
    if display_from(bundle) > 1:
        # The backfill is not inserted: it is replayed, meal by meal, in order.
        return advance(app, ctx, user_id, to="backfill_complete")
    return state(ctx, user_id)


def reset(app, ctx: V2Context, mode: str = DEFAULT_MODE) -> dict:
    """Return one demo mode to its initial state.

    Deletes that mode's demo account through the public account-deletion endpoint --
    which removes that one user's rows and nothing else -- and initializes it again.
    Only ever touches the account whose email is that mode's demo email AND that has a
    replay-state row: never an ordinary account, never the other demo mode.
    """
    _require_enabled(ctx)
    bundle = get_bundle(ctx, mode)
    _require_genuine_model(ctx, bundle)
    with _LOCK:
        user_id = _demo_user_id(ctx, mode)
        if user_id is not None:
            api = _Api(app, ctx, user_id)
            response = api.call("DELETE", "/account", json={"password": ctx.settings.demo_password})
            if response.status_code != 204:
                api.close()
                raise demo_error(500, "demo_step_failed", "Could not delete the demo account.",
                                 {"status": response.status_code})
        else:
            with ctx.engine.connect() as conn:
                clash = conn.execute(select(t.users.c.user_id).where(
                    t.users.c.email == demo_email(ctx, mode))).scalar()
            if clash is not None:
                # An ordinary account holds the demo email. Never delete it.
                raise demo_error(409, "demo_email_in_use",
                                 "A non-demo account uses the demo email; refusing to touch it.")
        return _initialize(app, ctx, bundle, mode)


# --------------------------------------------------------------------------- #
# advance
# --------------------------------------------------------------------------- #


def _meal_label(bundle: Bundle) -> str:
    """How a replayed meal says where it came from. A fixture is never called research."""
    if bundle.data_class == "research_dataset":
        return f"Research dataset meal record ({bundle.source.get('dataset', 'dataset')})"
    return "Generated test-fixture meal record (not real data)"


def _run_meal(api: _Api, bundle: Bundle, event: dict) -> dict:
    meal = bundle.meals[event["meal"] - 1]
    created = api.ok("POST", "/meals", json={
        "occurred_at": meal.occurred_at.isoformat(), "macros": meal.macros,
        "note": f"{_meal_label(bundle)}: {meal.source_meal_id}"})
    record = {"meal_id": created["meal_id"], "source_meal_id": meal.source_meal_id}
    response = api.call("POST", f"/meals/{created['meal_id']}/predict")
    body = response.json()
    if response.status_code != 200:
        # A refusal is a legitimate outcome of the real rules; it is recorded, not hidden.
        return {**record, "forecast": "refused", "code": body.get("code")}
    alert = body.get("alert")
    return {**record, "forecast": "created", "prediction_id": body["prediction_id"],
            "prediction_kind": body["prediction_kind"],
            "admissible_prior_meals": body["personalization"]["admissible_prior_meals"],
            "retrieval_active": body["personalization"]["retrieval_active"],
            "alert_id": alert and alert["alert_id"], "alert_status": alert and alert["status"]}


def _run_outcome(api: _Api, log: list[dict], event: dict) -> dict:
    logged = next((e for e in log if e.get("kind") == "meal_forecast" and e.get("meal") == event["meal"]), None)
    if logged is None or logged.get("forecast") != "created":
        return {"outcome": "not_applicable"}
    body = api.ok("GET", f"/meals/{logged['meal_id']}/outcome")
    return {"meal_id": logged["meal_id"], "outcome": body["status"],
            "eligible_for_personalization": body["eligible_for_personalization"],
            "ineligibility_reasons": body["ineligibility_reasons"]}


def _run_dose(api: _Api, event: dict) -> dict:
    slot = core.rfc3339(event["slot"])
    due = api.ok("GET", "/medication-reminders/due")["due"]
    match = next((d for d in due if d["scheduled_at"] == slot), None)
    if match is None:
        return {"dose": "no_due_slot"}
    stored = api.ok("POST", "/medication-doses", json={
        "regimen_id": match["regimen_id"], "action": event["action"], "scheduled_at": slot,
        "client_event_id": f"demo-{event['id']}-{event['action']}"})
    return {"dose_id": stored["dose_id"], "dose": stored["action"], "fictional": True}


def advance(app, ctx: V2Context, user_id: str, *, to: str | None = "next",
            minutes: int | None = None, expected_event_index: int | None = None) -> dict:
    """Move the replay forward. Never backward; reset is the only way back.

    `to`: "next" (the next checkpoint), a checkpoint id, or an alias
    (`retrieval_activation`, `final_meal`, `end`). `minutes`: instead, let time pass --
    releasing readings -- without executing a checkpoint. `expected_event_index` makes
    a retried request a no-op.
    """
    _require_enabled(ctx)
    bundle = get_bundle(ctx, mode_of(ctx, user_id))
    _require_genuine_model(ctx, bundle)
    timeline = build_timeline(bundle)
    with _LOCK:
        row = _state_row(ctx, user_id)
        if row is None:
            raise demo_error(403, "not_demo_account", "This account is not a demo replay account.")
        if row["bundle_sha256"] != bundle.sha256:
            raise demo_error(409, "demo_bundle_changed",
                             "The demo data differs from what this account was initialized with. Reset it.")
        if expected_event_index is not None and expected_event_index != row["event_index"]:
            return {**_state(ctx, row, bundle, timeline), "advanced": False}

        index, position, log = row["event_index"], row["position"], list(row["log"])
        if minutes is not None:
            limit = timeline[index]["at"] if index < len(timeline) else bundle.end
            target_time = min(position + dt.timedelta(minutes=minutes), limit)
            if target_time > position:
                _release(ctx, user_id, bundle, position, target_time)
                _save(ctx, user_id, position=target_time)
            return {**state(ctx, user_id), "advanced": target_time > position}

        target = _target_index(bundle, timeline, index, to or "next")
        if target < index:
            return {**_state(ctx, row, bundle, timeline), "advanced": False}

        api = _Api(app, ctx, user_id)
        try:
            while index <= target and index < len(timeline):
                event = timeline[index]
                # 1. Time moves. 2. Only then do the readings up to that time exist.
                # 3. Only then is anything asked of the application.
                _release(ctx, user_id, bundle, position, event["at"])
                position = event["at"]
                _save(ctx, user_id, position=position)
                if event["kind"] == "meal_forecast":
                    result = _run_meal(api, bundle, event)
                elif event["kind"] == "outcome_reveal":
                    result = _run_outcome(api, log, event)
                else:
                    result = _run_dose(api, event)
                log.append({"id": event["id"], "kind": event["kind"], "at": core.rfc3339(event["at"]),
                            **({"meal": event["meal"]} if "meal" in event else {}), **result})
                index += 1
                _save(ctx, user_id, event_index=index, log=log,
                      status="complete" if index >= len(timeline) else "ready")
        finally:
            api.close()
        return {**state(ctx, user_id), "advanced": True}


def _target_index(bundle: Bundle, timeline: list[dict], index: int, to: str) -> int:
    if to == "next":
        return next((e["index"] for e in timeline[index:] if e["checkpoint"]), len(timeline) - 1)
    event_id = _aliases(bundle, timeline).get(to, to)
    match = next((e["index"] for e in timeline if e["id"] == event_id and e["checkpoint"]), None)
    if match is None:
        raise demo_error(422, "unknown_checkpoint", "No such replay checkpoint.",
                         {"allowed": ["next", *sorted(_aliases(bundle, timeline))],
                          "checkpoint_ids": [e["id"] for e in timeline if e["checkpoint"]]})
    return match


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #


def state(ctx: V2Context, user_id: str) -> dict:
    row = _state_row(ctx, user_id)
    if row is None:
        raise demo_error(403, "not_demo_account", "This account is not a demo replay account.")
    bundle = get_bundle(ctx, mode_of(ctx, user_id))
    return _state(ctx, row, bundle, build_timeline(bundle))


def _state(ctx: V2Context, row: dict, bundle: Bundle, timeline: list[dict]) -> dict:
    index = row["event_index"]
    by_id = {entry["id"]: entry for entry in row["log"]}
    upcoming = next((e for e in timeline[index:] if e["checkpoint"]), None)
    next_checkpoint = None
    if upcoming is not None:
        next_checkpoint = {"id": upcoming["id"], "kind": upcoming["kind"],
                           "at": core.rfc3339(upcoming["at"]), "meal": upcoming.get("meal")}
        if upcoming["kind"] == "meal_forecast":
            meal = bundle.meals[upcoming["meal"] - 1]
            # The meal about to be logged. Its composition is known when it is eaten.
            next_checkpoint["meal_record"] = {
                "occurred_at": core.rfc3339(meal.occurred_at), **meal.macros,
                "source": f"{bundle.data_class}_meal_record"}
    forecasts = [e for e in row["log"] if e["kind"] == "meal_forecast" and e.get("forecast") == "created"]
    latest = forecasts[-1] if forecasts else None
    released = bundle.readings_between(None, row["position"])
    first = display_from(bundle)
    mode = mode_of(ctx, row["user_id"])
    return {
        "scenario_id": row["scenario_id"],
        "mode": mode,
        "mode_label": source.MODES[mode]["label"],
        # Meals before this number are history replayed at setup, not presented steps.
        "display_from_meal": first,
        "status": row["status"],
        "position": core.rfc3339(row["position"]),
        "event_index": index,
        "event_count": len(timeline),
        "wall_clock": core.rfc3339(ctx.wall_now()),
        "window": {"start": core.rfc3339(bundle.start), "end": core.rfc3339(timeline[-1]["at"]),
                   "timezone": bundle.timezone},
        "next_checkpoint": next_checkpoint,
        "aliases": _aliases(bundle, timeline),
        "checkpoints": [
            {"id": e["id"], "kind": e["kind"], "at": core.rfc3339(e["at"]), "meal": e.get("meal"),
             "phase": "backfill" if e.get("meal", first) < first else "display",
             "done": e["index"] < index, "result": by_id.get(e["id"])}
            for e in timeline if e["checkpoint"]],
        "personalization_progress": {
            "admissible_prior_meals": latest and latest["admissible_prior_meals"],
            "retrieval_active": latest and latest["retrieval_active"],
            "retrieval_min_history": source.RETRIEVAL_MIN_HISTORY,
            "as_of_forecast": latest and latest["id"],
            "description": "Glucose history and completed meal responses inform future "
                           "personalized predictions. No model parameter is retrained.",
        },
        "data": {
            "readings_released": len(released),
            "readings_total": len(bundle.cgm),
            "meals_logged": sum(1 for e in row["log"] if e["kind"] == "meal_forecast"),
            "meals_total": len(bundle.meals),
        },
        "provenance": {
            "glucose_and_meals": {
                "data_class": bundle.store_labels[1], "source": bundle.source.get("dataset"),
                "description": (
                    "Historical research recording replayed in order. Not a live device "
                    "and not this account's own data." if bundle.data_class == "research_dataset"
                    else "Generated test fixture replayed in order. Not real data.")},
            "profile_and_medications": {
                "data_class": "fictional", "description": profile.FICTIONAL_NOTICE},
            "forecasts": {
                "data_class": "model_output",
                "description": "Produced by the frozen fs-1.3 model at each replay instant "
                               "from only the data released by then."},
            "labels": ["DEMO", "HISTORICAL REPLAY", "NOT LIVE"],
            **({} if first == 1 else {"backfilled_history": {
                "meals": first - 1,
                "description": "Earlier meals from the same recording, replayed in order "
                               "through the ordinary forecast and outcome workflow before "
                               "the presented journey. " + str(
                                   (bundle.source.get("plan") or {}).get("backfill_fit_note", ""))}}),
        },
        "bundle_sha256": row["bundle_sha256"],
    }


# --------------------------------------------------------------------------- #
# digest (for determinism checks)
# --------------------------------------------------------------------------- #


def digest(ctx: V2Context, user_id: str) -> dict:
    """A canonical fingerprint of everything the demo account holds.

    Excludes random ids and wall-clock times, so two runs that reached the same replay
    position by the same route must produce the same digest.
    """
    with ctx.engine.connect() as conn:
        def rows(table, *columns, order):
            query = select(*columns).where(table.c.user_id == user_id).order_by(*order)
            return [[core.rfc3339(v) if isinstance(v, dt.datetime) else v for v in row]
                    for row in conn.execute(query)]

        g, m, c, p, e, a, d, r = (t.glucose_observations, t.meals, t.meal_confirmations,
                                  t.predictions, t.response_episodes, t.alert_events,
                                  t.medication_doses, t.medication_regimens)
        state_row = conn.execute(select(t.demo_replay_state.c.position, t.demo_replay_state.c.event_index,
                                        t.demo_replay_state.c.status)
                                 .where(t.demo_replay_state.c.user_id == user_id)).one()
        parts = {
            "replay": [core.rfc3339(state_row[0]), state_row[1], state_row[2]],
            "glucose": rows(g, g.c.observed_at, g.c.glucose_mg_dl, g.c.received_at, g.c.data_class,
                            order=(g.c.observed_at,)),
            "meals": rows(m, m.c.occurred_at, m.c.status, m.c.note, order=(m.c.occurred_at,)),
            "confirmations": rows(c, c.c.carbs_g, c.c.protein_g, c.c.fat_g, c.c.fiber_g,
                                  c.c.calories_kcal, c.c.source, c.c.confirmed_at, order=(c.c.confirmed_at,)),
            "predictions": rows(p, p.c.created_at, p.c.prediction_kind, p.c.prediction_status,
                                p.c.artifact_sha256, p.c.forecast, p.c.cohort_deltas,
                                order=(p.c.created_at,)),
            "episodes": rows(e, e.c.window_end_at, e.c.status, e.c.eligible_for_personalization,
                             e.c.ineligibility_reasons, e.c.errors, order=(e.c.window_end_at,)),
            "alerts": rows(a, a.c.created_at, a.c.status, a.c.kind, order=(a.c.created_at,)),
            "doses": rows(d, d.c.scheduled_at, d.c.action, d.c.actual_at, order=(d.c.scheduled_at, d.c.recorded_at)),
            "regimens": rows(r, r.c.name, r.c.status, order=(r.c.name,)),
            "onboarding": [list(x) for x in conn.execute(
                select(t.onboarding_responses.c.answers, t.onboarding_responses.c.completed)
                .where(t.onboarding_responses.c.user_id == user_id))],
        }
        # A forecast echoes the account's generated participant id, and a snapshot's
        # checksum covers generated meal ids. Both are random per account, so the
        # fingerprint uses the id-free content: the forecast minus that echo, and each
        # snapshot's 39 feature values.
        for row in parts["predictions"]:
            row[4] = {k: v for k, v in row[4].items() if k != "participant_id"}
        f = t.feature_snapshots
        parts["features"] = rows(f, f.c.as_of, f.c.feature_values, f.c.missing_features, order=(f.c.as_of,))
    sections = {name: hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
                for name, value in parts.items()}
    overall = hashlib.sha256(json.dumps(sections, sort_keys=True).encode()).hexdigest()
    return {"digest": overall, "sections": sections,
            "counts": {name: len(value) for name, value in parts.items()}}
