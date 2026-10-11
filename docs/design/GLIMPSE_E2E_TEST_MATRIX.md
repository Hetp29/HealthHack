# GLIMPSE end-to-end test matrix

## Release run (branch `release-integration`)

The browser journeys now live in the repository under `e2e/` (`npm run test:e2e`, see
`e2e/README.md`). They were run in headless Google Chrome against the integrated
backend in demo mode with the frozen fs-1.3 artifact (integrity verified by the server).

| Journey | Result |
|---|---|
| `new-account` — register, separate consents, onboarding, dashboard, refused-forecast explanation, consent change in Settings, sign out/in, account deletion | 16/16 |
| `demo-new-user` — four simulated sources, genuine replay to active personal history (exactly five meals), hypothetical-meal isolation, photo upload without analysis, Taken and Skip with persistence, alerts (4 raised by the server, acknowledged), redirects, phone layout on 11 screens, session expiry | 18/18 |
| `demo-returning` — history in use, forecast uses it, earlier outcomes with provenance, reset | 5/5 |
| `photo-analysis` — one real OpenAI analysis (6.4 s), edit, confirm, forecast; exactly one request | 5/5 |
| `demo-new-user` again with the backend started **without** an OpenAI key | 18/18 (Analyze not offered; manual path works) |

Not covered by a browser journey: CSV import, manual glucose entry, snooze, "+30
minutes", timezone edit, real provider OAuth (unavailable anywhere), Safari/Firefox,
assistive technology. PostgreSQL and Docker were not available on the release machine.

The sections below are the earlier redesign-branch run, kept for the record.

Run on 2026-10-11 in headless Google Chrome (Playwright driving the installed browser)
against `uvicorn backend.app.v2.asgi:app` in demo mode with a scratch SQLite database,
the frozen fs-1.3 artifact (integrity verified by the server) and both demonstration
accounts seeded. Frontend served by `vite dev`. 1440×900 unless noted.

## Journey A — brand-new account (17/17 passed)

| # | Check | Result |
|---|---|---|
| A1 | Unauthenticated visit to `/` lands on `/auth` | pass |
| A2 | Demo cards show `demo@glimpse.example` and `demo-returning@glimpse.example`; no old identifier | pass |
| A3 | Registration validates name and password length inline | pass |
| A4 | Registration creates the account and opens onboarding with the user's name | pass |
| A5 | Required consent blocks progress; both consents sent as separate PUTs | pass |
| A6 | About you: implausible height rejected; values saved | pass |
| A7 | Health: diagnosis year and CGM questions appear only when relevant | pass |
| A8 | Data sources: no simulated devices offered, preferences saved | pass |
| A9 | Back keeps earlier inputs | pass |
| A10 | Medications and meals saved | pass |
| A11 | Reload returns to the same step | pass |
| A12 | Alert level outside 100–400 mg/dL rejected; valid level saved | pass |
| A13 | Review lists the entered values; Finish completes | pass |
| A14 | First dashboard: real empty states and "Getting started"; no horizontal overflow | pass |
| A15 | Server holds exactly what was entered (`{value, unit}`, `"unknown"`, consents, alert rule); no device connection was created | pass |
| A16 | Sign out and in again: straight to the dashboard, no repeated onboarding | pass |
| A17 | Wrong password explained | pass |

## Journey B — new-user demonstration (18/18 passed)

| # | Check | Result |
|---|---|---|
| B1 | Demo card fills the email only; sign-in goes straight to the dashboard; recording shown as "Day 1", never as a 2020 date | pass |
| B2 | All four simulated devices inactive; no "Authorized/Live/Disconnect" wording | pass |
| B3 | "Simulate connection" activates only Apple Watch (server confirms `demo_simulated`); wearables labelled "Simulated sample" | pass |
| B4 | Glucose tab: historical-recording badge, chart, data entry hidden | pass |
| B5 | Advance: first recorded meal logged and forecast | pass |
| B6 | Forecast: 25 points, table, peak equals the backend's, outcome "waiting", no observed values, "General forecast" | pass |
| B7 | Advance: outcome scored; observed overlay and comparison equal the backend's | pass |
| B8 | Hand-entered meal: real forecast, "No comparison", outcome endpoint never called | pass |
| B9 | Log a dose: stored as taken, appears in history | pass |
| B10 | Add a medication | pass |
| B11 | Alert settings: invalid level rejected, valid level stored | pass |
| B12 | Insights: "of the 5 needed", meal table, no retraining claim | pass |
| B13 | Settings → Review my answers opens onboarding in edit mode at the consent step; simulated devices offered, none auto-activated | pass |
| B14 | `/predict`, `/medications`, `/profile`, `/connect-devices` redirect | pass |
| B15 | Session survives reload; unknown address shows the not-found page | pass |
| B16 | 390×844: nine screens, zero horizontal overflow, bottom navigation | pass (after fixing an 11 px grid overflow found by this check) |
| B17 | Invalid token → sign-in with "Your session ended" | pass |
| B18 | Sign out clears the token | pass |

## Journey C — returning-user demonstration and reset

| # | Check | Result |
|---|---|---|
| C1 | Sign-in goes to the dashboard; "Forecasts use your history" | pass |
| C2 | Newest forecast has `retrieval_active` and shows "Uses your history · N earlier meals" matching the backend | pass |
| C3 | Meals lists the 31 earlier meals plus the presented one; Insights "In use" with the forecast/observed chart | pass |
| C4 | Advance shows the right notification and the scored comparison | pass |
| C5 | Reset: dialog explains, account rebuilt, signed out, sign-in works, 31 meals restored | pass |

## Journey D — photo meal with one real analysis (new-user demo account)

| # | Check | Result |
|---|---|---|
| D1 | A `.heic` file is refused in the browser before any upload | pass |
| D2 | Preview shown; no analysis request until the button is pressed | pass |
| D3 | One real OpenAI analysis (`gpt-4.1-mini`, 7.98 s) returns items, assumptions and prefilled editable values | pass |
| D4 | Edit a value → confirm → forecast; meal shows the photo and "AI estimate, edited by you" | pass |
| D5 | Exactly one `POST /analyze` in the whole run | pass (1) |

The photo was a local research-dataset image; it is not in the repository.

## Automated checks

| Check | Result |
|---|---|
| `npm ci` | passes (lock file repaired) |
| `npx tsc --noEmit` | clean |
| `npx vitest run` | 3 files, 30 tests passed |
| `npm run build` | succeeds |
| `npx eslint src` | 0 errors |
| `python -m research.freeze.verify_artifacts` | PHASE 1: PASS |

## Not tested

* Real provider OAuth, Apple Health, push/email: not available on any environment here.
* CSV import, manual reading entry, account deletion, consent withdrawal, snooze/skip of
  a scheduled dose, alert acknowledgement (no alert was raised), +30 minutes.
* Safari, Firefox, real phones, screen readers (structure and labels are in place; no
  assistive-technology session was run).
* Docker and PostgreSQL: neither is installed on this machine.
* The E2E scripts live outside the repository and are not part of `npm test`.
