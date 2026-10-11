# Ruhaan — technical implementation report (R0–R8)

**Date:** 2026-10-10 · **Branch:** `ruhaan` · **Base:** `e362e88` (fs-1.3 freeze)

| commit | contents |
|---|---|
| `df3129c` | the four canonical planning documents (cherry-pickable, docs only) |
| `63e7007` | the v2 application layer |
| `2a755d7` | its test suite |
| `c3512b8` | contract artifacts, registry, decisions, blockers, handoffs, this report |
| *(follow-up)* | review rulings, D-006 rework, Docker artifact gates — see §0 |

Pushed to `origin/ruhaan` after the follow-up; the final SHA is in the push record.

## 0 · Addendum — review rulings and follow-up (2026-10-10)

Ruhaan reviewed R0–R8 and accepted the implementation as the **integration
baseline**, subject to the external and deployment blockers. It was explicitly not a
blanket approval: every v2 endpoint remains PROPOSED until the shared interface
review. The rulings are quoted verbatim in `DECISIONS.md` (R-001, D-006, D-007).
Sections 1–8 below are the original report; where this addendum differs, it wins.

| ruling | action taken |
|---|---|
| D-006 conditionally approved; no account-level fixed offset | Reworked to IANA, per-observation conversion. The engine's history never spans a UTC-offset change. True UTC stays in storage. 22 DST tests. No frozen file touched. |
| D-007 not approved for enablement | Level A retained. Level B stays off; documented as not to be enabled; evaluation by chronological replay recorded as the precondition (not started — out of scope for integration). |
| Keep v2 endpoints PROPOSED | Registry approval table rewritten to say so. |
| Docker path must never train or substitute a synthetic model | Training removed from the Dockerfile. Build-time and start-time checksum gates. `.dockerignore` excludes every other model directory. |

**A defect the D-006 rework found in the first version.** The UTC offset was computed
with `ZoneInfo.utcoffset()` on a UTC datetime, which reads its argument as local wall
time. Correct away from a transition; wrong within hours of one. Fixed, and covered
by the new tests. Section 4 and the "Known limitations" list below describe the
fixed-offset design, which no longer exists.

**Verification after these changes**

| check | result |
|---|---|
| `python -m pytest -q -rsxX` | **965 passed, 1 skipped, 0 failed** (703 pre-existing + 262 v2; the skip is the opt-in real-OpenAI call) |
| `python -m research.freeze.verify_artifacts` | **PASS** |
| `model.joblib` SHA-256 | `12ed0602…7415fe` — unchanged |
| `contracts/openapi.yaml` SHA-256 | `be836ddd…200f4` — unchanged |
| 23 frozen source files vs the manifest | all match |
| `scripts/smoke_v2.py` against a live server, frozen artifact, checksum enforced | **21 of 21 steps passed**, started with the container's own command (gate, then uvicorn) |
| `alembic upgrade head` / `check` / `downgrade base` (SQLite) | pass |
| `python -m contracts.v2.build_contract --check` | pass |

**Still not verified against anything real — unchanged by this follow-up:**

| | state |
|---|---|
| OpenAI | never called; mocked |
| Dexcom API, Fitbit, WHOOP | never contacted; mocked |
| Dexcom Clarity / LibreView file imports | fixtures only; no genuine export seen |
| Apple Health | no native app exists |
| PostgreSQL | DDL renders offline; never run on a server |
| S3 | never exercised |
| Docker image | never built (no daemon here) |
| Glimpse frontend | does not call v2 |
| A real user's device data | none has ever been received |

---

## 1 · Bottom line

The backend around the frozen model is built and tested end to end: account →
consent → glucose import → meal photo → AI estimate → user confirmation → frozen
forecast → stored snapshot → outcome scoring → personal history feeding later
forecasts, plus medications, reminders, alerts and device connections.

**What is not true yet, stated up front:**

1. **No external service has been contacted.** OpenAI, Dexcom, Fitbit and WHOOP are
   implemented against their published interfaces and tested with mocks. There are no
   credentials. None of them is "connected", and nothing in the product may say so.
2. **No real user's device has delivered a reading.** The genuine sensor data that
   went through the pipeline is CGMacros research data, labelled as such.
3. **There is no approved real-time CGM source** (BLOCKERS B-001). The frozen model
   needs a reading within 15 minutes before a meal; Dexcom's API is delayed, Libre has
   no API, and HealthKit needs an iOS app that does not exist. Retrospective use works
   today; live forecasting for real users needs a team decision.
4. **The frontend does not use v2.** Glimpse (on `origin/neil`) still calls v1 only.
   "The frontend can consume it" is shown by generated example payloads and
   HTTP-level tests, not by the real UI.
5. **Nothing is deployed**, and the schema has never run on PostgreSQL.
6. **Two decisions need your sign-off before production use** — see §4.

## 2 · Model integrity

| check | before | after |
|---|---|---|
| `model.joblib` SHA-256 | `12ed0602…7415fe` | `12ed0602…7415fe` — **unchanged** |
| `contracts/openapi.yaml` SHA-256 | `be836ddd…200f4` | `be836ddd…200f4` — **unchanged** |
| `python -m research.freeze.verify_artifacts` | PASS | **PASS** |
| Frozen source files vs the freeze manifest (23 files) | — | **all 23 match**, asserted by a test |
| v1 responses with v2 mounted | — | **byte-identical**, asserted for all seven endpoints |
| Pre-existing tests | 703 passed | **703 passed** |

No file under `ml/`, no frozen file under `backend/app/`, nothing under `contracts/`
(other than the new `contracts/v2/`), nothing under `data/`, and no existing test was
modified. Nothing was retrained. No artifact was written.

## 3 · Test results

```
python -m pytest -q -rsxX
937 passed, 1 skipped, 0 failed   (290 s)
```

| | count |
|---|---|
| Pre-existing tests | 703 |
| New v2 tests passing | 234 |
| Skipped | 1 — the real-OpenAI round trip, which is opt-in and needs an explicit key, model and call cap |

The 210 warnings are two third-party deprecation notices repeated once per test
client; none originates in project code.

Three safety guards were mutation-checked — each was deliberately broken and a test
failed each time, then the code was restored: the per-user ownership filter on meals,
the eligibility filter on personal history, and the strict `<` on the calibration
window boundary.

## 4 · Decisions that need you

Full text in `DECISIONS.md`. Everything is recorded as PROPOSED; I have not recorded
your approval of anything.

**D-006 — clock projection (the model-compatibility gate).** fs-1.3 was trained on
local wall-clock time; two frozen features read the stored clock directly. Given real
UTC they would read the wrong hour. The wrapper shifts the history by the account's
UTC offset before calling the engine, without touching frozen code. This is the one
place live data exposed a model incompatibility. It is implemented as the default
because the alternative is silently wrong features, but it changes what the model is
shown and it needs your explicit approval.

**D-007 — Level B calibration is built but off.** Level A (history-driven
personalization through the frozen `r_hist_*` / `q_retr_*` features) is on. Level B
(running the frozen shrinkage policy on a user's own residuals) is implemented and
tested, but it produces a calibration the artifact does not contain. One environment
variable enables it. I did not turn it on by default.

Smaller ones worth a look: D-004 (built-in identity as a stand-in for an IdP), D-009
(eligibility is stricter than training), D-013 (no default OpenAI model).

## 5 · Phase reports

### R0 — audit

```
PHASE:              R0 Repository audit
STATUS:             COMPLETE
FILES CHANGED:      docs/project/RUHAAN_PHASE0_AUDIT.md
FEATURES COMPLETED: Repository map, capability inventory, findings F1–F7, proposed API surface
TESTS PASSED:       Baseline 703 / 0 failed / 0 skipped
INTERFACES PUBLISHED: Proposed v2 route list
HANDOFFS TO HET:    F2 — the frozen artifact is not in git
HANDOFFS TO NEIL:   none
EXTERNAL BLOCKERS:  No credentials for any provider; no iOS app; no IdP chosen
MODEL INTEGRITY:    PASS — artifact and contract checksums match the freeze
NEXT PHASE:         R1
```

Material findings: v2 cannot be added to the frozen `main.py`; no auth or application
database existed; the frontend exists only on `origin/neil`; no cloud configuration
exists on any branch.

### R1 — database, identity, registry

```
PHASE:              R1 Shared database, identity, event schemas, API registry
STATUS:             COMPLETE
FILES CHANGED:      backend/app/v2/{app,asgi,core,deps,settings,schemas}.py,
                    backend/app/v2/db/{tables,engine}.py,
                    backend/app/v2/routers/{system,auth,onboarding}.py,
                    migrations/, alembic.ini, requirements.txt, .env.example
FEATURES COMPLETED: 24-table schema with FKs and constraints; Alembic up/down;
                    accounts, sessions, participant bindings; append-only consent;
                    onboarding storage with unit rules; account deletion; audit log;
                    readiness and capability endpoints
TESTS PASSED:       test_platform_integrity (18), test_auth_and_isolation (15),
                    test_migrations (3)
INTERFACES PUBLISHED: /auth/*, /me, /account, /onboarding, /consents, /ready, /status
HANDOFFS TO HET:    New start command; migration commands; env var names
HANDOFFS TO NEIL:   Auth, consent and onboarding contracts
EXTERNAL BLOCKERS:  PostgreSQL never exercised (B-007); IdP undecided (O-3)
MODEL INTEGRITY:    PASS
NEXT PHASE:         R2
```

### R2 — AI meal analysis

```
PHASE:              R2 AI meal analysis and confirmed-meal ingestion
STATUS:             COMPLETE in code; live OpenAI call BLOCKED (no key)
FILES CHANGED:      backend/app/v2/services/{images,vision,meals}.py,
                    backend/app/v2/routers/meals.py
FEATURES COMPLETED: Upload validation by content, full decode, EXIF-free re-encode;
                    structured vision estimate with ranges, assumptions,
                    clarification questions and provenance; server-side validation
                    of model output; immutable estimates and revisioned
                    confirmations; opt-in unreviewed path that is labelled;
                    per-user daily cap; idempotent analysis
TESTS PASSED:       test_meals_and_vision — 44 passed, 1 skipped (opt-in live call)
INTERFACES PUBLISHED: /meals, /meals/images, /meals/{id}, …/image, …/analyze,
                    …/confirm, …/consumption
HANDOFFS TO HET:    OPENAI_API_KEY and model variable; 16 MB body, 60 s timeout
HANDOFFS TO NEIL:   Estimate payload; raw-body upload; HEIC is rejected
EXTERNAL BLOCKERS:  No OpenAI key; approved model undecided (O-4)
MODEL INTEGRITY:    PASS
NEXT PHASE:         R3
```

The SDK interface was verified in the installed package (`openai==3.28.0`:
`client.responses.parse(model, instructions, input, text_format)` → `output_parsed`)
and the strict JSON schema compiles. It has never been called against the API.

### R3 — CGM

```
PHASE:              R3 CGM normalization and first genuine data import
STATUS:             COMPLETE for ingestion; live provider connection BLOCKED
FILES CHANGED:      backend/app/v2/services/glucose.py,
                    backend/app/v2/providers/cgm_files.py,
                    backend/app/v2/routers/cgm.py
FEATURES COMPLETED: Unit conversion with provenance; validation and rejection
                    reasons; dedupe; corrections with history; observed_at vs
                    received_at; freshness; gap reporting; single-sensor selection;
                    Dexcom Clarity and LibreView parsers
TESTS PASSED:       test_cgm (23), test_real_cgm_integration (5)
INTERFACES PUBLISHED: /cgm/readings, /cgm/import, /cgm/status, /cgm/imports
HANDOFFS TO HET:    none beyond R1
HANDOFFS TO NEIL:   Freshness states; ingestion summary; demo labelling
EXTERNAL BLOCKERS:  B-001 no real-time source; B-010 parsers unverified on real exports
MODEL INTEGRITY:    PASS
NEXT PHASE:         R4
```

**"First genuine data import", precisely:** 2,855 real Dexcom G6 Pro readings and 62
real meals for CGMacros participant 004 were ingested through the generic pipeline as
`research_dataset` and run through the whole loop with the frozen artifact, checksum
enforced: 61 episodes scored, every sampled forecast reproduced exactly. That is
genuine sensor data and the real model. It is not a user's device, and the 13.8 mg/dL
per-episode MAE it printed is **not a validation result** — the artifact was fitted on
that participant's earlier meals.

### R4 — orchestration

```
PHASE:              R4 Prediction orchestration and outcome persistence
STATUS:             COMPLETE
FILES CHANGED:      backend/app/v2/services/{history,orchestrator}.py
FEATURES COMPLETED: All twelve orchestration steps; SQL + frozen double filter
                    against future data; local-clock projection; snapshot with all
                    39 values and the full input history; artifact checksum on
                    every prediction; idempotent replay; exact reproduction;
                    prospective/retrospective labelling; explicit refusals for
                    zero, stale or insufficient history
TESTS PASSED:       test_prediction (29)
INTERFACES PUBLISHED: /meals/{id}/predict, /predictions, /predictions/{id},
                    /predictions/{id}/reproduce
HANDOFFS TO HET:    GLUCOTWIN_EXPECTED_MODEL_SHA256; artifact delivery (B-004)
HANDOFFS TO NEIL:   forecast is exactly the v1 ForecastResponse; context_quality
EXTERNAL BLOCKERS:  none
MODEL INTEGRITY:    PASS — orchestrator output equals a direct frozen-engine call
NEXT PHASE:         R5
```

### R5 — personalization cycle

```
PHASE:              R5 Future-only personalization update cycle
STATUS:             COMPLETE (Level A on; Level B gated)
FILES CHANGED:      backend/app/v2/services/{outcomes,personalization}.py,
                    workers/outcome_scoring.py
FEATURES COMPLETED: Scoring after window + grace; coverage and confounder
                    detection; forecast-vs-observed errors; eligibility marking;
                    rescoring on late or corrected data; history exposed to the
                    frozen retrieval logic; policy recorded per prediction
TESTS PASSED:       test_outcomes_and_personalization (20), plus history-count and
                    boundary cases in test_prediction
INTERFACES PUBLISHED: /meals/{id}/outcome
HANDOFFS TO HET:    outcome_scoring worker needs a scheduler (B-012)
HANDOFFS TO NEIL:   Outcome states and ineligibility reasons
EXTERNAL BLOCKERS:  B-011 — Libre's 15-minute data cannot activate retrieval
MODEL INTEGRITY:    PASS
NEXT PHASE:         R6
```

Verified: history counts 0, 1, 2, 3, 4, 5, 6 and 8; retrieval switches on at exactly
5; a prior meal at 119 and 120 minutes is excluded and at 121 minutes is admitted;
another user's history never appears; a stored prediction is never altered by its
outcome.

### R6 — Neil's backend services

```
PHASE:              R6 Supporting backend APIs
STATUS:             COMPLETE (in-app); push and email NOT IMPLEMENTED
FILES CHANGED:      backend/app/v2/services/{medications,alerts}.py,
                    backend/app/v2/routers/{medications,alerts}.py,
                    workers/reminders.py
FEATURES COMPLETED: Regimens; dose events idempotent on client id; server-side
                    reminder slots that never become "taken" unprompted, enforced
                    by a database constraint; DST-correct schedules; server-side
                    alert evaluation with threshold units, quiet hours, cooldown
                    and dedupe; honest per-channel delivery records;
                    source-cited education marked not clinically reviewed
TESTS PASSED:       test_medications_and_alerts (50)
INTERFACES PUBLISHED: /medications, /medication-doses, /medication-reminders/due,
                    /medication-education, /alert-settings, /alerts, /notifications
HANDOFFS TO HET:    reminders worker (B-012)
HANDOFFS TO NEIL:   HANDOFF_NEIL.md; 38 example payloads
EXTERNAL BLOCKERS:  No push/email service; no clinical reviewer (B-013)
MODEL INTEGRITY:    PASS — medication records proven not to change a forecast
NEXT PHASE:         R7
```

### R7 — wearables

```
PHASE:              R7 Wearable provider adapters and permissions
STATUS:             ADAPTERS COMPLETE AND MOCK-TESTED; every live connection BLOCKED
FILES CHANGED:      backend/app/v2/providers/{base,dexcom,fitbit,whoop,registry}.py,
                    backend/app/v2/services/{devices,wearables}.py,
                    backend/app/v2/routers/{devices,wearables}.py,
                    workers/device_sync.py
FEATURES COMPLETED: OAuth with PKCE where required, single-use hashed state,
                    Fernet-encrypted tokens, refresh, rotation, revocation,
                    scope-denial and rate-limit handling, sync dedupe, last-success
                    tracking; normalized wearable store; native-bridge upload;
                    capability matrix stating what each provider cannot do
TESTS PASSED:       test_devices_and_wearables (21)
INTERFACES PUBLISHED: /devices, /devices/{provider}/{connect,callback,sync},
                    /wearables/observations
HANDOFFS TO HET:    Redirect URIs; token-encryption key; provider secrets
HANDOFFS TO NEIL:   Connection status meanings; capability matrix
EXTERNAL BLOCKERS:  B-002 no credentials; B-003 no iOS app; B-006 Fitbit API status
MODEL INTEGRITY:    PASS — no wearable value reaches the model
NEXT PHASE:         R8
```

Glimpse has no native wrapper, so HealthKit is isolated behind a specified bridge
(`HEALTHKIT_BRIDGE_SPEC.md`) rather than claimed.

### R8 — integration

```
PHASE:              R8 Frontend integration and end-to-end verification
STATUS:             BACKEND VERIFIED OVER REAL HTTP; frontend wiring NOT DONE
FILES CHANGED:      contracts/v2/{build_contract.py,openapi.json,examples/},
                    scripts/smoke_v2.py, docs/project/*, README.md
FEATURES COMPLETED: OpenAPI generated from code (41 paths, 51 operations);
                    38 example payloads captured from the running application;
                    live-server smoke test; registry; decisions; blockers; handoffs
TESTS PASSED:       test_contract_artifacts (6); smoke_v2.py 21/21 against a live server
INTERFACES PUBLISHED: API_INTERFACE_REGISTRY.md, contracts/v2/openapi.json
HANDOFFS TO HET:    HANDOFF_HET.md
HANDOFFS TO NEIL:   HANDOFF_NEIL.md
EXTERNAL BLOCKERS:  B-009 — the frontend is on another branch and is not mine to edit
MODEL INTEGRITY:    PASS
NEXT PHASE:         your sign-off on D-006 and D-007; then Neil's wiring and Het's staging
```

## 6 · Exact commands

```bash
# Environment
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Model integrity (before and after any work)
python -m research.freeze.verify_artifacts
shasum -a 256 ml/artifacts_cgmacros_fs13/model.joblib   # 12ed0602…7415fe

# Full test suite
python -m pytest -q -rsxX                               # 937 passed, 1 skipped

# v2 only
python -m pytest tests/v2 -q

# Database
alembic upgrade head
alembic check
alembic downgrade base

# Run with the frozen artifact, checksum enforced
export GLUCOTWIN_MODEL_ARTIFACT=ml/artifacts_cgmacros_fs13/model.joblib
export GLUCOTWIN_METRICS_PATH=ml/artifacts_cgmacros_fs13/metrics.json
export GLUCOTWIN_EXPECTED_MODEL_SHA256=12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe
uvicorn backend.app.v2.asgi:app --host 127.0.0.1 --port 8000

# End-to-end over real HTTP (creates and deletes a throwaway account)
python scripts/smoke_v2.py http://127.0.0.1:8000        # 21 steps, ALL STEPS PASSED

# Workers
python -m workers.outcome_scoring
python -m workers.reminders
python -m workers.device_sync

# Contract artifacts
python -m contracts.v2.build_contract                   # regenerate
python -m contracts.v2.build_contract --check           # CI drift guard

# Opt-in real OpenAI test (spends money; one call)
GLUCOTWIN_OPENAI_TEST_KEY=… GLUCOTWIN_OPENAI_TEST_MODEL=… GLUCOTWIN_OPENAI_TEST_MAX_CALLS=1 \
  python -m pytest tests/v2/test_meals_and_vision.py -k real_openai
```

## 7 · Remaining issues

Tracked in `BLOCKERS.md`; in the order I would address them.

| # | issue | on |
|---|---|---|
| 1 | Sign off or reject D-006 and D-007 | you |
| 2 | Choose a real-time CGM source (B-001) | team |
| 3 | Frozen artifact delivery to deployments (B-004) | Het |
| 4 | Wire Glimpse to v2 (B-009) | Neil |
| 5 | Run the schema on PostgreSQL (B-007) | Het, then me |
| 6 | Register provider apps, obtain an OpenAI key, run the gated live tests (B-002) | you / team |
| 7 | Verify the two file parsers on genuine exports (B-010) | me, given one real file each |
| 8 | Rate limiting, password reset, email verification (B-005) | me / Het |
| 9 | Decide whether an iOS app will exist (B-003) | team |
| 10 | Verify the S3 image store (B-008) | Het |

Known limitations in what is built:

* Only meals that were forecast become personal history (D-009).
* When the most recent prior meal is ineligible, `prior_meal_carbs_g` comes from an
  older eligible meal (D-009).
* Across a DST change, clock-dependent features are one hour off for the part of the
  lookback on the far side (D-006).
* Withdrawing necessary consent also blocks reading one's own stored data; only
  deletion remains available.
* The checksum guard protects v2 forecasts. The frozen v1 `/api/predict` cannot be
  given one.
* Failed analysis attempts do not count toward the daily cap, only stored estimates.

## 8 · Document index

| file | purpose |
|---|---|
| `RUHAAN_PHASE0_AUDIT.md` | what existed before this work |
| `API_INTERFACE_REGISTRY.md` | every endpoint, its guarantees and its real status |
| `DECISIONS.md` | D-001…D-013 and open decisions |
| `BLOCKERS.md` | B-001…B-013 |
| `FS13_INPUT_MAPPING.md` | all 39 model inputs for live accounts |
| `HANDOFF_HET.md` | deployment requirements |
| `HANDOFF_NEIL.md` | building the UI against v2 |
| `HEALTHKIT_BRIDGE_SPEC.md` | the unbuilt native half, specified |
