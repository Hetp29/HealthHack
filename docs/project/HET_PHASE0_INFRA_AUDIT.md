# H0: Infrastructure audit (Het)

**Revision 2:** 2026-10-11 ~03:30 UTC · **Branch:** `het` @ `a1ab3f9` (includes Ruhaan's `origin/ruhaan` @ `72dc336`:
the fs-1.3 freeze, the v2 application layer and demo replay). The frontend was read from `origin/neil` @ `94e305d`
(read, not merged).
**Status:** audit only. No AWS resources were created, and none of the backend, model or frontend files were changed.

Revision 1 (at `09c896d`) predated Ruhaan's v2 work. Its findings F1 (a Dockerfile that trains) and F5 (no
`Authorization` header via CORS) are **now resolved on Ruhaan's side**, and F3 (the 568 MB warehouse at runtime) **no
longer applies to the deployment**. See §1.

Sources: the repository at the commit above, `docs/governance/FS13_MODEL_FREEZE_V1.md` and its manifest,
the four plan documents, and Ruhaan's `HANDOFF_HET.md`, `DEMO_HANDOFF_HET.md`, `BLOCKERS.md` and `DECISIONS.md`.

**Local verification at `a1ab3f9`:** `pip install -r requirements-dev.txt` followed by `python -m pytest -q` gave
**955 passed, 52 skipped, 0 failed** (6 min 13 s, macOS, Python 3.12.7). Frozen backend source hashes match the
manifest (checked `main.py`, `config.py`).

---

## 1. Findings

| # | Finding | Status | Consequence for infra |
|---|---|---|---|
| F1 | The old Dockerfile trained a synthetic model at build time. | **Resolved by Ruhaan (`0c7003d`).** The image now carries only `ml/artifacts_cgmacros_fs13/`, and `backend.app.v2.verify_artifact` runs at build (no image if missing or wrong) and at start (the container exits). `.dockerignore` excludes all other model directories. | Infra still has to **prove** it, because the image has never been built. H2 includes a deliberately-wrong-artifact build that must fail. |
| F2 | **The frozen artifact is not in Git** (5 files, about 440 KB; checksums are in `HANDOFF_HET.md` §4). Ruhaan holds the only copy. | **Open: B-004, owned by Het.** | Private, versioned, write-once S3 artifact prefix. The pipeline fetches it into the build context and checks the SHA-256 before `docker build`. |
| F3 | Earlier assumption: the CGMacros warehouse (568 MB) is needed at runtime. | **Not needed for deployment.** v2 uses its own application database. The demo needs only `cgmacros_demo_bundle.json` (87 KB) and optionally `cgmacros_returning_user_bundle.json` (94 KB). | Bundles are research data: store them privately alongside the artifact, never bake them into a public image, and mount or fetch them at start. **The CGMacros redistribution terms still need checking** before upload. |
| F4 | The backend source is hash-locked by the freeze (v1 `backend/app/*`, routes, services, `contracts/openapi.yaml`). | Unchanged; still holds. | Infra never edits backend code. Request IDs, auth and v2 CORS are now in Ruhaan's separate v2 ASGI app (`backend.app.v2.asgi:app`, D-003), outside the frozen files. |
| F5 | Frozen v1 CORS allows only the `Content-Type` header. | **Resolved for v2.** v2 sets its own CORS (`Authorization`, `If-Match`, `Content-Type`; exposes `X-Request-ID`) from `GLIMPSE_CORS_ORIGINS`. v1 keeps its frozen policy. | **Do not add CORS headers at the CDN or gateway** (duplicate headers break browsers). One origin (CloudFront) remains the recommended design, so no cross-origin requests happen at all. |
| F6 | The frontend is TanStack Start with server-side rendering (Lovable Vite config, Nitro with a Cloudflare default target), and `VITE_API_BASE_URL` is set at build time. | Unchanged. | Hosting choice needed from Neil (§4.3). |
| F7 | **The frontend is not wired to the real backend yet.** On `origin/neil`, `/auth` is a stub (hard-coded test credentials, local state), `/connect-devices` uses mock actions, and the app defaults to fixture/"Demo data" mode. | New; Neil + Ruhaan. | Infra can host it, but the end-to-end acceptance test (a browser using real auth against the deployed API) needs that wiring first. |
| F8 | **Local tooling:** Docker 28.0.4 installed but the **daemon is not running**. AWS CLI credentials in `~/.aws` are **invalid** (`InvalidClientTokenId`). Terraform 1.14.3 is available. | Open. | H2 needs Docker Desktop started. H3+ needs valid non-root AWS credentials and an account/region/budget decision. |
| F9 | **v2 has never run on PostgreSQL** (B-007), and `S3ImageStore` has never been exercised, with **`boto3` not pinned** (B-008). | Open; Het + Ruhaan. | Provide a scratch PostgreSQL to run `alembic upgrade head && alembic check && alembic downgrade base` and the v2 suite. Add a pinned `boto3` (with Ruhaan's agreement) and test against the real bucket. |
| F10 | **No rate limiting in the app** (B-005). | Open. | Rate-limit `/api/v2/auth/*` and the upload routes at the edge (WAF rate rule or equivalent). |
| F11 | Demo mode is **single-process** (an in-process advance lock), and seeding the returning-user scenario runs 31 forecasts (about 5 s). | By design. | The demo environment runs **one container, one worker**. Seed before taking traffic. `GLIMPSE_DEMO_MODE=1` only in the demo environment, never anywhere real users could be. |

---

## 2. Component inventory

Status values: **present**, **incomplete**, **missing**, **blocked**.

| Component | Status | Evidence / notes |
|---|---|---|
| Frontend framework | present | TanStack Start 1.168 / React 19 / Vite 8 / Nitro 3 beta on `origin/neil`. |
| Frontend → backend wiring | incomplete | Stub auth, mock devices, fixture mode by default (F7). The API base URL is set at build time. |
| Frontend AWS build target | incomplete | Nitro targets Cloudflare by default (F6). |
| Backend entry point | present | **`uvicorn backend.app.v2.asgi:app`**, which serves the frozen v1 plus `/api/v2`. Stateless, so multiple workers are fine (except in demo mode). |
| Python / dependencies | present | 3.12, every package pinned with `==` (new: Pillow, openai, cryptography, httpx, alembic, psycopg). No hash lockfile. **scikit-learn must stay 1.4.2** (pickle). `boto3` missing (F9). |
| Dockerfile | incomplete | Frozen-artifact-only, two checksum gates, non-root user, read-only artifact. **Never built** (F1, F8). |
| Model artifact | **blocked** | F2 / B-004. |
| Demo bundles | **blocked** | Ruhaan's machine only (87 KB + 94 KB); verified with `python -m scripts.demo_verify`. |
| Liveness | present | `GET /health` (frozen). |
| Readiness | present | `GET /api/v2/ready`: database + model + checksum, 503 with per-check flags. |
| Capabilities | present | `GET /api/v2/status` (no secrets). |
| Logging | present | Single-line stdout logs. v2 lines carry the request ID only, never health values. `X-Request-ID` is returned. |
| Authentication | present (unverified in the cloud) | Built-in email + password with opaque bearer tokens (D-004). No email verification or MFA (B-005). |
| Application DB | incomplete | 24 tables, Alembic migrations `0001`/`0002`, `postgresql+psycopg://`. **Never run on PostgreSQL** (F9). |
| Object storage | incomplete | Private bucket, streamed by the API (**no presigned URLs**, D-012), keys `<prefix><user_id>/<photo_id>.jpg`. Never exercised (F9). |
| Secrets | missing (infra) | Names are defined (§5). No store exists yet. |
| Background jobs | present (code) / missing (scheduler) | `workers.outcome_scoring` (5 min), `workers.reminders` (1 min), `workers.device_sync` (15 min). Idempotent one-shot commands from the same image. Need a real scheduler. |
| OAuth callbacks | present (code) | `/api/v2/devices/{dexcom_api,fitbit,whoop}/callback`. No provider credentials exist (B-002). No inbound webhooks (sync is by polling). |
| Rate limiting | missing | F10. |
| CI/CD, IaC, environments | missing | No `.github/`, no IaC, no cloud environment. |
| AWS account / credentials | **blocked** | F8. |
| Domain / TLS | missing | No domain decided. CloudFront's default HTTPS hostname works without one. |

---

## 3. Current architecture (as found)

```
 Browser ─HTTPS─► Lovable-hosted Glimpse (SSR) ── fixture/demo data; stub auth
                                     ╳ (no deployed API)
 Laptop only:  uvicorn backend.app.v2.asgi:app ─► frozen fs-1.3 (+ v2 app layer)
               ├─ application DB: SQLite file      ├─ photos: local disk
               └─ workers: run by hand              └─ secrets: none configured
```

---

## 4. Proposed target architecture (staging, then demo/prod)

Design goals: run the **unchanged** image, keep **one origin**, add rate limiting at the edge, use managed services
only, have **no NAT gateway**, define everything in Terraform, and keep the artifact and bundles private.

```
                ┌──────────────── CloudFront (HTTPS, one origin, WAF rate rules) ─────────────────┐
 Browser ─────► │ /*                       → frontend (§4.3)                                        │
                │ /api/*, /health, /docs   → backend   (no CORS headers added here; body ≤16 MB,    │
                │                                       origin timeout ≥60 s)                      │
                └───────────────────────────────────────┬──────────────────────────────────────────┘
                                                        ▼
                     ┌───────── Backend: one container image, tagged by git SHA (ECR) ─────────┐
                     │ API:     verify_artifact → uvicorn backend.app.v2.asgi:app               │
                     │ Workers: same image; python -m workers.{outcome_scoring,reminders,        │
                     │          device_sync}, triggered by EventBridge Scheduler                │
                     └──┬────────────┬───────────────┬──────────────┬───────────────────────────┘
                        │            │               │              │
           RDS PostgreSQL (private,  S3 photos       Secrets Manager CloudWatch logs/alarms
           encrypted, PITR ≥7 d)     (private, SSE,  (names in §5)   (model_integrity_failure,
                                      no public URLs)                  v2_unhandled, 5xx, latency)
           S3 artifacts + demo bundles (private, versioned, write-once prefix; fetched at build/start)
```

### 4.1 Backend compute: decide in H1
| Option | For | Against |
|---|---|---|
| **ECS Fargate** (API service + scheduled worker tasks) | One image for API and workers. EventBridge Scheduler → `RunTask` is native. Works with private RDS. Ephemeral storage sized as needed. | Needs a load balancer (ALB) or other origin for CloudFront, which costs money while idle. Avoid NAT with public-subnet tasks plus security groups, or VPC endpoints. |
| **App Runner** (API) + Fargate scheduled tasks (workers) | Simplest API hosting with built-in HTTPS. | Two compute products. Needs a VPC connector for private RDS. **Availability to new accounts must be verified** before choosing it. |

Initial lean: **ECS Fargate**, because the workers and the API share one image and one network model.

### 4.2 Application database
RDS PostgreSQL, smallest burstable class, single-AZ for staging, encrypted, automated backups with point-in-time
recovery for **at least 7 days** (per `HANDOFF_HET.md` §6), not publicly accessible. Migrations run as a **one-off
ECS task (`alembic upgrade head`) before** a rollout, never at container boot.

### 4.3 Frontend hosting: decide with Neil
| Option | Notes |
|---|---|
| A. Keep Lovable hosting | Zero AWS work, but cross-origin: requires `GLIMPSE_CORS_ORIGINS` set to the Lovable origin. Works with v2's CORS. |
| B. Amplify Hosting (SSR) via Nitro's AWS preset | Managed SSR, but the build preset changes in the Lovable config (Neil's agreement needed). |
| C. Static/SPA build → S3 + CloudFront | Cheapest and gives one origin. Only valid if the app doesn't need SSR (Neil to confirm). |

### 4.4 Environments
- **demo:** `GLIMPSE_DEMO_MODE=1`, a single container, a demo password secret, the bundles mounted. **No real users.**
- **staging:** demo mode off, used for integration and smoke tests.
- **prod:** out of scope before the deadline, and needs the compliance review in §7.

---

## 5. Secret inventory (names only; values are entered by their owner)

| Secret | Used by | Owner who enters it | Notes |
|---|---|---|---|
| `GLIMPSE_APP_DB_URL` | API, all workers, migrations | Het (generated with RDS) | `postgresql+psycopg://…` |
| `GLIMPSE_TOKEN_ENCRYPTION_KEY` | API, `device_sync` | Het (generated once) | Fernet key. **Losing it makes stored OAuth tokens unreadable; rotating it disconnects every user.** Never auto-rotate. |
| `OPENAI_API_KEY` | API only | team / Ruhaan | Not needed for the demo. |
| `DEXCOM_CLIENT_SECRET`, `FITBIT_CLIENT_SECRET`, `WHOOP_CLIENT_SECRET` | API, `device_sync` | whoever registers the developer app | B-002: none exist yet. |
| `GLIMPSE_DEMO_PASSWORD` | demo environment only | Het | Shared with Neil out of band. |

Non-secret configuration (environment variables): `GLIMPSE_MODEL_ARTIFACT`, `GLIMPSE_METRICS_PATH`,
`GLIMPSE_EXPECTED_MODEL_SHA256` (as the Dockerfile sets them; never overridden), `GLIMPSE_CORS_ORIGINS`,
`GLIMPSE_PUBLIC_API_BASE_URL`, `GLIMPSE_FRONTEND_RETURN_URL`, `GLIMPSE_IMAGE_S3_BUCKET`/`_PREFIX`,
`GLIMPSE_OPENAI_VISION_MODEL`, the provider client IDs, `DEXCOM_ENVIRONMENT`, the demo variables. **Never set
`GLIMPSE_PERSONALIZATION_POLICY`** (D-007). The frontend gets `VITE_API_BASE_URL` only.

---

## 6. Resources and what drives cost

No prices are listed here. H1 will price them with the AWS Pricing Calculator for the agreed region and attach the
estimate.

| Resource | Phase | Costs while idle? | Main cost driver |
|---|---|---|---|
| ECR repository | H3 | yes (small) | stored image GB |
| ECS Fargate API service (+ ALB) | H3 | **yes** | task vCPU/memory hours; ALB hours + capacity units |
| Fargate scheduled worker tasks | H6 | no (per run) | runs × duration |
| RDS PostgreSQL (smallest burstable, single-AZ) | H5 | **yes** | instance hours + storage + backup storage |
| S3 (artifacts, bundles, photos) | H3/H5 | yes (small) | GB stored, requests |
| CloudFront + WAF rate rules | H4 | WAF: **yes** (web ACL + rules) | requests; WAF ACL/rule/request charges |
| Secrets Manager | H5 | **yes** | per secret per month + API calls |
| CloudWatch logs, metrics, alarms | H7 | yes (small) | ingested GB, retention, alarms |
| AWS Budgets | H3 | small | budgets/actions |
| **Avoid:** NAT gateway, Multi-AZ RDS, Aurora provisioned, oversized tasks | — | **yes, significant** | hourly |

Tags on every resource: `project=glucotwin`, `env=<demo|staging|prod>`, `owner=het`, `managed_by=terraform`.
Logs are kept 14 days in demo and staging. **Budgets and alerts are created first.** Teardown is `terraform destroy`
per environment, preceded by an RDS final snapshot (documented in the runbook).

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| The artifact or bundles aren't handed over in time | Ask Ruhaan now. Without them the container correctly refuses to start, so nothing is faked. |
| The Docker image fails on first build (never built) | H2 builds locally first, including a wrong-artifact negative test. |
| PostgreSQL behaviour differs from SQLite (B-007) | Scratch RDS plus the v2 suite before any user-facing deploy. |
| CGMacros-derived bundles hosted without clearing the licence | Check the terms. Private bucket only, no public URL. |
| Demo mode enabled where real users exist | Separate demo environment. The flag is set only by the Terraform demo workspace. |
| Duplicate CORS headers from the CDN | The CDN passes the app's CORS through untouched. Tested by browser preflight. |
| Idle billing (ALB, RDS, WAF, Secrets Manager) | Budgets first, smallest sizes, scripted teardown. |
| **Deadline 2026-10-11 12:00 ET** | Prioritise: H2 (verified image) → H3/H4 minimal demo environment. Everything else is documented and marked unverified until tested. |
| A HIPAA/regulated-use assumption | **No HIPAA or BAA claim.** Real-user health data in production requires a compliance review first. |

---

## 8. Approvals and handoffs needed

| From | Item |
|---|---|
| **Het / team** | AWS account + **region** + budget/credits; a non-root IAM identity for Het (current local credentials are invalid); a domain (optional). |
| **Ruhaan** | The 5 artifact files + 2 demo bundles (checksums in `HANDOFF_HET.md` §4 and `DEMO_HANDOFF_HET.md` §2); approval to add a pinned `boto3`; review of this audit (O-10). |
| **Neil** | Frontend hosting option (§4.3); wiring `/auth` to `/api/v2/auth/*` and live mode before the end-to-end browser test. |
| **Organization / legal** | CGMacros redistribution terms for cloud-hosted bundles; compliance review before any real users. |

Handoffs **from Het** once done: staging/demo API base URL and frontend origin; the artifact location; a scratch
PostgreSQL for B-007; the bucket name and prefix for B-008; gateway limits (body 16 MB, timeout ≥60 s); the worker
schedules.

---

## 9. Milestones

| Phase | Deliverable | Needs |
|---|---|---|
| **H1** | Architecture decision record (Fargate vs App Runner, frontend option, environments), Pricing Calculator estimate | Region and budget |
| **H2** | Image built locally; wrong-artifact build fails; container passes `/health`, `/api/v2/ready`, `scripts/smoke_v2.py`, `demo_verify` | **Docker running + artifact + bundles** |
| **H3** | Terraform: budgets, tags, ECR, private artifact bucket, VPC without NAT, RDS, ECS API service, logs | AWS credentials and authorization |
| **H4** | CloudFront one origin + WAF rate rules + frontend hosting; browser smoke test | Neil's decision |
| **H5** | Secrets Manager entries, photo bucket + task-role policy, Postgres verification (B-007/B-008) | — |
| **H6** | EventBridge Scheduler → worker tasks (3 schedules), failure alarms | — |
| **H7** | GitHub Actions: tests, `research.freeze.verify_artifacts`, `alembic check`, contract check, image by SHA, deploy, rollback; dashboards/runbook | OIDC deploy role |
| **H8** | Integrated smoke tests in the demo/staging environment; handoff | All of the above |
