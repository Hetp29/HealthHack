# GLIMPSE | NEIL IMPLEMENTATION BRIEF

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

## 8. Mission and product philosophy

Own smaller user-facing functions: multi-step questionnaire, medication register/dose UX, medication reminders, non-clinical predicted-glucose-spike alert UX and notification preferences, plus related validation/help/empty states. Do NOT modify model code, server health-data logic, infrastructure or frozen API v1.0. Build against Ruhaan-approved versioned API contracts; mocked fixtures must be marked in UI and replaced only after real server path passes integration tests.

## 9. Questionnaire: full 35-question bank

SECTION 1 BASICS: Q1 date of birth; Q2 sex assigned at birth (including prefer not); Q3 height with units; Q4 weight with units; Q5 primary goal (meal responses, spike awareness, diabetes management, metabolic exploration, other). SECTION 2 METABOLIC: Q6 diabetes/prediabetes/gestational/unknown/no status; Q7 diagnosis year (conditional); Q8 last HbA1c value and unit or unknown; Q9 usual fasting glucose and unit or unknown; Q10 clinician-provided glucose plan (optional). SECTION 3 MONITORING: Q11 CGM/finger-prick/both/none; Q12 CGM brand if applicable; Q13 connect now/later/no; Q14 duration of CGM usage. SECTION 4 MEDS: Q15 taking glucose-affecting medications yes/no/unsure; Q16 medication search/multiple selection; Q17 prescribed dosage/unit/schedule per medication; Q18 insulin yes/no/prefer not; Q19 medication reminders yes/no; Q20 discontinued medication log option. SECTION 5 MEALS: Q21 meals/day; Q22 typical meal-time ranges; Q23 dietary pattern; Q24 food allergies/restrictions; Q25 meal logging method photo/manual/both. SECTION 6 WEARABLES: Q26 use smartwatch/tracker; Q27 manufacturer/platform; Q28 connect now/later/no; Q29 permission choices for available data types. SECTION 7 ALERTS: Q30 informational predicted-spike alerts yes/no; Q31 threshold in mg/dL or mmol/L with unit handling; Q32 push/email/in-app (only real channels); Q33 quiet hours for non-urgent notifications. SECTION 8 CONSENT: Q34 necessary meal/glucose processing consent (must be explicit); Q35 optional use of completed personal meal/glucose history for future personalization. Conditional skip logic: no diabetes should not force diagnosis year; no medications should not force dose forms; no wearable should not force connection. Typical user sees ~12–18 prompts. Add Save & Continue, Skip optional, edit later and consent withdrawal screens.

## 10. Questionnaire behavior and data quality

Validate age/eligibility under applicable policy without inventing acceptance rules; accept unknown/prefer not; do not force diagnostic disclosure; convert weight/height display units accurately while retaining original entered value; HbA1c percent vs mmol/mol is explicit rather than silently guessed; glucose mg/dL/mmol/L explicit. Use searchable medication inputs and free text fallback while avoiding unsafe autosuggest. Consent requests are distinct and auditable. Never place onboarding values into frozen model feature array. Protect form state from accidental loss, handle slow save and offline/retry, support keyboard and screen readers.

## 11. Medication tab UX and state model

Tabs or sections: Current, Previous, Dose History, Reminders, Education. A regimen stores drug name, form, prescribed dose/unit, frequency, time zone, optional start/end, prescribing-source note (optional), current/discontinued status. A dose event stores scheduled_at, action(taken/skipped/snoozed/unknown), actual_at (only if confirmed), actor and provenance. Never mark a dose taken because the pop-up disappeared. Display editable history with caution for corrections. Allow skip, later, snooze; avoid a false “missed” label before grace period rule is approved. Prevent double entries through idempotency and display stale sync.

## 12. Medication glucose education guardrails

Education explains broadly documented medication classes’ potential relationship to glucose (e.g., lowering blood glucose, risk of hypoglycemia depending on regimen) with source citation, verification date, and clear limitations. Never predict exact individual stabilizing glucose concentration, never propose starting/stopping/dose change, never infer medicine was taken from regimen presence, never advise emergency intervention based on uncalibrated fs-1.3 forecast. If information missing, display “we do not have a validated individualized medication-effect forecast.” Provide relevant clinical caution and contact-professional guidance, not fabricated causal simulation.

## 13. Forecast-spike alert UX

Alert rule includes enabled, threshold with unit, channels, quiet hours, cooldown and optional meal-forecast scope. Server evaluates frozen forecast, not Neil's frontend math (except purely explanatory display). Example label: “Forecast may rise above your selected level within the next 2 hours”; show generated_at, target level, predicted crossing estimate, latest CGM age, uncertainty status “not calibrated,” and view prediction action. Suppress/downgrade if required CGM stale, no forecast, or user disabled. Deduplicate repeat notifications. Explicitly distinguish informational GLIMPSE alerts from validated CGM-device alarms. If notifications unavailable, show in-app status honestly.

## 14. Reminder experience and notification system

Support on-screen reminder with Taken, Snooze and Skip; optional push/email if channel is truly configured; respect timezone and DST as provided by backend; quiet hours require a documented exception policy (do not silently suppress prescribed-time reminders without consent). Persist each action only after API success and show retry on failure. Prompt to enable device/browser permissions only when user opts in. Scheduled events must be backed by Ruhaan/Het durable task service; no browser timer can be sole source of truth. Log delivery and action separately.

## 15. Frontend API dependency list

From Ruhaan request: GET/PATCH onboarding, connection status and scopes, GET/POST/PATCH medications, dose events create/list, medication education content source/version, GET/PUT alert rules, GET alerts, preference channel availability, authentication/profile, GET predictions with freshness metadata. For each request show loading/empty/success/error/retry, 401 session recovery, 403 unauthorized, 409 conflict, validation errors, background sync status, stale data. New paths are proposed in master; do not create independently inconsistent endpoint names. Het supplies frontend API base URL and published permitted origins.

## 16. UI interaction and accessibility

Design mobile-first forms, large tap targets, visible labels, explicit required/optional marks, WCAG-friendly contrast, screen reader status changes, focus management for reminders, no color-only glucose alert meaning, unit labels on every value, correct local-time rendering, accessible modal dismissals, no auto-advancing past clinical/consent questions. Save answers incrementally without duplicate requests, preserve form progress, and make screenshots/video demos use fake identities and clearly labeled demo data.

## 17. Neil acceptance checklist

All 35 questions accessible through conditional flow, optional skips work, saved profile round-trips API, consent required/optional split accurate, medications separated current/previous and regimen/dose event, reminder actions persist and never imply medicine taken without confirmation, forecast spike notifications tied to actual server rule, no claim of calibrated prediction or medication-specific simulation, loading/empty/errors tested, mobile and keyboard QA complete, UI exposes connected vs demo vs unsupported appropriately, frozen model and API unchanged.
