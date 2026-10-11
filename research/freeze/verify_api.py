"""Phases 6 and 7: API contract verification, determinism and performance.

    python -m research.freeze.verify_api --db-url sqlite:///data/local/cgmacros_v2.db

Runs the service against the REAL frozen fs-1.3 artifact and the REAL CGMacros
warehouse, because that is the deployed configuration. Responses are validated
against the frozen JSON Schemas in `contracts/schemas/`, not against the backend's
own Pydantic models -- checking the service against its own types would only prove
internal consistency.

Nothing here adds, removes or reorders a contract field, and nothing changes a
prediction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import statistics
import time

ART = pathlib.Path("ml/artifacts_cgmacros_fs13")
SCHEMAS = pathlib.Path("contracts/schemas")
RESULTS = pathlib.Path("research/freeze/results")

#: Declared tolerance T9: the contract serialises glucose to 1 dp.
T9_CONSISTENCY = 0.05
#: Recorded fs-1.3 latency baseline (median, ms). Advisory only -- see TOLERANCES.md.
RECORDED_MEDIAN_MS = 359.0


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-url", required=True)
    args = ap.parse_args()

    before = {f.name: sha(f) for f in sorted(ART.iterdir())}
    os.environ["GLIMPSE_MODEL_ARTIFACT"] = str(ART / "model.joblib")
    os.environ["GLIMPSE_METRICS_PATH"] = str(ART / "metrics.json")
    os.environ["GLIMPSE_DATA_BACKEND"] = "sqlite"
    os.environ["GLIMPSE_DB_URL"] = args.db_url

    import tracemalloc

    from fastapi.testclient import TestClient
    from jsonschema import Draft202012Validator

    from backend.app.config import load_settings
    from backend.app.main import create_app

    blockers: list[str] = []
    report: dict = {"artifact_sha256_before": before}

    def validator(name: str):
        return Draft202012Validator(json.loads((SCHEMAS / name).read_text()))

    tracemalloc.start()
    t0 = time.perf_counter()
    app = create_app(load_settings())
    client = TestClient(app)
    with client:
        init_ms = (time.perf_counter() - t0) * 1000.0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        report["model_init_ms"] = round(init_ms, 1)
        report["peak_traced_mem_mb"] = round(peak / 1e6, 1)
        print("=== PHASE 6: API CONTRACT VERIFICATION ===")
        print(f"  app init {init_ms:.0f} ms | peak traced memory {peak/1e6:.1f} MB")

        info = client.get("/api/model/info")
        print(f"  GET /api/model/info -> {info.status_code}")
        if info.status_code != 200:
            print("  *** service did not come up:", info.text[:300])
            blockers.append(f"/api/model/info returned {info.status_code}")
            return 1
        body = info.json()
        report["model_info"] = body
        # `contract_version` is NOT a field of model_info_response -- that schema has
        # exactly six fields and the service forbids extras, so the contract version is
        # carried by GET /api/health instead.
        for key, want in (("feature_set_version", "fs-1.3"), ("data_source", "official"),
                          ("model_version", "v0.1.0")):
            got = body.get(key)
            ok = got == want
            print(f"    {key:22s} {got!r} {'OK' if ok else f'*** expected {want!r} ***'}")
            if not ok:
                blockers.append(f"model/info {key}={got!r}, expected {want!r}")

        # --- every endpoint validated against its OWN frozen schema -------- #
        print("\n  endpoint schema conformance:")
        endpoints = [
            ("/health", "health_response.schema.json"),
            ("/api/model/info", "model_info_response.schema.json"),
            ("/api/model/metrics", "metrics_response.schema.json"),
            ("/api/participants", "participants_response.schema.json"),
        ]
        for path, schema in endpoints:
            resp = client.get(path)
            if resp.status_code != 200:
                print(f"    {path:24s} -> {resp.status_code} *** not 200 ***")
                blockers.append(f"{path} returned {resp.status_code}")
                continue
            errs = sorted(validator(schema).iter_errors(resp.json()),
                          key=lambda e: list(e.path))
            print(f"    {path:24s} -> 200  {schema:38s} "
                  f"{'VALID' if not errs else f'{len(errs)} ERRORS'}")
            for e in errs[:3]:
                blockers.append(f"{path} violates contract at {list(e.path)}: "
                                f"{e.message[:120]}")
        health = client.get("/health").json()
        hv = health.get("version")
        ok = hv == "1.0.0"
        print(f"    /health version {hv!r} "
              f"{'OK -- contract v1.0' if ok else '*** expected 1.0.0 ***'}")
        if not ok:
            blockers.append(f"/health version={hv!r}, expected '1.0.0'")
        report["health"] = health

        parts = client.get("/api/participants")
        plist = parts.json()
        items = plist.get("participants", plist) if isinstance(plist, dict) else plist
        pid = items[0]["participant_id"] if items else None
        print(f"  GET /api/participants -> {parts.status_code}, {len(items)} participants")
        report["n_participants"] = len(items)

        # pick a participant and a meal time with real history
        import sqlite3
        con = sqlite3.connect(args.db_url.replace("sqlite:///", ""))
        row = con.execute(
            "SELECT participant_id, timestamp, carbs_g, protein_g, fat_g, fiber_g, "
            "calories_kcal FROM meals WHERE carbs_g IS NOT NULL AND protein_g IS NOT NULL "
            "AND fat_g IS NOT NULL AND fiber_g IS NOT NULL AND calories_kcal IS NOT NULL "
            "ORDER BY participant_id, timestamp LIMIT 1 OFFSET 40").fetchone()
        pid, ts = row[0], row[1]
        meal = {"carbs_g": row[2], "protein_g": row[3], "fat_g": row[4],
                "fiber_g": row[5], "calories_kcal": row[6]}
        req = {"participant_id": pid, "meal_timestamp": ts.replace(".000000Z", "Z"),
               "meal": meal}
        print(f"  prediction subject: {pid} at {req['meal_timestamp']}")

        r = client.post("/api/predict", json=req)
        print(f"  POST /api/predict -> {r.status_code}")
        if r.status_code != 200:
            print("  *** body:", r.text[:400])
            blockers.append(f"/api/predict returned {r.status_code}")
            return 1
        pred = r.json()
        report["prediction_keys"] = sorted(pred.keys())

        # --- contract schema conformance ---------------------------------- #
        schema_name = "predict_response.schema.json"
        errs = sorted(validator(schema_name).iter_errors(pred),
                      key=lambda e: list(e.path))
        print(f"  contract schema {schema_name}: "
              f"{'VALID' if not errs else f'{len(errs)} ERRORS'}")
        for e in errs[:6]:
            print(f"    - {list(e.path)}: {e.message[:140]}")
            blockers.append(f"contract violation at {list(e.path)}: {e.message[:140]}")

        # --- trajectory structure ----------------------------------------- #
        traj = pred["points"]
        n = len(traj)
        print(f"  trajectory points: {n} {'OK' if n == 25 else '*** expected 25 ***'}")
        if n != 25:
            blockers.append(f"trajectory has {n} points, contract requires 25")
        minute_key = next(k for k in ("minute", "minutes_from_meal", "offset_minutes")
                          if k in traj[0])
        value_key = next(k for k in ("glucose_mg_dl", "predicted_glucose_mg_dl")
                         if k in traj[0])
        mins = [p[minute_key] for p in traj]
        print(f"  point fields: minute={minute_key!r} value={value_key!r} "
              f"all keys {sorted(traj[0].keys())}")
        ascending = all(mins[i] < mins[i + 1] for i in range(len(mins) - 1))
        print(f"  strictly ascending minute order: {ascending}")
        if not ascending:
            blockers.append("trajectory minutes are not strictly ascending")
        expected = list(range(0, 125, 5))
        print(f"  minute grid: {mins[:4]}...{mins[-2:]} "
              f"{'OK' if mins == expected else '*** WRONG ***'}")
        if mins != expected:
            blockers.append(f"minute grid {mins} != 0..120 step 5")
        vals = [p[value_key] for p in traj]
        nonfinite = [v for v in vals if v is None or not math.isfinite(float(v))]
        print(f"  non-finite glucose values: {len(nonfinite)} "
              f"{'OK' if not nonfinite else '*** PRESENT ***'}")
        if nonfinite:
            blockers.append(f"{len(nonfinite)} non-finite glucose values in trajectory")
        print(f"  glucose range: {min(vals):.1f} .. {max(vals):.1f} mg/dL")
        report["trajectory"] = {"n": n, "minutes": mins, "values": vals}

        # --- summary vs trajectory consistency (T9) ------------------------ #
        def find(d, *names):
            for nm in names:
                if nm in d:
                    return d[nm]
            for v in d.values():
                if isinstance(v, dict):
                    got = find(v, *names)
                    if got is not None:
                        return got
            return None

        checks = []
        metrics = pred["metrics"]
        print(f"  metrics fields: {sorted(metrics.keys())}")
        peak_val = find(metrics, "peak_glucose_mg_dl", "peak_mg_dl")
        if peak_val is not None:
            d = abs(float(peak_val) - max(vals))
            checks.append(("peak magnitude", float(peak_val), max(vals), d, T9_CONSISTENCY))
        ttp = find(metrics, "time_to_peak_minutes", "peak_minutes")
        if ttp is not None:
            arg = mins[vals.index(max(vals))]
            checks.append(("time-to-peak", float(ttp), float(arg),
                           abs(float(ttp) - arg), 0.0))
        iauc = find(metrics, "incremental_auc_mg_dl_min", "iauc_mg_dl_min", "iauc")
        if iauc is not None:
            base = vals[0]
            recomputed = sum(
                5.0 * ((max(vals[i] - base, 0.0) + max(vals[i + 1] - base, 0.0)) / 2.0)
                for i in range(len(vals) - 1))
            checks.append(("incremental AUC", float(iauc), recomputed,
                           abs(float(iauc) - recomputed), 5.0 * len(vals) * 0.05))
        for h in (30, 60, 90, 120):
            hv = pred["glucose_at_minutes"].get(str(h))
            if hv is not None:
                tv = vals[mins.index(h)]
                checks.append((f"{h}-min horizon", float(hv), float(tv),
                               abs(float(hv) - tv), T9_CONSISTENCY))
        print("\n  summary-vs-trajectory consistency:")
        for label, reported, derived, diff, tol in checks:
            ok = diff <= tol
            print(f"    {label:18s} reported {reported:9.2f}  from trajectory "
                  f"{derived:9.2f}  |diff| {diff:7.3f}  tol {tol:5.2f}  "
                  f"{'PASS' if ok else 'FAIL'}")
            if not ok:
                blockers.append(f"{label} inconsistent: {reported} vs {derived}")
        report["consistency_checks"] = [
            {"check": c[0], "reported": c[1], "derived": c[2], "diff": c[3], "tol": c[4]}
            for c in checks]

        drivers = pred["drivers"]
        print(f"\n  explanatory drivers: {len(drivers) if drivers else 0}")
        if drivers:
            from ml.features import FEATURE_NAMES
            for d in (drivers if isinstance(drivers, list) else [])[:5]:
                nm = d.get("feature") if isinstance(d, dict) else d
                grounded = nm in FEATURE_NAMES if isinstance(nm, str) else False
                print(f"    {str(nm)[:34]:34s} grounded in the 39 features: {grounded}")
                if isinstance(nm, str) and not grounded:
                    blockers.append(f"driver {nm!r} is not one of the 39 fs-1.3 features")
        unc = pred["uncertainty_status"]
        print(f"  uncertainty_status: {json.dumps(unc)[:200]}")
        print(f"  limitations declared: {len(pred['limitations'])}")
        for lim in pred["limitations"][:3]:
            print(f"    - {str(lim)[:110]}")
        report["drivers"] = drivers
        report["uncertainty"] = unc
        mv = pred["model_version"]
        print(f"  model_version in response: {mv!r}")

        # --- error handling ------------------------------------------------ #
        print("\n  error handling:")
        cases = [
            ("invalid participant", {**req, "participant_id": "does_not_exist"}, (404, 422)),
            ("malformed meal", {**req, "meal": {"carbs_g": "lots"}}, (400, 422)),
            ("impossible numeric", {**req, "meal": {**meal, "carbs_g": -50}}, (400, 422)),
            ("missing required field", {"participant_id": pid}, (400, 422)),
            ("extra unknown field", {**req, "surprise": 1}, (400, 422)),
            ("naive timestamp", {**req, "meal_timestamp": "2020-05-01 14:23:00"}, (400, 422)),
        ]
        errs_report = {}
        for label, payload, allowed in cases:
            resp = client.post("/api/predict", json=payload)
            ok = resp.status_code in allowed
            print(f"    {label:24s} -> {resp.status_code} "
                  f"{'OK' if ok else f'*** expected one of {allowed} ***'}")
            errs_report[label] = resp.status_code
            if not ok:
                blockers.append(f"{label}: got {resp.status_code}, expected {allowed}")
        bad_src = client.get("/api/model/info")
        report["error_handling"] = errs_report

        # --- PHASE 7: determinism and latency ------------------------------ #
        print("\n=== PHASE 7: DETERMINISM AND PERFORMANCE ===")
        first = client.post("/api/predict", json=req).json()
        identical = True
        for _ in range(9):
            again = client.post("/api/predict", json=req).json()
            if again != first:
                identical = False
        print(f"  10 identical requests byte-identical JSON (T1): "
              f"{'YES' if identical else '*** NO ***'}")
        if not identical:
            blockers.append("repeated identical requests produced different responses")

        latencies = []
        subjects = list(con.execute(
            "SELECT participant_id, timestamp, carbs_g, protein_g, fat_g, fiber_g, "
            "calories_kcal FROM meals WHERE carbs_g IS NOT NULL AND protein_g IS NOT NULL "
            "AND fat_g IS NOT NULL AND fiber_g IS NOT NULL AND calories_kcal IS NOT NULL "
            "ORDER BY participant_id, timestamp LIMIT 60 OFFSET 100"))
        distinct_ok = 0
        statuses: set = set()
        for s in subjects:
            body = {"participant_id": s[0],
                    "meal_timestamp": s[1].replace(".000000Z", "Z"),
                    "meal": {"carbs_g": s[2], "protein_g": s[3], "fat_g": s[4],
                             "fiber_g": s[5], "calories_kcal": s[6]}}
            t = time.perf_counter()
            resp = client.post("/api/predict", json=body)
            latencies.append((time.perf_counter() - t) * 1000.0)
            if resp.status_code == 200:
                distinct_ok += 1
                payload = resp.json()
                tj = payload["points"]
                vv = [p[value_key] for p in tj]
                bad_schema = sorted(validator(schema_name).iter_errors(payload),
                                    key=lambda e: list(e.path))
                if (len(tj) != 25
                        or any(v is None or not math.isfinite(float(v)) for v in vv)
                        or bad_schema):
                    blockers.append(
                        f"bad response for {s[0]} @ {s[1]}: points={len(tj)} "
                        f"schema_errors={len(bad_schema)}")
                statuses.add(payload["prediction_status"])
        latencies.sort()
        med = statistics.median(latencies)
        p95 = latencies[int(0.95 * len(latencies)) - 1]
        print(f"  {len(latencies)} predictions across distinct participants and meal sizes;"
              f" {distinct_ok} returned 200")
        print(f"  median latency {med:.0f} ms | p95 {p95:.0f} ms | "
              f"min {latencies[0]:.0f} | max {latencies[-1]:.0f}")
        print(f"  recorded fs-1.3 baseline median {RECORDED_MEDIAN_MS:.0f} ms "
              f"(T10 advisory, no gate)")
        print(f"  prediction_status values observed: {sorted(statuses)}")
        report["prediction_statuses"] = sorted(statuses)
        report["latency_ms"] = {"n": len(latencies), "median": round(med, 1),
                                "p95": round(p95, 1), "min": round(latencies[0], 1),
                                "max": round(latencies[-1], 1),
                                "recorded_baseline_median": RECORDED_MEDIAN_MS}

    after = {f.name: sha(f) for f in sorted(ART.iterdir())}
    report["artifact_sha256_after"] = after
    report["artifact_unchanged"] = after == before
    print(f"\n  frozen artifact unchanged: {after == before}")
    if after != before:
        blockers.append("ARTIFACT CHANGED during API verification")

    report["blockers"] = blockers
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "phase6_phase7_api.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n")
    print(f"\nwrote {RESULTS}/phase6_phase7_api.json")
    if blockers:
        print("\n*** BLOCKERS ***")
        for b in blockers:
            print("  -", b)
        return 1
    print("\nPHASE 6 + 7: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
