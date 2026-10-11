# GLIMPSE final comprehensive audit

**Verdict: LOCALLY VERIFIED — DEPLOYMENT CHECKS PENDING.**
Audited on 2026-10-11, branch `final-audit`. Companion documents: `BUG_REGISTER.md`,
`FINAL_TEST_MATRIX.md`, `RELEASE_BLOCKERS.md`, `HOME_DATA_FLOW_AUDIT.md`, `RUNBOOK.md`.

## 1. Baseline

| Ref | Commit | What it is |
|---|---|---|
| `origin/main` | `2f167f4` | Het's infrastructure + settings rename + patches to the **pre-redesign** frontend |
| `origin/release-integration` | `98b524b` | first release candidate (redesign + hypothetical-meal fix + E2E suite) |
| `release-integration` (local) | `a93d304` | + forecast copy, Home data-flow fixes |
| **`final-audit`** | see the final report | `a93d304` merged with `origin/main`, + audit fixes and documents |

`98b524b` is no longer the right baseline: three commits followed it locally and three
landed on `main`. `main`'s frontend commits patch screens the redesign replaces; the
redesign already does what they add (server-side sign-out, waiting for the onboarding
answer before redirecting, forecasts read from the API), so the redesigned files were
kept in the merge. `main`'s settings rename was taken unchanged. Nothing is deployed.

## 2. What the system is

| Layer | Pieces | Tests that cover it |
|---|---|---|
| Frontend (React, TanStack Start) | routes `/auth`, `/onboarding`, `/`, `/meals`, `/meals/log`, `/meals/$mealId`, `/insights`, `/health` (5 tabs), `/settings`, 4 redirects; shell, demo controls, notifications; typed API client; session; query hooks | 35 unit tests; 5 browser journeys (72 checks) |
| Backend v2 (FastAPI) | 52 protected operations + sign-in/registration/status: accounts, consent, onboarding, CGM, meals, vision, forecasts, outcomes, devices, wearables, medications, alerts, demo replay | full Python suite; `smoke_v2.py`, `smoke_demo.py`, `demo_verify` |
| Frozen model | fs-1.3 artifact, checksum-gated at start-up; v1 contract API | freeze verification; integrity tests |
| Data | SQLite locally, PostgreSQL in deployment; Alembic 0001–0003 | migration tests on SQLite; PostgreSQL **not run here** |
| Infrastructure | Dockerfile, compose, `infra/local/*` | **not run here** |

## 3. Findings

Eleven defects were found and fixed across the release work; three of them in this
audit (required-consent screens that loaded forever, double clicks recording two
doses, a deleted meal's page still looking live). Five items remain open, none above
P2. Details, reproduction and regression tests: `BUG_REGISTER.md`.

Root causes, grouped:

1. **A demonstration is one person's recording, and the code did not always know it**
   (BUG-001, BUG-002, BUG-008): same-instant timestamps, hypothetical meals treated as
   eaten, checkpoint lists indexed as if complete.
2. **Screens that assumed their request would be sent** (BUG-005): a request that is
   correctly withheld looked like one still loading.
3. **Trusting a disabled button** (BUG-006).
4. **A frontend written against an API that was imagined, not read** (BUG-011).

## 4. Verified working (in a browser, against the real backend)

* Registration, sign-in, sign-out, wrong password, expired session, per-tab sessions.
* Onboarding: all eight steps, validation, back/forward, skip, resume after reload and
  after sign-out, completion, revisit from Settings; nothing claimed saved that was not.
* Consent: two separate endpoints; withdrawing the required one stops every read and
  write (403) and every screen says why; re-granting restores data intact.
* Home: every component traced to its endpoint and compared with it
  (`HOME_DATA_FLOW_AUDIT.md`); meals and forecasts appear without a reload; measured
  glucose, wearables and medications do not move when a meal is logged.
* Meals: photo upload (raw bytes), draft resume, manual entry, validation, confirmation,
  forecast only after confirmation, deletion, refused forecast explained, failed
  analysis explained.
* Forecast: 25 points from the frozen model, tied to the confirmation it used; values
  sent = stored = model inputs; 30 g vs 90 g of carbohydrate give different curves from
  identical context.
* Demonstrations: both scenarios from the sign-in screen; one event per click; no
  reading from the replay's future; outcomes revealed and compared; personal history on
  at exactly five eligible meals; reset restores the start; replay end handled.
* Hypothetical meals: never scored, never history, cannot disturb a recorded meal.
* Simulated devices: off until clicked, labelled, never called connected.
* Medications: add, validate, reminder switch, Taken, Skip, Snooze, previous/current,
  persistence. Alerts: settings validation and save, history, acknowledge.
* Glucose: manual reading, Clarity CSV import, repeat import, broken file; mmol/L.
* Settings: name, timezone, units, account deletion.
* Isolation: another account gets 404 for meals, photos, forecasts and outcomes, and 403
  for demonstration controls.
* Layout at 390 px on 11 screens; server outage and recovery.

## 5. Verified by server tests only

Missing or wrong model artifact (server refuses to forecast); OpenAI timeouts and
provider errors; DST and timezone projection; LibreView import; real-provider OAuth
adapters against fixtures; migrations on SQLite.

## 6. Not verified

* Anything on PostgreSQL or in a container, on this build.
* A public deployment (none exists).
* Real OpenAI analysis on the final commit (passed on `98b524b`; code path unchanged).
* Reload during an image upload; reset while a request is pending; database outage.
* Safari, Firefox, real phones, screen readers.
* Real Dexcom/Fitbit/WHOOP/Apple Health connections (no credentials, no iOS app).

## 7. Scientific integrity

* Model artifact SHA-256 `12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe`,
  verified by the freeze check and by the server at start-up. No model code, weight,
  feature or retrieval rule was changed by the release or the audit.
* Contract v1.0: changed on `main` by the settings rename (one description line); see
  `RELEASE_BLOCKERS.md` "Things to confirm".
* Replay end-state fingerprints match the recorded ones for both scenarios, before and
  after every fix.
* The app shows no range, band or probability; says wearables and medications are not
  model inputs; says the model is fixed and automatic retraining is a future capability.

## 8. Security

Authentication is enforced on all 52 protected operations; sessions are revoked on
sign-out; sign-in does not reveal which emails exist; CORS admits only configured
origins; uploads are type- and size-checked in the browser and again on the server,
decoded, re-encoded and stripped of metadata; photos are served only to their owner;
the OpenAI key exists only in the backend's environment; logs carry no tokens,
passwords, keys or health values; only `.env.example` is tracked. **Open:** no
throttling of sign-in or uploads (OPEN-1), assigned to the deployment edge.

## 9. Known limitations

Light mode only · sessions end with the tab · glucose unit stored per browser · peak
time falls on a 30-minute checkpoint · demonstration replay needs a single backend
worker · photos stored on the backend's disk unless object storage is configured.
