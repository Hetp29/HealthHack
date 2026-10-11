# GLIMPSE frontend redesign — specification

Branch `frontend-redesign`, started from `main` at `cd2906e`. This describes what was
built. It changes no backend file, no model file and no contract.

## 1. What was wrong before

| Finding | Detail |
|---|---|
| `npm ci` failed on `main` | `package-lock.json` was out of sync with `package.json` (missing `@emnapi/*`). Re-synced. |
| Wrong demo identifier | The sign-in screen defaulted to `demo@glucotwin.example`; the backend seeds `demo@glimpse.example` and `demo-returning@glimpse.example`. |
| Bindings that did not match the API | `meal.macros` (real: `confirmation`), alert `level/headline/detail` (real: `payload{…}`), `personalization_progress.message` (real: `description`), `data.readings/meals` (real: `readings_released/_total`, `meals_logged/_total`). Errors read `body.detail`, so the server's `code` and `message` were lost. |
| Fixture fallback | A live/fixture toggle and `developmentFixtures.ts` could show invented data in place of the server's. Removed. |
| Consent granted silently | Signing in auto-granted the required consent. Consent is now two explicit choices sent to their own endpoints. |
| Stale naming and claims | Title "Lovable App", "GlucoTwin model", "Research prototype using synthetic data" on real recorded data, clinical-sounding "Patient overview". |
| `origin/het` is not in `main` | Het's 10 infrastructure commits (PostgreSQL verification, container smoke scripts) are unmerged. Not touched here; reported only. |

## 2. Design system

Tokens live in `src/styles.css` as CSS variables exposed to Tailwind; components use
semantic names only.

| Token | Value | Use |
|---|---|---|
| `background` | `#F5F7FA` | page |
| `card` | `#FFFFFF` | surfaces |
| `primary` / `primary-hover` | `#176B68` / `#125B58` | actions, active navigation |
| `foreground` / `muted-foreground` | `#152A35` / `#536471` | text / secondary text |
| `predicted` | `#176B68` (teal) | every forecast value and line |
| `observed` | `#4F46E5` (indigo) | every measured glucose value and line |
| `warning*` | amber | informational warnings and the alert level only |
| `destructive`, `success` | red, green | errors, confirmed states |

Type: Inter/system stack, 15 px body, 13 px secondary, tabular numerals for values.
Radius 12 px (cards 16 px). 44 px minimum tap targets on primary controls. One focus
ring (2 px primary). Reduced-motion respected. Light mode only.

Shared components (`src/components/glimpse/primitives.tsx`): `PageHeader`, `Panel`,
`Stat`, `Pill`, `SourceBadge`, `Notice`, `EmptyState`, `ErrorState`, `SkeletonLines`,
`SkeletonBlock`, `Async` (one query's loading/error/data), `Disclosure`, `KeyValue`,
`Field`, `ChoiceGroup`, `Logo`. Dialogs, sheets, menus and switches are the existing
shadcn kit. No dependency was added to the application (`playwright-core` is a dev
dependency used only for browser testing).

### Data provenance

`SourceBadge` labels anything that is not the user's own measurement:
*Historical recording* (`research_dataset`), *Sample data* (`demo`), *Simulated sample*
(simulated wearables), *Your data* (`user_device`). A demonstration account also shows
a permanent *Historical replay* pill with the recording time in the header.

### Time

A demonstration account replays a 2020 recording. Its timestamps are shown as
"Day N, h:mm" of the recording (`src/lib/time.ts`) and never as calendar dates, so old
data cannot be mistaken for today's. Other accounts see real time in their own timezone.

## 3. Structure

```
src/lib/api.ts        typed v2 client, ApiError{status,code,message,details}, plain-language errors
src/lib/types.ts      response types written from contracts/v2
src/lib/session.tsx   token, /me, /onboarding, /status, /demo/replay; the account's clock
src/lib/queries.ts    one react-query hook per read
src/lib/units.tsx     mg/dL ↔ mmol/L display preference (this device only)
src/components/shell  AppShell (sidebar, header, bottom nav, notifications, account), DemoControls
src/components/charts GlucoseHistoryChart, ForecastChart
src/features          onboarding model, device cards, forecast/outcome views, meal form, health tabs
src/routes            /auth /onboarding / /meals /meals/log /meals/$mealId /insights /health /settings
```

Navigation: Home, Meals, Insights, Health (Data sources · Glucose · Wearables ·
Medications · Alerts), Settings. Sidebar from 1024 px, bottom navigation below.
Old addresses redirect: `/predict`→`/meals/log`, `/medications`→`/health?tab=medications`,
`/profile`→`/settings`, `/connect-devices`→`/health?tab=devices`.

The gate (`src/routes/__root.tsx`): no token → `/auth`; signed in and
`onboarding.completed` false → `/onboarding`; otherwise the app. A 401 on any protected
call ends the session and returns to `/auth` with an explanation.

## 4. Onboarding

`/auth` carries the product introduction and registration/sign-in. `/onboarding` then
runs eight steps with a progress bar, "why we ask" notes, Back, Skip (optional steps)
and Save and continue:

1. Welcome 2. Your permission 3. About you 4. Glucose and health 5. Your data sources
6. Medications and meals 7. Alerts and units 8. Review → completion screen with next
actions → dashboard with a "Getting started" checklist driven by real account state.

Rules it keeps:

* Required and optional consent are separate switches, sent as separate
  `PUT /consents/{type}` calls. The flow cannot continue without the required one.
* Each step saves with `PATCH /onboarding` (merge). Only server-recognised question
  ids are sent; measurements are `{value, unit}` or `"unknown"`; answers hidden by an
  earlier choice are cleared with `null`. Nothing is called saved unless the server
  accepted it; a failed save keeps the inputs and offers a retry.
* Alert preferences are written to both the questionnaire and the real
  `PUT /alert-settings`. The glucose display unit is a device preference and says so.
* Naming a medication is a profile note; it does not create a reminder, and the screen
  says so and points to Health → Medications.
* No device is activated automatically. An ordinary account is told plainly that direct
  provider connections are not available on the server; a demonstration account is
  offered "Simulate connection" buttons that do nothing until pressed.
* Progress survives a reload (the step is remembered; saved answers come from the
  server). An account whose onboarding is complete never sees it again, and can reopen
  it from Settings → Health profile in an "edit" mode.

Questions the server accepts but the app does not ask yet: `clinician_plan`,
`medication_schedule`, `discontinued_medications`, `meal_time_ranges`,
`wearable_permissions`.

## 5. Meal → forecast

Photo: choose or take → type/size check in the browser (JPEG/PNG/WebP, ≤ 10 MB; HEIC
explained) → preview → **Analyze this photo** (upload as raw bytes, then one analysis)
→ items, assumptions, things to check → editable carbs/protein/fat/fibre/calories with
the estimate's ranges → **Confirm and forecast** → forecast screen. Manual entry skips
to the form. The forecast is requested only after confirmation.

Paid analysis runs only from an explicit button press, never on load and never as an
automatic retry; the photo is uploaded once and a retry re-asks only for the analysis,
which the server makes idempotent. An unfinished meal can be resumed from the Meals list.

If the forecast is refused (for example no glucose reading in the 15 minutes before the
meal) the meal stays saved and the screen says why, with retry and a link to add data.

## 6. Forecast and outcome

`ForecastView` draws the 25 returned points (0–120 min) in teal, with peak, peak change,
time to peak, the four horizons, the user's alert level when one applies, the
personal-history state and eligible-meal count, reading age at forecast time, the
server's limitations, a table of the same values, and "How this forecast works". No band,
range or probability is drawn: the model's uncertainty is not calibrated.

Observed glucose (indigo) is overlaid only when `GET /meals/{id}/outcome` returns
`scored`. Before that the screen says it is waiting. In a demonstration account, a meal
the user typed in is never compared with the recording, because the recorded glucose
belongs to what the participant actually ate; the outcome endpoint is not even called.

## 7. Known limitations

* Light mode only.
* The glucose unit preference is stored in the browser, not on the account.
* Real provider connections (Dexcom, Fitbit, WHOOP, Apple Health) cannot be exercised:
  the server has no credentials and there is no iOS app. The cards say so.
* Push and email notifications do not exist; only in-app alerts are offered.
* Recognising a demonstration account costs one `GET /demo/replay` that returns 403 for
  ordinary accounts on a demo-mode server (visible in the browser console).
* A hand-entered meal in a replay is still scored by the backend against the recording
  and may count towards personal history there; the frontend only declines to show it.
* Insights requests one outcome per forecast meal (32 requests for the returning demo).
