# Home dashboard data-flow audit

What each thing on Home is, where it comes from, and what should and should not move
when a meal is logged. Verified in Chrome against the real backend with the frozen
fs-1.3 artifact by `e2e/journeys/meal-to-home.mjs`; the server-side binding is pinned by
`tests/v2/test_meal_to_forecast_binding.py`.

## The chain

```
photo ──POST /meals/images (raw bytes)──▶ meal (draft) + stored, re-encoded photo
      ──POST /meals/{id}/analyze───────▶ meal_estimates (the AI's numbers; never overwritten)
user edits the five values
      ──PATCH /meals/{id}/confirm──────▶ meal_confirmations (a new revision; the user's numbers)
      ──POST /meals/{id}/predict───────▶ feature_snapshots (exact model inputs) + predictions
                                          (names the confirmation_id it was made from)
Home ◀─ GET /meals, GET /predictions ── read those same rows
```

A forecast is refused (409) for a meal that is not confirmed. A forecast already made
keeps the values it was made from; editing the meal afterwards creates a new forecast.

## Metric map

| Home component | Source | Derived from | Confirmed meal changes it? | Changing macros changes it? | New forecast changes it? |
|---|---|---|---|---|---|
| Greeting, recording time | `GET /me`, `GET /demo/replay` | account, replay position | no | no | no |
| Glucose: latest reading, age, freshness, "ready to forecast" | `GET /cgm/status` | measured CGM | **no** | **no** | **no** |
| Glucose chart (indigo line) | `GET /cgm/readings` | measured CGM | **no** | **no** | **no** |
| Glucose chart meal markers | `GET /meals` | meals | yes (a marker at the meal's time) | no | no |
| Latest forecast: which meal, its nutrition, peak, time to peak, 60/120 min, history pill | `GET /predictions` joined to `GET /meals` | prediction + its meal | only once forecast | yes, via the new forecast | **yes** |
| Recent meals: time, nutrition, status, forecast peak | `GET /meals` (+ `GET /predictions` for the peak) | meals, predictions | **yes** | **yes** | yes (peak and status) |
| Getting started / Next in the replay | `GET /cgm/status`, `/meals`, `/predictions`, `/medications`, `/demo/replay` | counts | yes (ticks "log a meal") | no | yes (ticks "first forecast") |
| Forecast alerts | `GET /alerts` | server-evaluated alerts | no | no | only if the server raised one |
| Medications | `GET /medications`, `/medication-reminders/due` | medications | no | no | no |
| Personal history | latest forecast's `personalization` (else `GET /demo/replay`) | eligible scored meals as of that forecast | no | no | reflects the newest forecast's count |
| Wearables | `GET /wearables/observations` | simulated samples | **no** | **no** | **no** |

"Personal history" shows the eligible-meal count *as of the latest forecast*, which is
what that forecast could use. A meal scored since then appears in Insights at once and
in this count with the next forecast.

## What the audit run showed (new-user demonstration, recording time Day 1, 1:26 PM)

| | Confirmed by the user | Stored / fetched back | Model inputs (database) | Forecast |
|---|---|---|---|---|
| Recorded meal (replay) | 40 C · 76 P · 17 F · 12 Fi · 585 kcal | same | same | peak 123.5 at 60 min |
| Profile A (photo, values typed) | 30 C · 20 P · 12 F · 5 Fi · 308 kcal | same | same | peak 126.5 at 60 min; 117.5 / 126.5 / 121.3 / 121.6 |
| Profile B (manual) | 90 C · 20 P · 12 F · 5 Fi · 548 kcal | same | same | peak 144.5 at 60 min; 125.7 / 144.5 / 135.8 / 134.2 |

A and B were forecast at the same replay instant: both curves start at 108 mg/dL, all 37
model inputs other than carbohydrate and calories are identical, and the curves differ
by up to 18 mg/dL. Peak timing is the same because this model can only place a peak on
a 30-minute checkpoint. Every forecast carries the frozen artifact's SHA-256.

Home before → after confirming and forecasting profile A, without a page reload:

* **Changed:** Latest forecast (recorded meal, peak 124 → the photo meal, "30 g carbs ·
  20 g protein · 12 g fat · 308 kcal", peak 127); Recent meals (1 row → 2, the new one
  with its nutrition and "Forecast peak 127 mg/dL").
* **Unchanged, as they must be:** latest glucose (108), the glucose panel, the measured
  readings themselves, wearables, medications, personal history.
* Identical after a reload and after signing out and in.
* Before confirmation the uploaded meal appeared in Recent meals as "Nutrition not
  confirmed yet" with no forecast, and nothing else moved.

## What was fixed

1. **"Latest forecast" could show the wrong meal.** In a demonstration every forecast
   made at one replay position has the same timestamp and the server keeps no insertion
   order, so the newest was picked arbitrarily; a forecast for a deleted meal could also
   be shown. Home now shows only a meal's current forecast, prefers a meal the person
   added over the replay's at the same instant, and names the meal's nutrition so there
   is no doubt which meal it is.
2. **Recent meals did not show the forecast.** Each row now shows its forecast peak.
3. **Screens opened on old data for a moment after a change.** Meal changes now refetch
   exactly the meal, forecast, outcome and alert lists (including ones not on screen)
   before moving on, and nothing else. Replay and consent changes refetch everything.

## Remaining limits

* Two meals the person adds at the *same* replay position cannot be ordered by anyone:
  Home shows one of them, always the same one, described truthfully. A proper fix is an
  insertion-order column on the server, which needs a migration and was not made.
* Real OpenAI analysis was **not** re-run in this audit, by choice. The estimate →
  edit → confirm path is covered with the test double on the server and passed against
  the real model twice earlier on this branch.
* The analysis-failure check used the server's own daily-limit refusal (429), so no
  request left the machine. Provider errors (502/504) share the same screen path and
  are covered by server tests only.
