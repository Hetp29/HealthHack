# GLIMPSE | HET IMPLEMENTATION BRIEF

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

## 8. Mission and deployment target

Own reproducible cloud infrastructure and production-like staging for frontend and FastAPI, not predictive behavior or domain feature logic. Prefer infrastructure-as-code and minimal viable services within budget. Proposed topology: web frontend static hosting/CDN (depending actual frontend stack) -> HTTPS domain -> containerized FastAPI compute (AWS App Runner/ECS Fargate depending network, job and scaling needs) -> managed PostgreSQL and private S3 -> background worker/scheduler -> managed secrets/logging. This is a proposal, not a predetermined deployment platform; document a justified final choice and costs.

## 9. AWS decision checklist

Agree AWS account owner, region, environment names, budgets, billing alerts, domain, DNS owner, IAM role owners, recovery posture, frontend hosting platform, API hosting platform, database size/backup plan, object retention, VPC/private networking constraints, HTTPS certificate, allowed inbound CORS origins, secrets names, CI/CD provider and runner permissions. Never use personal root IAM access keys. Separate dev/staging/prod where feasible; do not copy real participant data to dev.

## 10. Deployment architecture details

Backend: deterministic Docker image with pinned Python and OS dependencies, health/readiness routes, appropriate workers/timeouts, model artifacts included immutably and checksum verified at startup. DB: schema migrations managed by Ruhaan, Het provisions role/database/backups; use migrations one-way with rollback plan. S3: bucket private, block all public access, least-privilege role, short-expiry pre-signed URLs, lifecycle/deletion policy. Secrets: Secrets Manager/SSM per env, IAM task identity; OpenAI keys and OAuth secrets never bundled into JS or container image. Tasks: a real scheduler/queue for sync, outcome scoring and reminders; do not use in-process web workers for durable timed alerts. Logs: request_id, redaction, security alerting; no raw CGM or prescription values in logs.

## 11. Frontend connectivity / CORS

Publish stable STAGING_API_BASE_URL and frontend URL. Configure allowed origins explicitly, HTTPS only, methods/headers based on approved contract, credentials policy matching chosen auth. Test browser preflight, token expiry, unauthorized request, upload size, network timeout and reverse-proxy limits. Web frontend environment variables must contain public data only; frontend never receives privileged OpenAI/device OAuth client secrets. Set DNS/cert renewal and staging/production split.

## 12. CI/CD and release workflow

On pull request: format/lint/tests, frozen artifact hash verification, schemas/OpenAPI diff check, container build, dependency scan, no-secrets scan. On staging merge: build tagged image by commit SHA, deploy, migrate with documented approval, run /health and /api/predict smoke using demo-safe inputs, synthetic/demo status clearly labeled, verify all seven v1 endpoint behaviors, check browser calls. Production promotion gated by owner approval; retain prior working image and DB restore/checkpoint. Cloud configuration commits go to Het's branch with PR review; deployment must not modify backend inference files.

## 13. Device webhook and job operations

Check whether each provider actually supports webhooks, data-export or polling and only deploy required mechanism. Secure OAuth redirect URIs, state/PKCE where appropriate, server-side token encryption, token rotation, provider signature verification for supported events. Background sync jobs need idempotency keys, rate-limit backoff, retry cutoff, dead-letter/quarantine, last_success_at and status surfaces. Reminder engine needs durable schedule, timezone/DST decisions, quiet hours, dedupe and no phantom “taken” state. Spike alerts are informational; delivery failure may be shown but not framed as failed medical monitoring.

## 14. Security posture and data governance

Document data classification and privacy responsibility, least-privilege IAM, encryption in transit/at rest, encrypted DB/backups, retention/deletion, incident response contact, alerting and audit trail. Verify service-region and external processing policy (OpenAI image calls, Fitbit/WHOOP OAuth) with Ruhaan. Do not assume HIPAA compliance or BAA exists; if medical-grade or covered-entity processing is intended, pause for appropriate legal/compliance review. Integrate authenticated delete/disconnect workflows into storage teardown.

## 15. Observability and runbooks

Metrics: 2xx/4xx/5xx, p50/p95/p99 latency, CPU/memory, model initialization failures, DB connectivity, queue age, sync failures, stale-device percentages, estimated per-image API usage, error budgets, storage and API costs. Dashboards and alerts to humans. Runbooks for bad deployment, expired secret, provider outage, data ingestion backlog, RDS unavailable, S3 permission denied, image upload failure, cost spike and model checksum mismatch. Provide one-step rollback and clearly state any actions not automatically reversible.

## 16. Het acceptance checklist

Staging reproducible from documented configuration; TLS and domain work; app reads model artifact without mutation; persisted data survives container restarts; S3 images private; client can upload with approved signed flow; secrets not in repo/frontend; actual browser can call backend with auth; job scheduler functions across restarts; logs redact health records; alarms and cost budget configured; CI/CD rollback tested; Ruhaan and Neil receive URLs, docs and proof of connectivity. Deployment green is not feature green.
