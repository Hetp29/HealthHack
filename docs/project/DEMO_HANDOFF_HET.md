# Demo handoff to Het — running the demonstration backend

From Ruhaan. What a deployment needs so the sample-data demonstration runs. Nothing
here provisions anything, and nothing here has been run on AWS or in a container —
it is verified on a local machine only. Scenario details: `DEMO_SCENARIO_SPEC.md`.
General deployment requirements: `HANDOFF_HET.md` (still current).

## 0 · Two scenarios

| scenario | flag | account | starts with | data file |
|---|---|---|---|---|
| **NEW USER** (default) | *(none)* or `--scenario new_user` | `demo@glucotwin.example` | no meals; history builds over nine meals | `cgmacros_demo_bundle.json` |
| **RETURNING USER** (optional) | `--scenario returning_user` | `demo-returning@glucotwin.example` | 31 earlier meals already replayed; personal history active | `cgmacros_returning_user_bundle.json` |

They are separate accounts with the same password (`GLUCOTWIN_DEMO_PASSWORD`). Seed
either or both; they never share state, and resetting one does not touch the other.
The returning-user scenario is optional — if its bundle is not installed, it reports
`demo_data_unavailable` and the default scenario works normally.

## 1 · What the demo is, operationally

One fictional account replays a historical research recording through the normal
API. "Now" for that account is a simulated replay position; every other account is
unaffected. It adds **one table** (`demo_replay_state`, migration `0002`), **three
gated routes** and **no new service**.

## 2 · Two files that are not in git

The artifact and the NEW USER bundle are required; the RETURNING USER bundle is
needed only for that scenario. None is ever substituted; if one is missing or wrong
the backend refuses and says why.

| file | what | size | SHA-256 | how to get it |
|---|---|---|---|---|
| `ml/artifacts_cgmacros_fs13/model.joblib` (+ the 4 sibling files) | frozen fs-1.3 artifact | 440 KB | `12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe` | from Ruhaan; see `HANDOFF_HET.md` §4 |
| `data/local/demo/cgmacros_demo_bundle.json` | the selected participant's readings and meal records | 87 KB | canonical checksum `4ef9dbd8193cafee700ff8599059346928262ef8baa05c19ee8287196e1efe22` | from Ruhaan, or rebuild with `python -m scripts.demo_select` where the CGMacros warehouse exists |
| `data/local/demo/cgmacros_returning_user_bundle.json` *(optional)* | the same participant's whole recording up to the final presented meal: 40 meal records | 94 KB | canonical checksum `6a1376eb7c348e7e28f4fbe48faebd05716b917c2dda87937736e675a3c6e2d5` | from Ruhaan, or `python -m scripts.demo_select --scenario returning_user` |

The bundle checksum the backend enforces is the **canonical content hash** recorded
in the committed manifests (`backend/app/v2/demo/manifest.json` and
`manifest_returning_user.json`, field `bundle_sha256`), not the file's byte hash — do not compare it with `shasum`. `python -m scripts.demo_verify` is the
check.

**The bundle is research data.** CGMacros is a de-identified public research
dataset, but its redistribution terms are the dataset owner's, not ours to assume.
Treat the bundle like the artifact: private storage, no public URL, not baked into a
public image, not committed. It contains no names or contact details — a participant
code, date-shifted timestamps, glucose values and meal macros.

The 568 MB warehouse is **not** needed in a deployment. Only the bundle is.

## 3 · Environment variables (demo-specific)

| name | secret? | value |
|---|---|---|
| `GLUCOTWIN_DEMO_MODE` | no | `1` — **only in the demo environment** |
| `GLUCOTWIN_DEMO_PASSWORD` | **yes** | you choose it (10+ chars); give it to Neil for the demo login |
| `GLUCOTWIN_DEMO_BUNDLE` | no | path to the bundle; default `data/local/demo/cgmacros_demo_bundle.json` |
| `GLUCOTWIN_DEMO_EMAIL` | no | optional; default `demo@glucotwin.example` |
| `GLUCOTWIN_DEMO_RETURNING_BUNDLE` | no | path to the returning-user bundle; default `data/local/demo/cgmacros_returning_user_bundle.json` |
| `GLUCOTWIN_DEMO_RETURNING_EMAIL` | no | optional; default `demo-returning@glucotwin.example` |

Plus the standing ones, which the demo **requires**:

```
GLUCOTWIN_MODEL_ARTIFACT=ml/artifacts_cgmacros_fs13/model.joblib
GLUCOTWIN_METRICS_PATH=ml/artifacts_cgmacros_fs13/metrics.json
GLUCOTWIN_EXPECTED_MODEL_SHA256=12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe
GLUCOTWIN_APP_DB_URL=…
GLUCOTWIN_CORS_ORIGINS=<the frontend origin>
```

Do **not** set `GLUCOTWIN_PERSONALIZATION_POLICY`. The demo refuses to run with
Level B enabled. `OPENAI_API_KEY` is **not** needed: the demo uses recorded meals.

## 4 · Initialization order

```bash
# 0. Place the artifact directory and the bundle file (not in git).
# 1. Schema.
alembic upgrade head
# 2. Prove the demo works here, in a scratch database (touches nothing real).
#    Verifies every scenario whose bundle is installed: 28 checks for NEW USER,
#    32 for RETURNING USER. Exit 0 only if all pass.
python -m scripts.demo_verify
# 3. Create the demo account(s).
python -m scripts.demo_seed                              # NEW USER  (or: --to meal_06_outcome)
python -m scripts.demo_seed --scenario returning_user    # optional; ~5 s locally (replays 31 meals)
# 4. Start the server (the container command already does the artifact gate).
python -m backend.app.v2.verify_artifact && \
  uvicorn backend.app.v2.asgi:app --host 0.0.0.0 --port ${PORT:-8000}
# 5. Verify over HTTP.
python scripts/smoke_demo.py https://<demo-url> --leave-at meal_06_outcome
python scripts/smoke_demo.py https://<demo-url> --scenario returning_user     # if seeded
```

Steps 2, 3 and 5 need `GLUCOTWIN_DEMO_PASSWORD` exported; steps 2 and 3 run inside
the image (`python -m scripts.…` — `scripts/` is now copied into it).

`demo_seed` is idempotent: if the demo account exists it is left exactly as it is.

## 5 · Commands

Every demo command takes `--scenario new_user|returning_user` (default `new_user`).

| command | does | touches the real database? |
|---|---|---|
| `python -m scripts.demo_seed [--scenario S] [--to CP]` | create that scenario's account at the start of its presented journey (or advance to `CP`) | yes — creates one account |
| `python -m scripts.demo_reset [--scenario S] [--to CP]` | delete and re-create that scenario's account | yes — that one account only |
| `python -m scripts.demo_verify [--scenario S\|all]` | full replay twice + reset in a temp database | **no** |
| `python scripts/smoke_demo.py URL [--scenario S] [--leave-at CP]` | full replay over HTTP against a running server; resets before and after | yes — that scenario's account only |
| `python -m scripts.demo_select [--scenario S]` | rebuild a bundle + manifest from the warehouse | no (writes the two files) |
| `python scripts/smoke_v2.py URL` | the existing 21-step smoke test | creates and deletes a throwaway account |

Checkpoints, NEW USER: `meal_01_forecast` … `meal_09_outcome`, or the aliases
`retrieval_activation`, `final_meal`, `end`. RETURNING USER numbers the whole
recording, so its presented meals are `meal_32_forecast` … `meal_40_outcome`
(aliases `first_display_meal`, `final_meal`, `end`); seeding leaves it just before
`meal_32_forecast`.

## 6 · Reset safeguards

* Reset acts on **one scenario's account**: the one whose email is that scenario's
  demo email **and** which has a replay-state row. Resetting NEW USER never touches
  RETURNING USER, or the reverse (tested both ways, including through the API). If an ordinary account holds the demo email, seed and reset
  refuse (`demo_email_in_use`) and delete nothing.
* Deletion goes through the same account-deletion path a user would use: that
  user's rows by foreign-key cascade. Tested: another account's readings, meals and
  forecasts are byte-identical before and after resets.
* Replay routes act only on the caller's own account, and only if it is the demo
  account (`403 not_demo_account` otherwise).
* The replay clock applies to the demo account's requests only. Other accounts stay
  on the wall clock, and the three background workers skip the demo account.

## 7 · Where demo mode must not be on

With `GLUCOTWIN_DEMO_MODE` unset, the `/api/v2/demo/*` routes are **not registered**
— they are 404, absent from `/api/v2/openapi.json`, and `GET /api/v2/status` reports
`"demo_mode": false`. Keep it unset in any environment that holds, or might hold,
real users. A startup warning (`demo_mode_enabled`) is logged when it is on.

One process, please: advancing the replay is serialized by an in-process lock, so
run the demo environment as a **single worker / single container**. Two workers
could interleave two advance requests.

## 8 · Database and storage

* Any database the backend supports. NEW USER is ~2,600 glucose rows, 9 meals and 9
  forecasts with snapshots; RETURNING USER adds another ~2,600 rows, 40 meals and 40
  forecasts. A few megabytes in total.
* **PostgreSQL is still unverified** (BLOCKERS B-007), now including migration `0002`
  and the replay. Running `python -m scripts.demo_verify` uses its own SQLite scratch
  file, so it does not test your PostgreSQL; `smoke_demo.py` against the deployed
  server does.
* No object storage is needed: the demo uploads no photos.
* Ephemeral storage is fine. If the database is wiped, `alembic upgrade head` +
  `demo_seed` restores the demo exactly.

## 9 · Must be in the deployed image

Already handled by the `Dockerfile` on this branch (never built — B-004):
`backend/` (includes `backend/app/v2/demo/manifest.json`), `scripts/`, `workers/`,
`migrations/`, `alembic.ini`, `ml/`, `data/` (code only), the artifact directory.
The manifests for both scenarios are in `backend/app/v2/demo/`. The **bundles are
not copied by the Dockerfile** (`.dockerignore` excludes `data/local`): mount them,
or fetch them at start, and point `GLUCOTWIN_DEMO_BUNDLE` (and, if used,
`GLUCOTWIN_DEMO_RETURNING_BUNDLE`) at them.

## 10 · What I need back

1. The demo URL and frontend origin.
2. Confirmation that `smoke_demo.py` passes against the deployed server (it has only
   ever passed against `127.0.0.1`).
3. Where you want the bundle and artifact delivered.

## 11 · Known limitations

* Verified on one machine, SQLite, single process. Not in a container, not on
  PostgreSQL, not on a deployed server.
* Single worker only: the advance lock is in-process.
* Seeding RETURNING USER runs 31 forecasts (about 5 s here); allow for it in any
  startup or health-check timing, and seed before taking traffic.
* The end-state fingerprints recorded in the manifests were computed on this
  machine. `demo_verify` prints whether yours matches as a NOTE, not a failure —
  floating-point results can differ across platforms. The checks that matter
  (reproducibility, chronology, counts) are platform-independent.
