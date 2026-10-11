# Demonstration scenario specification

> Two scenarios exist. This document describes the default, **NEW USER**, in §1–§12.
> The optional **RETURNING USER** scenario is §13. They use separate accounts and
> never share state.

**Scenario id:** `cgmacros-out-of-fit-replay-v1` · **Owner:** Ruhaan · **Status:** implemented and verified locally
**Machine-readable source of truth:** `backend/app/v2/demo/manifest.json` (committed metadata)

The demonstration replays one participant's genuine CGMacros recording, in order,
through the real v2 API and the frozen fs-1.3 model. It is **not live**, it is **not
this account's own data**, and it is **not a validation result**. Everything below
was produced by running it; nothing is a mock-up.

## 1 · What is real and what is not

| element | nature | stored as |
|---|---|---|
| Glucose readings | **Real.** Dexcom G6 Pro readings from CGMacros v1.0.0, value-for-value and time-for-time | `data_class: research_dataset`, provider `research_cgmacros` |
| Meal times and macronutrients | **Real.** The dataset's meal records | meals with a note naming the source record |
| Forecasts | **Real model output.** Frozen fs-1.3, checksum enforced, computed at each replay instant | ordinary `predictions` rows |
| Outcomes, eligibility, personal history | **Real application behaviour** on the above | ordinary `response_episodes` rows |
| Alerts | **Real** output of the existing server-side alert engine | ordinary `alert_events` rows |
| Passage of time | **Simulated.** The replay position advances on command | `demo_replay_state.position` |
| The account, its name and questionnaire answers | **Fictional** | display name "Demo User (fictional)" |
| Medications, reminders, dose history | **Fictional**, visibly labelled | names end "(fictional)"; note says FICTIONAL DEMO RECORD |
| Alert threshold (140 mg/dL) | A **user-selected sample setting** | ordinary alert rule |
| Device connection | **None.** No provider is connected or shown as connected | connection status never `connected` |

No clinical value is invented: the questionnaire's HbA1c and fasting glucose are
`"unknown"` rather than made up beside real glucose data.

## 2 · Demo account identity

| | |
|---|---|
| Email | `demo@glimpse.example` (`GLUCOTWIN_DEMO_EMAIL`) |
| Password | the value of `GLUCOTWIN_DEMO_PASSWORD` — chosen by whoever seeds; no default exists |
| Display name | `Demo User (fictional)` |
| Timezone | `UTC` (see §4) |
| Participant id | a normal generated `gtp_…` id. **Never** the research participant's id |
| Consents | `necessary_processing` and `personalization_history` both granted |

## 3 · Data provenance and selection

**Source.** CGMacros v1.0.0, read from the local research warehouse
(`data/local/cgmacros_v2.db`) built by the repository's documented pipeline. Nothing
is downloaded. If the warehouse or the bundle is absent the commands stop with setup
instructions; no substitute is generated.

**Selected participant: `cgmacros_001`.** Chosen by a fixed rule that reads
timestamps and macro completeness only — never a glucose value, a forecast, or a
forecast error:

1. Consider only meals **at or after the participant's Day-8 fit cutoff** (below).
2. Keep participants whose such meals can show the whole progression: no history →
   growing history → the retrieval gate opening at five → two further forecasts.
3. Require at least one ineligible meal before the gate opens, so exclusion is shown.
4. Prefer the fewest meals; then the lowest participant id.

35 of 44 participants were feasible. Nine tie on the first three criteria with nine
meals each; `cgmacros_001` is first by id. Participant 004, used in an earlier
integration test, was not preferred — it ranks lower (it needs more meals).

**Relationship to the model's fit — read this before describing accuracy.** The
frozen pipeline's own Day-8 split puts this participant's cutoff at
`2020-05-08T13:26`. The shipped artifact was fitted only on episodes at or before
`cutoff − 120 min`. Every replayed meal is at or after the cutoff, so **none was in
the artifact's fit**. They are, however, among the 394 Day-8 reference episodes that
were inspected during development. So: out-of-fit, and **not independent validation**.
A handful of meals from one person says nothing about accuracy in general.

**The earlier sensor history.** Eight days of this participant's genuine readings
from before the first replayed meal are released at the start, as the sensor history
a person would bring. Meals from that period are **not** logged, so personal history
starts at zero; their glucose excursions are still present in the trace.

## 4 · Chronology

All times are the recording's own. CGMacros stores date-shifted, participant-local
wall-clock time with no timezone; it is carried under a UTC label and the account's
timezone is UTC, so the D-006 projection offset is zero and the clock is unchanged.
**Display these as "recording time", not as today's date and not as a real calendar
date.**

| | |
|---|---|
| Replay start | `2020-05-08T12:56Z` (30 min before the first meal) |
| First meal | `2020-05-08T13:26Z` |
| Final meal | `2020-05-10T09:27Z` |
| Replay end | `2020-05-10T11:57Z` |
| Readings in the bundle | 2,600 (2,030 released at start; 2,594 by the end) |
| Meals | 9 |
| Timeline | 21 events: 9 forecasts, 9 outcome reveals, 3 scripted dose actions |

### Meal sequence and observed backend states

`admissible` and `retrieval` are what the backend reported in each forecast's
`personalization` block — verified, not predicted by the frontend.

| # | recording time | forecast made | admissible prior meals | retrieval | outcome revealed | eligible as history | alert |
|---|---|---|---|---|---|---|---|
| 1 | 05-08 13:26 | 13:26 | 0 | off | 15:56 | yes | — |
| 2 | 05-08 20:16 | 20:16 | 1 | off | 22:46 | **no — intervening meal** | — |
| 3 | 05-08 20:22 | 20:22 | 1 | off | 22:52 | yes | — |
| 4 | 05-08 22:29 | 22:29 | 2 | off | 05-09 00:59 | yes | active |
| 5 | 05-09 09:35 | 09:35 | 3 | off | 12:05 | yes | **active** ← clearest example |
| 6 | 05-09 13:46 | 13:46 | 4 | off | 16:16 | yes | — |
| 7 | 05-09 18:26 | 18:26 | **5** | **on** | 20:56 | **no — intervening meal** | — |
| 8 | 05-09 18:41 | 18:41 | 5 | on | 21:11 | yes | active |
| 9 | 05-10 09:27 | 09:27 | 6 | on | 11:57 | yes | — |

Points worth showing:

* **Meal 3 sees one prior meal, not two.** Meal 2 was six minutes earlier; its
  120-minute window was still open, so it was not history yet.
* **Meal 2 never becomes history.** Meal 3 fell inside its window, so its measured
  response blends two meals. The backend marks it `intervening_meal` and excludes it.
* **The gate opens at meal 7**, when five completed, eligible meals exist — not before.
* **Meal 8 still sees five.** Meal 7's window is open, and it is then excluded.

### Data availability rule

At replay position *P*, the account's store contains exactly the readings with
`observed_at ≤ P` and the meals logged at or before *P*. Later readings are in the
bundle file and nowhere else. A forecast for a meal at *T* is made with the position
at *T*; its outcome is requested only at `T + 150 min` (120-minute window + the
standard 30-minute grace). See DECISIONS D-014.

## 5 · Personalization — how to describe it

Say: **"Glucose history and completed meal responses inform future personalized
predictions."**

Do not say the model retrains, learns or updates itself. No parameter changes at any
point. What changes is the *input*: the frozen model reads a summary of the person's
own completed, eligible meal responses, and from five such meals it also retrieves
the most similar ones. Level B calibration is off and stays off (D-007); every
forecast reports `calibration_applied: false`.

## 6 · Alert example

The rule is `140 mg/dL`, in-app only, 60-minute cooldown, labelled a **user-selected
sample setting**. No forecast in the window reaches 180 or 160 mg/dL; three reach
140. The threshold was set to the highest of 180/160/140 that any real forecast
reaches — the forecasts were not altered to meet a threshold.

| meal | crossing at | forecast peak | note |
|---|---|---|---|
| 5 | minute 35 | 151.8 mg/dL at 60 min | **use this one**: starts below, rises above |
| 4 | minute 5 | 153.6 mg/dL at 120 min | glucose was already near the level |
| 8 | minute 5 | 155.0 mg/dL at 0 min | pre-meal glucose was already above the level |

For meal 8 the existing engine fires because forecast points after minute 0 are at
or above the threshold; the "peak" it reports is the measured baseline. That is the
engine's real behaviour and is shown as is — it is not a rise prediction.

These are informational forecasts with uncalibrated uncertainty. They are not CGM
alarms and carry no probability.

## 7 · Fictional medication example

| | |
|---|---|
| Current | **Demozide (fictional)** 500 mg tablet, twice daily, reminders at 08:00 and 20:00 |
| Discontinued | **Samplitol (fictional)** 10 mg capsule |
| Class | `other` — so the real-medication education text is never attached to them |
| Scripted actions | 05-08 20:00 **taken** · 05-09 08:00 **skipped** · 05-09 20:00 **taken** |
| Left unanswered | the 05-10 08:00 slot — a genuine pending reminder to act on live |

Each scripted action is an explicit `POST /medication-doses`, 10 minutes after its
slot. Nothing is ever marked taken automatically, and none of this reaches the model
(verified: the forecasts are identical with and without the dose actions).

## 8 · Expected UI states, by checkpoint

| replay state | what the standard endpoints return |
|---|---|
| Start | Sensor chart with history up to 12:56; no meals; `cgm/status.freshness = fresh`; `data_class = research_dataset`; devices: one `pending` source, none connected |
| After a `meal_forecast` | The meal; a 25-point forecast, `prediction_kind: prospective`; `outcome_status: pending`; maybe an alert |
| Between forecast and reveal | `GET /meals/{id}/outcome` → `pending`, `observed: null`, `scoring_not_before` |
| After an `outcome_reveal` | `scored`; `observed.curve` for the overlay; errors; `eligible_for_personalization` and reasons |
| Meals 1–6 | `retrieval_active: false`, `admissible_prior_meals` 0→4 |
| Meal 7 onward | `retrieval_active: true` |
| End | `status: complete`, `next_checkpoint: null`; one reminder still due |

## 9 · Replay controls

Initialize → inspect → advance (next / to a checkpoint / by minutes) → reset.
Forward only. Deterministic. Idempotent with `expected_event_index`. Contracts in
`DEMO_HANDOFF_NEIL.md`; commands in `DEMO_HANDOFF_HET.md`.

Named checkpoints: `retrieval_activation` (meal 7's forecast), `final_meal` (meal
9's forecast), `end`. Suggested presentation start:
`python -m scripts.demo_reset --to meal_06_outcome` — five eligible meals are complete,
so the next forecast (meal 7) is the one where retrieval turns on.

## 10 · Failure and fallback behaviour

| situation | behaviour |
|---|---|
| Bundle file missing or not the selected one | `503 demo_data_unavailable` / command error with setup steps. Nothing is synthesized |
| Model is not the frozen artifact, or its checksum is not enforced | `503 demo_requires_frozen_model`. No synthetic fallback |
| Level B enabled | `503 demo_requires_level_a_only` |
| Demo mode off | the `/demo` routes do not exist (404) |
| A non-demo account calls a replay route | `403 not_demo_account` |
| A forecast is refused by the real rules (e.g. no baseline reading) | recorded in the checkpoint result as `forecast: "refused"` with the code; the replay continues. (Does not occur in this scenario) |
| A retried or duplicated advance | no-op, `advanced: false` |
| No OpenAI key | photo analysis answers `503 vision_not_configured`; the replay uses manual meals and is unaffected |
| Demo email belongs to an ordinary account | seed/reset refuse to touch it |

## 11 · Reset strategy

Reset deletes the demo account through the ordinary account-deletion endpoint — that
one user's rows, by foreign-key cascade, and nothing else — and creates it again at
the start. Sessions for the old account stop working. A state fingerprint that
excludes random ids and wall-clock times is identical after reset + the same replay,
and across independent runs (verified by `scripts.demo_verify` and the test suite).

## 12 · Scientific caveats — say these out loud

1. This is a **replay of a historical research recording**, not a live sensor.
2. One participant, nine meals. It demonstrates that the system works, **not how
   accurate it is.** Per-meal error in this window ranges from about 7 to 32 mg/dL.
3. The meals were not in the model's fit, but they are previously inspected
   reference episodes — **not independent validation.**
4. Forecast uncertainty is **not calibrated**. There are no probabilities.
5. Meal macros are the dataset's recorded values; a real user's would be estimates.
6. Heart rate and medications are **not** model inputs here.
7. Alerts are informational; the 140 mg/dL level is a sample setting.
8. The medications and the profile are **fictional**.
9. No model parameter changes during the demo. Personalization is history, not training.

## 13 · Optional scenario: RETURNING USER

**Scenario id:** `cgmacros-returning-user-replay-v1` · **Mode:** `returning_user`
**Manifest:** `backend/app/v2/demo/manifest_returning_user.json`
**Status:** implemented and verified locally. The NEW USER scenario above is
unchanged: its end-state fingerprint is identical to the one recorded before this
scenario was added.

Same participant, same nine presented meals — but the account arrives with the
personal history a returning user would have.

### 13.1 Is there authentic history? (the gate)

The recording holds **31 meals before the first presented meal** (`2020-05-01 14:23`
to `2020-05-08 09:58`), with continuous sensor data from `2020-05-01 11:39`. Every
one was checked against the v2 rules, from timestamps and macro completeness only:

| check | result |
|---|---|
| Meals found | 31 |
| Valid macros within contract bounds | 31 of 31 |
| Baseline reading within 15 min before | 31 of 31 |
| ≥ 20 native readings in the 120-min window (all have 24) | 31 of 31 |
| All four horizon readings present | 31 of 31 |
| Full window complete before the first presented meal | 31 of 31 |
| **Eligible** | **27** |
| Excluded — another meal inside the window | 4 (meals 11, 12, 21, 25) |

27 admissible at the first presented meal, against a gate of 5: the scenario is
supported by authentic data. Had fewer than five qualified, it would not have been
built. Nothing was invented, and no meal was chosen or dropped — the backfill is
*every* meal the recording holds before the presented ones. No forecast or error was
consulted.

### 13.2 How the history gets there

**It is replayed, not inserted.** `demo_seed --scenario returning_user` starts the
replay at the recording's first meal and runs all 31 earlier meals in order through
the ordinary workflow — release readings to the meal instant → log the meal →
real frozen-model forecast → wait for the window and grace → real outcome scoring →
eligibility decision — and stops just before the first presented meal. No completed
response record is written directly; every history meal has its own forecast and
snapshot. Setup takes about 5 seconds locally.

During that setup the application's own counts run `0, 1, 2, 3, 4, 5, …`: retrieval
first turns on at **meal 6**, after five eligible meals have completed, exactly as
in the NEW USER scenario. It is not switched on by the scenario.

### 13.3 The presented journey

| presented meal | recording time | admissible prior meals | retrieval |
|---|---|---|---|
| 1 (meal 32) | 05-08 13:26 | **27** | **on** |
| 2 | 05-08 20:16 | 28 | on |
| 3 | 05-08 20:22 | 28 | on |
| 4 | 05-08 22:29 | 29 | on |
| 5 | 05-09 09:35 | 30 | on |
| 6 | 05-09 13:46 | 31 | on |
| 7 | 05-09 18:26 | 31 | on |
| 8 | 05-09 18:41 | 31 | on |
| 9 | 05-10 09:27 | 29 | on |

The count *falls* at the last meal: the model is shown at most eight days of
history, so the recording's first-day meals have aged out. That is the frozen
lookback, shown as it is.

The same two presented meals (2 and 7) are excluded for an intervening meal.
Forecasts differ from the NEW USER scenario's for the same meals, because the model
is reading a real personal history — that difference is the point of the scenario.

### 13.4 Caveat specific to this scenario — important

**The 31 history meals precede the participant's Day-8 cutoff, so they were in the
frozen artifact's fit.** Forecasts and errors shown for *those* meals are in-sample
and say nothing about accuracy; do not present their forecast-versus-actual as
performance. The nine presented meals are after the cutoff and were not in the fit,
exactly as in the NEW USER scenario. Neither set is independent validation.

What the history legitimately contributes is the person's own *measured* responses,
which is what the frozen `r_hist_*` and `q_retr_*` inputs summarize. No model
parameter is fitted, at setup or afterwards. Level B stays off.

### 13.5 Everything else

Identity: `demo-returning@glimpse.example`, display name "Returning Demo User
(fictional)", same password variable, its own `gtp_…` participant id. Same fictional
questionnaire and medications (dose history is longer: the scripted taken/skipped
pattern runs across the whole recording). Same 140 mg/dL sample alert setting.
Same failure behaviour, same reset strategy — per mode: resetting one never touches
the other or any ordinary account.

## 14 · Simulated connected-health sources (both scenarios)

**Status:** implemented and verified locally. Neither scenario's forecasts, outcomes
or glucose changed: both end-state fingerprints are identical with every simulated
source switched on.

Either demo account can switch on four **simulated** sources — Apple Health, Apple
Watch, Fitbit, WHOOP — to show how wearable data enters GLIMPSE. They start
inactive and are activated one at a time from the demo-only routes.

### 14.1 What is what

| source | nature | `data_class` | connection status |
|---|---|---|---|
| Glucose | **genuine** CGMacros Dexcom G6 Pro readings, replayed in order | `research_dataset` | not a connection; shown as a replaying research source |
| Apple Health, Apple Watch, Fitbit, WHOOP | **generated** sample values | `demo` | `demo_simulated` |
| A real person's real device | — | `user_device` | does not occur in the demo |

`demo_simulated` is a status of its own, added so that `authorized` and `connected`
never mean "simulated". No token, scope, external account id or OAuth state exists
for a simulated source, no provider callback is fabricated, and no vendor API is
called. The real provider ids (`fitbit`, `whoop`, `dexcom_api`) are untouched and
still report `provider_not_configured`.

### 14.2 Generation rules (`backend/app/v2/demo/sim_devices.py`, version `sim-1`)

Deterministic: a fixed seed, the profile name and the calendar date. Same inputs,
same values, on every run.

* **One simulated person-day** is drawn per date — steps (weekday ≈ 7,800, weekend
  ≈ 9,200), sleep (≈ 415 / 445 min), wake time, resting heart rate (≈ 61 bpm), HRV
  (≈ 48 ms), a recovery score, and on about two days in three a 25–45 minute
  workout. All four sources describe that same day, so they agree with each other.
* **Each source applies a small fixed difference** (Fitbit counts ≈ 4 % more steps,
  WHOOP reports slightly less sleep), as two real trackers would.
* **Apple Health repeats the Apple Watch's steps and workouts**: it is an aggregation
  platform, not a second sensor.
* **Heart rate** (Apple Watch every 15 min, Fitbit every 30 min) follows a daily
  curve above the day's resting rate, drops during the simulated sleep, and rises
  during the simulated workout. **WHOOP has no heart-rate series**: its API does not
  expose one, so none is invented.
* **Coherence with the recording:** the only thing read from the research data is the
  *time* of each logged meal, so a simulated sleep never covers a recorded meal (sleep
  starts 45 minutes after a late meal) and a workout that would contain one is
  dropped. No glucose value, macronutrient or forecast is read.
* Every value lies inside the replayed observation period.

Each stored observation carries: `provider` (`demo_<profile>`), `metric`, `value`,
canonical `unit`, `observed_at` / `observed_end_at`, `received_at` (the replay
position at which it was released), `data_class: "demo"`, and `detail` with
`simulated: true`, `device_profile`, `source_label` and `simulation_version`.

### 14.3 Chronology

A simulated observation is released only when the replay position reaches the moment
it could exist: a heart-rate sample at its own instant, a day's step total at the end
of that day, a sleep session when it ends. Activating a source reveals its history up
to the current position; advancing the replay reveals more. Reset returns every
source to inactive and removes its observations; the same actions then reproduce the
same data.

### 14.4 What the forecast uses — and does not

The frozen fs-1.3 forecast uses: the glucose history, the confirmed meal's five
macronutrient values, meal timing, and the account's own completed, eligible meal
responses. It does **not** use heart rate, steps, sleep, recovery, HRV, workouts,
medication records or questionnaire answers. The model's one wearable-shaped input
(`hr_mean_30min_bpm`) is left missing for every app account, and stays missing with
simulated devices on — verified, along with every forecast being byte-identical.

Showing wearable context beside a forecast is a display choice. It must not be
described as the model taking that context into account.

### 14.5 Caveats

* The wearable values are invented. They are not from CGMacros, not from any device,
  and not physiologically linked to the glucose trace beside them — a simulated
  workout has no effect on the real glucose curve. Do not point at correlations.
* The simulated day does not know about meals that were never logged in this account
  (in NEW USER mode, the days before the first presented meal).
* Brand names are used to label what is being simulated. Nothing here is an Apple,
  Google/Fitbit or WHOOP integration, and none has been verified.
