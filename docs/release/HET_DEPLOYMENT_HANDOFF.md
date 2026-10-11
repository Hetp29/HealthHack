# Deployment handoff for Het

What must be done and proven before GLIMPSE is public. The application was verified
locally on the release commit named in the final report; nothing below has been run.
Do not deploy `main` as it stands: it still has the pre-redesign frontend and the edited
frozen files (see §1).

## 1. Build from the right commit

* Build and deploy the commit named in the final report (branch `final-audit`), or
  `main` **after** that branch has been reviewed and merged with a merge commit.
* First check, on the build machine:

  ```bash
  python -m pytest tests/test_freeze_record_agreement.py tests/test_freeze_integrity.py -q
  shasum -a 256 contracts/openapi.yaml   # be836dddaac5ed4cd78af7adac02bf9b7a1b77133143b84df34621f1be9200f4
  ```

  Frozen files are never edited and their checksums are never re-recorded (DECISIONS
  D-020). If a frozen file needs to change, stop and ask Ruhaan for a new freeze.

## 2. Configuration

* Settings may be given as `GLIMPSE_X` or `GLUCOTWIN_X` (new name wins). The frozen
  engine reads `GLUCOTWIN_*`; the app copies `GLIMPSE_X` onto it at start-up, so either
  works. The start command must import the app through `backend.app…` (it does:
  `python -m backend.app.v2.verify_artifact` then `uvicorn backend.app.v2.asgi:app`).
* Required: `…_APP_DB_URL` (PostgreSQL), `…_MODEL_ARTIFACT`, `…_METRICS_PATH`,
  `…_EXPECTED_MODEL_SHA256=12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe`,
  `…_CORS_ORIGINS=<the frontend's public origin, exactly>`, `…_IMAGE_DIR` (or object
  storage), `…_DEMO_MODE=1`, `…_DEMO_PASSWORD` (secret store), both demonstration bundles
  mounted, `OPENAI_API_KEY` and `…_OPENAI_VISION_MODEL` (secret store, backend only).
* Frontend build: `VITE_API_BASE_URL=<the backend's public origin>`.
* **One** API worker/task for the service that serves the demonstration.

## 3. Checks that could not be run on the release machine

| Check | Command | Must show |
|---|---|---|
| PostgreSQL migrations + suite | `infra/local/verify_postgres.sh` | migrations up/check/down/up OK; v2 suite green (the 2 known harness deselections aside). New since your last run: `tests/v2/test_hypothetical_meal_isolation.py`, `tests/v2/test_meal_to_forecast_binding.py`. |
| Container build + smoke | `infra/local/container_smoke.sh` | ALL CHECKS PASSED, including the wrong-checksum refusal and both demonstrations |
| Full local stack | `infra/local/e2e_stack.sh up` then `E2E_BASE_URL=… E2E_API_URL=… GLIMPSE_DEMO_PASSWORD=… npm run test:e2e` | all journeys pass |

## 4. Rate limiting (not implemented anywhere yet)

The application has no throttling by design (BLOCKERS B-005): it belongs at the edge.
Measured locally: 25 wrong passwords in a row were each answered 401, none refused.
Configure at the load balancer / WAF / API gateway, per client IP:

| Path | Method | Suggested limit | Why |
|---|---|---|---|
| `/api/v2/auth/login` | POST | 10 per minute, 50 per hour | the demonstration password is shared; password guessing |
| `/api/v2/auth/register` | POST | 5 per minute, 30 per hour | account flooding |
| `/api/v2/meals/images` | POST | 10 per minute | 10 MB bodies; storage and CPU |
| `/api/v2/meals/*/analyze` | POST | 6 per minute | each call is a paid OpenAI request (the app also caps analyses per account per day: `…_VISION_DAILY_LIMIT`, default 40) |
| `/api/v2/cgm/import` | POST | 6 per minute | large CSV bodies |
| `/api/v2/demo/replay/reset` | POST | 6 per minute | rebuilds an account |
| everything else under `/api/` | any | a generous general limit (for example 600 per minute) | abuse |

Also at the edge: a request-body cap of about 12 MB, and HTTPS only.

Prove it on the deployed URL, and record the output:

```bash
# expect the first ~10 answers to be 401 and the rest 429
for i in $(seq 1 25); do
  curl -s -o /dev/null -w "%{http_code} " -X POST "$API/api/v2/auth/login" \
    -H 'content-type: application/json' \
    -d '{"email":"nobody@glimpse.example","password":"wrong-password-000"}'
done; echo
# a legitimate sign-in still works afterwards from another client, and from this one
# once the window has passed
```

Make sure the limiter keys on the real client address (`X-Forwarded-For` from the
load balancer), or every judge behind one proxy shares one bucket. Rate limiting is
**not** to be described as implemented until this output exists.

## 5. After deploying

```bash
python scripts/smoke_v2.py   "$API"
python scripts/smoke_demo.py "$API" --scenario new_user
python scripts/smoke_demo.py "$API" --scenario returning_user
E2E_BASE_URL="$APP" E2E_API_URL="$API" GLIMPSE_DEMO_PASSWORD=… npm run test:e2e
```

`GET $API/api/v2/ready` must show database, model_loaded and model_integrity all true,
and `GET $API/api/v2/status` must show `demo_mode: true` and the model SHA above. Both
demonstration cards must be on the public sign-in screen and both must sign in.

The browser journeys reset the demonstration accounts: run them before judges arrive,
then `python -m scripts.demo_reset` for each scenario.

## 6. Report back

Commit deployed; public URLs; outputs of §3, §4 and §5; the PostgreSQL version; the
worker count of the demonstration service.
