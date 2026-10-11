# GLIMPSE — API interface registry

The single registry of every HTTP interface (master §6). Maintained by Ruhaan.

| | |
|---|---|
| Machine-readable schema | `contracts/v2/openapi.json` — generated from the running code, never hand-edited |
| Live copy | `GET /api/v2/openapi.json`, interactive docs at `/api/v2/docs` |
| Example payloads | `contracts/v2/examples/*.json` — real responses captured from the application |
| Regenerate both | `python -m contracts.v2.build_contract` |
| Drift guard | `tests/v2/test_contract_artifacts.py` fails if the published schema is stale or an operation is missing from this file |

**Request and response schemas are not duplicated here.** For any endpoint, the
schema is in `openapi.json` and a real example is the file named in the *example*
column. This file records what the schema cannot: who owns and consumes each
endpoint, its guarantees, and its status.

## Approval status

**Every v2 endpoint below is PROPOSED.** On 2026-10-10 Ruhaan accepted the
implementation as the *integration baseline* and stated explicitly that this is not
blanket API approval (DECISIONS R-001). Endpoints move to APPROVED only after the
shared interface review.

| who | what they sign off | status |
|---|---|---|
| Ruhaan | server contract and correctness | accepted as integration baseline; **endpoints remain PROPOSED** |
| Neil | that the payloads support the screens | **not yet reviewed** |
| Het | reachability, CORS, body-size limits | **not yet reviewed** |

Build against these contracts now, and raise anything wrong during the review. Field
names are intended to be stable; a change before approval will be announced in
`DECISIONS.md`, and any change after approval is a versioned change.

---

## 1 · Frozen contract v1.0 — DO NOT CHANGE

Owner: Ruhaan · Consumer: Neil (the GLIMPSE frontend today) · Auth: none · Status: **FROZEN, VERIFIED**

| method | route |
|---|---|
| GET | `/health` |
| GET | `/api/participants` |
| GET | `/api/participants/{id}/history` |
| POST | `/api/predict` |
| POST | `/api/compare` |
| GET | `/api/model/metrics` |
| GET | `/api/model/info` |

Defined by `contracts/openapi.yaml` (SHA-256 `be836ddd…200f4`). Error envelope
`{code, message, detail}`. These serve **research-dataset participants only** and are
unauthenticated; they hold no user data. Byte-identity with v2 mounted is tested.

---

## 2 · Conventions for every v2 endpoint

**Base path** `/api/v2`. Paths below are relative to it.

**Authentication.** `Authorization: Bearer <token>` from `POST /auth/login` or
`/auth/register`. The acting user always comes from the token. No endpoint accepts a
`user_id`; sending one is a 422. Another user's resource is indistinguishable from a
missing one: **404 `not_found`**, never 403.

**Consent.** Endpoints marked **C** additionally require the `necessary_processing`
consent to be `granted`, else **403 `consent_required`**.

**Errors.** Every non-2xx response:

```json
{"code": "meal_not_confirmed", "message": "…", "request_id": "req_…", "details": {}}
```

Branch on `code`; `message` may be reworded. `details` never echoes a submitted
health value. Every response carries `X-Request-ID`.

| status | codes used across the API |
|---|---|
| 401 | `unauthenticated`, `session_expired`, `invalid_credentials` |
| 403 | `consent_required`, `account_disabled`, `registration_closed` |
| 404 | `not_found` |
| 409 | state conflicts — listed per endpoint |
| 413 | `image_too_large`, `file_too_large` |
| 415 | `unsupported_image_type`, `content_type_mismatch` |
| 422 | `invalid_request` (with `details.errors[] = {field, message, type}`), plus specific codes per endpoint |
| 429 | `analysis_limit_reached` |
| 5xx | `internal_error`, `not_ready`, `model_unavailable`, `model_integrity_failure`, `vision_*`, `provider_not_configured` |

**Timestamps.** RFC 3339 with an explicit offset, in and out. Responses are UTC with
`Z`. A timestamp without an offset is rejected (422), never assumed to be UTC. The
frontend converts to local time for display using the account `timezone`.

* `observed_at` — when a measurement physically happened.
* `received_at` — when GLIMPSE stored it. Never interchangeable.

**Units.** Glucose is stored and returned in **mg/dL**; a request must state
`"unit": "mg/dL"` or `"mmol/L"`, and the original value and unit are preserved
(`1 mmol/L = 18.016 mg/dL`). Macronutrients in **grams**, energy in **kcal**. Every
numeric field's unit is in its name or in a sibling `unit`/`units` field.

**Numbers.** `NaN` and `Infinity` are rejected everywhere.

**Unknown fields** are rejected (422), not ignored.

**Data class.** Observations carry `data_class`: `user_device`, `demo` or
`research_dataset`. The UI must label `demo` visibly.

**Null means unknown**, not zero. A missing optional value is `null`.

---

## 3 · System

Owner: Ruhaan · Auth: none

| method | route | consumer | purpose | example |
|---|---|---|---|---|
| GET | `/ready` | Het | Readiness probe. 200 only when the database is reachable, the model is loaded and its checksum is verified. 503 `not_ready` otherwise, with per-check booleans. | `system_ready` |
| GET | `/status` | Neil, Het | Model identity, personalization policy, which integrations are configured, which notification channels really exist. No secrets. | `system_status` |

Liveness stays on the frozen `GET /health`.

## 4 · Identity

Owner: Ruhaan · Consumer: Neil

| method | route | auth | notes | failures | example |
|---|---|---|---|---|---|
| POST | `/auth/register` | none | 201. `timezone` (IANA) is required — local time of day is a model input. Creates the participant binding. | 409 `email_taken`, 403 `registration_closed`, 422 | `auth_register` |
| POST | `/auth/login` | none | Returns `session.token` and `expires_at`. Same message for unknown email and wrong password. | 401 `invalid_credentials` | — |
| POST | `/auth/logout` | bearer | 204. Revokes this session. | 401 | — |
| GET | `/me` | bearer | Profile, `participant_id`, current consents. | 401 | `auth_me` |
| PATCH | `/me` | bearer | `display_name`, `timezone`. | 422 | — |
| DELETE | `/account` | bearer | 204. Body `{password}`. Deletes every row and photo; revokes provider grants where the provider allows. **Irreversible.** | 401 `invalid_credentials` | — |

Idempotency: register is naturally idempotent-safe (second call → 409). Token lifetime
14 days (`GLIMPSE_SESSION_TTL_HOURS`). On 401 the frontend should return to login.

## 5 · Onboarding and consent

Owner: Ruhaan (server) / Neil (questions, wording, skip logic) · Consumer: Neil

| method | route | auth | notes | failures | example |
|---|---|---|---|---|---|
| GET | `/onboarding` | bearer | `answers`, `completed`, `version`, and the recognized question ids by section. | — | — |
| PATCH | `/onboarding` | bearer | **Merge** semantics: send only changed answers; `null` clears one. Optional `expected_version` for optimistic concurrency. | 409 `version_conflict`, 422 unknown question id / missing unit | `onboarding_saved` |
| GET | `/consents` | bearer | Both consent types with state `granted` / `withdrawn` / `not_recorded`. | — | — |
| PUT | `/consents/{consent_type}` | bearer | Body `{state}`. Types: `necessary_processing`, `personalization_history`. Append-only history. | 404 unknown type | `consent_granted` |

* Measured answers (`height`, `weight`, `hba1c`, `fasting_glucose`, `alert_threshold`)
  must be `{"value": n, "unit": "…"}` or the string `"unknown"` /
  `"prefer_not_to_say"`. A bare number is rejected — HbA1c 6.5 % and glucose
  6.5 mmol/L are different things. The entered value and unit are stored unchanged.
* **No onboarding answer is a model input.** Tested.
* Withdrawing `necessary_processing` blocks every **C** endpoint at once; it does not
  delete data (that is `DELETE /account`). Withdrawing `personalization_history`
  stops completed meals being used as history from the next forecast.
* Question ids are a proposal for Neil to amend: see `GET /onboarding` → `sections`.

## 6 · Meals

Owner: Ruhaan · Consumer: Neil · Auth: bearer + **C**

| method | route | notes | failures | example |
|---|---|---|---|---|
| POST | `/meals` | 201. Manual meal. With `macros` it is created already confirmed. | 422 | — |
| POST | `/meals/images` | 201. **Body is the raw image**; `Content-Type: image/jpeg\|png\|webp`; `occurred_at` is a query parameter. Max 10 MB. | 413, 415, 422 `invalid_image` | `meal_photo_uploaded` |
| GET | `/meals` | Newest first. `start`, `end`, `limit` ≤ 200. | — | — |
| GET | `/meals/{meal_id}` | Photo metadata, latest estimate, current confirmation, `prediction_id`. | 404 | `meal_detail` |
| GET | `/meals/{meal_id}/image` | The sanitized JPEG. Needs the bearer header, so fetch it and use a blob URL — it cannot be an `<img src>`. | 404 | — |
| DELETE | `/meals/{meal_id}/image` | 204. Removes the photo only. | 404 | — |
| POST | `/meals/{meal_id}/analyze` | AI estimate. Returns the existing estimate unless `?force=true`. | 409 `meal_has_no_photo`, 429, 503 `vision_not_configured`, 502/504 `vision_*` | `meal_analyzed` |
| PATCH | `/meals/{meal_id}/confirm` | All five values required. Each call is a new immutable revision. | 409 `meal_voided`, 422 | `meal_confirmed` |
| PATCH | `/meals/{meal_id}/consumption` | `as_logged` / `partial` / `not_eaten`. Decides outcome eligibility. | 404 | — |
| DELETE | `/meals/{meal_id}` | 204. Voids the meal, deletes the photo. | 404 | — |

**Meal status:** `draft` → `analyzed` (a usable estimate exists) → `confirmed` →
(`voided`). **`occurred_at`** may be at most 15 minutes in the future.

**The estimate is an estimate.** `estimate.is_estimate` and
`needs_user_confirmation` are always `true`. It carries `totals` (grams rounded to
1 g, energy to 5 kcal), `ranges` (low/high per value), `items[]` with
`visibility` (`visible` / `partially_visible` / `assumed_hidden`) and a qualitative
`confidence`, `assumptions`, `ambiguities`, `clarification_questions`, `warnings[]`
and `provenance` (`ai_model_requested`, `ai_model_reported`, `prompt_version`).
There are no confidence percentages by design. `status: "not_food"` has
`totals: null`.

Warning codes: `low_confidence`, `hidden_fats_likely`, `no_portion_reference`,
`multiple_plates`, `calorie_macro_mismatch`, `fiber_exceeds_carbohydrate`,
`exceeds_supported_range`, `estimate_outside_own_range`, `no_items_identified`.

**Confirmation `source`** is set by the server: `manual`, `ai_accepted` (unchanged),
`ai_edited`, or `ai_unconfirmed`. Bounds are those of contract v1.0: carbs / protein /
fat 0–1000 g, fiber 0–200 g, energy 0–10000 kcal.

iPhone note: HEIC is rejected (415). Use `<input type="file" accept="image/jpeg">` or
convert client-side.

## 7 · Forecasts and outcomes

Owner: Ruhaan · Consumer: Neil · Auth: bearer + **C**

| method | route | notes | failures | example |
|---|---|---|---|---|
| POST | `/meals/{meal_id}/predict` | Runs frozen fs-1.3. **Idempotent**: one forecast per confirmation revision; a repeat returns it with `replayed: true`. Optional body `{allow_unconfirmed_estimate: true}`. | 409 `meal_not_confirmed` / `meal_voided` / `no_usable_estimate`, 422 `insufficient_premeal_context`, 503 `model_unavailable` / `model_integrity_failure` | `prediction_created`, `prediction_replayed` |
| GET | `/predictions` | Newest first, `limit` ≤ 100. | — | — |
| GET | `/predictions/{prediction_id}` | The stored record. Never changes after creation. | 404 | — |
| POST | `/predictions/{prediction_id}/reproduce` | Re-runs the model on the stored inputs; `reproduced: true` if identical. | 404 | `prediction_reproduced` |
| GET | `/meals/{meal_id}/outcome` | Observed versus forecast. | 409 `no_prediction` | `outcome_pending`, `outcome_scored` |

**`forecast` is exactly the frozen contract v1.0 `ForecastResponse`** — 25 points at
minutes 0, 5, …, 120; `glucose_at_minutes`; `metrics`; `drivers`;
`uncertainty_status: "not_calibrated"`; `limitations[]`. Render it with the existing
v1 components. Always show `limitations`.

Around it, v2 adds:

| field | meaning |
|---|---|
| `prediction_kind` | `prospective` (requested within 15 min of the meal) or `retrospective` |
| `inputs_confirmed` | `false` only for the opt-in unreviewed-AI path — label the forecast prominently |
| `model` | id, version, feature set, artifact SHA-256 |
| `personalization` | `policy`, `consent`, `admissible_prior_meals`, `retrieval_active` (true from 5 completed meals), `calibration_applied` |
| `context_quality` | `latest_cgm_age_minutes`, `cgm_readings_last_60_min`, `cgm_device`, `multi_device_overlap`, `missing_features[]`, `history_truncated_at_offset_change`, `offset_change_at` (true for up to 8 days after a daylight-saving change: a shorter history was used) |
| `outcome_status`, `outcome_not_before` | when the comparison with reality will exist |
| `alert` | the alert event this forecast raised, or `null` |

`422 insufficient_premeal_context` means no CGM reading within 15 minutes before the
meal; `details.latest_cgm_age_minutes` says how old the newest one is. Show a "sensor
data is stale" state — do not retry automatically.

**Outcome status:** `pending` (window or grace period not over; see
`scoring_not_before`), `scored`, `insufficient_data` (retried for 72 h as data
arrives), `voided`. `errors.by_horizon[h] = {predicted_mg_dl, observed_mg_dl,
error_mg_dl}` with `error = predicted − observed`; also `mae_mg_dl`, `rmse_mg_dl`,
`bias_mg_dl`, `peak_error_mg_dl`. `observed.curve[]` is the measured series for the
forecast-versus-actual plot — real readings only, with gaps left as gaps.

`ineligibility_reasons[]`: `missing_horizon_targets`, `insufficient_curve_points`,
`intervening_meal`, `sensor_correction_in_window`, `quality_flagged_readings`,
`meal_partially_eaten`, `meal_not_eaten`, `inputs_unconfirmed`,
`meal_inputs_revised`, `personalization_consent_absent`, `meal_voided`.

**Request a forecast for every confirmed meal**, including ones logged afterwards:
only a forecast meal gets an outcome, and only outcomes become personal history.

## 8 · Glucose

Owner: Ruhaan · Consumer: Neil · Auth: bearer + **C**

| method | route | notes | failures | example |
|---|---|---|---|---|
| POST | `/cgm/readings` | JSON push, ≤ 5000 readings. `source`: `manual_entry`, `healthkit_bridge`, `demo`. **Idempotent.** | 422 | `cgm_push` |
| POST | `/cgm/import` | **Body is the CSV file.** `?format=dexcom_clarity_csv\|libreview_csv`, optional `date_order=MDY\|DMY`. Max 15 MB. **Idempotent.** | 413, 422, 422 `ambiguous_date_format` | — |
| GET | `/cgm/readings` | Ascending. Default last 24 h. `unit`, `limit` ≤ 5000. Includes `gaps[]`. | 422 | `cgm_readings` |
| GET | `/cgm/status` | `freshness`, `latest_age_minutes`, `forecast_ready`, coverage, gaps, chosen sensor. | — | `cgm_status_fresh`, `cgm_status_stale`, `cgm_status_no_data` |
| GET | `/cgm/imports` | Past batches with counts and rejection reasons. | — | — |

Ingestion returns `counts = {received, inserted, duplicates, corrected, rejected,
rejected_by_reason}` and the first 50 `rejections[] = {index, reason}`. A batch is
never rejected whole because some rows are bad. Rejection reasons:
`out_of_range` (outside 20–600 mg/dL), `future_timestamp`, `nonfinite_value`,
`naive_timestamp`, `unknown_unit`, `missing_timestamp`,
`conflicting_duplicate_in_batch`, `sensor_range_marker` (a "Low"/"High" marker —
never converted to a number), `ambiguous_local_time`, `nonexistent_local_time`,
`unparseable_value`, `unparseable_timestamp`.

`freshness`: `fresh` (≤ 15 min), `delayed` (≤ 60 min), `stale`, `no_data`. Only
`fresh` can anchor a forecast.

A changed value for an already-stored instant is a **correction**: the old value is
kept in history and the reading is returned with `corrected: true`.

## 9 · Devices

Owner: Ruhaan · Consumer: Neil (UI), Het (redirect URIs, secrets)

| method | route | auth | notes | failures | example |
|---|---|---|---|---|---|
| GET | `/devices` | bearer | The user's connections plus the capability matrix for every provider. | — | `devices_list` |
| POST | `/devices/{provider}/connect` | bearer + C | Returns `authorization_url` to open in the browser. Providers: `dexcom_api`, `fitbit`, `whoop`. | 503 `provider_not_configured` (names the missing settings), 404 | `error_provider_not_configured` |
| GET | `/devices/{provider}/callback` | **none** — authenticated by the single-use `state` | OAuth redirect target. 303 to `GLIMPSE_FRONTEND_RETURN_URL?provider=…&status=…` when set, else JSON. | 400 `invalid_oauth_state` | — |
| POST | `/devices/{provider}/sync` | bearer + C | Always 200 once the provider was contacted — read `outcome`: `ok`, `authorization_revoked`, `rate_limited`, `provider_error`, `provider_unreachable`. | 409 `not_authorized` / `authorization_revoked`, 503 | — |
| DELETE | `/devices/{provider}` | bearer | Destroys stored tokens; `revoked_upstream` says whether the provider was told. Stored data is kept. | 404 | — |

**Connection status — the UI must not blur these:**

| status | meaning | show as |
|---|---|---|
| `pending` | connect started | "Connecting…" |
| `authorized` | account linked, **no data received** | "Linked — waiting for data" |
| `connected` | authentic observations are stored | "Connected" |
| `demo_simulated` | a labelled sample source in a demo account; nothing was contacted, data is generated | "Simulated sample" — never "Connected" |
| `error` | last attempt failed (`last_error_code`) | error + retry |
| `revoked` | the provider withdrew access | "Reconnect" |
| `disconnected` | disconnected by the user | — |

`authentic_data_received` is the field to trust. A demo or sandbox source never
becomes `connected`. Tokens are never returned by any endpoint.

**Implementation status of every provider: see section 13.** No provider has been
contacted; none may be described as working.

## 10 · Wearables

Owner: Ruhaan · Consumer: Neil; a future native iOS client · Auth: bearer + **C**

| method | route | notes | failures | example |
|---|---|---|---|---|
| POST | `/wearables/observations` | Native-bridge upload, ≤ 5000 samples, `source`: `healthkit_bridge`, `demo`. **Idempotent** on `source_record_id`; a changed value updates. | 422 | `wearables_pushed` |
| GET | `/wearables/observations` | `metric`, `provider` *(new, for review)*, `start`, `end`. Default last 7 days. | — | `demo_wearable_observations_daily`, `demo_wearable_observations_heart_rate` |

Metrics and their one accepted unit: `heart_rate` bpm, `resting_heart_rate` bpm,
`steps_daily` count, `sleep_duration` min, `hrv_rmssd` ms, `hrv_sdnn` ms,
`recovery_score` percent, `workout` min. Every returned observation has
`used_by_forecast_model: false` — wearable data is display-only (DECISIONS D-008).

## 11 · Medications

Owner: Ruhaan · Consumer: Neil · Auth: bearer + **C**

| method | route | notes | failures | example |
|---|---|---|---|---|
| GET | `/medications` | `?status=current\|discontinued`. | — | `medications_list` |
| POST | `/medications` | 201. `schedule_times` are local `HH:MM` in the regimen `timezone` (defaults to the account's). | 422 | `medication_created` |
| PATCH | `/medications/{regimen_id}` | Edit, or `status: "discontinued"`. | 404, 422 | — |
| POST | `/medication-doses` | 201 new / 200 when it resolves a reminder slot or is a replay. **Idempotent on `client_event_id`** (required, 8–80 chars). | 404, 422 | `medication_dose_taken` |
| GET | `/medication-doses` | `regimen_id`, `start`, `end`. Newest first. | — | — |
| GET | `/medication-reminders/due` | Slots awaiting a decision. Server-side schedule; poll it. | — | `medication_reminders_due` |
| GET | `/medication-education` | Versioned, source-cited, class-level text. `review_status: "not_clinically_reviewed"`. | — | `medication_education` |

**A regimen is not a dose and a reminder is not a dose.** Dose `action`:

| action | set by | `actual_at` |
|---|---|---|
| `scheduled` | the system, when a reminder time passes | always `null` |
| `taken` | the user only | the confirmed time (defaults to now) |
| `skipped`, `unknown` | the user | `null` |
| `snoozed` | the user, with a future `snoozed_until` | `null` |

Nothing turns `scheduled` into `taken` except an explicit `POST /medication-doses`,
and the database refuses an `actual_at` on any other action. To answer a reminder,
send its `scheduled_at` back. There is deliberately **no `missed` state** — the grace
rule has not been decided (DECISIONS O-8); the UI should not label one.

Reminders are not suppressed by alert quiet hours (DECISIONS D-011). Medication
records never affect a forecast or an alert. Tested.

## 12 · Alerts and notifications

Owner: Ruhaan (evaluation) / Neil (UX) · Consumer: Neil · Auth: bearer + **C**

| method | route | notes | failures | example |
|---|---|---|---|---|
| GET | `/alert-settings` | Off by default. | — | — |
| PUT | `/alert-settings` | Full replace. `threshold: {value, unit}` in the user's unit, 100–400 mg/dL equivalent. `channels`, `quiet_hours_start/end` (`HH:MM`, both or neither), `cooldown_minutes` 0–1440. | 422 | `alert_settings_saved` |
| GET | `/alerts` | `?status=`. Newest first. | — | `alerts_list` |
| POST | `/alerts/{alert_id}/acknowledge` | Idempotent. | 404 | — |
| GET | `/notifications` | What was delivered on which channel. | — | `notifications_list` |

**The server evaluates the rule**, on the stored forecast, when the forecast is
created. One alert per forecast. Alert `status`: `active`, `suppressed_quiet_hours`,
`suppressed_cooldown`, `suppressed_retrospective`.

`payload`: `label`, `threshold {value, unit, mg_dl}`, `predicted_crossing_minute`,
`predicted_peak {mg_dl, minute, in_user_unit}`, `forecast_generated_at`,
`latest_cgm_age_minutes`, `uncertainty_status: "not_calibrated"`,
`informational_only: true`, `disclaimer`. **Always render the disclaimer.** These are
not CGM alarms and must not look like one.

**Channels.** Only `in_app` delivers. `push` and `email` are accepted as preferences
and returned with `available: false`; attempts are recorded as
`channel_unavailable`. Do not show them as working.

The GLIMPSE frontend's client-side `spikeAlert.ts` should become display-only once this is wired.

## 12a · Demonstration replay — exists only when `GLIMPSE_DEMO_MODE` is enabled

Owner: Ruhaan · Consumer: Neil · Auth: bearer, **demo account only** · Status: **PROPOSED**

When demo mode is off these routes are not registered: 404, and absent from the live
`/api/v2/openapi.json`. (The published `contracts/v2/openapi.json` is generated with
demo mode on so the schemas are available.) `GET /status` → `demo_mode` says which.

| method | route | notes | failures | example |
|---|---|---|---|---|
| GET | `/demo/replay` | Replay position, checkpoints, personalization progress, provenance labels. | 403 `not_demo_account`, 503 `demo_data_unavailable` | `demo_replay_state_initial` |
| POST | `/demo/replay/advance` | `{to}`, `{minutes}`, optional `expected_event_index`. Forward only; a passed checkpoint or stale index is a no-op with `advanced: false`. **Idempotent** with `expected_event_index`. | 403, 409 `demo_bundle_changed`, 422 `unknown_checkpoint`, 503 `demo_requires_frozen_model` | `demo_replay_advance_meal_forecast`, `demo_replay_advance_outcome_reveal`, `demo_replay_advance_minutes`, `demo_replay_state_retrieval_active`, `demo_replay_retry_is_no_op` |
| POST | `/demo/replay/reset` | Deletes and re-creates the demo account. The caller's token stops working. Touches no other account. | 403, 409 `demo_email_in_use` | `demo_replay_reset` |
| GET | `/demo/devices` | Four simulated wearable profiles (`inactive` / `demo_simulated`) with per-metric counts, plus the glucose source (a research replay, not simulated). | 403, 503 | `demo_devices_initial` |
| POST | `/demo/devices/{profile}/activate` | Switch on `apple_health`, `apple_watch`, `fitbit` or `whoop`. No body. **Idempotent.** No provider contacted, no token. | 403, 404 `unknown_demo_device` | `demo_device_activated`, `demo_device_activate_repeat_is_no_op` |
| GET | `/demo/health-timeline` | `hours` ≤ 240, `limit` ≤ 1000. Meals, alerts, doses and wearable summaries ordered up to the replay position, each tagged with `data_class` and `simulated`. | 403 | `demo_health_timeline` |

* Timestamps: `position` and every checkpoint `at` are instants on the **recording's
  historical timeline**; `wall_clock` is the real time. Never display `position` as
  "now".
* For the demo account, every other v2 endpoint treats `position` as the current
  time. Nothing later than `position` is in its store.
* These routes move time and trigger scripted actions. They return no forecast,
  outcome or reading themselves — read those through the ordinary endpoints.
* The `demo_replay_*` example files replay a generated test fixture
  (`data_class: demo`), not the research recording.

* **Two modes, two accounts.** `new_user` (default) and the optional
  `returning_user`. Which one a request acts on is decided solely by which demo
  account is signed in; there is no mode parameter. Reset affects the caller's mode
  only.
* **Added for simulated devices — FOR SHARED REVIEW:** the three `/demo/devices` and
  `/demo/health-timeline` routes above, the `provider` filter on
  `GET /wearables/observations`, and the connection status `demo_simulated`.
* **Added for the returning-user mode — FOR SHARED REVIEW:** state fields `mode`,
  `mode_label`, `display_from_meal`; checkpoint field `phase` (`backfill` /
  `display`); `provenance.backfilled_history`; aliases `backfill_complete`,
  `first_display_meal`. Additive only; examples
  `demo_replay_state_returning_user_initial`,
  `demo_replay_returning_user_first_presented_meal`. The OpenAPI document is
  unchanged (the state body is an open object).

Full consumer guide: `DEMO_HANDOFF_NEIL.md`. Scenario: `DEMO_SCENARIO_SPEC.md`.

---

## 13 · What is real — implementation status

| capability | status |
|---|---|
| Accounts, sessions, consent, isolation, account deletion | **IMPLEMENTED AND VERIFIED** (tests) |
| Onboarding storage | **IMPLEMENTED AND VERIFIED**; question ids await Neil |
| Photo upload, validation, sanitization, local storage | **IMPLEMENTED AND VERIFIED** |
| S3 photo storage | PRESENT, **NOT EXERCISED** (no bucket) |
| OpenAI meal analysis | IMPLEMENTED, verified against the installed SDK's interface and mocked responses. **Never called live** — no key (BLOCKERS B-002) |
| Meal confirmation, revisions | **IMPLEMENTED AND VERIFIED** |
| Glucose ingestion, units, dedupe, corrections, freshness | **IMPLEMENTED AND VERIFIED** |
| Dexcom Clarity / LibreView import | IMPLEMENTED, fixture-tested. **Not verified with a genuine export** (B-010) |
| Forecast orchestration, snapshot, reproduction | **IMPLEMENTED AND VERIFIED**, including with genuine CGMacros sensor data and the frozen artifact |
| Outcome scoring, eligibility, rescoring | **IMPLEMENTED AND VERIFIED** |
| Personalization Level A | **IMPLEMENTED AND VERIFIED** |
| Personalization Level B | PRESENT, **DISABLED — NOT APPROVED FOR ENABLEMENT** (D-007); needs a chronological replay evaluation first |
| Medications, doses, reminders | **IMPLEMENTED AND VERIFIED** (in-app) |
| Forecast alerts | **IMPLEMENTED AND VERIFIED** (in-app) |
| Push / email delivery | **NOT IMPLEMENTED** |
| Dexcom API | IMPLEMENTED, mock-tested. **BLOCKED BY EXTERNAL SERVICE** — never contacted |
| Fitbit | IMPLEMENTED, mock-tested. **BLOCKED BY EXTERNAL SERVICE** — never contacted (see B-006) |
| WHOOP | IMPLEMENTED, mock-tested. **BLOCKED BY EXTERNAL SERVICE** — never contacted |
| Apple Health / Apple Watch | Server ingest IMPLEMENTED AND VERIFIED. Native client **NOT IMPLEMENTED — REQUIRES TEAM DECISION** (B-003) |
| Real-time CGM for live forecasts | **BLOCKED** (B-001) |
| Simulated connected-health sources (demo only) | **IMPLEMENTED AND VERIFIED** locally. Generated data, labelled `demo` / `demo_simulated`. **Not** an Apple, Fitbit or WHOOP integration; none of those is verified |
| Demonstration replay (seed, advance, reset, verify) | **IMPLEMENTED AND VERIFIED** locally with genuine CGMacros data and the frozen artifact, in-process and over HTTP. Not run on any deployed server |
| Frontend consuming v2 | **NOT IMPLEMENTED** (B-009) |
| Local-clock policy across DST | **IMPLEMENTED AND VERIFIED** — IANA, per observation (D-006, conditionally approved) |
| Container image | Dockerfile PRESENT, **NEVER BUILT**; never trains, ships only the checksum-verified frozen artifact |
| PostgreSQL | **NOT VERIFIED** — DDL renders offline only (B-007) |
| Deployment | **NOT IMPLEMENTED** — Het |

"IMPLEMENTED AND VERIFIED" means automated tests exercise the behaviour, including its
failure paths and user isolation. It does not mean a real user has used it.
