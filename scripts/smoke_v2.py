"""End-to-end smoke test against a RUNNING GLIMPSE deployment.

    python scripts/smoke_v2.py http://127.0.0.1:8000

Walks the whole journey over real HTTP with a throwaway account, then deletes that
account. Every glucose value it sends is generated arithmetic pushed with source
`demo`, so nothing it writes can be mistaken for a device reading.

Exits 0 when every step passes, 1 at the first failure. It does not need the OpenAI
key: a deployment without one must answer `503 vision_not_configured`, and that is
checked as a pass.
"""

from __future__ import annotations

import datetime as dt
import io
import math
import secrets
import sys

import httpx
from PIL import Image

FROZEN_SHA256 = "12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe"
MEAL = {"carbs_g": 60, "protein_g": 25, "fat_g": 18, "fiber_g": 8, "calories_kcal": 500}


def main(base_url: str) -> int:
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=90)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    password = secrets.token_urlsafe(18)
    email = f"smoke-{secrets.token_hex(6)}@example.com"
    headers: dict[str, str] = {}
    failures = 0

    def step(name: str, ok: bool, note: str = "") -> bool:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + note if note else ''}")
        return ok

    try:
        health = client.get("/health")
        step("v1 liveness", health.status_code == 200 and health.json().get("version") == "1.0.0")
        info = client.get("/api/model/info").json()
        step("v1 model info reports fs-1.3", info.get("feature_set_version") == "fs-1.3",
             f"model_version={info.get('model_version')} data_source={info.get('data_source')}")

        ready = client.get("/api/v2/ready")
        step("v2 readiness", ready.status_code == 200, str(ready.json().get("checks", ready.json().get("details"))))
        model = client.get("/api/v2/status").json()["model"]
        frozen = model["artifact_sha256"] == FROZEN_SHA256
        step("artifact is the frozen fs-1.3 checksum", frozen,
             "" if frozen else f"loaded {str(model['artifact_sha256'])[:16]}… -- NOT the frozen model")
        step("artifact checksum is enforced", bool(model["integrity_enforced"]),
             "" if model["integrity_enforced"] else "GLUCOTWIN_EXPECTED_MODEL_SHA256 is not set")
        step("unauthenticated request is refused", client.get("/api/v2/meals").status_code == 401)

        registered = client.post("/api/v2/auth/register", json={
            "email": email, "password": password, "timezone": "America/New_York"})
        if not step("register", registered.status_code == 201, registered.text[:120] if registered.status_code != 201 else ""):
            return 1
        headers = {"Authorization": f"Bearer {registered.json()['session']['token']}"}
        step("processing is blocked before consent",
             client.get("/api/v2/meals", headers=headers).json().get("code") == "consent_required")
        for consent in ("necessary_processing", "personalization_history"):
            client.put(f"/api/v2/consents/{consent}", headers=headers, json={"state": "granted"})

        readings = [{"observed_at": (now - dt.timedelta(minutes=5 * i)).isoformat(),
                     "value": round(104 + 8 * math.sin(i / 9), 1), "unit": "mg/dL"} for i in range(72)]
        pushed = client.post("/api/v2/cgm/readings", headers=headers, json={
            "source": "demo", "device_id": "smoke-test", "readings": readings}).json()
        step("demo glucose stored", pushed["counts"]["inserted"] == 72 and pushed["data_class"] == "demo")
        step("sensor reported fresh", client.get("/api/v2/cgm/status", headers=headers).json()["freshness"] == "fresh")

        buffer = io.BytesIO()
        Image.new("RGB", (800, 600), (200, 150, 90)).save(buffer, format="JPEG")
        upload = client.post("/api/v2/meals/images", params={"occurred_at": now.isoformat()},
                             headers={**headers, "Content-Type": "image/jpeg"}, content=buffer.getvalue())
        if not step("photo upload", upload.status_code == 201, upload.text[:120] if upload.status_code != 201 else ""):
            return 1
        meal_id = upload.json()["meal_id"]
        photo = client.get(f"/api/v2/meals/{meal_id}/image", headers=headers)
        step("photo is served back to its owner", photo.status_code == 200 and photo.content[:3] == b"\xff\xd8\xff")

        analysis = client.post(f"/api/v2/meals/{meal_id}/analyze", headers=headers)
        code = analysis.json().get("code")
        step("meal analysis", analysis.status_code == 200 or code == "vision_not_configured",
             "live estimate returned" if analysis.status_code == 200 else f"{analysis.status_code} {code}")

        blocked = client.post(f"/api/v2/meals/{meal_id}/predict", headers=headers)
        if analysis.status_code != 200:
            step("forecast is refused before confirmation", blocked.json().get("code") == "meal_not_confirmed")
        client.patch(f"/api/v2/meals/{meal_id}/confirm", headers=headers, json=MEAL)

        forecast = client.post(f"/api/v2/meals/{meal_id}/predict", headers=headers)
        if not step("forecast", forecast.status_code == 200, forecast.text[:160] if forecast.status_code != 200 else ""):
            return 1
        body = forecast.json()
        step("forecast is the 25-point contract shape", len(body["forecast"]["points"]) == 25
             and body["forecast"]["uncertainty_status"] == "not_calibrated")
        step("forecast records its artifact", body["model"]["artifact_sha256"] == model["artifact_sha256"])
        again = client.post(f"/api/v2/meals/{meal_id}/predict", headers=headers).json()
        step("repeat request replays the stored forecast",
             again["replayed"] is True and again["prediction_id"] == body["prediction_id"])
        reproduced = client.post(f"/api/v2/predictions/{body['prediction_id']}/reproduce", headers=headers).json()
        step("forecast reproduces from its snapshot", reproduced.get("reproduced") is True)
        step("outcome is pending until the window closes",
             client.get(f"/api/v2/meals/{meal_id}/outcome", headers=headers).json()["status"] == "pending")
    finally:
        if headers:
            deleted = client.request("DELETE", "/api/v2/account", headers=headers, json={"password": password})
            step("throwaway account deleted", deleted.status_code == 204)

    print(f"\n{'ALL STEPS PASSED' if failures == 0 else f'{failures} STEP(S) FAILED'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1]))
