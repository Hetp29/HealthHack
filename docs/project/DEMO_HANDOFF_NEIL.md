# Demo handoff to Neil — the demonstration from the frontend's side

From Ruhaan. How the GLIMPSE frontend drives and displays the sample-data demonstration. Scenario
and numbers: `DEMO_SCENARIO_SPEC.md`. General API rules: `HANDOFF_NEIL.md` and
`API_INTERFACE_REGISTRY.md` — everything there still applies; the demo account is an
ordinary account that happens to be replaying history.

**Status:** the backend side is implemented and verified over real HTTP. The replay
routes are PROPOSED like the rest of v2. The GLIMPSE frontend has not been wired to any of it.

## 0 · Two scenarios, two demo accounts

| | NEW USER (default) | RETURNING USER (optional) |
|---|---|---|
| Sign in as | `demo@glimpse.example` | `demo-returning@glimpse.example` |
| Display name | Demo User (fictional) | Returning Demo User (fictional) |
| Starts with | no meals, no personal history | 31 earlier meals with forecasts and outcomes |
| First presented forecast | 0 admissible meals, retrieval **off** | 27 admissible meals, retrieval **on** |
| Shows | history building; the gate opening at five | a forecast that already uses personal history |
| Presented meals | 9 (`meal_01` … `meal_09`) | the same 9 (`meal_32` … `meal_40`) |
| `GET /demo/replay` → `mode` | `new_user` | `returning_user` |

Same password, same routes, same payloads. **Which scenario a request acts on is
decided only by which account is signed in** — there is no mode parameter to send.
To switch scenario, sign in as the other account. A scenario picker in the UI is
just two login buttons.

The returning-user scenario may not be installed on a given server (it needs its own
data file). If login fails for it, hide that option.

## 1 · The idea in three sentences

The demo account holds a real research recording that is released into it over
*simulated* time. Pressing "advance" moves that simulated time to the next
checkpoint, where the backend logs the recorded meal and requests a real forecast,
or reveals a real outcome. **You read every result through the normal v2
endpoints** — the replay routes only move time.

## 2 · Set up locally

You need two files that are not in git, from Ruhaan: the model artifact directory
`ml/artifacts_cgmacros_fs13/` and `data/local/demo/cgmacros_demo_bundle.json`.

```bash
pip install -r requirements-dev.txt
export GLIMPSE_DEMO_MODE=1
export GLIMPSE_DEMO_PASSWORD='pick-anything-10-chars'
export GLIMPSE_MODEL_ARTIFACT=ml/artifacts_cgmacros_fs13/model.joblib
export GLIMPSE_METRICS_PATH=ml/artifacts_cgmacros_fs13/metrics.json
export GLIMPSE_EXPECTED_MODEL_SHA256=12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe

alembic upgrade head
python -m scripts.demo_seed
uvicorn backend.app.v2.asgi:app --port 8000
```

Without the two files you cannot run the real demo, but you can build every screen
against `contracts/v2/examples/demo_replay_*.json` — real responses from the same
code replaying a generated **test fixture** (`data_class: "demo"`). Same shapes,
arithmetic numbers.

## 3 · Sign in

```
POST /api/v2/auth/login   {"email": "demo@glimpse.example", "password": "<GLIMPSE_DEMO_PASSWORD>"}
```

A normal session token. Do not hard-code the password in the frontend bundle; take
it from a login form or a non-committed local env value. The account already has
both consents, a completed questionnaire, an alert rule and two fictional
medications — there is no onboarding to click through.

`GET /api/v2/status` → `"demo_mode": true` tells you the replay routes exist. If it
is `false`, they are 404: hide the demo controls.

## 4 · Replay controls

All three require the bearer token of the demo account. Any other account gets
`403 not_demo_account`.

### `GET /api/v2/demo/replay` — where are we

Example: `demo_replay_state_initial.json`.

| field | meaning |
|---|---|
| `mode`, `mode_label` | `new_user` / `returning_user`; `NEW USER` / `RETURNING USER` — show the label *(new, for review)* |
| `display_from_meal` | the first presented meal number: `1`, or `32` for the returning user *(new, for review)* |
| `position` | **the simulated "now"**, on the recording's timeline |
| `wall_clock` | the real time. Shown only so nobody confuses the two |
| `status` | `ready` or `complete` |
| `event_index` / `event_count` | progress; pass `event_index` back as `expected_event_index` |
| `next_checkpoint` | `{id, kind, at, meal}`; for a meal, also `meal_record` — the recorded meal about to be logged, with `source` = `research_dataset_meal_record` (or `test_fixture_meal_record` in the published examples) |
| `checkpoints[]` | every checkpoint with `done`, `phase` (`backfill` or `display`) and, once done, its `result` |
| `aliases` | `retrieval_activation`, `final_meal`, `end` → checkpoint ids; the returning user also has `first_display_meal` and `backfill_complete` |
| `personalization_progress` | `admissible_prior_meals`, `retrieval_active`, `retrieval_min_history: 5`, and a sentence you can show verbatim |
| `data` | readings and meals released so far, out of the totals |
| `provenance` | what is research data, what is fictional, and `labels` to display; the returning user adds `backfilled_history` `{meals, description}` |

### `POST /api/v2/demo/replay/advance` — move time forward

| body | effect |
|---|---|
| *(empty)* or `{"to": "next"}` | run to the next checkpoint and execute it |
| `{"to": "meal_07_forecast"}` or an alias | run every checkpoint up to and including that one |
| `{"minutes": 15}` | let 15 minutes pass — new readings appear — **without** executing a checkpoint; stops at the next checkpoint's time |
| `+ "expected_event_index": n` | makes a retry safe: if the replay has already moved, nothing happens |

Returns the same state object plus `advanced: true|false`. It is **forward-only**: a
checkpoint already passed is a no-op (`advanced: false`), never an error. Unknown
checkpoint → `422 unknown_checkpoint` listing the valid ids.

Always send `expected_event_index` from the last state you rendered. A double-click
or a retried request then cannot skip a step.

An advance to a far checkpoint runs several forecasts and can take a few seconds
(about 0.3 s per forecast). Use a 60 s timeout and a progress state.

Examples: `demo_replay_advance_meal_forecast.json`,
`demo_replay_advance_outcome_reveal.json`, `demo_replay_advance_minutes.json`,
`demo_replay_state_retrieval_active.json`, `demo_replay_retry_is_no_op.json`.

### `POST /api/v2/demo/replay/reset` — start over

Returns the initial state with `reset: true, sign_in_again: true`. **The account is
re-created, so your token is dead** — the next call returns 401. Log in again
immediately and discard anything cached (meal ids, prediction ids). Example:
`demo_replay_reset.json`.

### What a checkpoint does

| kind | backend does | then you read |
|---|---|---|
| `meal_forecast` | releases readings up to the meal instant; logs the recorded meal; requests the forecast | `result.meal_id`, `result.prediction_id` → `GET /meals/{id}`, `GET /predictions/{id}`; `GET /alerts`; `GET /cgm/readings` |
| `outcome_reveal` | releases readings up to window end + grace; asks for the outcome | `GET /meals/{id}/outcome` |

`result` for a forecast: `{meal_id, prediction_id, forecast: "created", prediction_kind,
admissible_prior_meals, retrieval_active, alert_id, alert_status}`. If the real rules
refuse a forecast it is `{forecast: "refused", code}` — show it as a refusal.

## 5 · The complete user journey, call by call

```
POST /auth/login
GET  /status                         demo_mode
GET  /demo/replay                    position, next_checkpoint, provenance
GET  /me                             "Demo User (fictional)", consents
GET  /onboarding                     completed questionnaire
GET  /cgm/status                     freshness (on the replay clock)
GET  /cgm/readings?start=…&end=…     the sensor chart up to `position`
GET  /medications                    two fictional regimens
GET  /medication-reminders/due       pending reminder(s)
GET  /alert-settings                 140 mg/dL sample setting

── loop ──────────────────────────────────────────────────────────────
POST /demo/replay/advance            {expected_event_index}
  meal_forecast   → GET /predictions/{prediction_id}     the 25-point forecast
                    GET /alerts                          if result.alert_id
  outcome_reveal  → GET /meals/{meal_id}/outcome         observed vs forecast
GET  /cgm/readings                   chart grows to the new position
──────────────────────────────────────────────────────────────────────

POST /medication-doses               presenter taps Taken/Skipped on the pending reminder
POST /demo/replay/reset              start over (then log in again)
```

For `GET /cgm/readings` pass explicit `start` and `end` from the replay state
(`window.start` … `position`); the default "last 24 hours" is relative to the replay
position, which is what you want, but a wider chart needs explicit bounds. `limit`
max 5000.

**The presenter can also log a meal by hand.** The demo account is a normal account
whose clock is the replay position, so `POST /meals` with `occurred_at` = `position`
(and macros), then `POST /meals/{id}/predict`, works and gives a genuine prospective
forecast. Use `{"minutes": n}` to let readings accumulate afterwards. A hand-logged
meal becomes part of that run's history; reset to get back to the scripted journey.

## 6 · Suggested demonstration sequence

Start from `python -m scripts.demo_reset --to meal_06_outcome` (or advance there):
five eligible meals complete, retrieval not yet active.

1. **Overview.** Sensor history, six logged meals, the progress indicator at
   "4 of 5" on the last forecast (meal 6). Point at meal 2: scored, but excluded —
   another meal fell inside its window.
2. **Advance → meal 7 forecast.** `retrieval_active` turns **true**,
   `admissible_prior_meals: 5`. This is the moment personal history starts driving
   retrieval. No outcome yet: `pending`.
3. **Advance → meal 8 forecast** (15 minutes later). Still 5 — meal 7's window is
   open. An alert is raised.
4. **Advance** through the dose action to **meal 7 outcome**: observed curve over the
   forecast; meal 7 is marked ineligible (meal 8 intervened).
5. **Advance → meal 8 outcome**: eligible.
6. **Advance → meal 9 forecast** (next morning): 6 admissible meals.
7. **Medication tab:** history shows taken / skipped / taken; the 08:00 reminder is
   pending. Tap Taken → it resolves.
8. **Advance → meal 9 outcome**: forecast versus reality for the final meal. Done.

To show the alert that rises from below the threshold, start earlier
(`--to meal_04_outcome`) and advance to `meal_05_forecast`.

## 6a · The RETURNING USER scenario

Start: `python -m scripts.demo_reset --scenario returning_user`. The account opens
with 31 logged meals, each with a forecast and a scored outcome, and the replay
positioned just before the first presented meal.

1. **Overview.** A week of meals and sensor history. `personalization_progress`
   already shows history active. Four history meals are marked ineligible
   (intervening meal) — the same rule, applied the same way.
2. **Advance → `meal_32_forecast`.** The first presented forecast:
   `admissible_prior_meals: 27`, `retrieval_active: true`. Compare with the NEW USER
   forecast for the same meal (0 admissible, retrieval off) to show what personal
   history changes.
3. Continue exactly as the NEW USER sequence: outcomes, the alert, the medication
   reminder, the final meal.

Rendering rules specific to this scenario:

* **Show only `phase: "display"` checkpoints as steps.** `backfill` checkpoints are
  history that setup already ran; all are `done`. List their meals under a "History"
  heading, not as demo steps.
* Number presented meals yourself (`meal − display_from_meal + 1`): show "Meal 1 of
  9", not "Meal 32".
* **Label the history as in-sample.** `provenance.backfilled_history.description`
  says it: these earlier meals were in the model's fit. Do **not** present their
  forecast-versus-actual charts or errors as accuracy. A caption such as "History
  meals — shown for context, not as a measure of accuracy" is required wherever
  their outcomes appear.
* The admissible count goes 27 → 31 and then **down to 29** at the last meal: the
  model sees at most eight days of history, so the oldest meals age out. Show the
  backend's number; do not treat a decrease as an error.
* Paginate history lists (`GET /meals?limit=200`); there are 40 meals by the end.
* Its dose history is longer (17 scripted actions across the week). Same labels.

## 6b · Simulated connected-health sources

**Status:** implemented and verified locally; PROPOSED like the rest of v2.

Both demo accounts can switch on four **simulated** wearable sources. Their data is
generated, labelled as generated, and flows through the real wearable ingestion and
storage. Nothing is connected to Apple, Fitbit or WHOOP. The glucose is different: it
is the real research recording, never simulated.

### Endpoints (demo account only; 404 when demo mode is off)

| method | route | purpose | example |
|---|---|---|---|
| GET | `/api/v2/demo/devices` | the four profiles + the glucose source, with status and counts | `demo_devices_initial.json` |
| POST | `/api/v2/demo/devices/{profile}/activate` | switch one on. No body. **Idempotent** | `demo_device_activated.json`, `demo_device_activate_repeat_is_no_op.json` |
| GET | `/api/v2/demo/health-timeline?hours=24&limit=200` | meals, alerts, doses and wearable summaries, ordered | `demo_health_timeline.json` |
| GET | `/api/v2/wearables/observations?provider=&metric=&start=&end=&limit=` | the observations themselves (existing endpoint; `provider` filter is new) | `demo_wearable_observations_daily.json`, `demo_wearable_observations_heart_rate.json` |
| GET | `/api/v2/devices` | existing connections list; simulated ones have status `demo_simulated` | `demo_devices_connections.json` |

`{profile}` is one of `apple_health`, `apple_watch`, `fitbit`, `whoop`. Anything else
→ `404 unknown_demo_device` with `details.allowed` (`error_demo_unknown_device.json`).

### `GET /demo/devices`

```jsonc
{
  "position": "2020-05-08T12:56:00Z",          // replay time
  "simulation_version": "sim-1",
  "disclaimer": "Simulated sample data for demonstration. No Apple, Fitbit or WHOOP account is connected …",
  "profiles": [{
    "profile": "apple_watch",
    "provider": "demo_apple_watch",            // use as ?provider= on /wearables/observations
    "label": "Apple Watch",
    "display_label": "Apple Watch (simulated sample)",   // show THIS
    "kind": "wearable_device",                 // apple_health is "health_data_platform"
    "description": "Simulated Apple Watch.",
    "status": "inactive",                      // or "demo_simulated"
    "simulated": true,
    "data_class": "demo",
    "authorization": "none - no account was linked and no provider was contacted",
    "metrics": [{"metric": "heart_rate", "unit": "bpm",
                 "observations_visible": 0, "latest_observed_at": null}],
    "notes": {"heart_rate": "One simulated sample every 15 minutes."}
  }],
  "glucose_source": {
    "provider": "research_cgmacros",
    "label": "CGMacros research recording (Dexcom G6 Pro)",
    "kind": "historical_replay", "simulated": false, "data_class": "research_dataset",
    "status": "replaying", "description": "Genuine historical glucose readings … Not a live sensor and not a connected Dexcom account.",
    "readings_visible": 2030, "latest_observed_at": "2020-05-08T12:54:00Z"
  },
  "used_by_forecast_model": {"glucose_source": true, "simulated_profiles": false, "explanation": "…"}
}
```

`POST …/activate` returns the same object plus `profile`, `already_active` and
`observations_released`.

### Profiles and measurements

| profile | what it is | metrics (unit) | cadence |
|---|---|---|---|
| `apple_health` | **a data-aggregation platform, not a sensor** — say so | `steps_daily` (count), `resting_heart_rate` (bpm), `sleep_duration` (min), `workout` (min) | daily; per session |
| `apple_watch` | wearable | `heart_rate` (bpm), `steps_daily` (count), `workout` (min) | HR every 15 min |
| `fitbit` | wearable | `heart_rate` (bpm), `steps_daily` (count), `sleep_duration` (min), `resting_heart_rate` (bpm) | HR every 30 min |
| `whoop` | wearable | `recovery_score` (percent), `sleep_duration` (min), `resting_heart_rate` (bpm), `hrv_rmssd` (ms) | daily. **No heart-rate stream** — do not draw one |

One unit per metric, always. A `workout` observation's `value` is its duration in
minutes; `detail.sport` is `walking`, `running` or `cycling`. Interval observations
(sleep, workout, daily steps) have `observed_at` (start) and `observed_end_at` (end).

The four sources describe one simulated person, so they roughly agree and differ
slightly, as real trackers do. Apple Health's steps and workouts equal the Apple
Watch's (it aggregates the watch). Showing the same day's steps from three sources
side by side is fine; do not sum them.

### Each observation

```jsonc
{
  "observation_id": "wob_…", "provider": "demo_whoop",
  "metric": "sleep_duration", "value": 428.0, "unit": "min",
  "observed_at": "2020-05-02T00:15:00Z", "observed_end_at": "2020-05-02T07:45:00Z",
  "received_at": "2020-05-08T12:56:00Z",      // the REPLAY time it became visible
  "data_class": "demo",
  "detail": {"simulated": true, "device_profile": "whoop", "source_label": "WHOOP",
             "simulation_version": "sim-1"},
  "used_by_forecast_model": false
}
```

Provenance to key the UI on: **`data_class`**. `demo` = simulated → badge it.
`research_dataset` = the real glucose recording. `user_device` = a real person's real
device; it does not occur in the demo. `detail.simulated === true` is the
per-observation confirmation, and `provider` always starts `demo_` for these.

### Activation flow

1. `GET /demo/devices` → four cards, all `inactive`, plus the glucose-source card.
2. The presenter taps **"Simulate Apple Health connection"**.
3. `POST /demo/devices/apple_health/activate` → `status: "demo_simulated"`,
   `observations_released: N`.
4. `GET /wearables/observations?provider=demo_apple_health&start=…&end=…` → that
   source's simulated history up to the replay position appears.
5. Repeat for other sources; each adds its own observations.
6. `POST /demo/replay/advance` — glucose **and** active simulated sources move forward
   together; newly "recorded" observations appear.
7. Forecast, outcome, alerts: exactly as before. Show the wearable context beside the
   glucose chart.

A double tap or a retry is harmless: the second call returns `already_active: true`
and releases nothing.

**The button must say "Simulate".** Do not build a screen that imitates Apple's,
Google's or WHOOP's sign-in or consent page, do not show their login forms, and do not
show a "Connected" state. There is no account, no permission prompt and no token
behind this. A plain confirmation — "This adds simulated sample data. No real account
is involved." — is the right interstitial.

### Timeline behaviour

* Observations become visible only once the replay position has reached them: a
  heart-rate sample at its own time, a daily step total when that day ends, a sleep
  session when it ends. Today's steps are therefore not shown until the replay
  crosses midnight. That is deliberate.
* Activating a source reveals its history up to the current position at once (as
  importing a device's history would). Those rows share one `received_at`.
* `GET /demo/health-timeline` gives an ordered event list ending at the replay
  position. Item `type`: `wearable`, `meal`, `alert`, `medication_dose`. Every item
  has `at`, `data_class`, `simulated`, `source`; wearable items add `metric`,
  `value`, `unit`, `observed_at`, `observed_end_at`, `provider`,
  `used_by_forecast_model: false`. Heart-rate samples and glucose readings are
  **series**, not events — chart them from `/wearables/observations?metric=heart_rate`
  and `/cgm/readings`.
* `/wearables/observations` defaults to the last 7 days before the replay position;
  pass `start`/`end` for other ranges; `limit` ≤ 5000.

### Expected dashboard states

| state | what you get |
|---|---|
| Fresh / after reset | four profiles `inactive`; no wearable observations; `GET /devices` lists only the research glucose source (`pending`) |
| After activating one | that profile `demo_simulated` with non-zero `observations_visible`; others `inactive` |
| After advancing | higher counts on active profiles; inactive ones unchanged |
| All four on, replay complete (NEW USER) | about 1,400 simulated observations |
| RETURNING USER | identical behaviour; devices also start `inactive` (the 31-meal setup runs with none on) |

### Required disclaimer text

Show `disclaimer` from `GET /demo/devices` verbatim wherever simulated data appears:

> Simulated sample data for demonstration. No Apple, Fitbit or WHOOP account is
> connected and no real device produced these values. They are not inputs to the
> glucose forecast.

And, per source, `display_label` — "Fitbit (simulated sample)" — not the bare brand
name. Near the forecast, state what the model actually uses
(`used_by_forecast_model.explanation`): glucose history, the confirmed meal's
macronutrients, meal timing and the account's own completed meal responses. **Not**
heart rate, steps, sleep, recovery, workouts or medication. Putting wearable data next
to a forecast must not suggest the forecast used it.

For the glucose card use `glucose_source.label` and `description`: a research
recording being replayed — not "Dexcom connected".

### Errors

| response | show |
|---|---|
| 404 on `/demo/devices…` | demo mode is off: hide the simulation controls |
| `403 not_demo_account` | "Sign in with a demo account" |
| `404 unknown_demo_device` | developer error; `details.allowed` lists the profiles |
| `already_active: true` | not an error — refresh the card |
| `503 demo_data_unavailable` | "Demo data is not installed on this server" |

### Reset

`POST /demo/replay/reset` (or `demo_reset`) returns every simulated source to
`inactive` and removes its observations, along with everything else in that demo
account. The same taps in the same order then produce the same data. Simulation is
per account: switching sources on for NEW USER does nothing to RETURNING USER.

## 7 · Required labels

Non-negotiable; `provenance.labels` gives you the strings.

* A persistent **DEMO · HISTORICAL REPLAY · NOT LIVE** banner whenever the demo
  account is signed in.
* The scenario name from `mode_label` — **NEW USER** or **RETURNING USER** — next to
  that banner, so nobody mistakes one account's state for the other's.
* Show `position` as **"Replay time"** (or "Recording time"), never as "now" and
  never as today's date. The dates are date-shifted research timestamps — prefer
  "Day 1, 13:26" style labels computed from `window.start` over a calendar date.
* Glucose: `data_class: "research_dataset"` → label **"Research dataset (CGMacros)"**.
  Never "your CGM", never a device brand as a connection, never "live".
* The devices screen must show **no connected device**. The research source appears
  with status `pending` and `authentic_data_received: false`; render it as
  "Sample data source", not as a pairing.
* Simulated wearable sources have status `demo_simulated` — render as "Simulated
  sample", never as "Connected" or "Authorized". See §6b.
* Every forecast here is `prediction_kind: "prospective"` **within the replay**.
  Label it "Forecast made at replay time 13:26" — not "live forecast".
* Medications: the names contain "(fictional)" and `prescriber_note` says
  `FICTIONAL DEMO RECORD…`. Keep both visible. Do not show the education cards for
  them (their class is `other`).
* The profile name is "Demo User (fictional)". Keep it.
* Alerts: the level is a **sample setting**; keep the standing disclaimer; no
  probability, no "alarm".
* Always show `forecast.limitations`.

## 8 · Personalization progress

Use the backend's numbers only — do not count meals yourself.

* Per forecast: `GET /predictions/{id}` → `personalization.admissible_prior_meals`,
  `retrieval_active`, `retrieval_min_history` (5).
* Latest: `GET /demo/replay` → `personalization_progress`.

A "N of 5 completed meals" meter that fills and then reads "Personal history active"
is accurate. The copy to use: **"Glucose history and completed meal responses inform
future personalized predictions."** Do not write "retraining", "learning" or "the AI
improves after every meal" — no model parameter changes.

Why the count sometimes does not go up: a meal counts only when its full 120-minute
window has ended before the next meal, and only if it is eligible. `GET
/meals/{id}/outcome` → `eligible_for_personalization` and `ineligibility_reasons`
(`intervening_meal` in this scenario) explain each case — show them.

## 9 · Observed versus predicted

`GET /meals/{id}/outcome`:

* `pending` → show the forecast alone with "Outcome available at replay time
  `scoring_not_before`". `observed` is `null`: there is nothing to draw, by design.
* `scored` → overlay `observed.curve[] = {minute, glucose_mg_dl}` on
  `forecast.points[]`. The observed series is real readings on a 5-minute grid;
  **draw gaps as gaps**, do not interpolate.
* `errors.by_horizon` gives predicted, observed and error at 30/60/90/120 min;
  `errors.mae_mg_dl` summarizes. Error = predicted − observed.

Do not present these errors as the model's accuracy. One participant, nine meals.

## 10 · Errors and fallback states

| response | show |
|---|---|
| `GET /status` → `demo_mode: false`, or 404 on a replay route | no demo controls |
| `403 not_demo_account` | "Sign in with the demo account" |
| `401` after reset | log in again (expected) |
| `503 demo_data_unavailable` | "Demo data is not installed on this server" + `message` |
| `503 demo_requires_frozen_model` | "The server is not running the approved model" |
| `409 demo_bundle_changed` | "Demo data changed — reset the demo" |
| `422 unknown_checkpoint` | developer error; `details.checkpoint_ids` lists valid ids |
| `advanced: false` | not an error — refresh from the returned state |
| checkpoint `result.forecast: "refused"` | "No forecast: `code`" for that meal; continue |
| `status: "complete"` | disable advance; offer reset |

## 10a · Known limitations

* The returning user's 31 history meals were in the model's fit (in-sample). The nine
  presented meals were not. Neither is independent validation.
* One participant. Both scenarios replay the same person's recording.
* Resetting the returning user takes several seconds (it replays 31 meals; about 5 s locally). Use a
  60 s timeout and a progress state.
* The new state fields (`mode`, `mode_label`, `display_from_meal`, `phase`,
  `provenance.backfilled_history`) are additive and PROPOSED — tell me in the
  interface review if a name should change.
* The published `demo_replay_*returning_user*` examples replay a generated test
  fixture (7 history meals, not 31). Same shapes, smaller numbers.

## 11 · What I need from you

1. Whether these three routes and the state object give the screens what they need.
2. Whether you want smaller time steps as a first-class control (today: `minutes`).
3. Any label wording you want changed in `provenance`.
