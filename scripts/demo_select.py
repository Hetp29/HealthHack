"""Select the demonstration recording and build its data bundle.

    python -m scripts.demo_select [--warehouse sqlite:///data/local/cgmacros_v2.db]

Reads the local CGMacros research warehouse, applies the fixed, model-free selection
rule in `backend/app/v2/demo/source.py`, and writes:

  * `data/local/demo/cgmacros_demo_bundle.json` -- the selected participant's genuine
    readings and meal records for the replay window. Research data: gitignored.
  * `backend/app/v2/demo/manifest.json` -- what was selected and why, with the
    bundle's checksum. Metadata only: committed.

Selection never consults a forecast, a forecast error or a glucose value. Afterwards
one full replay is run in a scratch database to (a) confirm empirically that the
real application reaches the predicted personalization states and (b) pick the
informational alert threshold shown in the demo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import tempfile

from scripts._demo_common import build_app, fail, run_guarded

DEFAULT_WAREHOUSE = "sqlite:///data/local/cgmacros_v2.db"
ALERT_CANDIDATES_MG_DL = (180.0, 160.0, 140.0)
#: Released at replay start: the sensor history from before the first logged meal.
PRE_HISTORY = dt.timedelta(days=8)
TAIL = dt.timedelta(minutes=180)


def fit_cutoffs(repository) -> dict[str, dt.datetime]:
    """Each participant's Day-8 cutoff, from the FROZEN pipeline's own split.

    The shipped artifact was fitted on episodes at or before `cutoff - 120 min`. Every
    meal at or after the cutoff is therefore outside its fit. Read-only use of frozen
    code: nothing is trained and nothing is written.
    """
    from ml.config import DEFAULT_CONFIG
    from ml.evaluate import split_day8_holdout
    from ml.targets import build_episodes

    episodes, _ = build_episodes(repository, DEFAULT_CONFIG)
    train, validation, test = split_day8_holdout(episodes, DEFAULT_CONFIG)
    if (len(train) + len(validation), len(test)) != (1171, 394):
        fail(f"the frozen split did not reproduce (got {len(train) + len(validation)} / {len(test)}); "
             "this warehouse is not the one the model was built from")
    cutoffs: dict[str, dt.datetime] = {}
    for episode in test:
        cutoffs[episode.participant_id] = min(
            cutoffs.get(episode.participant_id, episode.meal_timestamp), episode.meal_timestamp)
    last_fit: dict[str, dt.datetime] = {}
    for episode in train + validation:
        last_fit[episode.participant_id] = max(
            last_fit.get(episode.participant_id, episode.meal_timestamp), episode.meal_timestamp)
    for participant, cutoff in cutoffs.items():
        if participant in last_fit and last_fit[participant] > cutoff - dt.timedelta(minutes=120):
            fail(f"{participant}: a fitted episode lies after cutoff - 120 min; separation broken")
    return cutoffs


def build_returning_user(args) -> int:
    """Build the optional returning-user bundle for the ALREADY selected participant.

    Nothing is re-selected. It takes the new-user scenario's participant and its nine
    presented meals, and adds every meal the recording holds before them -- all of
    them, in order, none chosen or dropped by hand. Those earlier meals are replayed
    through the ordinary forecast and outcome workflow at setup, so the presented
    journey begins with a real personal history.
    """
    from backend.app.v2.demo import replay, source
    from data.storage.repository import SqliteDataRepository

    base = source.load_manifest(source.manifest_path("new_user"))
    participant = base["source"]["participant"]
    path = pathlib.Path(args.warehouse.replace("sqlite:///", ""))
    if not path.exists():
        fail(f"the CGMacros research warehouse is not at {path}. No dataset is downloaded and none is substituted.")
    history = SqliteDataRepository(args.warehouse).get_history(participant)
    display_ids = [m["source_meal_id"] for m in base["meals"]]
    final_at = dt.datetime.fromisoformat(base["window"]["final_meal"])
    meals = [m for m in history.meals if m.timestamp <= final_at]
    if [m.meal_id for m in meals[-len(display_ids):]] != display_ids:
        fail("the warehouse meals do not end with the new-user scenario's presented meals")
    rows = [(m.meal_id, m.timestamp, None if not m.has_complete_macros else
             {k: getattr(m, k) for k in source.MACROS}) for m in meals]
    times = [p.timestamp for p in history.cgm]
    assessed = source.assess_meals(times, rows, [m.timestamp for m in history.meals])
    display_from = len(meals) - len(display_ids) + 1
    backfill = assessed[:display_from - 1]
    unloggable = [a["source_meal_id"] for a in backfill if not a["loggable"]]
    if unloggable:
        fail(f"earlier meals cannot be logged (invalid macros): {unloggable}; nothing is invented to replace them")

    def admissible(index: int) -> int:
        return source.expected_admissible(assessed, index, lookback=source.LOOKBACK)

    eligible_prior = admissible(display_from - 1)
    print(f"{participant}: {len(backfill)} meals precede the presented journey; "
          f"{sum(a['expected_eligible'] for a in backfill)} eligible; "
          f"{eligible_prior} admissible at the first presented meal")
    if eligible_prior < source.RETRIEVAL_MIN_HISTORY:
        fail(f"only {eligible_prior} authentic prior meals are admissible; a returning-user "
             "demonstration is not supported by this data and none will be manufactured")

    activation = next(i for i in range(len(assessed))
                      if assessed[i]["forecastable"] and admissible(i) >= source.RETRIEVAL_MIN_HISTORY)
    fit_note = ("These earlier meals precede the participant's Day-8 fit cutoff, so they WERE in "
                "the frozen artifact's fit: forecasts shown for them are in-sample and are not "
                "evidence of accuracy. The presented meals are after the cutoff and were not.")
    payload = source.make_payload(
        scenario_id="cgmacros-returning-user-replay-v1", data_class="research_dataset",
        timezone="UTC", device_id="dexcom-g6-pro",
        source={
            "dataset": "CGMacros v1.0.0", "participant": participant,
            "sensor": "Dexcom G6 Pro (primary series)",
            "time_basis": "Date-shifted participant-local wall-clock time carried under a UTC label; "
                          "the demo account's timezone is UTC, so the clock is unchanged.",
            "fit_cutoff": base["fit_relationship"]["fit_cutoff"],
            "plan": {"display_from_meal": display_from, "final_meal": len(meals),
                     "activation_meal": activation + 1,
                     "alert_threshold_mg_dl": base["alert"]["threshold_mg_dl"],
                     "backfill_fit_note": fit_note},
        },
        cgm=[(p.timestamp, p.glucose_mg_dl) for p in history.cgm if p.timestamp <= final_at + TAIL],
        meals=[(a["source_meal_id"], a["occurred_at"], rows[i][2]) for i, a in enumerate(assessed)],
    )
    bundle = source.bundle_from_payload(payload)

    print("running a verification replay (about 40 forecasts)...", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        app, ctx = build_app(app_db_url=f"sqlite:///{tmp}/select.db", image_dir=pathlib.Path(tmp) / "img",
                             demo_password="selection-run-only", bundle={"returning_user": bundle},
                             create_schema=True)
        initial = run_guarded(lambda: replay.initialize(app, ctx, "returning_user"))
        user_id = replay._demo_user_id(ctx, "returning_user")
        final = run_guarded(lambda: replay.advance(app, ctx, user_id, to="end"))
    forecasts = [c for c in final["checkpoints"] if c["kind"] == "meal_forecast"]
    refused = [c["id"] for c in forecasts if c["result"].get("forecast") != "created"]
    if refused:
        fail(f"forecasts were refused for {refused}; the plan does not hold")
    observed = [c["result"]["admissible_prior_meals"] for c in forecasts]
    planned = [admissible(i) for i in range(len(assessed))]
    if observed != planned:
        fail(f"the application's admissible counts {observed} differ from the plan {planned}")
    first = forecasts[display_from - 1]["result"]
    if not first["retrieval_active"] or initial["next_checkpoint"]["id"] != f"meal_{display_from:02d}_forecast":
        fail("retrieval is not active at the first presented meal")

    out = pathlib.Path(args.out or "data/local/demo/cgmacros_returning_user_bundle.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, sort_keys=True) + "\n")
    reasons: dict[str, int] = {}
    for a in backfill:
        for reason in a["expected_ineligibility_reasons"]:
            reasons[reason] = reasons.get(reason, 0) + 1
    manifest = {
        "scenario_id": bundle.scenario_id, "mode": "returning_user",
        "bundle_sha256": bundle.sha256, "bundle_schema": source.SCHEMA,
        "bundle_default_path": str(out), "data_class": "research_dataset",
        "source": {"dataset": "CGMacros v1.0.0", "participant": participant,
                   "sensor": "Dexcom G6 Pro (primary series)",
                   "warehouse": "data/local/cgmacros_v2.db (local, not committed)"},
        "selection": {
            "rule": "Same participant and same presented meals as the new-user scenario. The "
                    "backfill is EVERY meal the recording holds before them, in order; none is "
                    "chosen, dropped or altered.",
            "inputs_consulted": "timestamps and macro completeness only",
            "inputs_not_consulted": ["glucose values", "forecasts", "forecast errors"],
        },
        "fit_relationship": {
            "fit_cutoff": base["fit_relationship"]["fit_cutoff"],
            "statement": fit_note + " Neither set is independent validation.",
        },
        "backfill": {
            "meals_found": len(backfill),
            "eligible": sum(a["expected_eligible"] for a in backfill),
            "ineligible": sum(not a["expected_eligible"] for a in backfill),
            "ineligibility_reasons": reasons,
            "unloggable": 0,
            "admissible_at_first_presented_meal": eligible_prior,
            "lookback_days": source.LOOKBACK.days,
        },
        "window": {
            "replay_start": bundle.start.isoformat(), "first_meal": meals[0].timestamp.isoformat(),
            "first_presented_meal": assessed[display_from - 1]["occurred_at"].isoformat(),
            "final_meal": final_at.isoformat(), "last_reading": bundle.end.isoformat(),
            "readings": len(bundle.cgm), "meals": len(bundle.meals),
        },
        "meals": [{
            "number": i + 1, "phase": "backfill" if i + 1 < display_from else "display",
            "source_meal_id": a["source_meal_id"], "occurred_at": a["occurred_at"].isoformat(),
            "readings_in_window": a["readings_in_window"], "expected_eligible": a["expected_eligible"],
            "expected_ineligibility_reasons": a["expected_ineligibility_reasons"],
            "expected_admissible_prior_meals": planned[i],
        } for i, a in enumerate(assessed)],
        "plan": {"display_from_meal": display_from, "final_meal": len(meals),
                 "activation_meal": activation + 1,
                 "retrieval_active_at_first_presented_meal": True,
                 "verified_by_replay": True},
        "alert": {"threshold_mg_dl": base["alert"]["threshold_mg_dl"],
                  "label": "user-selected sample setting, same as the new-user scenario"},
        "excluded_from_git": ["the bundle file (research data)", "the warehouse", "the model artifact"],
    }
    source.manifest_path("returning_user").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"meals              {len(bundle.meals)} ({len(backfill)} backfill + {len(display_ids)} presented)")
    print(f"first presented    meal {display_from}: {planned[display_from - 1]} admissible prior meals, retrieval active")
    print(f"readings           {len(bundle.cgm)}")
    print(f"bundle             {out}  sha256 {bundle.sha256}")
    print(f"manifest           {source.manifest_path('returning_user').name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from backend.app.v2.demo import replay, source

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--warehouse", default=DEFAULT_WAREHOUSE)
    parser.add_argument("--scenario", choices=sorted(source.MODES), default="new_user")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    if args.scenario == "returning_user":
        return build_returning_user(args)
    args.out = args.out or "data/local/demo/cgmacros_demo_bundle.json"

    path = pathlib.Path(args.warehouse.replace("sqlite:///", ""))
    if not path.exists():
        fail(f"the CGMacros research warehouse is not at {path}. Build it with the documented "
             "data pipeline (docs/data_setup.md). No dataset is downloaded and none is substituted.")
    from data.storage.repository import SqliteDataRepository

    repository = SqliteDataRepository(args.warehouse)
    print("computing Day-8 fit cutoffs with the frozen split (about a minute)...", flush=True)
    cutoffs = fit_cutoffs(repository)

    candidates, table = [], []
    for participant in sorted(cutoffs):
        history = repository.get_history(participant)
        times = [point.timestamp for point in history.cgm]
        all_meal_times = [meal.timestamp for meal in history.meals]
        tail = [meal for meal in history.meals if meal.timestamp >= cutoffs[participant]]
        meals = [(meal.meal_id, meal.timestamp,
                  None if not meal.has_complete_macros else
                  {k: getattr(meal, k) for k in source.MACROS}) for meal in tail]
        assessed = source.assess_meals(times, meals, all_meal_times)
        plan = source.plan_window(assessed)
        row = {"participant": participant, "fit_cutoff": cutoffs[participant].isoformat(),
               "out_of_fit_meals": len(tail), "feasible": plan is not None}
        if plan is not None:
            row.update(plan)
            candidates.append((source.rank_key(participant, plan), participant, plan, assessed, history))
        table.append(row)
    if not candidates:
        fail("no participant can show the full personalization progression on out-of-fit meals")
    candidates.sort(key=lambda c: c[0])
    _, participant, plan, assessed, history = candidates[0]
    window = assessed[:plan["final_index"] + 1]
    first, last = window[0]["occurred_at"], window[-1]["occurred_at"]

    def payload(threshold: float | None) -> dict:
        by_id = {meal.meal_id: meal for meal in history.meals}
        return source.make_payload(
            scenario_id="cgmacros-out-of-fit-replay-v1", data_class="research_dataset",
            timezone="UTC", device_id="dexcom-g6-pro",
            source={
                "dataset": "CGMacros v1.0.0", "participant": participant,
                "sensor": "Dexcom G6 Pro (primary series)",
                "time_basis": "The dataset records date-shifted participant-local wall-clock "
                              "time with no timezone. It is carried here under a UTC label and "
                              "the demo account's timezone is UTC, so the clock is unchanged.",
                "fit_cutoff": cutoffs[participant].isoformat(),
                "plan": {"activation_meal": plan["activation_index"] + 1,
                         "final_meal": plan["final_index"] + 1,
                         **({} if threshold is None else {"alert_threshold_mg_dl": threshold})},
            },
            cgm=[(p.timestamp, p.glucose_mg_dl) for p in history.cgm
                 if first - PRE_HISTORY <= p.timestamp <= last + TAIL],
            meals=[(a["source_meal_id"], a["occurred_at"],
                    {k: getattr(by_id[a["source_meal_id"]], k) for k in source.MACROS}) for a in window],
        )

    # One real replay, in a scratch database, to verify the plan and observe forecasts.
    print(f"selected {participant}; running a verification replay...", flush=True)
    provisional = source.bundle_from_payload(payload(None))
    with tempfile.TemporaryDirectory() as tmp:
        app, ctx = build_app(app_db_url=f"sqlite:///{tmp}/select.db", image_dir=pathlib.Path(tmp) / "img",
                             demo_password="selection-run-only", bundle=provisional, create_schema=True)
        state = run_guarded(lambda: replay.initialize(app, ctx))
        user_id = replay._demo_user_id(ctx)
        state = run_guarded(lambda: replay.advance(app, ctx, user_id, to="end"))
        from sqlalchemy import select
        from backend.app.v2.db import tables as t
        with ctx.engine.connect() as conn:
            peaks = [max(point["glucose_mg_dl"] for point in forecast["points"][1:])
                     for (forecast,) in conn.execute(select(t.predictions.c.forecast))]
    results = [c["result"] for c in state["checkpoints"] if c["kind"] == "meal_forecast"]
    observed = next((i + 1 for i, r in enumerate(results) if r.get("retrieval_active")), None)
    if observed != plan["activation_index"] + 1:
        fail(f"the application activated retrieval at meal {observed}, the plan predicted "
             f"{plan['activation_index'] + 1}; the model-free rules and the real ones disagree")
    crossing = {str(int(c)): sum(1 for p in peaks if p >= c) for c in ALERT_CANDIDATES_MG_DL}
    threshold = next((c for c in ALERT_CANDIDATES_MG_DL if crossing[str(int(c))] >= 1), None)
    if threshold is None:
        threshold = float(int(max(peaks)) // 5 * 5)
        threshold_reason = (f"No forecast reaches {int(min(ALERT_CANDIDATES_MG_DL))} mg/dL, so the "
                            "demo uses a lower user-selected sample setting.")
    else:
        threshold_reason = (f"Highest of {[int(c) for c in ALERT_CANDIDATES_MG_DL]} mg/dL that at "
                            "least one real forecast in the window reaches.")

    final_payload = payload(threshold)
    bundle = source.bundle_from_payload(final_payload)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(final_payload, sort_keys=True) + "\n")

    manifest = {
        "scenario_id": bundle.scenario_id,
        "bundle_sha256": bundle.sha256,
        "bundle_schema": source.SCHEMA,
        "bundle_default_path": str(out),
        "data_class": "research_dataset",
        "source": {"dataset": "CGMacros v1.0.0", "participant": participant,
                   "sensor": "Dexcom G6 Pro (primary series)",
                   "warehouse": "data/local/cgmacros_v2.db (local, not committed)"},
        "selection": {
            "rule": source.SELECTION_RULE,
            "inputs_consulted": "timestamps and macro completeness only",
            "inputs_not_consulted": ["glucose values", "forecasts", "forecast errors"],
            "feasible_participants": sum(1 for r in table if r["feasible"]),
            "participants_considered": len(table),
            "ranking": [{"participant": c[1], "ineligible_before_activation": c[2]["ineligible_before_activation"],
                         "meal_count": c[2]["meal_count"]} for c in candidates[:8]],
        },
        "fit_relationship": {
            "fit_cutoff": cutoffs[participant].isoformat(),
            "statement": "Every replayed meal is at or after this participant's Day-8 cutoff. The "
                         "frozen artifact was fitted only on episodes at or before cutoff - 120 min, "
                         "so none of these meals was in its fit. They ARE among the previously "
                         "inspected Day-8 reference episodes: this is not independent validation.",
        },
        "window": {
            "replay_start": bundle.start.isoformat(), "first_meal": first.isoformat(),
            "final_meal": last.isoformat(), "last_reading": bundle.end.isoformat(),
            "readings": len(bundle.cgm), "meals": len(bundle.meals),
            "pre_history_days": PRE_HISTORY.days,
        },
        "meals": [{
            "number": i + 1, "source_meal_id": a["source_meal_id"],
            "occurred_at": a["occurred_at"].isoformat(),
            "readings_in_window": a["readings_in_window"],
            "expected_eligible": a["expected_eligible"],
            "expected_ineligibility_reasons": a["expected_ineligibility_reasons"],
            "expected_admissible_prior_meals": source.expected_admissible(assessed, i),
        } for i, a in enumerate(window)],
        "plan": {"activation_meal": plan["activation_index"] + 1, "final_meal": plan["final_index"] + 1,
                 "activation_verified_by_replay": True},
        "alert": {"threshold_mg_dl": threshold, "reason": threshold_reason,
                  "forecasts_reaching": crossing, "forecasts": len(peaks),
                  "label": "user-selected sample setting; informational, not a CGM alarm"},
        "excluded_from_git": ["the bundle file (research data)", "the warehouse", "the model artifact"],
    }
    source.MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"participant        {participant}")
    print(f"meals              {len(bundle.meals)} (retrieval activates at meal {plan['activation_index'] + 1})")
    print(f"readings           {len(bundle.cgm)}")
    print(f"alert threshold    {threshold:g} mg/dL")
    print(f"bundle             {out}  sha256 {bundle.sha256}")
    print(f"manifest           {source.MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
