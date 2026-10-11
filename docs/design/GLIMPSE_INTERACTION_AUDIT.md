# GLIMPSE interaction audit

Every control in the application and what it really does. "Browser-verified" means it
was exercised in Chrome against the running v2 backend (see the E2E matrix).

| Screen | Control | Effect | Verified |
|---|---|---|---|
| Auth | Sign in / Create account tabs | switches the form, clears errors | browser |
| Auth | Sign in | `POST /auth/login`; wrong password explained inline | browser |
| Auth | Create account | inline validation (name, email, ≥ 10 characters) then `POST /auth/register`; "email taken" offers sign-in | browser |
| Auth | Show/hide password | toggles field type | — |
| Auth | Demonstration cards | fill the demo email and focus the password; never a password | browser |
| Onboarding | Get started / Save and continue | validates, saves the step to the server, advances | browser |
| Onboarding | Back | previous step, inputs kept | browser |
| Onboarding | Skip for now | next step without saving (optional steps only) | browser |
| Onboarding | Consent switches | two separate `PUT /consents/{type}` on continue | browser |
| Onboarding | "I don't know" | stores `"unknown"` for that measurement | browser |
| Onboarding | Import readings | `POST /cgm/import` with the chosen file | unit-level only |
| Onboarding | Review → Edit | jumps to that step | — |
| Onboarding | Finish setup / Save changes | `PATCH /onboarding {completed: true}` | browser |
| Onboarding | Sign out / Close | ends session / returns to Settings | browser (Close) |
| Shell | Navigation (sidebar, bottom bar) | route change, `aria-current` | browser |
| Shell | Log a meal | `/meals/log` | browser |
| Shell | Historical replay pill, Demo controls | open the demo sheet | browser |
| Shell | Bell | lists active alerts and due reminders, links to them | rendered |
| Shell | Account → Settings / Sign out | navigate / `POST /auth/logout` and clear token | browser |
| Demo controls | Advance to next event | `POST /demo/replay/advance {expected_event_index}`; a stale index is a no-op and the sheet shows the true position | browser |
| Demo controls | +30 minutes | same endpoint with `minutes: 30` | — |
| Demo controls | Reset demonstration | confirm dialog explaining data loss and sign-out → `POST /demo/replay/reset` → signed out | browser |
| Home | 3 h / 6 h / 24 h | re-queries `GET /cgm/readings` | rendered |
| Home | panel links | navigate | browser |
| Meals | row | opens the meal, or resumes an unfinished one | browser |
| Log a meal | Take a photo / Choose a photo | file picker; type and size checked before upload | browser |
| Log a meal | Analyze this photo | upload once, then one `POST /analyze` | browser (1 real call) |
| Log a meal | Save photo, enter values myself | upload only, no analysis | — |
| Log a meal | Try again (after a failed analysis) | re-asks for analysis on the same meal; manual, never automatic | — |
| Log a meal | Enter nutrition | manual form | browser |
| Log a meal | Confirm and forecast | validate five values → create/confirm → `POST /predict` | browser |
| Log a meal | Start over | clears the draft on screen | — |
| Meal | Get the forecast | `POST /predict` for a confirmed meal without one | — |
| Meal | View these values as a table | accessible table of the curve | browser |
| Meal | Delete | confirm dialog → `DELETE /meals/{id}` (hidden for recorded replay meals) | — |
| Health · Data sources | Simulate connection | `POST /demo/devices/{profile}/activate`; only that profile | browser |
| Health · Data sources | Connect / Disconnect (real providers) | shown only when the server reports the provider configured / linked | not reachable locally |
| Health · Glucose | range buttons | re-query | rendered |
| Health · Glucose | Add reading / Import readings | `POST /cgm/readings` / `POST /cgm/import` | — |
| Health · Medications | Taken / Snooze 15 min / Skip | `POST /medication-doses` with a unique `client_event_id` | browser (Taken, unscheduled) |
| Health · Medications | Reminders switch | `PATCH /medications/{id} {reminders_enabled}` | — |
| Health · Medications | Add medication → Save | `POST /medications` | browser |
| Health · Medications | Move to previous / back to current | `PATCH … {status}` | — |
| Health · Alerts | Save alert settings | validates 100–400 mg/dL → `PUT /alert-settings` | browser |
| Health · Alerts | Acknowledge | `POST /alerts/{id}/acknowledge` | no alert was raised in the run |
| Settings | Save profile | `PATCH /me` | — |
| Settings | Review my answers | onboarding in edit mode | browser |
| Settings | Consent switches | immediate `PUT /consents/{type}`; withdrawing the required one asks first | — |
| Settings | Units | device preference | — |
| Settings | Sign out | ends session | browser (via menu) |
| Settings | Delete my account | password dialog → `DELETE /account` (hidden for demo accounts) | — |

## Deliberately absent

* No "Disconnect" on simulated devices: the backend has no such endpoint.
* No "Authorized", "Connected" or "Live" wording on anything simulated or replayed.
* No read/unread state beyond alert acknowledgement, which is all the backend persists.
* No confidence band, probability or "accuracy %" on any forecast.
* No control that changes the forecasting model, its policy or calibration.

Rows marked "—" are implemented against real endpoints and type-checked but were not
exercised in the recorded browser runs.

## Release update

Now exercised by the repository's browser journeys (`e2e/`): all four **Simulate
connection** buttons, **Save photo, enter values myself**, a due dose's **Taken** and
**Skip** (with a reload to confirm persistence), alert **Acknowledge**, the optional
consent switch in Settings, **Delete my account**, and the refused-forecast recovery
on Log a meal. The outcome of a meal added to a demonstration is now decided by the
server (`not_applicable`), not by the frontend.

Still implemented but not browser-exercised: CSV import, Add reading, Snooze, +30
minutes, Save profile, withdrawing the required consent, medication reminders switch,
Move to previous, Start over, real-provider Connect/Disconnect (unreachable locally).
