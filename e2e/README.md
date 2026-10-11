# GLIMPSE browser journeys

End-to-end tests that drive the real application in a real browser against a real
backend. Nothing is mocked. They are the release check for the working prototype.

| Journey          | What it proves                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `new-account`    | Register → separate consents → onboarding → dashboard → sign out/in, state persists, onboarding not repeated; a refused forecast is explained; the account deletes itself.                                                                                                                                                                                                                                                                                          |
| `demo-new-user`  | Simulated Apple Health / Apple Watch / Fitbit / WHOOP; genuine replay: forecast → outcome reveal → personal history active at exactly five meals; hypothetical-meal isolation; photo upload without analysis; medications (Taken, Skip, persistence); alerts; redirects; phone layout; session expiry.                                                                                                                                                              |
| `demo-returning` | History already in use; forecast uses it; earlier outcomes; reset restores the start.                                                                                                                                                                                                                                                                                                                                                                               |
| `meal-to-home`   | Photo upload → edit → confirm → forecast, checked at every step against the server: the edited values are what is sent, stored and forecast; Home shows the right meal and forecast without a reload and after reload and sign-in; measured glucose, wearables and medications do not move; two nutrition profiles give different forecasts from identical context; account isolation; a refused forecast and (optionally) a failed analysis show no false success. |
| `photo-analysis` | One **real, paid** OpenAI analysis → edit → confirm → forecast. Opt-in only.                                                                                                                                                                                                                                                                                                                                                                                        |

## Run

Start the backend in demo mode and the frontend (see `docs/release/RUNBOOK.md`), then:

```bash
export GLUCOTWIN_DEMO_PASSWORD='the server's demo password'
npm run test:e2e                      # everything except the paid test
npm run test:e2e -- demo-new-user     # one journey
```

| Variable                      | Default                 | Meaning                                                                                                                                               |
| ----------------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `E2E_BASE_URL`                | `http://localhost:5173` | the frontend                                                                                                                                          |
| `E2E_API_URL`                 | `http://localhost:8000` | the backend origin                                                                                                                                    |
| `GLUCOTWIN_DEMO_PASSWORD`     | —                       | the demonstration password the server was started with                                                                                                |
| `E2E_OPENAI`                  | unset                   | `1` runs the paid photo-analysis journey                                                                                                              |
| `E2E_MEAL_PHOTO`              | —                       | path to a JPEG/PNG/WebP you are allowed to send to OpenAI                                                                                             |
| `E2E_EXPECT_ANALYSIS_FAILURE` | unset                   | `1` adds the failed-analysis check to `meal-to-home`; start the backend with `GLUCOTWIN_VISION_DAILY_LIMIT=0` so it refuses without contacting OpenAI |
| `E2E_HEADED`                  | unset                   | `1` shows the browser                                                                                                                                 |
| `E2E_BROWSER_CHANNEL`         | `chrome`                | installed browser to drive; empty uses Playwright's Chromium                                                                                          |

## Things to know

- **The demo journeys reset the demonstration accounts** (through the real reset
  endpoint) before they start. Do not run them against a demonstration someone is
  presenting.
- `new-account` creates a throwaway account and deletes it at the end.
- The paid journey makes exactly one analysis request and never retries. No photograph
  is stored in this repository, and that journey saves no screenshots.
- Screenshots go to `e2e/.artifacts/` (git-ignored).
- A run that cannot test Demo Mode (no demo-mode server or no password) fails: Demo
  Mode is a release requirement.
