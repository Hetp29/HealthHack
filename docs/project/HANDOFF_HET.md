# Handoff to Het — deploying the GLIMPSE backend

From Ruhaan. Everything the backend needs from its environment. Nothing here
provisions anything: AWS resources, networking, secrets infrastructure and CI/CD are
yours. Where something is unverified it says so.

## 0 · How to get this work onto your branch

Everything is on the remote branch **`origin/ruhaan`**. Nothing is on `main`.

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

## 1 · Runtime

| | |
|---|---|
| Python | **3.12** (developed and tested on 3.12.10) |
| Dependencies | `requirements.txt` — every package pinned to an exact version |
| scikit-learn | must stay **1.4.2**: the model artifact is a pickle and another minor version may not load it |
| OS packages | none beyond what the Python wheels bundle (Pillow, cryptography and psycopg ship binary wheels) |
| Accelerator | none; CPU only |
| Memory | model load ~6 MB; budget 512 MB per process |

`requirements-dev.txt` adds pytest, PyYAML and jsonschema for the test suite only.

## 2 · Start command

```bash
alembic upgrade head
uvicorn backend.app.v2.asgi:app --host 0.0.0.0 --port ${PORT:-8000}
```

**The entry point changed.** It is `backend.app.v2.asgi:app`, not
`backend.app.main:app`. The new one serves the frozen v1 API unchanged *and* `/api/v2`.
Starting the old one gives you v1 only.

Run from the repository root (imports are `backend.…`, `ml.…`, `data.…`). The app
keeps no state in the process, so multiple workers or containers are fine:
`--workers N`. Uploads are up to 15 MB — raise any proxy body-size limit to **16 MB**.
The OpenAI call can take up to 45 s — set proxy/idle timeouts to **≥ 60 s**.

## 3 · Health and readiness

| probe | route | meaning |
|---|---|---|
| Liveness | `GET /health` | process is up. Frozen; independent of model and database. |
| Readiness | `GET /api/v2/ready` | 200 only if the database answers, the model is loaded **and the artifact checksum is verified**. 503 with per-check booleans otherwise. |
| Capabilities | `GET /api/v2/status` | which integrations are configured. No secrets. |

Route traffic on readiness, restart on liveness.

## 4 · The model artifact — action needed (BLOCKERS B-004)

**The deployment path never trains a model and never substitutes a synthetic one.**
The `Dockerfile` on this branch carries exactly the frozen fs-1.3 artifact and is
gated twice by `python -m backend.app.v2.verify_artifact`:

* **at build** — if `ml/artifacts_cgmacros_fs13/model.joblib` is missing from the
  build context, or is not the approved checksum, the build fails. No image.
* **at start** — the container command runs the same check before `uvicorn`. A wrong
  artifact means the container exits instead of serving. This is what protects the
  frozen v1 `/api/predict`, which cannot check for itself.

`.dockerignore` keeps every other model directory out of the image.

The frozen artifact directory is **gitignored**, so a clean clone cannot build. What
is needed from you:

1. Store these five files immutably (e.g. a versioned, write-once S3 prefix):

   | file | SHA-256 |
   |---|---|
   | `model.joblib` | `12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe` |
   | `model_manifest.json` | `e1b4d4c1c3a8ad5f5c9b6979dca5cc953eca5192c67f97c03ca8b546686599d9` |
   | `metrics.json` | `d519359621baee9891faef761a2f15dfe7b8f5813a16da0cc37c98fb7b3444a3` |
   | `metrics_day8_holdout.json` | same as `metrics.json` |
   | `evaluation_detail_day8_holdout.json` | `ec8829178554a12058b568e9ec9cd286b3241b8612b20ec1077e8a87f34e47fe` |

   Ruhaan holds the only copy today (local disk). Agree the transfer with him; do not
   regenerate it — `python -m ml.train` produces a *different* artifact.

2. In the pipeline, fetch them into `ml/artifacts_cgmacros_fs13/` before
   `docker build`.

3. Keep these three variables as the Dockerfile sets them; never override the
   artifact path to anything else:

   ```
   GLUCOTWIN_MODEL_ARTIFACT=ml/artifacts_cgmacros_fs13/model.joblib
   GLUCOTWIN_METRICS_PATH=ml/artifacts_cgmacros_fs13/metrics.json
   GLUCOTWIN_EXPECTED_MODEL_SHA256=12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe
   ```

4. Never run `python -m ml.train` or `python -m ml.evaluate` in a build or a
   deployment. (The test suite trains a small synthetic model under `ml/artifacts/`
   for its own use; that directory is excluded from the image.)

Independently of the container gates, v2 also refuses to forecast
(`503 model_integrity_failure`) and `/api/v2/ready` returns 503 when the loaded
artifact does not match `GLUCOTWIN_EXPECTED_MODEL_SHA256`.

**Unverified:** the image has never been built or run — there is no Docker daemon on
the development machine. Please build it, run `python scripts/smoke_v2.py <url>`
against the container, and tell me what breaks. Also confirm the gate by building once
with a deliberately wrong file: the build must fail.

## 5 · Environment variables

Names only. Full annotated list: `.env.example`.

**Required in every deployed environment**

| name | secret? | notes |
|---|---|---|
| `GLUCOTWIN_APP_DB_URL` | **yes** | `postgresql+psycopg://USER:PASSWORD@HOST:5432/DB` |
| `GLUCOTWIN_MODEL_ARTIFACT` | no | see §4 |
| `GLUCOTWIN_METRICS_PATH` | no | see §4 |
| `GLUCOTWIN_EXPECTED_MODEL_SHA256` | no | see §4 |
| `GLUCOTWIN_CORS_ORIGINS` | no | comma-separated exact origins of the frontend. Used by both v1 and v2. No wildcard. |
| `GLUCOTWIN_DATA_BACKEND` | no | `synthetic_dev` unless you deploy the research warehouse; v2 does not use it |

**Required for a feature to work — unset means that feature reports "not configured"**

| name | secret? | feature |
|---|---|---|
| `OPENAI_API_KEY` | **yes** | meal photo analysis |
| `GLUCOTWIN_OPENAI_VISION_MODEL` | no | meal photo analysis (no default; team decision) |
| `GLUCOTWIN_TOKEN_ENCRYPTION_KEY` | **yes** | any OAuth provider. A Fernet key. **Losing it makes every stored provider token unreadable; rotating it disconnects every user.** |
| `GLUCOTWIN_PUBLIC_API_BASE_URL` | no | OAuth redirect URIs |
| `GLUCOTWIN_FRONTEND_RETURN_URL` | no | where the browser lands after OAuth |
| `DEXCOM_CLIENT_ID` / `DEXCOM_CLIENT_SECRET` | secret: **yes** | Dexcom |
| `DEXCOM_ENVIRONMENT` | no | `sandbox` (default) or `production` |
| `FITBIT_CLIENT_ID` / `FITBIT_CLIENT_SECRET` | secret: **yes** | Fitbit |
| `WHOOP_CLIENT_ID` / `WHOOP_CLIENT_SECRET` | secret: **yes** | WHOOP |
| `GLUCOTWIN_IMAGE_S3_BUCKET` | no | S3 photo storage (else local disk) |
| `GLUCOTWIN_IMAGE_S3_PREFIX` | no | default `meal-images/` |

**Do not set** `GLUCOTWIN_PERSONALIZATION_POLICY`. It must stay at its default,
`history_only`: the other value is ruled NOT APPROVED FOR ENABLEMENT (DECISIONS D-007).

**Tunables with safe defaults:** `GLUCOTWIN_OUTCOME_GRACE_MINUTES`, `GLUCOTWIN_SESSION_TTL_HOURS`,
`GLUCOTWIN_REGISTRATION_OPEN`, `GLUCOTWIN_MAX_IMAGE_BYTES`,
`GLUCOTWIN_VISION_DAILY_LIMIT`, `GLUCOTWIN_OPENAI_TIMEOUT_SECONDS`,
`GLUCOTWIN_CONSENT_POLICY_VERSION`, `GLUCOTWIN_LOG_LEVEL`.

**Secret scope.** All six secrets are needed by the API container. The workers need
`GLUCOTWIN_APP_DB_URL`; `device_sync` additionally needs the token key and the
provider client secrets. Nothing needs the OpenAI key except the API. **No secret is
ever a frontend variable** — the frontend gets `VITE_API_BASE_URL` and nothing else.

## 6 · Database

| | |
|---|---|
| Engine | PostgreSQL in the cloud; SQLite for local development |
| Driver | `psycopg` 3 (pinned). URL scheme **`postgresql+psycopg://`** |
| Schema | 24 tables, defined in `backend/app/v2/db/tables.py` |
| Migrations | Alembic, in `migrations/`. URL is read from `GLUCOTWIN_APP_DB_URL`; none is in `alembic.ini` |

```bash
alembic upgrade head      # apply
alembic current           # what is applied
alembic downgrade -1      # roll back the last migration
alembic check             # fail if models and migrations have drifted (CI)
```

Run `alembic upgrade head` as a one-off task **before** starting new containers, not
from each container at boot.

**Not verified on PostgreSQL (BLOCKERS B-007).** The migration is confirmed to
generate valid PostgreSQL DDL offline and passes fully on SQLite. It has never been
run on a PostgreSQL server. To close this, against a **scratch** instance:

```bash
export GLUCOTWIN_APP_DB_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/scratch
alembic upgrade head && alembic check && alembic downgrade base

# Then the whole v2 suite against that database. The schema is DROPPED and recreated
# for every test -- never point this at a database that holds anything.
GLUCOTWIN_TEST_APP_DB_URL=$GLUCOTWIN_APP_DB_URL python -m pytest tests/v2 -q -p no:cacheprovider
```

**Role.** The application role needs `SELECT/INSERT/UPDATE/DELETE` on its tables. The
migration role additionally needs DDL. They can be the same role for staging.

**Backups and recovery.** This database is the only copy of user data: accounts,
glucose history, meals, predictions, outcomes and consent records. None of it can be
regenerated. Expectations: automated daily backups with point-in-time recovery,
encryption at rest, at least 7 days retention, and a tested restore. `audit_events`
intentionally survives account deletion (it holds no health values).

**The research warehouse** (`GLUCOTWIN_DB_URL`, `data/**`) is a separate database and
is not needed by v2. It contains no user data and is regenerable.

## 7 · Object storage

Meal photos. Requirements for the bucket: **private, all public access blocked,
server-side encryption, no public or pre-signed read URLs** — the API streams photos
to their authenticated owner itself. The task role needs `s3:PutObject`,
`s3:GetObject`, `s3:DeleteObject` on `arn:…:bucket/<prefix>*` and nothing else.
Objects are keyed `<prefix><user_id>/<photo_id>.jpg`. Deleting a meal, a photo or an
account deletes the object, so a lifecycle rule is a backstop, not the mechanism.
Versioning, if enabled, must expire noncurrent versions or deletions will not be real.

**`S3ImageStore` is not exercised (BLOCKERS B-008)** and `boto3` is not pinned. Add
`boto3` to the image and verify against the real bucket. Until then, local disk works
on a single node with a persistent volume at `GLUCOTWIN_IMAGE_DIR`; it does not work
across multiple containers.

## 8 · Background jobs

Three idempotent one-shot commands. Each exits 0 after one pass; `--loop SECONDS`
keeps it running instead. All are safe to run concurrently and to re-run.

| command | suggested schedule | needed for |
|---|---|---|
| `python -m workers.outcome_scoring` | every 5 min | keeping outcomes current for users who are not in the app |
| `python -m workers.reminders` | every 1 min | push/email reminders (once a channel exists) |
| `python -m workers.device_sync` | every 15 min | provider data without the user pressing sync |

**Nothing breaks if none of them runs**: the API scores a user's due outcomes and
creates their due reminder slots whenever that user makes a request. They exist so
state stays current when nobody is looking. They need a real scheduler (EventBridge +
ECS task, or cron) — not an in-process timer. Same image, same environment, different
command.

## 9 · Callbacks and webhooks

OAuth redirect URIs to register with each provider:

```
{GLUCOTWIN_PUBLIC_API_BASE_URL}/api/v2/devices/dexcom_api/callback
{GLUCOTWIN_PUBLIC_API_BASE_URL}/api/v2/devices/fitbit/callback
{GLUCOTWIN_PUBLIC_API_BASE_URL}/api/v2/devices/whoop/callback
```

HTTPS, publicly reachable, exact-match. These three routes are the **only**
unauthenticated v2 routes besides `/auth/register`, `/auth/login`, `/ready`,
`/status` and the docs. No inbound provider webhooks are implemented; sync is by
polling.

## 10 · CORS and authentication

* v2 authenticates with `Authorization: Bearer <token>`. **No cookies**, so
  `allow_credentials` is off and there is no CSRF surface.
* v2 allows `GET, POST, PUT, PATCH, DELETE, OPTIONS` and the request headers
  `Content-Type, Authorization, If-Match` from `GLUCOTWIN_CORS_ORIGINS`, and exposes
  `X-Request-ID`.
* v1 keeps its own frozen policy (`GET, POST, OPTIONS`; `Content-Type` only).
* Do not add CORS headers at the gateway or CDN — the app sets them and duplicates
  break browsers.
* Uploads send `Content-Type: image/jpeg` or `text/csv` with a raw body; make sure
  the gateway passes binary bodies through unmodified.
* **There is no rate limiting in the application (BLOCKERS B-005).** Please rate-limit
  `/api/v2/auth/*` and the upload routes at the gateway.
* Consider disabling `/docs` and `/api/v2/docs` in production.

## 11 · Logs

Single-line records on stdout under the `glucotwin` logger. v2 lines carry ids,
counts, state names and the request id only: no glucose value, macro, medication
name, token or email. Request bodies are never logged, and validation errors do not
echo submitted values. Correlate with the `X-Request-ID` response header. Useful
lines to alert on:

| line | meaning |
|---|---|
| `model_integrity_failure` | the loaded artifact does not match the expected checksum |
| `v2_unhandled` | an unexpected exception (traceback follows) |
| `device_sync … outcome=authorization_revoked` | a user's provider grant was withdrawn |
| `outcome_scoring … insufficient_data=N` | sensor data not arriving for scored windows |

## 12 · Smoke test

After a deployment, against the staging base URL:

```bash
B=https://staging.example.com

curl -fsS $B/health                                   # {"status":"ok","version":"1.0.0"}
curl -fsS $B/api/v2/ready                             # {"status":"ready",…}
curl -fsS $B/api/model/info | grep '"feature_set_version":"fs-1.3"'
curl -fsS $B/api/v2/status | grep '"integrity_verified":true'
curl -s -o /dev/null -w '%{http_code}\n' $B/api/v2/me # 401

# CORS preflight from the real frontend origin
curl -s -o /dev/null -w '%{http_code}\n' -X OPTIONS $B/api/v2/meals \
  -H 'Origin: https://app.example.com' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: authorization,content-type'   # 200
```

Full journey over real HTTP -- register, consent, demo CGM, photo upload, confirm,
forecast, reproduce, then delete the throwaway account it created:

```bash
python scripts/smoke_v2.py $B
```

It prints one line per step and exits non-zero on the first failure. It pushes
readings with source `demo` only.

In CI on every pull request:

```bash
python -m pytest -q -rsxX                    # full suite
python -m research.freeze.verify_artifacts   # frozen artifact + source checksums
alembic check                                # migrations match the models
python -m contracts.v2.build_contract --check   # published OpenAPI is current
```

## 13 · What I need back from you

1. Staging API base URL and frontend origin(s).
2. Confirmation the artifact delivery in §4 is in place, with the checksum variable set.
3. A scratch PostgreSQL instance to close B-007.
4. The bucket name and prefix, to close B-008.
5. The gateway's body-size and timeout limits.
