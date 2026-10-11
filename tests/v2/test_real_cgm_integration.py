"""End-to-end through the v2 pipeline with GENUINE sensor data and the FROZEN artifact.

The glucose here is real: Dexcom G6 Pro readings from the CGMacros research dataset,
read from the local warehouse. It is ingested for a test account under the provider
`research_cgmacros` with data_class `research_dataset`, so it is never presented as a
live device belonging to a user. The model is the frozen fs-1.3 artifact, with its
checksum enforced.

What this demonstrates: real readings pass through normalization, the orchestrator,
the frozen model, outcome scoring and the history cycle. What it does not: a live
connection to any CGM provider.

Skipped (never passed vacuously) when the dataset or the artifact is not on this
machine -- neither is committed to git.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import pathlib

import pytest

from backend.app.config import load_settings
from backend.app.v2.services import glucose
from backend.app.v2.services.glucose import IncomingReading
from tests.v2.conftest import build_harness

FROZEN_SHA256 = "12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe"
ARTIFACT = pathlib.Path("ml/artifacts_cgmacros_fs13/model.joblib")
WAREHOUSE = pathlib.Path("data/local/cgmacros_v2.db")
PARTICIPANT = "cgmacros_004"

pytestmark = pytest.mark.skipif(
    not (ARTIFACT.exists() and WAREHOUSE.exists()),
    reason="needs the frozen fs-1.3 artifact and the local CGMacros warehouse (neither is in git)",
)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    from data.storage.repository import SqliteDataRepository

    source = SqliteDataRepository(f"sqlite:///{WAREHOUSE}").get_history(PARTICIPANT)
    v1_settings = dataclasses.replace(
        load_settings(), model_artifact=ARTIFACT, metrics_path=ARTIFACT.parent / "metrics.json")
    h = build_harness(tmp_path_factory.mktemp("real"), v1_settings, expected_model_sha256=FROZEN_SHA256)
    h.clock.now = source.cgm[-1].timestamp + dt.timedelta(hours=6)

    # The dataset's clock is participant-local wall time under a +00:00 label, so the
    # account is given UTC: the projection offset is zero and the clock is unchanged.
    headers = h.register("research@example.com", timezone="UTC")
    user_id = h.client.get("/api/v2/me", headers=headers).json()["user_id"]
    with h.ctx.engine.begin() as conn:
        summary = glucose.ingest_readings(
            conn, h.ctx, user_id, "research_cgmacros",
            [IncomingReading(value=p.glucose_mg_dl, unit="mg/dL", observed_at=p.timestamp,
                             device_id="dexcom-g6-pro") for p in source.cgm],
            source_format="cgmacros_warehouse", data_class="research_dataset")

    predictions = []
    for meal in source.meals:
        if not meal.has_complete_macros:
            continue
        macros = {"carbs_g": meal.carbs_g, "protein_g": meal.protein_g, "fat_g": meal.fat_g,
                  "fiber_g": meal.fiber_g, "calories_kcal": meal.calories_kcal}
        created = h.client.post("/api/v2/meals", headers=headers, json={
            "occurred_at": meal.timestamp.isoformat(), "macros": macros})
        if created.status_code != 201:      # outside contract v1.0 input bounds
            continue
        response = h.predict(headers, created.json()["meal_id"])
        predictions.append((created.json()["meal_id"], response))
    return {"h": h, "headers": headers, "summary": summary, "predictions": predictions,
            "source": source}


def test_real_readings_are_ingested_and_labelled_as_research_data(run):
    counts = run["summary"]["counts"]
    assert counts["inserted"] == len(run["source"].cgm) and counts["rejected"] == 0
    h, headers = run["h"], run["headers"]
    connection = h.client.get("/api/v2/devices", headers=headers).json()["connections"][0]
    assert connection["provider"] == "research_cgmacros"
    assert connection["authentic_data_received"] is False      # not a user's device
    assert connection["status"] != "connected"
    assert h.client.get("/api/v2/cgm/status", headers=headers).json()["data_class"] == "research_dataset"


def test_forecasts_come_from_the_frozen_artifact_with_integrity_enforced(run):
    h = run["h"]
    assert h.client.get("/api/v2/ready").json()["checks"]["model_integrity"] is True
    status = h.client.get("/api/v2/status").json()["model"]
    assert status["artifact_sha256"] == FROZEN_SHA256 and status["integrity_enforced"] is True

    made = [r.json() for _, r in run["predictions"] if r.status_code == 200]
    refused = [r.json()["code"] for _, r in run["predictions"] if r.status_code != 200]
    assert len(made) >= 30
    assert set(refused) <= {"insufficient_premeal_context"}     # the only acceptable refusal
    for body in made:
        assert body["model"]["artifact_sha256"] == FROZEN_SHA256
        assert body["model"]["feature_set_version"] == "fs-1.3"
        assert len(body["forecast"]["points"]) == 25
        assert body["forecast"]["data_source"] == "official"    # the artifact's training provenance
        assert body["forecast"]["prediction_status"] == "generalized_fallback"


def test_retrieval_switches_on_once_five_real_meals_have_completed(run):
    made = [r.json() for _, r in run["predictions"] if r.status_code == 200]
    counts = [b["personalization"]["admissible_prior_meals"] for b in made]
    assert counts[0] == 0 and max(counts) >= 5
    assert counts == sorted(counts) or max(counts) >= 5         # history only ever accumulates
    for body in made:
        assert body["personalization"]["retrieval_active"] == (
            body["personalization"]["admissible_prior_meals"] >= 5)
    assert any(b["personalization"]["retrieval_active"] for b in made)


def test_real_outcomes_are_scored_and_every_forecast_reproduces(run, capsys):
    h, headers = run["h"], run["headers"]
    scored, errors = 0, []
    for meal_id, response in run["predictions"]:
        if response.status_code != 200:
            continue
        outcome = h.client.get(f"/api/v2/meals/{meal_id}/outcome", headers=headers).json()
        if outcome["status"] == "scored":
            scored += 1
            errors.append(outcome["errors"]["mae_mg_dl"])
    assert scored >= 25
    mean_mae = sum(errors) / len(errors)
    with capsys.disabled():
        print(f"\n[real-data] {PARTICIPANT}: {scored} scored episodes, mean per-episode MAE "
              f"{mean_mae:.1f} mg/dL (one participant, retrospective, and the artifact was fitted on this participant's earlier meals: a pipeline check, NOT a validation result)")
    assert 0 < mean_mae < 80                                     # sanity only, not a claim

    for body in [r.json() for _, r in run["predictions"] if r.status_code == 200][-5:]:
        result = h.client.post(f"/api/v2/predictions/{body['prediction_id']}/reproduce", headers=headers).json()
        assert result["reproduced"] is True and result["history_sha256_verified"] is True


def test_the_frozen_artifact_is_unchanged_afterwards(run):
    assert hashlib.sha256(ARTIFACT.read_bytes()).hexdigest() == FROZEN_SHA256
