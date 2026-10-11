# GLIMPSE — running, verifying and demonstrating the prototype

Everything here was run on the release commit. Nothing below contains a secret; you
supply those through your own environment.

## What you need (none of it is in git)

| Item | Where it goes | Why it is not in git |
|---|---|---|
| Frozen model, 5 files | `ml/artifacts_cgmacros_fs13/` | model artifact; checksum-verified at start-up |
| Two demonstration bundles | `data/local/demo/` | derived from the CGMacros research dataset |
| `OPENAI_API_KEY` (optional) | backend environment only | secret; without it photo analysis is off and manual entry still works |
| A demonstration password | `GLIMPSE_DEMO_PASSWORD` | you choose it; there is no default |

The server refuses to start forecasting if the model's SHA-256 is not
`12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe`. It never trains or
substitutes a model.

> **Setting names.** A setting may be given as `GLIMPSE_X` or as the original
> `GLUCOTWIN_X`; if both are set, `GLIMPSE_X` wins. The frozen fs-1.3 engine itself reads
> only `GLUCOTWIN_*` and is not edited: `backend/app/env_aliases.py` copies each
> `GLIMPSE_X` onto `GLUCOTWIN_X` when the application starts (DECISIONS D-020). Lines that start with `#` in `.env` are comments and are ignored:
> `GLIMPSE_DEMO_MODE` and `GLIMPSE_DEMO_PASSWORD` must be uncommented to take effect.

## A. Run locally without Docker (what was verified for this release)

```bash
# 1. dependencies
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
npm ci

# 2. backend environment (demo mode, frozen model, a database of your choice)
export GLIMPSE_DEMO_MODE=1
export GLIMPSE_DEMO_PASSWORD='choose-at-least-10-characters'
export GLIMPSE_MODEL_ARTIFACT=ml/artifacts_cgmacros_fs13/model.joblib
export GLIMPSE_METRICS_PATH=ml/artifacts_cgmacros_fs13/metrics.json
export GLIMPSE_EXPECTED_MODEL_SHA256=12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe
export GLIMPSE_APP_DB_URL=sqlite:////absolute/path/glimpse.db      # or a PostgreSQL URL
export GLIMPSE_IMAGE_DIR=/absolute/path/meal-images
export GLIMPSE_CORS_ORIGINS=http://localhost:5173
# optional, backend only:  export OPENAI_API_KEY=...  GLIMPSE_OPENAI_VISION_MODEL=gpt-4.1-mini

# 3. database and both demonstrations
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.demo_seed --scenario new_user
.venv/bin/python -m scripts.demo_seed --scenario returning_user

# 4. backend: ONE worker (the replay keeps its position in-process-locked)
.venv/bin/uvicorn backend.app.v2.asgi:app --port 8000

# 5. frontend, in another terminal
VITE_API_BASE_URL=http://localhost:8000 npm run dev -- --port 5173
```

Open <http://localhost:5173>. Backend: <http://localhost:8000> (`/api/v2/ready`,
`/api/v2/status`).

## B. Run locally with Docker and PostgreSQL (Het's launcher)

```bash
infra/local/e2e_stack.sh up      # PostgreSQL 16 + backend image (demo, single worker) + frontend
infra/local/e2e_stack.sh down
```

It prints the URLs and writes the generated demonstration password to a 0600 file.
`infra/local/container_smoke.sh` and `infra/local/verify_postgres.sh` are the container
and PostgreSQL checks; their recorded results are in `infra/README.md`.

## Signing in

| | Email | Password |
|---|---|---|
| New-user demonstration | `demo@glimpse.example` | your `GLIMPSE_DEMO_PASSWORD` |
| Returning-user demonstration | `demo-returning@glimpse.example` | the same |
| Your own account | *Create account* on the sign-in screen | yours |

Both demonstration cards are on the sign-in screen whenever the server is in demo mode.

## Verify a build

```bash
# backend
.venv/bin/python -m pytest -q                         # full suite
.venv/bin/python -m research.freeze.verify_artifacts  # frozen artifact
shasum -a 256 contracts/openapi.yaml                  # be836dddaac5ed4c…  (Contract v1.0, as frozen)
.venv/bin/python -m scripts.demo_verify               # replay determinism and leak checks
.venv/bin/python scripts/smoke_v2.py   http://localhost:8000
.venv/bin/python scripts/smoke_demo.py http://localhost:8000 --scenario new_user
.venv/bin/python scripts/smoke_demo.py http://localhost:8000 --scenario returning_user

# frontend
npm ci && npx tsc --noEmit && npm test && npm run build && npx eslint src

# the real application in a real browser (resets the demonstration accounts)
npm run test:e2e
E2E_OPENAI=1 E2E_MEAL_PHOTO=/path/to/your/photo.jpg npm run test:e2e -- photo-analysis   # one paid call
```

## Resetting a demonstration

In the app: **Demo controls → Reset demonstration** (signs you out; sign in again).
From a terminal: `python -m scripts.demo_reset --scenario new_user|returning_user`.
A reset rebuilds that one demonstration account and touches nothing else.

## Deploying (owner: Het) — not done as part of this release

The release was verified locally. A public deployment still needs, and must be tested
through its real URL:

1. The backend image built with the genuine artifact (the build fails without it or
   with a wrong checksum), behind HTTPS.
2. PostgreSQL, with `alembic upgrade head` run once as a one-off task.
3. `GLIMPSE_DEMO_MODE=1`, a demonstration password from the secret store, both
   bundles mounted, and **exactly one** API worker/task for the demonstration service.
4. `GLIMPSE_CORS_ORIGINS` set to the frontend's public origin, and the frontend built
   with `VITE_API_BASE_URL` set to the backend's public origin.
5. `OPENAI_API_KEY` in the backend's secret store only.
6. `demo_seed` for both scenarios, then `smoke_v2.py`, `smoke_demo.py` (both) and
   `npm run test:e2e` with `E2E_BASE_URL` / `E2E_API_URL` pointing at the public URLs.

Demo Mode is a required feature of the release, not a test utility: a deployment in
which either demonstration cannot be opened from the sign-in screen is incomplete.

## Troubleshooting

* **`npm ci` fails with `ENOTEMPTY` while removing `node_modules`**: a local file-system
  race, seen once on macOS. Run it again.
* **The dev server dies with `EPERM` reading files under `~/Desktop`**: macOS file-access
  protection, seen repeatedly on the release machine. Run the frontend from a copy of
  the repository outside `~/Desktop`/`~/Documents`, or grant the terminal Full Disk Access.
* **Demo sign-in says the password is wrong after you changed it**: a demonstration
  account keeps the password it was seeded with. Re-seed into a fresh database, or
  reset with the old password still configured.
* **The browser tests cannot start Chrome**: a previous run may have left a headless
  Chrome behind; `pkill -f playwright_chromiumdev_profile` and run again.

## Known operational limits

* Demonstration replay needs a single backend worker.
* Meal photos are stored on the backend's disk (`GLIMPSE_IMAGE_DIR`) unless object
  storage is configured.
* Sessions live in the browser tab (`sessionStorage`): closing the tab signs you out.
