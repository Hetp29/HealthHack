# Handoff to Neil — building the GLIMPSE frontend against the v2 API

From Ruhaan. What you can build against today, where the payloads are, and the rules
the UI has to respect. The backend is accepted as the integration baseline, and every
v2 endpoint is still **PROPOSED** until the shared interface review — yours included —
is done. Field names are meant to be stable; tell me what looks wrong during the
review, and I will announce any change in `DECISIONS.md` before making it.

## 0 · How to get this work onto your branch

Everythig is on the remote branch **`origin/ruhaan`**. Nothing is on `main`.

```bash
git fetch origin

# (a) The four canonical planning documents -- one documentation-only commit, made to
#     be cherry-picked so all three branches hold identical copies (master plan §5).
git cherry-pick df3129c

# (b) Read anything without changing your branch:
git show origin/ruhaan:docs/project/API_INTERFACE_REGISTRY.md
git show origin/ruhaan:contracts/v2/openapi.json > /tmp/glucotwin-v2-openapi.json

# (c) Copy the shared docs, v2 contract and examples into your working tree:
git checkout origin/ruhaan -- docs/project contracts/v2
```

| you want | path on `origin/ruhaan` |
|---|---|
| Canonical plan and role briefs | `docs/project/00_MASTER_…md`, `01_RUHAAN_…md`, `02_HET_…md`, `03_NEIL_…md` |
| Shared decision log, blocker register, interface registry | `docs/project/DECISIONS.md`, `BLOCKERS.md`, `API_INTERFACE_REGISTRY.md` |
| v2 contract (machine-readable) | `contracts/v2/openapi.json` |
| Integration examples — real captured responses | `contracts/v2/examples/*.json` (`_meta.json` explains what they are) |
| Frozen v1 contract | `contracts/openapi.yaml`, `contracts/schemas/`, `contracts/examples/` |

`DECISIONS.md`, `BLOCKERS.md` and `API_INTERFACE_REGISTRY.md` are the **single shared
copies**. Do not create your own: propose changes by pull request against `ruhaan`
(or send them to Ruhaan), so there is one log.

To run the backend itself, merge or check out `origin/ruhaan`; step (c) alone gives
you documents and contracts, not code.

**Status of what you are integrating against:** accepted as the integration baseline,
with every v2 endpoint still PROPOSED pending the shared interface review. Nothing
external is verified — see `BLOCKERS.md`.

## 1 · Where everything is

| what | where |
|---|---|
| Every endpoint, its guarantees and status | `docs/project/API_INTERFACE_REGISTRY.md` |
| Exact request/response schemas | `contracts/v2/openapi.json` (or live at `/api/v2/openapi.json`, browsable at `/api/v2/docs`) |
| **Real example payloads to mock against** | `contracts/v2/examples/*.json` — 38 files |
| Frozen v1 contract (unchanged) | `contracts/openapi.yaml` |

Generate TypeScript types straight from the schema:

```bash
npx openapi-typescript contracts/v2/openapi.json -o src/types/v2.ts
```

**The examples are real responses**, captured from the running backend, not written
by hand. Each file is `{request, status, body}`. Their glucose values are generated
arithmetic and the nutrition estimate is a fixed stub, so they are demo payloads —
keep them marked as such in the UI until the real path is wired
(`_meta.json` says exactly what they are).

Fixtures you asked for:

| case | file |
|---|---|
| happy-path forecast | `prediction_created.json` |
| 4 completed meals — below the retrieval gate | `prediction_history_4_below_retrieval_gate.json` |
| 5 completed meals — retrieval active | `prediction_history_5_retrieval_active.json` |
| stale sensor | `cgm_status_stale.json`, `error_insufficient_premeal_context.json` |
| no sensor data at all | `cgm_status_no_data.json` |
| no consent | `error_consent_required.json` |
| unconfirmed meal | `error_meal_not_confirmed.json` |
| outcome before / after scoring | `outcome_pending.json`, `outcome_scored.json` |
| validation error shape | `error_validation.json` |
| provider not set up | `error_provider_not_configured.json` |
| reminder awaiting a decision | `medication_reminders_due.json` |
| forecast alert | `alerts_list.json`, and `alert` inside `prediction_created.json` |

## 2 · Run the backend locally

```bash
pip install -r requirements-dev.txt
python -m ml.train --source synthetic_dev        # once; a labelled development model
alembic upgrade head
uvicorn backend.app.v2.asgi:app --reload --port 8000
```

Note the entry point: `backend.app.v2.asgi:app`. It serves the v1 API you already use
**and** `/api/v2`. Your existing v1 calls keep working unchanged.

Local CORS allows `http://localhost:5173` and `http://127.0.0.1:5173`. Meal analysis
answers `503 vision_not_configured` without an OpenAI key — build the manual-entry
path first; it needs nothing.

## 3 · What `apiClient.ts` needs

Your client currently does GET/POST with JSON only. v2 needs:

1. **A bearer header** on every call: `Authorization: Bearer <token>`. Store the token
   from `/auth/login` or `/auth/register`; keep it out of URLs.
2. **`PUT`, `PATCH`, `DELETE`.**
3. **Raw-body uploads.** Photos and CSVs are sent as the request body, not multipart:

   ```ts
   await fetch(`${API}/api/v2/meals/images?occurred_at=${encodeURIComponent(iso)}`, {
     method: "POST",
     headers: { Authorization: `Bearer ${token}`, "Content-Type": file.type },
     body: file,
   });
   ```

4. **The v2 error envelope** — different from v1's:

   ```json
   {"code": "…", "message": "…", "request_id": "req_…", "details": {}}
   ```

   Branch on `code`, never on `message`. Show `request_id` on error screens; it is how
   I find the request in the logs.
5. **Photos need the header too**, so `GET /meals/{id}/image` cannot be an
   `<img src>`. Fetch it and use `URL.createObjectURL(blob)`.
6. **A longer timeout for analysis.** `POST /meals/{id}/analyze` can take up to 45 s;
   your 15 s default will cut it off.

Status handling:

| status | do |
|---|---|
| 401 | session gone → login screen |
| 403 `consent_required` | consent screen, not an error |
| 404 | "not found" (also what you get for someone else's data) |
| 409 | a state conflict — the `code` says which; usually refresh and show current state |
| 413 / 415 | file too big / wrong type — tell the user what is accepted |
| 422 | validation; `details.errors[]` is `{field, message, type}` per field |
| 429 | analysis limit reached — offer manual entry |
| 502 / 503 / 504 `vision_*` | analysis failed — offer manual entry and a retry |
| 503 `model_unavailable`, `model_integrity_failure` | forecasting is down; do not retry in a loop |

## 4 · The journey, call by call

```
POST /auth/register                     → token
PUT  /consents/necessary_processing     {state: "granted"}      ← required, explicit
PUT  /consents/personalization_history  {state: "granted"}      ← optional, separate screen
PATCH /onboarding                       {answers: {…}}          ← save as they go

GET  /cgm/status                        → is a forecast possible right now?
POST /cgm/import?format=…               (CSV body)   or   POST /cgm/readings

POST /meals/images?occurred_at=…        (image body)  → meal_id
POST /meals/{id}/analyze                → estimate (editable)
PATCH /meals/{id}/confirm               {carbs_g, protein_g, fat_g, fiber_g, calories_kcal}
POST /meals/{id}/predict                → forecast (+ alert)
GET  /meals/{id}/outcome                → pending … scored
```

Manual entry skips the photo and analysis: `POST /meals` with `macros`.

## 5 · Rules the UI must follow

**Consent**
* The two consents are separate requests and must be separate, explicit choices.
  `necessary_processing` cannot be a pre-ticked box or buried in optional settings.
* Nothing health-related works until it is granted; `403 consent_required` is the
  cue to show the consent screen. Provide a withdrawal screen
  (`PUT …/{type} {state: "withdrawn"}`) and account deletion (`DELETE /account`).

**AI nutrition estimate**
* It is an estimate. Show `ranges`, the qualitative `overall_confidence`,
  `assumptions`, `clarification_questions` and `warnings`. Do not render a confidence
  percentage — there isn't one, on purpose.
* All five values must be editable before confirming. Mark `assumed_hidden` items as
  assumed.
* `status: "not_food"` → show `not_food_reason` and go to manual entry.
* Never call predict on an unconfirmed meal. The opt-in
  `{allow_unconfirmed_estimate: true}` exists; if you offer it, the result has
  `inputs_confirmed: false` and must be labelled as unreviewed, prominently.

**Forecast**
* `forecast` is exactly the v1 `ForecastResponse` — reuse `PredictionResults.tsx`.
* Always show `forecast.limitations`. Uncertainty is `not_calibrated`: no confidence
  bands, no probabilities.
* Label `prediction_kind: "retrospective"` — it is a look back, not a prediction the
  person could have acted on.
* `personalization.retrieval_active` turns true at 5 completed meals. A "N of 5 meals
  logged" progress indicator from `admissible_prior_meals` is accurate.
* `context_quality.latest_cgm_age_minutes` and `missing_features` are worth
  surfacing as "based on a reading N minutes old".
* `context_quality.history_truncated_at_offset_change: true` appears for up to 8 days
  after a daylight-saving change: the forecast used a shorter history. The matching
  sentence is already in `forecast.limitations`; no special handling is needed beyond
  showing limitations.
* `422 insufficient_premeal_context` → "your sensor data is too old to forecast"
  with `details.latest_cgm_age_minutes`. Not a generic error.
* **Call predict for every confirmed meal**, including ones logged after the fact.
  Only forecast meals get an outcome, and outcomes are what personalize later
  forecasts.

**Outcome**
* `pending` until `scoring_not_before`. Plot `observed.curve` against
  `forecast.points`; the observed series has real gaps — draw them as gaps.
* Show `ineligibility_reasons` in plain language when a meal will not count toward
  personalization.
* Offer "I didn't finish this" → `PATCH /meals/{id}/consumption`.

**Data provenance**
* `data_class: "demo"` must be visibly labelled DEMO wherever it appears.
* A device is "Connected" only when `status === "connected"`. `authorized` means
  linked with no data yet — say so. The `providers` list in `GET /devices` tells you
  what each provider can and cannot do; do not advertise more.
* Glucose arrives in mg/dL. Convert for display with the user's unit;
  `GET /cgm/readings` also accepts `?unit=mmol/L`.
* All timestamps are UTC. Render in the account `timezone` from `GET /me`.

**Medications**
* Regimens and dose events are different things on different endpoints.
* A reminder slot has `action: "scheduled"` and `actual_at: null`. It becomes `taken`
  only when the user taps Taken and you `POST /medication-doses`. Dismissing or
  ignoring the reminder must send nothing.
* Generate a fresh `client_event_id` (a UUID) per user action and reuse it on retry —
  that is what prevents double entries.
* To answer a reminder, send its `scheduled_at` back with the action.
* There is no `missed` state. Do not show one until the rule is agreed (O-8).
* Persist the action only after the API returns 2xx; on failure show retry.
* Education content has `review_status: "not_clinically_reviewed"` and a `limitation`
  string. Show both. Show `sources`.

**Alerts**
* The server decides. `spikeAlert.ts` should stop computing alerts and only render
  what `alert` / `GET /alerts` returns.
* Always render `payload.disclaimer`. Do not style these like a CGM alarm.
* Only `in_app` delivers. `push` and `email` come back `available: false` — show them
  as unavailable, not as options that silently do nothing.
* Poll `GET /alerts` and `GET /medication-reminders/due`; there is no push channel.

## 6 · What is not available — design for it

| | |
|---|---|
| Apple Health / Apple Watch | Not possible from a web app. Show as "requires the iOS app" (no such app exists yet). |
| Dexcom, Fitbit, WHOOP | Adapters exist; no credentials. `connect` returns `503 provider_not_configured`. Show "not available yet". |
| Libre | File import only. No API exists. |
| Real-time CGM | No approved source (BLOCKERS B-001). Live forecasts currently need manual or imported recent readings. |
| Push and email | Not built. |
| Password reset, email verification | Not built. |
| Photos from iPhone in HEIC | Rejected with 415. Request JPEG capture. |

## 7 · What I need from you

1. Review the question ids in `GET /onboarding` → `sections` against your 35
   questions; send me the ones to rename or add.
2. Tell me which payloads are missing something a screen needs.
3. The grace rule for a "missed" dose (O-8), so I can add the state.
4. Your quiet-hours exception policy for medication reminders (D-011).
