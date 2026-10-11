# GLIMPSE screen inventory

Every screen, what it shows, and where each value comes from. All paths are under
`/api/v2`. Nothing on any screen comes from a fixture.

| Screen | Route | Reads | Writes |
|---|---|---|---|
| Sign in / create account | `/auth` | `GET /status` (demo mode) | `POST /auth/login`, `POST /auth/register` |
| Onboarding (8 steps + completion) | `/onboarding` | `GET /me`, `GET /onboarding`, `GET /devices` or `GET /demo/devices`, `GET /alert-settings` | `PUT /consents/necessary_processing`, `PUT /consents/personalization_history`, `PATCH /me`, `PATCH /onboarding`, `PUT /alert-settings`, `POST /cgm/import`, `POST /demo/devices/{profile}/activate` |
| Home | `/` | `GET /cgm/status`, `GET /cgm/readings`, `GET /meals`, `GET /predictions`, `GET /alerts`, `GET /medication-reminders/due`, `GET /medications`, `GET /wearables/observations`, `GET /demo/replay` | — |
| Meals | `/meals` | `GET /meals` | — |
| Log a meal | `/meals/log` (`?meal=` resumes) | `GET /cgm/status`, `GET /meals/{id}`, `GET /meals/{id}/image` | `POST /meals/images`, `POST /meals/{id}/analyze`, `POST /meals`, `PATCH /meals/{id}/confirm`, `POST /meals/{id}/predict` |
| Meal, forecast and outcome | `/meals/$mealId` | `GET /meals/{id}`, `GET /predictions/{id}`, `GET /meals/{id}/outcome`, `GET /meals/{id}/image`, `GET /alert-settings` | `POST /meals/{id}/predict`, `DELETE /meals/{id}` |
| Insights | `/insights` | `GET /meals`, `GET /predictions`, `GET /meals/{id}/outcome` (each), `GET /status`, `GET /api/model/info` (v1, read-only) | — |
| Health · Data sources | `/health?tab=devices` | `GET /demo/devices` (demo) or `GET /devices` | `POST /demo/devices/{profile}/activate`, `POST /devices/{provider}/connect`, `DELETE /devices/{provider}` |
| Health · Glucose | `/health?tab=glucose` | `GET /cgm/status`, `GET /cgm/readings`, `GET /meals` | `POST /cgm/readings`, `POST /cgm/import` (hidden for demo accounts) |
| Health · Wearables | `/health?tab=wearables` | `GET /wearables/observations` | — |
| Health · Medications | `/health?tab=medications` | `GET /medications`, `GET /medication-reminders/due`, `GET /medication-doses`, `GET /medication-education` | `POST /medications`, `PATCH /medications/{id}`, `POST /medication-doses` |
| Health · Alerts | `/health?tab=alerts` | `GET /alert-settings`, `GET /alerts`, `GET /notifications` | `PUT /alert-settings`, `POST /alerts/{id}/acknowledge` |
| Settings | `/settings` | `GET /me`, `GET /onboarding`, `GET /status`, `GET /demo/replay` | `PATCH /me`, `PUT /consents/{type}`, `POST /auth/logout`, `DELETE /account` |
| Demo controls (sheet) | header, demo accounts only | `GET /demo/replay` | `POST /demo/replay/advance` (with `expected_event_index`), `POST /demo/replay/reset` |
| Notifications (popover) | header | `GET /alerts`, `GET /medication-reminders/due` | — |

## States each data screen handles

Loading (skeleton), empty (what is missing and the next action), error (plain-language
reason + Try again), server unreachable (full-screen with retry and sign out), session
expired (back to sign-in with a notice), consent withdrawn (explains and links to
Settings), partial (each panel loads and fails independently).

## Removed

`src/services/*`, `src/state/AppState.tsx`, `src/fixtures/developmentFixtures.ts`,
`src/types/contracts.ts`, the participant selector, meal presets, the local profile
store, client-side spike-alert arithmetic, and the live/fixture toggle. Alerts are now
whatever the server raised; the frontend computes none.

Bound in the client but not used by a screen: `GET /demo/health-timeline`.
Backend endpoints with no screen: `POST /predictions/{id}/reproduce`,
`PATCH /meals/{id}/consumption`, `DELETE /meals/{id}/image`, `GET /cgm/imports`,
`POST /devices/{provider}/sync`, `POST /wearables/observations` (bridge only).
