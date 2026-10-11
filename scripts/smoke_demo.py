"""End-to-end demonstration smoke test against a RUNNING server.

    export GLUCOTWIN_DEMO_PASSWORD=...
    python scripts/smoke_demo.py http://127.0.0.1:8000 [--leave-at CHECKPOINT]

Signs in as the demo account, resets it, and walks the whole replay over real HTTP,
checking at every checkpoint -- through the ordinary v2 endpoints only -- that no
future data is visible, that forecasts are genuine frozen-model output and reproduce,
and that the personalization states match the committed manifest.

Finishes by resetting again, so the server is left at the start of the replay (or at
`--leave-at`). Exits 0 only when every step passes. The server must be running with
GLUCOTWIN_DEMO_MODE=1 and must already have been seeded (`python -m scripts.demo_seed`).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import httpx

FROZEN_SHA256 = "12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe"
MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "backend/app/v2/demo/manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base_url")
    parser.add_argument("--leave-at", default=None)
    args = parser.parse_args(argv)
    email = os.environ.get("GLUCOTWIN_DEMO_EMAIL", "demo@glucotwin.example")
    password = os.environ.get("GLUCOTWIN_DEMO_PASSWORD")
    if not password:
        print("ERROR: GLUCOTWIN_DEMO_PASSWORD is not set", file=sys.stderr)
        return 1
    manifest = json.loads(MANIFEST.read_text())
    client = httpx.Client(base_url=args.base_url.rstrip("/") + "/api/v2", timeout=180)
    failures = 0

    def step(name: str, ok: bool, note: str = "") -> bool:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + note if note else ''}")
        return ok

    def login() -> dict:
        response = client.post("/auth/login", json={"email": email, "password": password})
        if response.status_code != 200:
            raise SystemExit(f"FAIL  demo sign-in  -- {response.status_code} {response.text[:160]}; "
                             "is the server seeded (python -m scripts.demo_seed)?")
        return {"Authorization": f"Bearer {response.json()['session']['token']}"}

    status = client.get("/status").json()
    step("server is in demo mode", status.get("demo_mode") is True)
    step("server runs the frozen fs-1.3 artifact, checksum enforced",
         status["model"]["artifact_sha256"] == FROZEN_SHA256 and status["model"]["integrity_enforced"] is True)
    step("Level B calibration is off", status["personalization_policy"] == "history_only")
    step("replay controls refuse an unauthenticated caller", client.get("/demo/replay").status_code == 401)

    headers = login()
    reset = client.post("/demo/replay/reset", headers=headers)
    step("reset", reset.status_code == 200 and reset.json().get("event_index") == 0, reset.text[:120] if reset.status_code != 200 else "")
    step("the pre-reset session no longer works", client.get("/me", headers=headers).status_code == 401)
    headers = login()

    state = client.get("/demo/replay", headers=headers).json()
    step("replays the recording the manifest selected", state["bundle_sha256"] == manifest["bundle_sha256"])
    step("starts with no meals and no forecasts",
         client.get("/meals", headers=headers).json()["meals"] == []
         and client.get("/predictions", headers=headers).json()["predictions"] == [])
    me = client.get("/me", headers=headers).json()
    step("demo identity is fictional with a generated participant id",
         "fictional" in (me["display_name"] or "").lower() and me["participant_id"].startswith("gtp_")
         and manifest["source"]["participant"] not in me["participant_id"])
    devices = client.get("/devices", headers=headers).json()["connections"]
    step("research data is not presented as a connected device",
         all(d["status"] != "connected" and not d["authentic_data_received"] for d in devices))

    def latest_visible(position: str) -> str | None:
        readings = client.get("/cgm/readings", headers=headers, params={
            "start": state["window"]["start"], "end": "2100-01-01T00:00:00Z", "limit": 5000}).json()["readings"]
        return readings[-1]["observed_at"] if readings else None

    leak_free = prospective = genuine = reproducible = True
    admissible, checkpoints = [], [c for c in state["checkpoints"]]
    for checkpoint in checkpoints:
        response = client.post("/demo/replay/advance", headers=headers, json={"to": checkpoint["id"]})
        if response.status_code != 200:
            step(f"advance to {checkpoint['id']}", False, response.text[:160])
            break
        current = response.json()
        visible = latest_visible(current["position"])
        leak_free &= visible is not None and visible <= current["position"]
        leak_free &= all(m["occurred_at"] <= current["position"]
                         for m in client.get("/meals", headers=headers, params={"limit": 200}).json()["meals"])
        if checkpoint["kind"] != "meal_forecast":
            continue
        result = next(c["result"] for c in current["checkpoints"] if c["id"] == checkpoint["id"])
        if result.get("forecast") != "created":
            step(f"{checkpoint['id']} produced a forecast", False, str(result))
            continue
        prediction = client.get(f"/predictions/{result['prediction_id']}", headers=headers).json()
        admissible.append(prediction["personalization"]["admissible_prior_meals"])
        prospective &= prediction["prediction_kind"] == "prospective" and prediction["created_at"] == checkpoint["at"]
        genuine &= (prediction["model"]["artifact_sha256"] == FROZEN_SHA256
                    and len(prediction["forecast"]["points"]) == 25
                    and prediction["forecast"]["uncertainty_status"] == "not_calibrated"
                    and prediction["personalization"]["calibration_applied"] is False
                    and prediction["personalization"]["retrieval_active"] == (admissible[-1] >= 5))
        outcome = client.get(f"/meals/{result['meal_id']}/outcome", headers=headers).json()
        leak_free &= outcome["status"] == "pending" and outcome["observed"] is None
        reproduced = client.post(f"/predictions/{result['prediction_id']}/reproduce", headers=headers).json()
        reproducible &= reproduced.get("reproduced") is True

    final = client.get("/demo/replay", headers=headers).json()
    step("replay ran to completion", final["status"] == "complete", f"{final['event_index']} of {final['event_count']} events")
    step("no reading, meal or outcome from the future was ever visible", leak_free)
    step("every forecast was prospective at its historical instant", prospective)
    step("every forecast is genuine frozen-model output (25 points, checksum, no calibration)", genuine)
    step("every forecast reproduces exactly from its snapshot", reproducible, f"{len(admissible)} forecasts")
    planned = [m["expected_admissible_prior_meals"] for m in manifest["meals"]]
    step("personalization states match the manifest", admissible == planned, str(admissible))
    step("retrieval switched on at exactly five admissible meals", 4 in admissible and 5 in admissible)

    outcomes = [c["result"] for c in final["checkpoints"] if c["kind"] == "outcome_reveal"]
    step("every outcome was scored once revealed", all(o["outcome"] == "scored" for o in outcomes))
    step("ineligible meals were excluded, with reasons",
         [o["eligible_for_personalization"] for o in outcomes].count(False)
         == sum(1 for m in manifest["meals"] if not m["expected_eligible"]))
    alerts = client.get("/alerts", headers=headers).json()["alerts"]
    step("a server-generated informational alert exists",
         any(a["status"] == "active" and a["payload"]["informational_only"] for a in alerts), f"{len(alerts)} alerts")
    regimens = client.get("/medications", headers=headers).json()["regimens"]
    step("medications are labelled fictional", bool(regimens) and all("fictional" in r["name"].lower() for r in regimens))
    doses = client.get("/medication-doses", headers=headers).json()["doses"]
    step("dose history has deliberate taken and skipped actions",
         {"taken", "skipped"} <= {d["action"] for d in doses})
    due = client.get("/medication-reminders/due", headers=headers).json()["due"]
    step("a reminder is pending and not marked taken", bool(due) and all(d["actual_at"] is None for d in due))
    onboarding = client.get("/onboarding", headers=headers).json()
    step("questionnaire is populated", onboarding["completed"] is True and len(onboarding["answers"]) >= 8)

    client.post("/demo/replay/reset", headers=headers)
    headers = login()
    if args.leave_at:
        client.post("/demo/replay/advance", headers=headers, json={"to": args.leave_at})
    left = client.get("/demo/replay", headers=headers).json()
    step("left reset for the presentation", left["status"] == "ready", f"at event {left['event_index']}, replay time {left['position']}")

    print(f"\n{'ALL STEPS PASSED' if failures == 0 else f'{failures} STEP(S) FAILED'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
