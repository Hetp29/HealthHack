# GLIMPSE — Rutgers HealthHack 2026 submission package

**GLIMPSE — Glucose Learning through Integrated Multimodal Prediction and Sensor
Evidence.** Log a meal, confirm what was in it, and see a forecast of your glucose for
the next two hours; then see how that forecast compared with what your sensor recorded.

| | |
|---|---|
| Source | <https://github.com/Hetp29/HealthHack>, branch `release-integration` |
| How to run it | [`RUNBOOK.md`](RUNBOOK.md) |
| Live URL | **none yet** — verified locally; public deployment is pending (see "Status") |
| Screenshots | [`screenshots/`](screenshots/), captured from the running application |
| Browser test report | [`../design/GLIMPSE_E2E_TEST_MATRIX.md`](../design/GLIMPSE_E2E_TEST_MATRIX.md) |

## What a judge can do

1. **Create an account**: choose separately whether GLIMPSE may process your data
   (required) and whether it may learn from your past meals (optional); answer a short,
   skippable questionnaire; arrive at a dashboard that tells you what to do next.
2. **Or open a demonstration** from the sign-in screen, with no devices or accounts:
   * *New user* — starts with no personal history. Step through a recorded day: each
     meal is forecast, then its real glucose response is revealed and compared. After
     five eligible meals, forecasts begin to draw on that personal history.
   * *Returning user* — starts with a week of scored meals, so personal history is in
     use from the first forecast.
3. **Simulate device connections** (Apple Health, Apple Watch, Fitbit, WHOOP) and see
   labelled sample data appear.
4. **Photograph a meal**: a real OpenAI vision model lists the foods and estimates the
   nutrition; you correct and confirm it; only then is it forecast. Manual entry always
   works, including when no AI key is configured.
5. **Medications and alerts**: mark a due dose taken or skipped, see the dose history,
   and see informational alerts when a forecast crosses the level you chose.

## What is real and what is not

| Category | In GLIMPSE | Label in the app |
|---|---|---|
| Authentic research measurements | Glucose readings and meal records from the public CGMacros dataset, replayed in order | "Historical recording", "Historical replay", "From the recording" |
| Genuine model inference | Every forecast: the frozen fs-1.3 model, checksum-verified at start-up | teal curve, "Forecast" |
| Real AI analysis | Nutrition estimates from a submitted photo (OpenAI) | "AI estimate" — always editable, never forecast unconfirmed |
| Simulated or fictional | Wearable observations, device "connections", the demo profile and its medications, the speed of time | "Simulated sample", "FICTIONAL DEMO RECORD" |

Nothing simulated is an input to the forecast. The model uses glucose history before the
meal, the confirmed nutrition, the meal's time, and — once five eligible meals exist and
the user has allowed it — the account's own earlier meal responses.

## The model

`glucotwin-fs13-blend-personalized` v0.1.0, feature set fs-1.3 (39 features): an equal
blend of ridge regression and histogram gradient boosting that predicts glucose change
at 30, 60, 90 and 120 minutes, drawn as a 25-point curve. Personalization is
retrieval-based: it compares a new meal with the account's own earlier scored meals. The
model is **frozen** — it is never retrained, tuned or replaced by the application, and
the server will not forecast if the artifact's SHA-256 is not
`12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe`.

Scope, stated plainly:

* A research prototype. Informational only: not a diagnosis, not medical advice, not a
  dosing aid, and not a replacement for a CGM's own alarms.
* **Its uncertainty is not calibrated**, so the app shows no range, band or probability.
* It was evaluated retrospectively on research data. That is not clinical validation,
  and the handful of meals in the demonstration are illustrations, not a result.
* Evaluation method and limits: `docs/validation.md`, `docs/methodology.md`.

## The photo pipeline

Browser checks type and size → raw bytes to the backend → the image is decoded,
re-encoded and stripped of metadata → the photo alone (nothing about the user) goes to
the vision model → a structured estimate with items, portions, ranges and assumptions
comes back → the user edits and confirms → only the confirmed values reach the
forecast. The key lives only on the backend. One analysis per photo; no automatic
retries.

## Architecture

```
Browser (React, TanStack Start)  ──HTTPS──▶  FastAPI
  /auth /onboarding / /meals …                ├─ /api/v2  accounts, consent, meals, CGM, devices,
                                              │           medications, alerts, demo replay
                                              ├─ /api     frozen Contract v1.0 (model info, forecast)
                                              ├─ frozen fs-1.3 artifact (checksum-gated)
                                              ├─ PostgreSQL (SQLite for local work), Alembic migrations
                                              └─ OpenAI vision (optional, backend-only key)
```

Planned cloud layout (Het): container image on AWS behind HTTPS, managed PostgreSQL,
secrets in the platform's secret store, one worker for the demonstration service. See
`docs/project/02_HET_AWS_Cloud_Deployment.md` and `infra/README.md`.

## Scientific-integrity safeguards worth knowing about

* **No look-ahead.** A forecast sees only data from before the meal; the replay releases
  recorded readings only up to its current position.
* **A meal you add to a demonstration is hypothetical.** It gets a real forecast, but
  the recorded participant never ate it, so it is never scored against the recording,
  never becomes personal history, and cannot alter any recorded meal's forecast, score
  or alert (decision D-019, enforced on the server and tested by replaying the scenario
  with and without such meals).
* **Simulated devices are never called connected or authorized**, and are switched on
  only by an explicit click.
* **Deterministic.** Resetting a demonstration and replaying it gives the same result.

## Verification on the release commit

| Check | Result |
|---|---|
| Backend test suite | 1028 passed, 1 skipped |
| Frozen artifact verification; Contract v1.0 checksum | pass; unchanged |
| Replay verification (`demo_verify`) | all checks passed; end state matches the recorded fingerprint |
| HTTP smoke: general, new-user demo, returning-user demo | all steps passed |
| Frontend: `npm ci`, typecheck, unit tests, build, lint | pass (30 tests; 0 lint errors) |
| Browser journeys (Chrome, real backend) | first-time user 16/16 · new-user demo 18/18 · returning demo 5/5 |
| Real OpenAI photo analysis from the browser | 5/5, exactly one call |
| Same demo journey with OpenAI switched off | 18/18 |
| Phone layout (390 px), 11 screens | no horizontal overflow |
| PostgreSQL and container checks | **not run for this commit** (no Docker or PostgreSQL server on the release machine); last recorded pass is Het's, in `infra/README.md`, on an earlier commit |
| Public deployment | **not done** |

## A five-minute walkthrough

1. Sign-in screen → **New user demonstration** → enter the demonstration password.
2. Home: point out *Historical replay* and the recorded glucose (indigo).
3. Health → Data sources → **Simulate connection** on Apple Watch → Wearables shows
   *Simulated sample*; note "not used by the forecast".
4. **Demo controls → Advance**: a recorded meal is logged and forecast (teal curve,
   peak, four checkpoints, "General forecast · 0 of 5").
5. **Advance** again: the recorded response appears over the forecast, with the
   comparison table.
6. **Log a meal → photo**: analyze, correct a value, confirm, forecast. Note "No
   comparison for this meal" — and why.
7. Sign out → **Returning user demonstration**: "Forecasts use your history"; Insights
   shows a week of forecast-versus-observed peaks.
8. Health → Medications: mark a dose taken. Health → Alerts: the informational alert
   rule and history.

## Status

* Working prototype: **verified locally** end to end in a real browser.
* Public HTTPS deployment: **pending** (Het). Until then, judges need the local
  start-up in the runbook, which requires the model artifact and demonstration bundles
  that are deliberately not in git.
* Demo recording: not made yet. Screenshots are in `screenshots/`.
* Hackathon submission upload: not performed by this work.

## Limitations

Light mode only · sessions end when the tab closes · the glucose display unit is stored
per browser · real Dexcom/Fitbit/WHOOP/Apple Health connections are not available (no
provider credentials, no iOS app) · no push or email notifications · demonstration
replay requires a single backend worker · predicted peak time can only fall on a
30-minute checkpoint, a known property of the four-point model.
