"""The frozen v1 API and model are untouched by the v2 layer."""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app

MANIFEST = json.loads(pathlib.Path("docs/governance/fs13_integrity_manifest.json").read_text())
PREDICT = {
    "participant_id": "synthetic_p001", "meal_timestamp": "2026-10-05T12:30:00Z",
    "meal": {"carbs_g": 55, "protein_g": 22, "fat_g": 16, "fiber_g": 7, "calories_kcal": 460},
}


def test_every_frozen_source_file_still_matches_the_freeze_manifest():
    """Adding v2 must not have edited a single file on the frozen inference path."""
    changed = [
        path for path, expected in MANIFEST["inference_source_sha256"].items()
        if hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() != expected
    ]
    assert changed == [], f"frozen files were modified: {changed}"


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/health", None),
    ("GET", "/api/participants", None),
    ("GET", "/api/participants/synthetic_p001/history?start=2026-10-05T00:00:00Z&end=2026-10-05T06:00:00Z", None),
    ("POST", "/api/predict", PREDICT),
    ("GET", "/api/model/metrics", None),
    ("GET", "/api/model/info", None),
    ("GET", "/api/participants/nobody/history", None),          # 404 envelope
    ("POST", "/api/predict", {"participant_id": "synthetic_p001"}),  # 422 envelope
    ("GET", "/openapi.json", None),
])
def test_v1_responses_are_byte_identical_with_v2_mounted(harness, v1_settings, method, path, body):
    plain = TestClient(create_app(v1_settings))
    kwargs = {"json": body} if body is not None else {}
    reference = plain.request(method, path, **kwargs)
    combined = harness.client.request(method, path, **kwargs)
    assert combined.status_code == reference.status_code
    assert combined.content == reference.content
    assert combined.headers.get("content-type") == reference.headers.get("content-type")


def test_v1_compare_is_identical_apart_from_its_random_id(harness, v1_settings):
    body = {"participant_ids": ["synthetic_p001", "synthetic_p002"],
            "meal_timestamp": PREDICT["meal_timestamp"], "meal": PREDICT["meal"]}
    reference = TestClient(create_app(v1_settings)).post("/api/compare", json=body).json()
    combined = harness.client.post("/api/compare", json=body).json()
    reference.pop("comparison_id"), combined.pop("comparison_id")
    assert combined == reference


def test_v1_cors_policy_is_unchanged(harness, v1_settings):
    headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
               "Access-Control-Request-Headers": "content-type"}
    reference = TestClient(create_app(v1_settings)).options("/api/predict", headers=headers)
    combined = harness.client.options("/api/predict", headers=headers)
    for name in ("access-control-allow-methods", "access-control-allow-headers",
                 "access-control-allow-origin"):
        assert combined.headers.get(name) == reference.headers.get(name)
    # v1 never allowed PATCH or Authorization, and still does not.
    assert "PATCH" not in combined.headers["access-control-allow-methods"]


def test_v2_preflight_allows_bearer_auth_and_patch(harness):
    response = harness.client.options("/api/v2/meals/x/confirm", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
    assert response.headers.get("access-control-allow-credentials") is None


def test_v2_uses_its_own_error_envelope_with_a_request_id(harness):
    response = harness.client.get("/api/v2/me")
    assert response.status_code == 401
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "details"}
    assert body["code"] == "unauthenticated"
    assert body["request_id"] == response.headers["x-request-id"]


def test_v2_validation_errors_do_not_echo_submitted_values(harness):
    response = harness.client.post("/api/v2/auth/register", json={
        "email": "not-an-email", "password": "secret-value-12345", "timezone": "Mars/Olympus"})
    assert response.status_code == 422
    assert "secret-value-12345" not in response.text
    assert "not-an-email" not in response.text
    fields = {e["field"] for e in response.json()["details"]["errors"]}
    assert {"email", "timezone"} <= fields


def test_v2_publishes_its_own_openapi_document(harness):
    spec = harness.client.get("/api/v2/openapi.json").json()
    assert spec["info"]["title"] == "GlucoTwin Application API"
    assert "/meals/{meal_id}/predict" in spec["paths"]
    # The frozen routes are not re-declared inside v2.
    assert "/api/predict" not in spec["paths"] and "/predict" not in spec["paths"]


def test_readiness_and_status_expose_no_secrets(harness):
    assert harness.client.get("/api/v2/ready").json()["status"] == "ready"
    status = harness.client.get("/api/v2/status").json()
    assert status["model"]["loaded"] is True
    assert status["notification_channels"]["push"]["available"] is False
    text = json.dumps(status)
    assert "client_secret" not in text and "api_key" not in text


def test_forecasting_is_refused_when_the_artifact_checksum_is_wrong(tmp_path, v1_settings):
    from tests.v2.conftest import BASE_NOW, build_harness
    import datetime as dt

    h = build_harness(tmp_path, v1_settings, expected_model_sha256="0" * 64)
    assert h.client.get("/api/v2/ready").status_code == 503
    headers = h.register()
    h.push_cgm(headers, BASE_NOW - dt.timedelta(hours=3), BASE_NOW)
    response = h.predict(headers, h.meal(headers, BASE_NOW))
    assert response.status_code == 503
    assert response.json()["code"] == "model_integrity_failure"
