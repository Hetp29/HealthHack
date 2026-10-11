# Ruhaan — Phase Zero repository audit

**Audited commit:** `e362e8838e31fa7aab4b85ca6cbd9f950e96c54f` (branch `ruhaan`, identical to `origin/ruhaan`)
**Audit date:** 2026-10-10
**Method:** every classification below comes from reading the code or running it, not
from an endpoint name, a README or a planning document.

## 1 · Baseline verification (before any change)

| check | result |
|---|---|
| Branch | `ruhaan`, clean apart from the untracked `docs/project/` planning docs |
| `python -m research.freeze.verify_artifacts` | **PASS** |
| `ml/artifacts_cgmacros_fs13/model.joblib` SHA-256 | `12ed0602…7415fe` — matches the freeze |
| `contracts/openapi.yaml` SHA-256 | `be836ddd…200f4` — matches the freeze |
| `python -m pytest -q -rsxX` | **703 passed, 0 failed, 0 skipped** (201.6 s) |

## 2 · Controlling documents

All four project documents exist at the stated paths under `docs/project/`, as do
`docs/governance/FS13_MODEL_FREEZE_V1.md` and `fs13_integrity_manifest.json`. Nothing
was substituted.

Discrepancies found and **not** silently resolved:

1. **Role reassignment between document generations.** The older specs in `docs/`
   (`GlucoTwin_Het_Database_Data_Engineering.docx`, `GlucoTwin_Neil_Frontend_Experience.docx`)
   and the code comments in `data/**` name **Het as data-layer owner**. The new
   `docs/project/` set moves storage and API design to Ruhaan and gives Het AWS. The
   new set is treated as controlling; `data/**` is left untouched (see D-002).
2. **`docs/project/` is untracked.** Master §5 says all three branches add the same
   four docs from one documentation-only commit. Neither `origin/het` nor `origin/neil`
   contains them yet.
3. **`DECISIONS.md`, `API_INTERFACE_REGISTRY.md`, `BLOCKERS.md` did not exist.** Created
   on this branch as the single shared copies.

## 3 · Repository map

| path | what it is | owner | frozen? |
|---|---|---|---|
| `ml/` | Feature builder, training, inference, personalization, metrics | Ruhaan | **Yes** — every file on the inference path is checksummed in the freeze manifest |
| `ml/artifacts_cgmacros_fs13/` | The frozen artifact directory (gitignored) | Ruhaan | **Yes** |
| `backend/app/{main,config,schemas,errors,dependencies}.py`, `routes/`, `services/` | Contract v1.0 FastAPI service, 7 endpoints | Ruhaan | **Yes** — all checksummed in the manifest |
| `contracts/` | `openapi.yaml` v1.0, JSON Schemas, examples | Ruhaan | **Yes** |
| `data/` | Research warehouse: CGMacros/MetaboNet adapters, SQLite storage, `SqliteDataRepository` | Het (per file headers) | Not model-frozen, but not mine to edit |
| `research/` | Negative-result research tracks + freeze verification scripts | Ruhaan | Verification scripts are reference |
| `tests/` | 703 tests | shared | — |
| `Dockerfile`, `docker-compose.yml` | Written, **never built** (no Docker daemon on the dev machine) | → Het | — |
| *(absent)* `frontend/`, `.github/`, IaC | — | — | — |

Remote branches:

* `origin/neil` — the Glimpse frontend lives **only here**, at the repository root
  (`src/`, `package.json`): TanStack Start + React 19 + zod, Lovable-connected. It
  consumes the seven v1 endpoints only. No native wrapper (no Capacitor, no `ios/`).
* `origin/het` — one commit ahead of an old base: a self-contained `experiments/het/`
  LightGBM/CLIP ablation. **No AWS, IaC, CI or deployment configuration exists on any
  branch.**
* `origin/main` — the initial commit only.

## 4 · Capability inventory

Legend: **VERIFIED** = implemented and verified · **INCOMPLETE** · **PLACEHOLDER** ·
**NONE** = not implemented · **EXT** = blocked by external service · **TEAM** =
requires team decision.

| capability | status at audit | evidence |
|---|---|---|
| FastAPI app, 7 v1 endpoints | VERIFIED | `tests/test_api.py`, `test_contracts.py`, freeze phase 6 |
| Frozen model load + invocation | VERIFIED | `PredictionService`, `ml.inference.forecast`, freeze phases 1–7 |
| 39-feature builder, leakage chokepoint | VERIFIED | `ml/features.py`, `tests/test_leakage.py`, lineage audit |
| Retrieval/fingerprint personalization (history-driven) | VERIFIED for CGMacros | `_prior_observed_responses`; works for any participant id |
| Per-participant calibration offsets | VERIFIED **for the 45 training ids only** | `artifact.personalization.calibrations`; an unknown id gets `generalized_fallback` |
| Research data warehouse | VERIFIED | `data/storage`, SQLite, 45 CGMacros participants |
| **Application database** (users, meals, …) | **NONE** | no table for any of it |
| **Authentication / user identity** | **NONE** | v1 has no auth; CORS middleware sets `allow_credentials=False` and only allows `Content-Type` |
| Per-user authorization | NONE | `participant_id` is a caller-supplied string |
| Schema migrations | NONE | `create_all` + a storage-version string that demands a rebuild |
| Meal photo upload / storage | NONE | — |
| AI meal analysis | NONE | `research/vision` is a CLIP-embedding study (negative result), unrelated to nutrition estimation |
| Meal confirmation records | NONE | — |
| Live/imported CGM for app users | NONE | `data/adapters` are one-shot research-dataset batch loaders |
| mmol/L handling | NONE | canonical layer is mg/dL-only |
| Wearable ingestion (Fitbit/WHOOP/HealthKit) | NONE | — |
| Prediction persistence / snapshots | NONE | `/api/predict` is stateless |
| Outcome scoring of app predictions | NONE | `ml/targets.py` builds training targets; nothing scores a live forecast |
| Medication / alert / onboarding APIs | NONE | Neil's `profile.ts` keeps answers in `localStorage`; `spikeAlert.ts` computes alerts client-side |
| Background jobs | NONE | — |
| Cloud configuration | PLACEHOLDER | Dockerfile unbuilt; it trains a **synthetic** model inside the image rather than shipping fs-1.3 |
| CI | NONE | no `.github/` |
| Frontend ↔ v2 integration | NONE | frontend has no auth, no upload, GET/POST only |
| Dexcom / Fitbit / WHOOP credentials | EXT | no developer app registered; no secrets present |
| HealthKit | EXT + TEAM | no native iOS app exists |
| Identity provider choice | TEAM | master §17 |
| OpenAI model, retention, cost ceiling | TEAM | master §17; no key present in the environment |

## 5 · Existing data flow (v1, unchanged)

```
client ──POST /api/predict {participant_id, meal_timestamp, meal}──▶ routes/predict.py
   └▶ PredictionService.predict
        ├─ repository.get_participant / get_history_as_of   (research warehouse)
        └─ ml.inference.forecast(artifact, history, t, composition)
             └─ build_features → history.truncated_at(t)    ← leakage chokepoint
             └─ blend.predict_deltas → + calibration offset → 25-point curve
```

Stateless, unauthenticated, and bound to research participants. Nothing is persisted.

## 6 · Findings that shape the design

**F1 — The frozen files cannot be edited to add v2.** `backend/app/main.py`,
`config.py`, `errors.py`, `schemas.py`, `dependencies.py`, every route and both
services are checksummed in `fs13_integrity_manifest.json`. v2 is therefore built as a
**separate ASGI application** composed beside the frozen one, not by adding routers to
`main.py`. See D-003.

**F2 — The frozen artifact is not in git.** `ml/artifacts_*/` is gitignored and the
Dockerfile trains a synthetic model. A deployment built from a clean clone would serve
a *different model* under the same version string. Het needs an artifact delivery path
with checksum verification at startup. See B-004.

**F3 — Live UTC timestamps are not what the model was trained on.** *(model
compatibility issue.)* CGMacros stores participant-local wall-clock time under a
`+00:00` label. Two frozen features read the stored clock directly:
`g_dev_from_night` (`point.timestamp.hour` between 2 and 6) and the time-of-day
components of every retrieval vector (`_minutes_of_day_or_nan`). Fed genuine UTC
instants, those would read the UTC hour, so a user in New York would have their
"overnight" window taken from 21:00–01:00 local, and the retrieval query (local time)
would be compared against prior meals (UTC clock). The frozen code is not changed;
instead the orchestrator presents history in the representation the model was trained
on. See D-006. **This needs owner sign-off before production use.**

**F4 — Calibration offsets exist only for training participants.** A new user always
receives `generalized_fallback` from the artifact. History-driven personalization
(`r_hist_*`, `q_retr_*`) does work for a new user with no code change — that is Level A.
Applying the frozen shrinkage policy to a new user's own completed episodes (Level B)
is implementable without touching the artifact, but it produces calibration parameters
the artifact does not contain. Implemented behind an explicit policy switch, default
off. See D-007.

**F5 — `hr_mean_30min_bpm` and `medication_exposure_recent` are live model inputs.**
Feeding wearable heart rate or logged medications would change forecasts. The briefs
forbid both without verification. App users therefore get an empty heart-rate series
(feature missing → fitted-median imputation, disclosed in `limitations`) and an empty
medication series (feature = 0.0). See D-008.

**F6 — The v1 error envelope differs from the master's proposed v2 envelope.** v1 is
`{code, message, detail}`; master §12 proposes `{code, message, request_id, details?}`.
Running v2 as its own application lets each keep its own handlers.

**F7 — Photo uploads from iPhones may be HEIC.** Pillow cannot decode HEIC without an
extra native dependency. v2 rejects it with a specific error; the frontend should
request JPEG capture. See the registry.

## 7 · Risks

| risk | mitigation |
|---|---|
| A v2 change alters a v1 response | v1 app object is constructed by the unmodified `create_app`; a test asserts v1 responses are byte-identical with and without v2 mounted |
| Cross-user data leak | every query is filtered by `user_id` taken from the session, never from the request; isolation tests on every resource |
| Future data entering a forecast | SQL filter `observed_at <= meal time`, then the frozen `truncated_at`; snapshot stores the exact inputs; an injection test asserts invariance |
| AI estimate treated as truth | prediction requires a confirmation record; the unconfirmed path needs an explicit flag and is labelled in the stored prediction |
| Provider marked "connected" without data | `connected` is only set after authentic observations are stored; OAuth completion alone yields `authorized` |
| PHI in logs | v2 logs ids and counts only; error payloads never echo submitted values |
| Fitbit Web API is being superseded by Google's Health API | adapter isolated behind the provider interface; flagged in BLOCKERS for verification before registering an app |

## 8 · Dependencies added

`openai`, `cryptography`, `alembic`, `Pillow`, and `httpx` promoted from dev to
runtime. Verified in this environment: `openai==3.28.0` exposes
`client.responses.parse(model=, input=, text_format=)` returning `output_parsed`.

## 9 · Proposed v2 surface

All under `/api/v2`, all bearer-authenticated except register/login. The authoritative
list with schemas is `API_INTERFACE_REGISTRY.md`; this is the summary agreed before
implementation.

| area | routes |
|---|---|
| auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /me`, `PATCH /me`, `DELETE /account` |
| onboarding / consent | `GET/PATCH /onboarding`, `GET /consents`, `PUT /consents/{type}` |
| meals | `POST /meals`, `POST /meals/images`, `GET /meals`, `GET /meals/{id}`, `POST /meals/{id}/analyze`, `PATCH /meals/{id}/confirm`, `POST /meals/{id}/predict`, `GET /meals/{id}/outcome`, `DELETE /meals/{id}` |
| predictions | `GET /predictions`, `GET /predictions/{id}` |
| CGM | `POST /cgm/readings`, `POST /cgm/import`, `GET /cgm/readings`, `GET /cgm/status` |
| devices | `GET /devices`, `POST /devices/{provider}/connect`, `GET /devices/{provider}/callback`, `POST /devices/{provider}/sync`, `DELETE /devices/{provider}` |
| wearables | `POST /wearables/observations`, `GET /wearables/observations` |
| medications | `GET/POST /medications`, `PATCH /medications/{id}`, `GET/POST /medication-doses`, `GET /medication-education` |
| alerts | `GET/PUT /alert-settings`, `GET /alerts`, `POST /alerts/{id}/acknowledge`, `GET /notifications` |

## 10 · Implementation sequence

R1 schema, identity, registry → R2 meal photo + AI + confirmation → R3 CGM
normalization and import → R4 orchestration and persistence → R5 outcome scoring and
future-only personalization → R6 Neil's backend services → R7 wearable adapters →
R8 integration evidence and handoffs.
