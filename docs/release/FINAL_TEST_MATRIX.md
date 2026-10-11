# GLIMPSE final test matrix

Run on branch `final-audit` (see `FINAL_COMPREHENSIVE_AUDIT.md` for the commit) on
2026-10-11. Browser runs: headless Google Chrome driving the real frontend against the
real backend in demo mode, frozen fs-1.3 artifact, integrity verified by the server,
isolated SQLite database. "NOT RUN" means not run; it does not mean passed.

## Automated suites

| Suite | Command | Result |
|---|---|---|
| Backend, full | `python -m pytest -q` | 1042 passed, 1 skipped (14 min 51 s) |
| Frozen artifact | `python -m research.freeze.verify_artifacts` | PHASE 1: PASS; model SHA-256 `12ed0602…15fe` |
| Replay verification | `python -m scripts.demo_verify` | ALL CHECKS PASSED; both end-state fingerprints match the recorded ones |
| HTTP smoke, general | `scripts/smoke_v2.py` | ALL STEPS PASSED |
| HTTP smoke, new-user demo | `scripts/smoke_demo.py --scenario new_user` | ALL STEPS PASSED |
| HTTP smoke, returning demo | `scripts/smoke_demo.py --scenario returning_user` | ALL STEPS PASSED |
| Frontend install | `npm ci` | passes (one local `ENOTEMPTY` race on first attempt; passed on rerun) |
| Frontend types | `npx tsc --noEmit` | clean |
| Frontend unit tests | `npm test` | 35 passed |
| Frontend build | `npm run build` | succeeds |
| Lint | `npx eslint src e2e` | 0 errors |
| Docker image build / container smoke | `infra/local/container_smoke.sh` | **NOT RUN** (no Docker) |
| PostgreSQL migrations and suite | `infra/local/verify_postgres.sh` | **NOT RUN** (no PostgreSQL server) |
| Public deployment | — | **NOT RUN** (none exists) |

## Browser journeys (`npm run test:e2e`)

| Journey | Covers | Result |
|---|---|---|
| `new-account` | A: registration validation, separate consents, every onboarding step, back/forward, reload resume, review, dashboard, refused forecast explained, consent change in Settings, sign-out/in, wrong password, account deletion | 16/16 |
| `demo-new-user` | B, D, G: demo sign-in, provenance, four simulated sources on click only, genuine forecast (25 points, frozen model), outcome reveal, hypothetical meals, photo upload without analysis, activation at exactly five eligible meals, replay to end, Taken and Skip with reload, alerts and acknowledge, onboarding revisit, redirects, 11 screens at 390 px, session expiry | 18/18 |
| `demo-returning` | E: history in use at sign-in, earlier outcomes with provenance, forecast uses history (count equals server's), Insights, reset restores 31 meals | 5/5 |
| `meal-to-home` | C: upload bytes, draft meal, edit, confirm, values sent = stored = model inputs, Home before/after against the server, reload and re-sign-in, two nutrition profiles from identical context, replay integrity, account isolation, refused forecast | 14/14 (15/15 with the failed-analysis check) |
| `edge-cases` | Phase 5 and H: double clicks (account, dose, replay), unfinished onboarding across sign-out, invalid and valid manual readings, Clarity CSV import (and a second import, and a broken file), mmol/L everywhere, negative/non-numeric/over-limit/zero nutrition, reload mid-submit, corrupt and oversized photos, meal deletion, medication validation/reminder switch/previous, profile and timezone, required-consent withdrawal (UI and API), server outage and recovery, second tab, account deletion, +30 minutes, no future readings visible, replay end, Snooze, demo endpoints refused to other accounts | 19/19 |
| `photo-analysis` | One real OpenAI analysis | **NOT RUN on this commit** (by choice); 5/5 on `98b524b` |

## Phase 5 edge cases

| # | Case | How it was exercised | Result |
|---|---|---|---|
| 1 | Double-clicking action buttons | E2E: create account, confirm meal, log dose, advance replay | pass (BUG-006 fixed) |
| 2 | Refresh during image upload | not exercised | NOT RUN |
| 3 | Refresh during prediction | E2E `edge-cases`: at most one meal, still forecastable | pass |
| 4 | Navigate away during a pending request | partly: draft meal resumed from Meals (`meal-to-home`) | partial |
| 5 | Same account in two tabs | E2E: second tab sees the first's meal; sign-out is per tab | pass |
| 6 | Expired authentication | E2E: invalid token → sign-in with explanation | pass |
| 7 | Backend unavailable | E2E: requests aborted in the browser; full-screen notice, Try again recovers | pass |
| 8 | Database unavailable | not exercised | NOT RUN |
| 9 | Missing model artifact | server tests (`test_deployment_guards`, `test_platform_integrity`) | pass (tests only) |
| 10 | Missing OpenAI key | E2E `demo-new-user` on a keyless backend (`98b524b`); server tests | pass |
| 11 | OpenAI timeout | server tests map it to 504 `vision_timeout`; same screen path as the 429 exercised in E2E | pass (tests only) |
| 12–14 | Invalid, oversized, corrupt images | E2E: HEIC and 10 MB+ refused in the browser, corrupt JPEG refused by the server with no stray meal; API probe: 11 MB connection refused | pass |
| 15–18 | Invalid, negative, very large, zero nutrition | E2E `edge-cases` | pass |
| 19–20 | Incomplete or missing CGM | E2E: forecast refused and explained; meal stays saved | pass |
| 21 | Empty account history | E2E `new-account` | pass |
| 22–24 | Four, five, more than five eligible meals | E2E on the real recording; server tests on the fixture | pass |
| 25 | Replay at its final event | E2E: controls disabled, further advance is a no-op | pass |
| 26 | Reset while a request is pending | not exercised | NOT RUN |
| 27 | Another user's records | E2E + API: 404 for meals, images, forecasts, outcomes; 403 for demo controls | pass |
| 28 | Incorrect or stale timestamps | future readings/meals refused in forms; server tests | pass |
| 29 | Network retries and partial failures | E2E: forecast failure after a saved meal; analysis failure after an upload | pass |
| 30 | Reload after every supported mutation | E2E: meals, doses, consent, profile, units, medications, replay | pass |

## Security probe (local, against the audit backend)

| Check | Result |
|---|---|
| All 52 protected operations without a token | 401 for every one |
| Forged token | 401 |
| Token after sign-out | 401 |
| Unknown email vs wrong password | identical answer (no account enumeration) |
| CORS preflight from a foreign origin | refused; configured origin allowed |
| Malformed JSON / path-like ids | 422 / 404, no stack trace in the body |
| Required consent withdrawn | 403 `consent_required` on reads and writes |
| Demo controls from an ordinary account / signed out | 403 / 401 |
| Server log after the full run | no tokens, passwords or keys; no glucose or nutrition values |
| Secrets tracked by git | only `.env.example` (placeholders) |
| Repeated wrong passwords | **not throttled** (OPEN-1) |
