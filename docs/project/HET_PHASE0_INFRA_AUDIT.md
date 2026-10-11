# H0: Infrastructure audit (Het)

**Date:** 2026-10-11 00:47 UTC · **Branch:** `het` @ `09c896d` (merge of Ruhaan's freeze `e362e88`) ·
**Status:** audit only. No AWS resources were created, and none of the files audited here were changed.

Scope: everything needed to run GlucoTwin (frontend + FastAPI + frozen fs-1.3) on AWS.
Sources: the repository at the commit above, `origin/neil` @ `e763598` for the frontend (read,
not merged), `docs/governance/FS13_MODEL_FREEZE_V1.md`, `docs/governance/fs13_integrity_manifest.json`,
and the four plan documents in this folder.

---

## 1. Findings that change the plan

| # | Finding | Why it matters |
|---|---|---|
| F1 | **The existing `Dockerfile` trains a model at build time** (`RUN python -m ml.train --source synthetic_dev`). It points at `ml/artifacts/model.joblib`, not the frozen `ml/artifacts_cgmacros_fs13/`. | It directly violates the freeze ("never retrain"), and it would serve a synthetic-data model. H2 must replace this build path. The file also says it was never built (no Docker daemon). |
| F2 | **The frozen artifact is not in Git.** `ml/artifacts_*/` is gitignored, and `model.joblib` (SHA `12ed0602…15fe`) exists only on Ruhaan's machine. | We can't build the real container until Ruhaan hands it over. It will be delivered through a private S3 artifact bucket, with the SHA-256 verified at build and at startup. |
| F3 | **Inference needs the CGMacros SQLite warehouse at runtime.** It uses `GLUCOTWIN_DATA_BACKEND=sqlite` and `GLUCOTWIN_DB_URL`. The freeze was verified against `data/local/cgmacros_v2.db` (on Ruhaan's machine). This machine has `data/glucotwin.db` (0.57 GB) from the same adapter. | (a) We must confirm with Ruhaan that both files are the same build, or obtain his. (b) The directive says there must be no raw CGMacros data in container layers, so the database lives outside the image, in a private S3 bucket and/or attached storage. (c) **The dataset licence must permit hosting derived data in the cloud.** This is not yet verified. |
| F4 | **The backend source is hash-locked by the freeze.** `fs13_integrity_manifest.json → inference_source_sha256` covers `backend/app/{main,config,schemas,errors,dependencies}.py`, all routes, `services/*` and `contracts/openapi.yaml`. Current hashes match (checked `main.py`, `config.py`). | Infra cannot add auth, request IDs, structured logging or CORS changes **inside** the backend. These must come from a wrapper or the edge (see §4), and must not change any response bytes. |
| F5 | **CORS is hard-coded in the frozen `main.py`:** `allow_headers=["Content-Type"]`, `allow_credentials=False`. | A browser on a *different* origin can't send an `Authorization` header. Recommended fix: **serve frontend and API from one origin** (CloudFront path routing `/api/*`, `/health` → backend). No cross-origin requests means no CORS problem, with no frozen-code change. |
| F6 | **The frontend is TanStack Start with server-side rendering**, built through `@lovable.dev/vite-tanstack-config` (Nitro, Cloudflare target by default). `VITE_API_BASE_URL` is baked in at **build** time. It is hosted today by Lovable. | It is not a plain static site. AWS options are in §4.3. With one origin (F5), the API base URL becomes relative and builds stop being per-environment. |
| F7 | **No auth, no Postgres, no object storage, no jobs, no CI.** No `.github/`, no IaC. | These are all new (H3–H7). New API routes are Ruhaan's (`/api/v2/*`, still proposals), so the infra builds the platform and his code fills it in. |
| F8 | **Local tooling:** Docker 28.0.4 installed but the **daemon is not running**. AWS CLI 2.32.28 installed and `~/.aws` exists, but the **credentials are invalid or expired** (`InvalidClientTokenId`). Terraform 1.14.3 is installed. No CDK, SAM or `gh`. | H2 needs Docker Desktop started. H3+ needs valid, non-root AWS credentials plus an account/region/budget decision. |

---

## 2. Component inventory

Status values: **present** (exists and works as needed), **incomplete** (exists, gaps), **missing**,
**blocked** (can't proceed without someone else).

| Component | Status | Evidence / notes |
|---|---|---|
| Frontend framework | present | TanStack Start 1.168 (React 19, Vite 8, Tailwind 4, Nitro 3 beta) on `origin/neil`. `npm run build` → `.output/`. Vitest tests pass (11). |
| Frontend build target for AWS | incomplete | Nitro defaults to Cloudflare via the Lovable config. An AWS preset or SPA mode is needed (§4.3). |
| Frontend API base URL | incomplete | `VITE_API_BASE_URL` is build-time; it is `""` in production unless set, and then requests error with "not configured". |
| Backend entry point | present | `uvicorn backend.app.main:app`, a FastAPI 0.109 factory. Port from `$PORT` in the Dockerfile. |
| Python version | present | 3.12 (`python:3.12-slim`; local `.venv` is 3.12.7). |
| Dependencies | incomplete | `requirements.txt` is pinned with `==`, but there is no hash-locked lockfile, so transitive dependencies float. |
| Dockerfile | **blocked / non-compliant** | F1: trains at build time and was never built. Needs a frozen-artifact build (H2). |
| docker-compose | incomplete | Never run. Its frontend service expects a `./frontend/` folder that doesn't exist. |
| Model artifact | **blocked** | F2: not in the repository; Ruhaan must supply it. The SHA is in the manifest. |
| Model loading | present | `GLUCOTWIN_MODEL_ARTIFACT`, `GLUCOTWIN_METRICS_PATH`; `/api/predict` returns 503 `model_not_trained` if missing. |
| Startup checksum verification | missing | Not done at app startup. It can't be added to frozen `main.py`, so it goes in a container entrypoint (H2). |
| Runtime data store | incomplete | Read-only SQLite (`SqliteDataRepository`, WAL). Needs a cloud placement and a licence check (F3). |
| Health check | present | `GET /health`: liveness only, independent of the model by design. |
| Readiness check | incomplete | No route for it. Readiness is inferred from `GET /api/model/info` (`model_version != "none"`). Infra probe planned in H2. |
| Logging | incomplete | Plain single-line stdout logs. No request ID and no JSON. Can't change in-app (F4); request ID goes at the edge or in a wrapper. |
| CORS | present (strict) | An explicit origin list from `GLUCOTWIN_CORS_ORIGINS` (wildcard warned). Headers are fixed to `Content-Type` (F5). |
| Authentication | missing | None ("The API has no auth"). Identity provider not chosen (master plan §17). |
| Relational DB (Postgres) | missing | Proposed entities exist only in the master plan §10. Ruhaan owns the schema and migrations. |
| Object storage (meal photos) | missing | None. |
| Secrets | missing (none needed yet) | No secrets today. Future: OpenAI, DB, auth, Fitbit, WHOOP, CGM, notifications, webhooks. |
| Background jobs / scheduler | missing | No workers, queues or schedules. |
| Webhooks / OAuth callbacks | missing | None implemented. Provider access isn't confirmed. |
| CI/CD | missing | No `.github/workflows`. Tests run locally (pytest: 507 at `8e436e5`; frontend vitest: 11). |
| IaC | missing | None. Terraform is available locally. |
| Environments | missing | Only local. Lovable hosts the frontend preview. |
| AWS account / credentials | **blocked** | F8: local credentials are invalid. Account, region and budget not agreed. |
| Domain / DNS / TLS | missing | No domain decided. A CloudFront default `*.cloudfront.net` HTTPS URL works without one. |

---

## 3. Current architecture (as found)

```
 Browser ──HTTPS──► Lovable-hosted frontend (TanStack Start SSR)
    │
    └─(would call)─► VITE_API_BASE_URL  ──►  nothing deployed
                                              (backend runs only on a laptop:
                                               uvicorn → frozen fs-1.3 → local SQLite)
```

---

## 4. Proposed target architecture (staging first)

Design goals: run the **frozen backend unchanged**, add security at the edge rather than in
frozen code, use **one origin** to avoid CORS, stick to managed services, avoid NAT gateways
and idle-heavy resources, and define everything in Terraform.

```
                      ┌─────────────────────── CloudFront (HTTPS, one origin) ────────────────────────┐
 Browser ── HTTPS ──► │  /*            → frontend (see §4.3)                                         │
                      │  /api/*, /health, /docs → backend origin                                     │
                      └──────────────────────────────────────────────────────────────────────────────┘
                                                         │
                                       ┌─────────────────▼──────────────────┐
                                       │ Backend container (App Runner or   │  image: ECR, tagged by git SHA
                                       │ ECS Fargate) — frozen FastAPI +    │  entrypoint verifies model SHA,
                                       │ fs-1.3, read-only                  │  never trains
                                       └──┬───────────┬──────────────┬──────┘
                       private S3 (artifacts,│           │Secrets Manager│ CloudWatch logs/metrics/alarms
                       CGMacros warehouse)  ─┘           │               │
                                                         ▼
                                (H5+, after Ruhaan's schema) RDS PostgreSQL — private
                                (H5+) private S3 meal-photo bucket, short-lived presigned URLs
                                (H6)  EventBridge Scheduler → SQS (+DLQ) → worker (Lambda/Fargate task)
                                (H5)  Cognito user pool (if chosen as the identity provider)
```

### 4.1 Backend compute: decide in H1

| Option | For | Against |
|---|---|---|
| **AWS App Runner** | Lowest operations: built-in HTTPS, health checks, deploy from ECR, autoscaling, no VPC/NAT needed for public egress. | Less control. Private RDS needs a VPC connector. Its availability to new accounts and its ephemeral disk limits for a 0.57 GB SQLite file must be **verified** before choosing it. |
| **ECS Fargate + ALB** | Full control, VPC-native, works with private RDS, ephemeral storage sized as needed. | More resources to manage. The ALB costs money while idle. Avoid NAT by using public-subnet tasks or VPC endpoints. |

### 4.2 Runtime warehouse (F3)
The working assumption is that the SQLite file is stored in a private S3 bucket, copied to local storage at
container start (read-only), and its SHA-256 checked. This keeps it out of image layers. It depends on
(a) the licence check, (b) Ruhaan confirming which `.db` matches the freeze, and (c) the disk-size limits of the
chosen compute.

### 4.3 Frontend hosting: decide with Neil
| Option | Notes |
|---|---|
| A. Keep Lovable hosting | Already HTTPS, zero AWS work. But it is a cross-origin API, so it runs into F5 (no `Authorization` header) once auth exists. |
| B. **Amplify Hosting (SSR)** using Nitro's AWS Amplify preset | Managed SSR, branch-based environments. The build preset must be overridden in the Lovable config, which needs Neil's agreement. |
| C. SPA/static build → S3 + CloudFront | Simplest and cheapest. Only works if the app doesn't rely on SSR, which needs Neil's confirmation. |

All three can sit behind one CloudFront distribution to achieve the single origin (F5).

### 4.4 Auth (needs Ruhaan + team decision)
The master plan leaves the identity provider open. The infra-side candidate is a **Cognito user pool**,
with tokens validated in Ruhaan's `/api/v2` code (and/or at the edge). Frozen v1 routes stay as they are.

---

## 5. Required resources (staging) and what drives cost

**No prices are listed here on purpose.** H1 will price these with the AWS Pricing Calculator for the agreed
region, and attach the export. This table shows what is billed even with **no traffic**:

| Resource | Phase | Costs while idle? | Main cost driver |
|---|---|---|---|
| ECR repository | H3 | yes (small) | stored image GB |
| App Runner service **or** ECS service + ALB | H3 | **yes** (provisioned instance memory, or ALB hours + task hours) | instance size × hours |
| S3 buckets (artifacts, warehouse, photos, logs) | H3/H5 | yes (small) | stored GB, requests |
| CloudFront | H4 | no (pay per use) | data out, requests |
| Secrets Manager | H5 | **yes** | per secret per month + API calls |
| RDS PostgreSQL (smallest burstable, single-AZ for staging) | H5 | **yes** | instance hours + storage + backups |
| Cognito user pool | H5 | depends on tier and number of active users | monthly active users |
| EventBridge Scheduler / SQS / Lambda | H6 | mostly no | invocations |
| CloudWatch logs, metrics, alarms | H7 | yes (small) | ingested GB, retention, alarms |
| AWS Budgets | H3 | small | number of budgets/actions |
| **Must avoid:** NAT gateway, Multi-AZ RDS, provisioned Aurora, oversized instances | — | **yes, significant** | hourly |

Tagging for every resource: `project=glucotwin`, `env=staging|prod`, `owner=het`, `managed_by=terraform`.
Log retention: 14 days in staging. Budgets with email alerts are created **before** anything else in H3.

---

## 6. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Building with the current Dockerfile ships a retrained synthetic model | Breaks the freeze; wrong forecasts | H2 replaces the build. CI fails if any `ml.train` appears in the container build path or the SHA differs. |
| The artifact or warehouse isn't supplied in time | No real-model deployment | Ask Ruhaan now (F2, F3). Fallback: deploy infra with the 503 `model_not_trained` state shown honestly. |
| Hosting a CGMacros-derived database without licence clearance | Compliance | Check the licence before uploading. Private bucket only, never public. |
| Changing frozen backend files for infra reasons | Breaks the freeze hashes | Wrap or use the edge only. A CI hash check against `fs13_integrity_manifest.json`. |
| Cross-origin auth blocked by frozen CORS | Login fails in the browser | One-origin CloudFront (F5). |
| Idle billing (ALB, RDS, Secrets Manager, NAT) | Spends the budget | Budgets first, no NAT, smallest sizes, a scripted `terraform destroy`. |
| Time: the deadline is **2026-10-11 12:00 ET** | Can't finish H3–H8 | Prioritize M1: backend + frontend reachable over HTTPS with the frozen model. Everything else is documented as planned. |
| Lovable sync on the frontend branch | Lost history if rewritten | Never force-push. Frontend build changes only with Neil's agreement. |
| Health data in logs once v2 exists | Privacy | Redaction rules and log review in H7. No payload logging at the edge. |

---

## 7. External approvals needed

| From | Item |
|---|---|
| **Het / team** | AWS account and owner, **region**, budget/credits, a non-root IAM identity for Het, a domain (optional). |
| **Ruhaan** | 1) `ml/artifacts_cgmacros_fs13/` files (SHA-verified handoff). 2) Which warehouse `.db` matches the freeze (or a copy). 3) Agreement that the frozen Dockerfile build is replaced by a frozen-artifact build. 4) Identity provider choice. 5) Postgres schema and migrations when v2 starts. |
| **Neil** | Frontend hosting option (§4.3), whether SSR is needed, and the build-preset change in the Lovable config. |
| **Organization / legal** | CGMacros licence for cloud hosting. **No HIPAA/BAA claim**: if real user health data is in scope, a compliance review must happen before production. |

---

## 8. Milestones

| Phase | Deliverable | Needs |
|---|---|---|
| **H1** | Architecture decision record (compute, frontend, warehouse, auth), Pricing Calculator estimate, Terraform layout | Region and budget decision |
| **H2** | Frozen-artifact Dockerfile (no training, SHA check at build and start, non-root, read-only), local smoke test of all 7 v1 routes | Docker running, artifact from Ruhaan |
| **H3** | Terraform: budgets, tags, ECR, artifact/warehouse buckets, backend service, CloudWatch logs | AWS credentials and authorization |
| **H4** | CloudFront single origin, frontend hosting, HTTPS, browser smoke test | Neil's hosting decision |
| **H5** | Secrets Manager names, private photo bucket and presigned-URL policy, RDS (after schema), Cognito (if chosen) | Ruhaan's schema and auth decision |
| **H6** | Scheduler → SQS (+DLQ) → worker skeleton, one real job path | Ruhaan's job semantics |
| **H7** | GitHub Actions (tests, hash check, image by SHA, staging deploy, rollback), alarms, runbook | Repo secrets via OIDC role |
| **H8** | Integrated staging smoke, handoff to Ruhaan and Neil | All of the above |

**Realistic before the deadline:** H1–H4 (M1: a real HTTPS app with the frozen model), provided AWS access and the
artifact arrive tonight. H5–H8 are designed and documented, and only marked done once verified.
