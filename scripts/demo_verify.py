"""Verify the demonstration end to end, in a scratch database.

    python -m scripts.demo_verify

Runs the complete replay twice with the frozen model and checks, on real output:

  * the bundle is the one the manifest selected, and the model is the frozen artifact;
  * readings are stored exactly as the source has them -- none added, none altered;
  * at every forecast, nothing later than the forecast instant was in the store;
  * the backend's own personalization states match the plan, and retrieval turns on
    at exactly five admissible meals;
  * every forecast is prospective, 25 points, and reproduces exactly from its snapshot;
  * outcomes exist only after their reveal time;
  * two independent runs, and a reset, give an identical state.

It never touches the configured application database. Exit code 0 only if every
check passes.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import zlib

from scripts._demo_common import FROZEN_SHA256, build_app, run_guarded


def main() -> int:
    from sqlalchemy import func, select
    from starlette.testclient import TestClient

    from backend.app.v2.db import tables as t
    from backend.app.v2.demo import replay, source

    failures: list[str] = []

    def check(name: str, ok: bool, note: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + note if note else ''}")
        if not ok:
            failures.append(name)

    manifest = source.load_manifest()
    digests = []
    with tempfile.TemporaryDirectory() as tmp:
        for run in (1, 2):
            app, ctx = build_app(app_db_url=f"sqlite:///{tmp}/verify{run}.db",
                                 image_dir=pathlib.Path(tmp) / f"img{run}",
                                 demo_password="verification-run-only", create_schema=True)
            bundle = run_guarded(lambda: replay.get_bundle(ctx))
            timeline = replay.build_timeline(bundle)
            run_guarded(lambda: replay.initialize(app, ctx))
            user_id = replay._demo_user_id(ctx)

            if run == 1:
                check("bundle matches the committed manifest", bundle.sha256 == manifest["bundle_sha256"])
                check("model is the frozen fs-1.3 artifact, checksum enforced",
                      ctx.model_integrity["artifact_sha256"] == FROZEN_SHA256 and ctx.model_integrity["enforced"])
                check("Level B calibration is off", ctx.settings.personalization_policy == "history_only")
                with ctx.engine.connect() as conn:
                    initial = conn.execute(select(func.max(t.glucose_observations.c.observed_at))).scalar()
                    meals0 = conn.execute(select(func.count()).select_from(t.meals)).scalar()
                check("at start, nothing later than the replay start is stored",
                      initial <= bundle.start and meals0 == 0, f"latest reading {initial}")

            leak_free = monotonic = True
            previous = bundle.start
            for event in [e for e in timeline if e["checkpoint"]]:
                state = run_guarded(lambda: replay.advance(app, ctx, user_id, to=event["id"]))
                with ctx.engine.connect() as conn:
                    latest = conn.execute(select(func.max(t.glucose_observations.c.observed_at))).scalar()
                    future_meals = conn.execute(select(func.count()).select_from(t.meals).where(
                        t.meals.c.occurred_at > event["at"])).scalar()
                leak_free &= latest <= event["at"] and future_meals == 0
                monotonic &= event["at"] >= previous
                previous = event["at"]
            digests.append(replay.digest(ctx, user_id)["digest"])
            if run == 2:
                continue

            check("the store never held a reading or meal later than the replay position", leak_free)
            check("replay position only moved forward", monotonic)
            check("replay completed", state["status"] == "complete")

            with ctx.engine.connect() as conn:
                stored = conn.execute(select(t.glucose_observations.c.observed_at,
                                             t.glucose_observations.c.glucose_mg_dl,
                                             t.glucose_observations.c.source_value,
                                             t.glucose_observations.c.source_unit,
                                             t.glucose_observations.c.data_class,
                                             t.glucose_observations.c.correction_count)
                                      .order_by(t.glucose_observations.c.observed_at)).all()
                predictions = conn.execute(select(t.predictions).order_by(t.predictions.c.created_at)).mappings().all()
                snapshots = {s["snapshot_id"]: s for s in conn.execute(select(t.feature_snapshots)).mappings()}
                episodes = conn.execute(select(t.response_episodes)).mappings().all()
                connections = conn.execute(select(t.device_connections)).mappings().all()
                binding = conn.execute(select(t.participant_bindings.c.participant_id)).scalar()
            expected = [(at, value) for at, value in bundle.cgm if at <= timeline[-1]["at"]]
            check("stored readings equal the source readings exactly (count, time, value)",
                  [(r[0], r[1]) for r in stored] == expected, f"{len(stored)} readings")
            check("no reading was converted, corrected or relabelled",
                  all(r[2] == r[1] and r[3] == "mg/dL" and r[4] == "research_dataset" and r[5] == 0 for r in stored))
            check("research data is not shown as a connected device",
                  all(c["status"] != "connected" and c["authentic_data_received_at"] is None
                      and c["access_token_enc"] is None for c in connections))
            check("demo account uses a generated participant id, not the research one",
                  binding.startswith("gtp_") and bundle.source["participant"] not in binding)

            results = [c["result"] for c in state["checkpoints"] if c["kind"] == "meal_forecast"]
            planned = [m["expected_admissible_prior_meals"] for m in manifest["meals"]]
            actual = [r.get("admissible_prior_meals") for r in results]
            check("admissible prior meals reported by the backend match the plan", actual == planned,
                  f"{actual}")
            check("retrieval is active exactly when five or more meals are admissible",
                  all(r["retrieval_active"] == (r["admissible_prior_meals"] >= 5) for r in results))
            check("the progression covers below, at and above the gate",
                  min(actual) == 0 and 4 in actual and 5 in actual and max(actual) > 5)
            check("every forecast was made prospectively at its historical instant",
                  all(p["prediction_kind"] == "prospective" and p["created_at"] == snapshots[p["snapshot_id"]]["as_of"]
                      for p in predictions))
            check("every forecast records the frozen artifact and has 25 points",
                  all(p["artifact_sha256"] == FROZEN_SHA256 and len(p["forecast"]["points"]) == 25
                      and p["applied_offsets"] == {"30": 0.0, "60": 0.0, "90": 0.0, "120": 0.0}
                      for p in predictions))
            no_future = True
            for p in predictions:
                history = json.loads(zlib.decompress(snapshots[p["snapshot_id"]]["history_blob"]))
                as_of = history["meal_timestamp"]
                no_future &= all(ts <= as_of for ts, _ in history["cgm"])
                no_future &= all(row[1] < as_of for row in history["meals"])
            check("no forecast's input history contains anything after its own instant", no_future)

            client = TestClient(app)
            login = client.post("/auth/login", json={"email": ctx.settings.demo_email,
                                                     "password": ctx.settings.demo_password}).json()
            headers = {"Authorization": f"Bearer {login['session']['token']}"}
            reproduced = [client.post(f"/predictions/{p['prediction_id']}/reproduce", headers=headers).json()
                          for p in predictions]
            check("every stored forecast reproduces exactly from its snapshot",
                  all(r.get("reproduced") and r.get("history_sha256_verified") for r in reproduced),
                  f"{len(reproduced)} forecasts")
            check("outcomes were scored only after their reveal time",
                  all(e["scored_at"] is None or e["scored_at"] >= e["window_end_at"] for e in episodes))
            by_end = {e["window_end_at"]: e for e in episodes}
            reasons_ok = True
            for meal in manifest["meals"]:
                import datetime as dt
                episode = by_end[dt.datetime.fromisoformat(meal["occurred_at"]) + source.WINDOW]
                reasons_ok &= episode["eligible_for_personalization"] == meal["expected_eligible"]
                reasons_ok &= episode["ineligibility_reasons"] == meal["expected_ineligibility_reasons"]
            check("each meal's eligibility and reasons match the manifest", reasons_ok)
            alerts = client.get("/alerts", headers=headers).json()["alerts"]
            check("at least one server-generated informational alert exists",
                  any(a["status"] == "active" and a["payload"]["uncertainty_status"] == "not_calibrated" for a in alerts),
                  f"{len(alerts)} alerts")
            doses = client.get("/medication-doses", headers=headers).json()["doses"]
            actions = sorted(d["action"] for d in doses)
            check("fictional medication history has deliberate taken and skipped actions",
                  "taken" in actions and "skipped" in actions
                  and all((d["actual_at"] is not None) == (d["action"] == "taken") for d in doses), str(actions))
            due = client.get("/medication-reminders/due", headers=headers).json()["due"]
            check("an unanswered reminder is pending and is not marked taken",
                  len(due) >= 1 and all(d["action"] == "scheduled" and d["actual_at"] is None for d in due))
            regimens = client.get("/medications", headers=headers).json()["regimens"]
            check("every medication is labelled fictional",
                  len(regimens) == 2 and all("fictional" in r["name"].lower() for r in regimens)
                  and {r["status"] for r in regimens} == {"current", "discontinued"})

            first = digests[0]
            run_guarded(lambda: replay.reset(app, ctx))
            new_user = replay._demo_user_id(ctx)
            run_guarded(lambda: replay.advance(app, ctx, new_user, to="end"))
            check("reset followed by the same replay gives an identical state",
                  replay.digest(ctx, new_user)["digest"] == first)
            with ctx.engine.connect() as conn:
                users = conn.execute(select(func.count()).select_from(t.users)).scalar()
            check("reset left exactly one account", users == 1)

    check("two independent runs give an identical state", digests[0] == digests[1], digests[0][:16])
    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} CHECK(S) FAILED'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
