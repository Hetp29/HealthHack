# GLIMPSE | MASTER DELIVERY PLAN

**Status:** Planning specification | **Date:** 2026-10-10 | **Source of truth:** Master + this role brief

> **Governance:** Freeze fs-1.3 / v0.1.0 and Contract v1.0; all new routes are proposals pending registry approval.

## 1. Status and source of truth

Status: implementation planning baseline, 10 October 2026. This plan is a specification for NEW application features, not evidence they already exist. Frozen prediction model: glucotwin-fs13-blend-personalized v0.1.0, feature set fs-1.3, 39 ordered features; artifact SHA-256 12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe. Technical freeze commit e362e8838e31fa7aab4b85ca6cbd9f950e96c54f. Existing API Contract v1.0 is immutable. Existing health and prediction endpoints remain untouched. New endpoints in these documents are PROPOSALS until Ruhaan records an agreed contract in the repository. Do not claim future features are complete merely because they are described here.

## 2. Unbreakable model and clinical boundaries

No change to frozen weights, ensemble 0.5 Ridge + 0.5 HistGradientBoostingRegressor, its 39-feature order, retrieval_min_history=5, retrieval_k=5, retrieval_min_curve_points=20, personalization shrinkage k=3.0, or the existing output schema. Prior meal 120-minute window must end strictly before new query time; equality is excluded. Glucose forecasts, spike notifications, medication education and nutritional estimates are informational, not diagnosis, medication or insulin dose instructions. Prediction uncertainty is NOT calibrated. Retrospective metrics use consumed-meal macro quantities and do not prove those values can be known in advance. The 394-episode reference evaluation has been repeatedly inspected; never advertise it as independent clinical validation.

## 3. Shared product objective

Deliver the journey: consent/onboarding -> connect/import CGM -> connect optional wearable -> capture meal image -> estimate nutritional values -> user verifies quantities -> obtain fs-1.3 prediction -> display forecast and optional informational alert -> ingest observed CGM -> score completed response -> append eligible historical episode -> improve future personalization within the frozen policy. Medication management, reminders and sourced education work alongside this loop; wearable metrics are stored separately unless an existing 39-feature input explicitly supports them.

## 4. Cross-team ownership rule

Ruhaan owns all core app/backend/AI integrations, storage and API design, personal glucose adaptation, and backend-frontend functional integration. Het owns AWS resources, networking, deployment, operations, secrets infrastructure, CI/CD and managed service configuration. Neil owns questionnaire, medication UX, reminder/alert UX, screens and user preferences; Ruhaan implements underlying APIs and services. Ownership of an interface is split deliberately: Ruhaan signs off server contract and correctness; Neil signs off interface states and usability; Het signs off reachability and cloud operations. No teammate edits another teammate’s owned files without prior agreement.

## 5. Repository and branches

All teammates create their branch from the SAME agreed base commit and add all four canonical docs under docs/project/. Work in independently scoped directories when possible. Branches are ruhaan (core), het (infra), neil (features) unless actual remote names differ. Each teammate cherry-picks or merges the documentation-only planning commit as appropriate; avoid simultaneously creating divergent copies. No direct main-branch pushes without review. Pull requests must include changed endpoint/schema references, migrations, tests, configuration impacts, screenshots (for Neil), rollout and rollback instructions (for Het), and explicit model-integrity verification when model-facing code changes.

## 6. Cross-team communication protocol

One shared decision log: docs/project/DECISIONS.md; one interface registry: docs/project/API_INTERFACE_REGISTRY.md; one blocker register: docs/project/BLOCKERS.md. Propose a contract change by recording owner, request/response example, auth, errors, event semantics, version and consumers; Ruhaan approves before frontend or cloud implements it. Het publishes staging URL, CORS origins and secret names (never values); Neil publishes UI requirements and mocks; Ruhaan publishes stable OpenAPI and database schemas. Agree on timestamps (ISO 8601 offset-aware UTC for stored events), source-local timestamps only when known, glucose internal mg/dL, macro grams, calories kcal, and immutable provider IDs. Use a short daily integration checkpoint with blocking issues first.

## 7. Global done criteria

All features must satisfy: authenticated per-user isolation; timestamp and source provenance; validation and nonfinite-value rejection; meaningful missing/empty/stale/error screens; reproducible tests; no real secrets committed; no protected health data in logs; accessibility basics; versioned API documentation; no false clinical claims; clean model artifact hash; smoke tests in a shared staging environment; rollback path. “Connected” means real authorized data were read successfully, not that a connect button exists.

## 8. Scope and explicit non-goals

IN SCOPE: onboarding, live/imported CGM, meal image nutrient estimates with user confirmation, completed-meal scoring, frozen personalization, wearable adapters, medication registry and reminders, non-clinical prospective spike alerts, secure AWS hosting and frontend-backend integration. NOT IN SCOPE: retraining global fs-1.3; automatic medication adjustment; clinical emergency alert claims; unapproved HealthKit access from the web browser; calibrated risk probabilities; predicting medications’ individualized effect with fs-1.3; exposing raw medical data to the image model; creating fabricated synthetic CGM disguised as live.

## 9. Component architecture

Web UI (GLIMPSE) -> authenticated API gateway / FastAPI service -> domain services: identity/onboarding, meal photo analysis, meal confirmation and prediction orchestrator, CGM ingestion/reconciliation, histories/outcome scoring, personalization record update, medication ledger, alerts and notifications, wearable connectors. Persistence: relational database with organization/user ownership, object storage for private images, secrets store, background job mechanism for sync/reminders. Frozen prediction module isolated as a read-only dependency. HealthKit requires native iOS bridge or a suitable approved connector, not direct browser database access. Other device providers require OAuth/scopes and their real permissions; mock mode must display DEMO distinctly.

## 10. Data lifecycle and schema contracts

Proposed entities: users(user_id,auth_subject,timezone,consent_state), participants(participant_id,user_id,model_binding), device_connections(connection_id,user_id,provider,scopes,status,last_success), health_observations(observation_id,user_id,provider,source_id,type,value,unit,observed_at,received_at,quality), meals(meal_id,user_id,occurred_at,photo_object_key,status), meal_estimates(estimate_id,meal_id,ai_model_version,structured_foods,macro_estimates,uncertainty), meal_confirmations(meal_id,confirmed_macros,confirmed_at), predictions(prediction_id,meal_id,model_id,model_sha,feature_snapshot_id,created_at,horizons,trajectory), response_episodes(episode_id,meal_id,observation_coverage,outcomes,eligible_after,status), medication_regimens(regimen_id,user_id,name,dose,unit,schedule,start/end,status), medication_doses(dose_id,regimen_id,scheduled_at,actual_at,status), alert_rules, alert_events, notification_deliveries, audit_events. Proposed only: Ruhaan must reconcile with existing DB before migrations. Uniqueness: (provider, source_id, user_id) for external observations; immutable audit trail for predictions and confirmed meal inputs. Separate observation time from ingestion time; avoid overwriting historical values without correction provenance.

## 11. Canonical event sequence

1 User authorizes data processing and optionally connects devices. 2 CGM observations enter normalized store; stale/unsupported units detected. 3 User captures meal photo; upload only that image to vision endpoint; never send private CGM history unnecessarily. 4 Model returns nutrition estimates with provenance and editable range; user confirms macros. 5 Backend takes an as-of snapshot and invokes EXISTING frozen /api/predict. 6 Snapshot, forecast, timestamp and context are persisted; UI renders status and informational alert logic. 7 After 120 minutes plus data-arrival delay, scorer checks completeness, intervening meals, sensor gaps and quality; uncertain/confounded episodes are not silently accepted. 8 Eligible completed episodes become available to frozen retrieval and calibration for FUTURE requests. 9 Notifications remain deduplicated and independently controllable.

## 12. API registry — proposed additional routes

Existing routes must remain exactly as frozen: GET /health, GET /api/participants, GET /api/participants/{id}/history, POST /api/predict, GET /api/model/metrics, GET /api/model/info, POST /api/compare. New route names require approval; examples: POST /api/v2/meals/images, POST /api/v2/meals/{id}/analyze, PATCH /api/v2/meals/{id}/confirm, POST /api/v2/meals/{id}/predict, GET /api/v2/predictions/{id}, GET /api/v2/meals/{id}/outcome, POST /api/v2/cgm/import, GET /api/v2/devices, POST /api/v2/devices/{provider}/connect, GET/PATCH /api/v2/onboarding, GET/POST/PATCH /api/v2/medications, POST /api/v2/medication-doses, GET/PUT /api/v2/alert-settings, GET /api/v2/alerts. Auth: bearer identity/session, server-side user ownership, strict size/rate limits. Return stable error {code,message,request_id,details?}; no health-data logs.

## 13. Questionnaire spec and routing

35-question pool in eight sections: basics (DOB, sex assigned at birth, height, weight, goal); metabolic health (diagnosis, year, HbA1c and unit, fasting glucose and unit, clinician plan); glucose devices (monitoring method, make, connect preference, usage duration); medications (current glucose-affecting meds, name(s), prescribed dose/time, insulin use, reminder preference, discontinued meds); meals (meal count, times, diet pattern, restrictions, photo/manual preference); wearables (use, provider, connect, permissions); alerts (enable forecast alerts, threshold/unit, channels, quiet hours); consent (necessary processing; optional personal-history use). Conditional sections and skip paths. Necessary consent must not be buried as optional; no diagnosis prerequisite. Show ~12–18 questions for typical users. Questionnaire values configure app behavior; they do NOT enlarge 39-feature model input.

## 14. Security, privacy and safety gates

Least-privilege OAuth; encrypted-at-rest storage; TLS; pre-signed short-lived photo upload; image retention/delete controls; explicit source consent and revocation; protected health records separated per user; no medical information in exception traces; authenticated webhook signature verification where provider supports it; protect from prompt injection in uploaded images/vision response; validate nutrition estimates and explicitly require human confirmation; alerts are informational and should not impersonate CGM device emergency alarms; medication content is source-attributed education, not personalized dose guidance. Implement account deletion and provider disconnect semantics, including token revocation where possible.

## 15. Milestones and acceptance

M0: Shared four docs adopted, owner assignments and unresolved questions logged, API registry approved. M1: Het staging URL + Ruhaan health/model endpoints live + Neil mock UI wired. M2: AI photo -> user-confirmed macros -> actual frozen forecast persisted; schema and UI verified. M3: real or importable CGM -> time/quality normalization -> forecast-versus-actual plot -> eligible history. M4: reminders, medication lists, questionnaire and forecast notifications work end-to-end. M5: external wearable/CGM sources according to real provider access; explicit unavailable states. M6: production-like smoke, load and security checks, signed handoff. Each milestone has API sample, owner, evidence, tests and rollback.

## 16. Integration test matrix

Required cases: (a) full happy path, (b) denied HealthKit permission, (c) stale CGM, (d) mmol/L -> mg/dL, (e) duplicate/provider replays, (f) photo upload failure, (g) incorrect AI macros corrected by user, (h) user leaves meal confirmation unfinished, (i) another meal within 120m, (j) 4 vs 5 prior completed meals, (k) subsequent episode ends exactly at new meal timestamp (excluded), (l) token expiry and disconnect, (m) alert dedupe and quiet hours, (n) medication missed/skipped/not-confirmed, (o) cross-user data isolation, (p) AWS restart/state persistence, (q) 25-point forecast consistency, (r) artifact SHA unchanged, (s) consent withdrawn and data deletion, (t) errors with safe UI state.

## 17. Risk register and decisions needed

Unknowns requiring decisions before code: source for initial real CGM (Dexcom developer access, Libre pathway, Apple Health native bridge, or authorized file import); whether there is an iOS native app (required for direct HealthKit); specific AWS account/region/budget, backend runtime and frontend hosting; authentication identity provider; database selection/migration method; notification channels and actual background scheduler; approved OpenAI model, image storage/retention policy, cost ceilings; whether prospective meal quantity is planned or entered after eating; consent/privacy policy ownership; whether medical educational content has a clinical reviewer. Do not assume SDK/provider API access because the logo exists.

## 18. Final team handoff

Ruhaan supplies OpenAPI examples, fixtures, database migrations, model-sha verification and integration tests. Het supplies infrastructure-as-code, network/secret names, staging/production URLs, CI/CD, deployment/rollback SOP, monitoring dashboards and cost guardrails. Neil supplies questionnaire flows, alerts/medication UX, reminder/notification state diagram, accessibility and frontend tests. All three jointly sign integrated smoke checklist; governance signoff remains separate from technical work.
