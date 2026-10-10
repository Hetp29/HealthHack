# Deployment

**Owner:** Ruhaan Shah · **Contract:** v1.0

> **Honest status.**
> **Verified:** local run on macOS / Python 3.12, including a live `uvicorn` server
> answering `/health`, `/api/predict` and `/api/model/metrics` with real forecasts and
> measured metrics.
> **Written but UNVERIFIED:** `Dockerfile` and `docker-compose.yml`. The development
> machine has no Docker daemon, so neither has been built or run.
> **Not started:** any AWS deployment. Workshop Studio permissions had not been
> enumerated at the time of writing.
>
> Nothing in this document may be presented as working unless its own verification
> checklist has been executed and its output captured.

---

## 1 · Local run — the verified path

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

python -m ml.train    --source synthetic_dev    # → ml/artifacts/model.joblib + manifest
python -m ml.evaluate --protocol all            # → ml/artifacts/metrics.json

uvicorn backend.app.main:app --reload --port 8000
```

`ml/artifacts/` is gitignored, so training is a required step on a fresh clone. The
service starts fine without it and returns `503 model_not_trained` from `/api/predict`
— by design, so the cause is visible.

### Smoke checklist

```bash
curl -s localhost:8000/health                       # {"status":"ok","version":"1.0.0"}
curl -s localhost:8000/api/model/info  | python -m json.tool | head -8
curl -s localhost:8000/api/participants | python -m json.tool | head -12
curl -s localhost:8000/api/model/metrics | python -m json.tool | head -20

curl -s -X POST localhost:8000/api/predict \
  -H 'Content-Type: application/json' \
  -d '{"participant_id":"synthetic_p002","meal_timestamp":"2026-10-05T12:30:00Z",
       "meal":{"carbs_g":85,"protein_g":24,"fat_g":18,"fiber_g":7,"calories_kcal":600},
       "context_override":null}' | python -m json.tool | head -30
```

Expected failure paths, all part of the checklist:

| Request | Expected |
|---|---|
| Unknown `participant_id` | `404 participant_not_found` |
| Negative `carbs_g` | `422 invalid_request` |
| `meal_timestamp` with no offset | `422 naive_datetime` |
| `context_override.as_of` after the meal | `422 future_context_override` |
| Meal time with no nearby pre-meal CGM | `422 insufficient_premeal_context` |
| No artifact present | `422`→ no; `/api/predict` → `503 model_not_trained` |

---

## 2 · Configuration

All settings come from the environment; copy `.env.example` to `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `GLUCOTWIN_CONTRACT_VERSION` | `1.0.0` | Version reported by `/health`. Changing it needs a contract bump. |
| `GLUCOTWIN_MODEL_ARTIFACT` | `ml/artifacts/model.joblib` | Trained artifact. Absent → `503`. |
| `GLUCOTWIN_METRICS_PATH` | `ml/artifacts/metrics.json` | Evaluation report. Absent → explicitly empty metrics. |
| `GLUCOTWIN_DATA_BACKEND` | `synthetic_dev` | `synthetic_dev` \| `sqlite` (Het's layer). |
| `GLUCOTWIN_DB_URL` | `sqlite:///data/glucotwin.db` | Used when backend is `sqlite`. |
| `GLUCOTWIN_CORS_ORIGINS` | `http://localhost:5173,…` | Comma-separated **exact** origins. |
| `GLUCOTWIN_LOG_LEVEL` | `INFO` | `DEBUG`…`ERROR`. |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Frontend's API base. |

**Secrets:** there are none. The API has no authentication, no database credentials
beyond a local SQLite path, and no third-party keys. `.env` is gitignored; temporary
AWS credentials must never be committed.

**CORS:** explicit origins only. A `*` value is permitted but logs a startup warning —
shipping a wildcard to a judged deployment is a real finding, not a convenience.
Credentials are not allowed, since the API uses no cookies or auth.

---

## 3 · Container — written, unverified

`Dockerfile` installs pinned dependencies, copies `contracts/`, `ml/` and `backend/`,
**trains inside the image** so the container ships with a working artifact, and runs
unprivileged as uid 10001. The `HEALTHCHECK` probes `/health` only, matching the
contract's liveness/readiness split.

### Verification steps — run these before claiming container support

```bash
docker build -t glucotwin-backend:v0.1.0 .
docker run --rm -p 8000:8000 \
  -e GLUCOTWIN_CORS_ORIGINS=http://localhost:5173 \
  glucotwin-backend:v0.1.0

# then, from another shell, the full smoke checklist in section 1
docker inspect --format='{{.State.Health.Status}}' <container>   # expect: healthy
```

If `docker build` fails, fix it or **remove the container claim from the deck**. Do not
present an unbuilt Dockerfile as containerized deployment.

---

## 4 · AWS — decision process, not yet a decision

The master plan requires verifying *actually available* services before choosing a
target. The step that has not happened is the enumeration.

### Step 1 — enumerate real permissions

```bash
aws sts get-caller-identity
aws ec2 describe-regions --query 'Regions[].RegionName' --output text
aws s3 ls
aws ecs list-clusters          ; aws ecr describe-repositories
aws apprunner list-services    ; aws lambda list-functions
aws elasticbeanstalk describe-applications
```

Record, for each, whether it returned a result or `AccessDenied`. That table is the
evidence base. Workshop Studio accounts are usually heavily restricted, so expect most
of these to be denied.

### Step 2 — choose the simplest permitted topology

| Preference | Topology | Needs |
|---|---|---|
| 1 | Static frontend on **S3** (+ CloudFront) · API on **App Runner** from **ECR** | `s3:*` on one bucket, ECR push, App Runner create |
| 2 | Frontend on S3 · API on **ECS Fargate** behind an ALB | ECR, ECS, ALB, VPC |
| 3 | Frontend on S3 · API as a **Lambda** via Mangum + API Gateway | Lambda, API Gateway, IAM role |
| 4 | Single **EC2** instance running Docker Compose | EC2 run-instances, security group |
| 5 | **None.** Local-only, with a recorded walkthrough. | — |

Option 5 is a legitimate outcome. A documented local deployment with honest reasoning
beats a half-broken cloud claim, and the master plan says so explicitly: *"no pretend
AWS architecture."*

### Step 3 — if deployed, verify

- `GET /health` reachable over the public URL.
- `GET /api/model/info` reports a real `model_version`, not `"none"`.
- One live `POST /api/predict` returning 25 points, captured as output.
- Frontend points at the deployed base URL — no hardcoded `localhost` in the build.
- CORS restricted to the deployed frontend origin.
- Every AWS component named in the deck appears in the deployed system.

### Step 4 — record it

Add the verified topology, region, service names and the captured smoke output to this
document. Temporary accounts are reclaimed automatically, so keep the recording and
screenshots **outside** AWS.

---

## 5 · Binding Het's data layer

No backend change is needed. When `data/storage/repository.py` exposes
`SqliteDataRepository`:

```bash
python -m data.storage.init_db --db-url sqlite:///data/glucotwin.db
python -m data.adapters.ingest --source synthetic_dev

export GLUCOTWIN_DATA_BACKEND=sqlite
export GLUCOTWIN_DB_URL=sqlite:///data/glucotwin.db
uvicorn backend.app.main:app
```

The `sqlite` branch already exists in `backend/app/services/data_access.py` and
currently fails with a message naming the missing module rather than an opaque
`ImportError`. Run `python -m pytest tests/test_data_contract.py` against the real
repository before switching the default; it is the acceptance suite for the seam.

Retrain afterwards — `python -m ml.train --source sqlite` — so the artifact and its
manifest record the new `data_source`.

---

## 6 · Operational notes

- **Startup cost** is dominated by deserializing the artifact (tens of ms for a Ridge
  model). It happens once per process, not per request.
- **Memory** is modest: the fixture holds ~12k CGM points in memory; the real data
  layer will stream from SQLite.
- **Latency has not been measured.** No p95 number is claimed. If one is needed, measure
  it and record the command and output here.
- **Logging** is single-line to stdout under the `glucotwin` logger, suitable for a
  container runtime. No participant-level physiological values and no request bodies
  are logged; error responses echo neither submitted values nor tracebacks.
- **Statelessness:** the service holds no mutable state, so it scales horizontally
  behind any load balancer without coordination.
