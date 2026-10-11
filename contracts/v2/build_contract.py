"""Generate the v2 contract artifacts from the running code.

    python -m contracts.v2.build_contract            # rewrite openapi.json + examples/
    python -m contracts.v2.build_contract --check    # fail if openapi.json is stale

Nothing here is hand-written JSON. `openapi.json` is the schema FastAPI derives from
the real routes, and every file in `examples/` is a response the real application
returned while a scripted scenario ran against a throwaway database.

The scenario's glucose is generated arithmetic pushed with source `demo`, and the
"vision model" is a stub returning a fixed estimate -- so the examples are DEMO
payloads for building a UI against. Their shapes are real; their numbers are not
anyone's health data. The forecast inside them comes from whichever model artifact is
loaded, recorded in `examples/_meta.json`.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import io
import itertools
import json
import math
import pathlib
import sys
import tempfile
import uuid

HERE = pathlib.Path(__file__).resolve().parent
EXAMPLES = HERE / "examples"
OPENAPI = HERE / "openapi.json"
FROZEN_ARTIFACT = pathlib.Path("ml/artifacts_cgmacros_fs13/model.joblib")
NOW = dt.datetime(2026, 9, 20, 16, 0, tzinfo=dt.timezone.utc)   # 12:00 America/New_York
MEAL = {"carbs_g": 55.0, "protein_g": 22.0, "fat_g": 16.0, "fiber_g": 7.0, "calories_kcal": 460.0}


def _demo_glucose(moment: dt.datetime, meals: list[dt.datetime]) -> float:
    minutes = moment.hour * 60 + moment.minute
    value = 102.0 + 9.0 * math.sin(2 * math.pi * minutes / 1440.0)
    for meal in meals:
        elapsed = (moment - meal).total_seconds() / 60.0
        if 0 < elapsed < 180:
            value += 48.0 * math.exp(-((elapsed - 55.0) / 34.0) ** 2)
    return round(value, 1)


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> dt.datetime:
        return self.now


class _StubVision:
    def analyze(self, jpeg: bytes):
        from backend.app.v2.services.vision import (
            FoodItem, MealVisionEstimate, RangedValue, Scene, Totals, VisionResult)

        def ranged(v: float) -> RangedValue:
            return RangedValue(estimate=v, low=round(v * 0.8, 1), high=round(v * 1.25, 1))

        estimate = MealVisionEstimate(
            is_food=True, not_food_reason=None,
            items=[
                FoodItem(name="grilled chicken breast", portion_description="one palm-sized piece",
                         estimated_weight_g=140.0, visibility="visible", confidence="medium", notes=None),
                FoodItem(name="steamed white rice", portion_description="about one cup",
                         estimated_weight_g=160.0, visibility="partially_visible", confidence="medium",
                         notes="part of the portion is hidden under the chicken"),
                FoodItem(name="cooking oil", portion_description="about a tablespoon",
                         estimated_weight_g=12.0, visibility="assumed_hidden", confidence="low",
                         notes="not visible; typical for pan frying"),
            ],
            totals=Totals(carbs_g=ranged(48.0), protein_g=ranged(36.0), fat_g=ranged(18.0),
                          fiber_g=ranged(6.0), calories_kcal=ranged(505.0)),
            overall_confidence="medium",
            scene=Scene(multiple_plates=False, contains_beverage=False, packaged_food=False,
                        hidden_fats_likely=True, portion_reference_visible=True),
            assumptions=["The chicken was pan fried in oil."],
            ambiguities=["Rice portion is partly hidden."],
            clarification_questions=["Was the rice white or brown?", "Was any sauce added?"],
        )
        return VisionResult(estimate=estimate, model_requested="<configured vision model>",
                            model_reported="<model id reported by the provider>",
                            usage={"input_tokens": 900, "output_tokens": 210, "total_tokens": 1110})


def _deterministic_ids() -> None:
    """Stable ids so regenerating the examples does not churn the diff."""
    from backend.app.v2 import core

    counter = itertools.count(1)
    core.new_id = lambda prefix: f"{prefix}_{next(counter):032x}"
    tokens = itertools.count(1)
    core.new_token = lambda: f"example-token-{next(tokens):04d}-not-a-real-credential"
    uuid_counter = itertools.count(1)
    uuid.uuid4 = lambda: uuid.UUID(int=next(uuid_counter))


def build_platform(tmp: pathlib.Path):
    from fastapi.testclient import TestClient

    from backend.app.config import load_settings
    from backend.app.main import create_app
    from backend.app.v2.app import create_v2_app
    from backend.app.v2.asgi import PlatformApp
    from backend.app.v2.db.engine import create_all, make_engine
    from backend.app.v2.deps import V2Context
    from backend.app.v2.services.images import LocalImageStore
    from backend.app.v2.settings import V2Settings

    v1_settings = load_settings()
    if FROZEN_ARTIFACT.exists():
        v1_settings = dataclasses.replace(v1_settings, model_artifact=FROZEN_ARTIFACT,
                                          metrics_path=FROZEN_ARTIFACT.parent / "metrics.json")
    v1_app = create_app(v1_settings)
    # Demo mode ON so the gated /demo routes are part of the published contract. They
    # exist in a deployment only when GLUCOTWIN_DEMO_MODE is enabled.
    settings = V2Settings(app_db_url=f"sqlite:///{tmp / 'app.db'}", image_dir=tmp / "images",
                          cors_origins=["http://localhost:5173"], demo_mode=True,
                          demo_password="example-only-password")
    engine = make_engine(settings.app_db_url)
    create_all(engine)
    clock = _Clock()
    ctx = V2Context(settings=settings, engine=engine, prediction_service=v1_app.state.service,
                    image_store=LocalImageStore(settings.image_dir), vision=_StubVision(), clock=clock)
    from backend.app.v2.demo.fixture import fixture_bundle

    # The published demo examples replay the labelled TEST FIXTURE, never research data.
    ctx.demo_bundle = fixture_bundle()
    v2_app = create_v2_app(ctx)
    return TestClient(PlatformApp(v1_app, v2_app)), ctx, clock, v2_app


def openapi_document(v2_app) -> str:
    spec = v2_app.openapi()
    spec["servers"] = [{"url": "/api/v2", "description": "All paths are relative to /api/v2"}]
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def run_scenario(client, ctx, clock, v2_app) -> dict[str, object]:
    out: dict[str, object] = {}
    api = "/api/v2"

    def save(name: str, response) -> dict:
        body = response.json() if response.content else None
        out[name] = {"request": f"{response.request.method} {response.request.url.path}",
                     "status": response.status_code, "body": body}
        return body

    def register(email: str, grant: tuple[str, ...] = ("necessary_processing", "personalization_history")):
        r = client.post(f"{api}/auth/register", json={
            "email": email, "password": "correct horse battery", "timezone": "America/New_York",
            "display_name": "Demo User"})
        headers = {"Authorization": f"Bearer {r.json()['session']['token']}"}
        for consent in grant:
            client.put(f"{api}/consents/{consent}", headers=headers, json={"state": "granted"})
        return r, headers

    def push(headers, start, end, meals=()):
        readings, moment = [], start
        while moment <= end:
            readings.append({"observed_at": moment.isoformat(), "value": _demo_glucose(moment, list(meals)),
                             "unit": "mg/dL"})
            moment += dt.timedelta(minutes=5)
        last = None
        for i in range(0, len(readings), 4000):
            last = client.post(f"{api}/cgm/readings", headers=headers, json={
                "source": "demo", "device_id": "demo-sensor", "readings": readings[i:i + 4000]})
        return last

    def manual_meal(headers, when, macros=MEAL):
        return client.post(f"{api}/meals", headers=headers, json={
            "occurred_at": when.isoformat(), "macros": macros}).json()["meal_id"]

    def seed_history(headers, count):
        times = [clock.now - dt.timedelta(hours=5 * (i + 1)) for i in range(count)][::-1]
        push(headers, clock.now - dt.timedelta(days=3), clock.now, times)
        for index, when in enumerate(times):
            meal_id = manual_meal(headers, when, {**MEAL, "carbs_g": 30.0 + 12 * index,
                                                  "calories_kcal": 380.0 + 48 * index})
            client.post(f"{api}/meals/{meal_id}/predict", headers=headers)

    # -- system -------------------------------------------------------------- #
    save("system_ready", client.get(f"{api}/ready"))
    save("system_status", client.get(f"{api}/status"))

    # -- user A: the main journey -------------------------------------------- #
    r, a = register("user.a@example.com", grant=())
    save("auth_register", r)
    save("error_consent_required", client.get(f"{api}/meals", headers=a))
    save("consent_granted", client.put(f"{api}/consents/necessary_processing", headers=a, json={"state": "granted"}))
    client.put(f"{api}/consents/personalization_history", headers=a, json={"state": "granted"})
    save("auth_me", client.get(f"{api}/me", headers=a))
    save("error_unauthenticated", client.get(f"{api}/me"))
    save("onboarding_saved", client.patch(f"{api}/onboarding", headers=a, json={"answers": {
        "primary_goal": "spike_awareness", "diabetes_status": "prediabetes",
        "weight": {"value": 165, "unit": "lb"}, "hba1c": {"value": 5.9, "unit": "%"},
        "fasting_glucose": "unknown", "monitoring_method": "cgm", "wants_spike_alerts": True,
        "alert_threshold": {"value": 160, "unit": "mg/dL"}}, "completed": False}))

    save("cgm_status_no_data", client.get(f"{api}/cgm/status", headers=a))
    save("cgm_push", push(a, NOW - dt.timedelta(hours=6), NOW))
    save("cgm_status_fresh", client.get(f"{api}/cgm/status", headers=a))
    save("cgm_readings", client.get(f"{api}/cgm/readings", headers=a, params={
        "start": (NOW - dt.timedelta(minutes=15)).isoformat(), "end": NOW.isoformat()}))

    save("alert_settings_saved", client.put(f"{api}/alert-settings", headers=a, json={
        "enabled": True, "threshold": {"value": 140, "unit": "mg/dL"}, "channels": ["in_app", "push"],
        "quiet_hours_start": "22:00", "quiet_hours_end": "07:00", "cooldown_minutes": 60}))

    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 150, 90)).save(buffer, format="JPEG")
    upload = client.post(f"{api}/meals/images", params={"occurred_at": NOW.isoformat()},
                         headers={**a, "Content-Type": "image/jpeg"}, content=buffer.getvalue())
    meal_id = save("meal_photo_uploaded", upload)["meal_id"]
    estimate = save("meal_analyzed", client.post(f"{api}/meals/{meal_id}/analyze", headers=a))["estimate"]
    save("error_meal_not_confirmed", client.post(f"{api}/meals/{meal_id}/predict", headers=a))
    edited = {**estimate["totals"], "carbs_g": 62.0}
    save("meal_confirmed", client.patch(f"{api}/meals/{meal_id}/confirm", headers=a, json=edited))
    prediction = save("prediction_created", client.post(f"{api}/meals/{meal_id}/predict", headers=a))
    save("prediction_replayed", client.post(f"{api}/meals/{meal_id}/predict", headers=a))
    save("prediction_reproduced", client.post(f"{api}/predictions/{prediction['prediction_id']}/reproduce", headers=a))
    save("outcome_pending", client.get(f"{api}/meals/{meal_id}/outcome", headers=a))
    save("alerts_list", client.get(f"{api}/alerts", headers=a))
    save("notifications_list", client.get(f"{api}/notifications", headers=a))

    regimen = save("medication_created", client.post(f"{api}/medications", headers=a, json={
        "name": "Metformin", "medication_class": "metformin", "form": "tablet", "dose_amount": 500,
        "dose_unit": "mg", "frequency": "twice daily", "schedule_times": ["12:30", "20:00"],
        "reminders_enabled": True}))
    save("medication_dose_taken", client.post(f"{api}/medication-doses", headers=a, json={
        "regimen_id": regimen["regimen_id"], "action": "taken", "client_event_id": "client-evt-0001"}))
    save("medication_education", client.get(f"{api}/medication-education", headers=a))
    save("devices_list", client.get(f"{api}/devices", headers=a))
    save("error_provider_not_configured", client.post(f"{api}/devices/fitbit/connect", headers=a))
    save("wearables_pushed", client.post(f"{api}/wearables/observations", headers=a, json={
        "source": "demo", "observations": [
            {"metric": "resting_heart_rate", "value": 58, "unit": "bpm",
             "observed_at": (NOW - dt.timedelta(hours=8)).isoformat(), "source_record_id": "demo-rhr-1"}]}))
    save("error_validation", client.patch(f"{api}/meals/{meal_id}/confirm", headers=a,
                                          json={"carbs_g": -5, "protein_g": 20}))

    # Three and a half hours later: the response is in, the reminder slot has passed.
    clock.now = NOW + dt.timedelta(minutes=210)
    push(a, NOW + dt.timedelta(minutes=5), clock.now, [NOW])
    save("outcome_scored", client.get(f"{api}/meals/{meal_id}/outcome", headers=a))
    save("medication_reminders_due", client.get(f"{api}/medication-reminders/due", headers=a))
    save("medications_list", client.get(f"{api}/medications", headers=a))
    save("meal_detail", client.get(f"{api}/meals/{meal_id}", headers=a))

    # A day without a sensor: the stale case.
    clock.now = NOW + dt.timedelta(hours=30)
    save("cgm_status_stale", client.get(f"{api}/cgm/status", headers=a))
    save("error_insufficient_premeal_context",
         client.post(f"{api}/meals/{manual_meal(a, clock.now)}/predict", headers=a))
    clock.now = NOW

    # -- user B: the 4 / 5 completed-meal boundary ---------------------------- #
    for count, name in ((4, "prediction_history_4_below_retrieval_gate"),
                        (5, "prediction_history_5_retrieval_active")):
        _, headers = register(f"history.{count}@example.com")
        seed_history(headers, count)
        save(name, client.post(f"{api}/meals/{manual_meal(headers, clock.now)}/predict", headers=headers))

    # -- demonstration replay (gated routes), on the labelled test fixture ---- #
    from backend.app.v2.demo import replay

    save("error_not_demo_account", client.get(f"{api}/demo/replay", headers=a))
    replay.initialize(v2_app, ctx)
    demo = {"Authorization": "Bearer " + client.post(f"{api}/auth/login", json={
        "email": ctx.settings.demo_email, "password": ctx.settings.demo_password}).json()["session"]["token"]}
    save("demo_replay_state_initial", client.get(f"{api}/demo/replay", headers=demo))
    save("demo_replay_advance_meal_forecast", client.post(f"{api}/demo/replay/advance", headers=demo))
    save("demo_replay_advance_outcome_reveal", client.post(f"{api}/demo/replay/advance", headers=demo))
    save("demo_replay_advance_minutes", client.post(f"{api}/demo/replay/advance", headers=demo,
                                                    json={"minutes": 30}))
    save("demo_replay_state_retrieval_active", client.post(
        f"{api}/demo/replay/advance", headers=demo, json={"to": "retrieval_activation"}))
    save("demo_replay_retry_is_no_op", client.post(
        f"{api}/demo/replay/advance", headers=demo, json={"expected_event_index": 0}))
    save("error_demo_unknown_checkpoint", client.post(
        f"{api}/demo/replay/advance", headers=demo, json={"to": "meal_99_forecast"}))
    save("demo_replay_reset", client.post(f"{api}/demo/replay/reset", headers=demo))

    integrity = ctx.model_integrity
    out["_meta"] = {
        "generated_by": "python -m contracts.v2.build_contract",
        "data_class": "demo",
        "notice": "Shapes are real application output. Glucose values are generated "
                  "arithmetic and the nutrition estimate is a fixed stub: no real health "
                  "data and no real AI output appears in these files.",
        "scenario_clock": NOW.isoformat().replace("+00:00", "Z"),
        "model_artifact": integrity["artifact_path"],
        "model_artifact_sha256": integrity["artifact_sha256"],
        "model_is_frozen_fs13": FROZEN_ARTIFACT.exists(),
        "demo_replay_examples": "The demo_replay_* files replay a generated TEST FIXTURE "
                                "(data_class demo), not the research recording the real "
                                "demonstration uses. Real demo responses contain research "
                                "data and are produced locally, never committed.",
    }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate v2 OpenAPI and example payloads.")
    parser.add_argument("--check", action="store_true", help="fail if openapi.json is out of date")
    args = parser.parse_args(argv)

    if not args.check:
        _deterministic_ids()   # patches id generation process-wide; scenario runs only
    with tempfile.TemporaryDirectory() as tmp:
        client, ctx, clock, v2_app = build_platform(pathlib.Path(tmp))
        document = openapi_document(v2_app)
        if args.check:
            if not OPENAPI.exists() or OPENAPI.read_text() != document:
                print("contracts/v2/openapi.json is stale; run `python -m contracts.v2.build_contract`",
                      file=sys.stderr)
                return 1
            return 0
        OPENAPI.write_text(document)
        EXAMPLES.mkdir(exist_ok=True)
        for stale in EXAMPLES.glob("*.json"):
            stale.unlink()
        for name, payload in run_scenario(client, ctx, clock, v2_app).items():
            (EXAMPLES / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {OPENAPI} and {len(list(EXAMPLES.glob('*.json')))} example files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
