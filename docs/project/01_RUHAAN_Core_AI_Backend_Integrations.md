# GLIMPSE | RUHAAN IMPLEMENTATION BRIEF

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

## 8. Mission and boundaries

Own reliable application intelligence and every model-adjacent data flow. Preserve fs-1.3 exactly. Separate new application orchestration from immutable inference. Own server interfaces, database migrations, adapters, OpenAI meal analysis, CGM ingestion, wearable integration, user-linked outcomes, personal-history update, backend integration with Neil's screens, and validation of the complete journey. Het, not Ruhaan, owns cloud resource provisioning; Neil, not Ruhaan, owns supporting-feature UI.

## 9. Module design and sequence

Suggested packages (adapt to actual repository, do not overwrite): backend/app/v2/{auth,profiles,meals,vision,cgm,devices,wearables,predictions,outcomes,personalization,medications,alerts,notifications}, backend/app/core/{db,config,security,logging}, workers/{device_sync,outcome_scoring,reminders}. Confirm current app layout before creating modules. Start with shared entities + adapter interfaces, then CGM and photo flows, then completed-meal eligibility, then optional providers, then alerts/reminders API and UI integration.

## 10. OpenAI meal-image estimate implementation

Backend accepts authenticated image metadata or pre-signed object key with MIME and size validation, strips unnecessary metadata for third-party transfer, calls a vision-capable OpenAI model server-side only, and validates the response against a strict structured schema. Required return: food_candidates(name,confidence_label,possible_portion_description), totals_estimate(carbs_g,protein_g,fat_g,fiber_g,calories_kcal), ranges/limitations, needs_user_confirmation=true, ai_model/version and provenance. Never fabricate precise confidence probabilities; use qualitative uncertainty if uncalibrated. No CGM data in the image-model request. Keep original estimate and user-adjusted confirmed macros as separate immutable records. Photo inference is an estimate, not USDA-verified measurement. Enforce max upload/cost, retry with idempotency, error behavior, privacy consent and prompt-injection resistance. User confirmation gates actual prediction.

## 11. CGM canonical adapter and ingestion

Interface: list_samples(user, since, until, cursor), health/status, authorize/revoke (where supported), deduplication and backfill. Normalize glucose into mg/dL with original values/units retained and explicit conversion; preserve timezone and source observation timestamps; do not invent UTC for naive external data. Store provider, device type, sample ID, quality flags and received_at. Handle out-of-order observations, corrections, duplicates, missingness, implausible values, sensor warmup, provider rate limit, revoked token, permissions, stale feeds. Do not interpolate native CGM into fabricated raw samples. Define a minimum recency/coverage gate for forecast availability rather than passing stale context silently.

## 12. HealthKit, Fitbit and WHOOP implementation

HealthKit cannot be accessed directly from FastAPI/browser: requires authorized native iOS bridge with HealthKit entitlement, type-scoped consent, local read, secure upload and sync cursor; confirm iOS app ownership with frontend team. Apple Watch data ordinarily flows via Apple Health as permitted. Fitbit and WHOOP use official OAuth when developer access exists; implement token refresh, scopes, cancellation and source-specific capabilities. WHOOP may not expose raw minute-level HR in the requested way; do not promise unsupported fields. Build provider-specific capability matrix; store sleep/recovery/HRV for UI unless input exactly maps to frozen feature policy. Device connectors cannot be marked working based solely on account linking.

## 13. Prediction orchestrator and historical retrieval

On prediction, require a confirmed meal event, participant binding, as-of timestamp and eligible CGM context. Snapshot all 39 feature values/order and model checksum; call frozen inference and store forecast/horizons/trajectory, quality flags, provenance and request_id. Ensure previous 120-min meal windows strictly end before as-of time; min_history=5, retrieval_k=5, min_curve_points=20. Do not patch frozen q_retr_n branch semantics. No future activity/CGM or image follow-up leaked. For proposed /api/v2/meals/{id}/predict return reference to frozen /api/predict response, not an altered contract.

## 14. Personalized retraining versus permitted updating

Vocabulary rule: Level A=append verified completed episode to participant historical store; Level B=run the frozen participant shrinkage/calibration policy at next inference; BOTH can be deployed as fs-1.3-compatible behavior. Level C=fit new participant-specific model parameters (true retraining), explicitly separate version / experimental opt-in and do NOT ship under frozen v0.1.0 without governance approval. Score response only after a complete 120-minute observation window plus delayed sensor sync. Mark overlapping meals, questionable CGM, missing observations, sensor corrections and unknown actual consumed amount. Store predicted vs observed deltas, error metrics and eligibility decisions; do not let today's outcome modify yesterday's persisted prediction.

## 15. Model-compatible input mapping

Create a table for each of 39 model inputs: provider source, unit, window, transformation, timestamp cutoff, null behavior, and tests. Do not assume non-CGMacros HR or activity signals match original training semantics. Do not feed metadata such as HRV, medication dose, sleep score, image embeddings or questionnaire answers into immutable feature vector. Keep unsupported extras for dashboard or future research only. Review distribution shifts when user-confirmed photo macros replace source-consumed macro estimates.

## 16. Server-facing support for Neil

Implement approved onboarding/profile, medications/regimens/dose-events, alert-settings, alerts, and notification events. UI lead is Neil, server lead Ruhaan. Clearly distinguish medications prescribed vs logged doses taken; reminders do not indicate ingestion. Provide clinician-neutral drug education via versioned sourced material; no individualized treatment predictions. Spike-alert evaluator uses forecast snapshots and a configurable user threshold, respects quiet hours/dedupe/rate limiting, labels not calibrated, and does not substitute for CGM safety alarms. Exact routes await API registry acceptance.

## 17. Contract handoff package

For each new endpoint provide typed request and response schemas; sample success and failures; response latency; auth scopes; field units; null/unknown status; pagination; idempotency; error code; observable side effects; test fixture; OpenAPI export. Deliver seed fixture users A/B, meals, synthetic DEMO CGM clearly tagged, stale sample case, no-consent case, and 4/5-history boundary. Notify Neil before breaking payloads and Het before new env vars, jobs or migrations.

## 18. Ruhaan acceptance checklist

End-to-end photo -> editable estimates -> confirmed meal -> frozen forecast; device/cgm data mapped with correct identity/time and source; successful 120-minute outcome scoring and future-only memory; artifact checksum unchanged; 39-feature invariance to injected future samples; no direct PHI to vision model; all v1 routes unchanged; per-user auth tests; reliable backend errors; new modules have tests and migration rollback; staging frontend displays actual API results; no unsupported provider advertised as connected.
