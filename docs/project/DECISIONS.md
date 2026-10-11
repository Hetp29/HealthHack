# GlucoTwin — shared decision log

One log for all three workstreams (master §6). Newest entries at the bottom.

**Status vocabulary**

* **PROPOSED** — implemented as the working default so work is not blocked, but not
  yet approved by the person who owns the decision. Changeable.
* **APPROVED** — the named owner has agreed. Nobody is recorded as approving anything
  they have not actually approved.
* **SUPERSEDED** — replaced; kept for history.

Every entry below was written during Ruhaan's R0–R8 implementation pass on
2026-10-10. Ruhaan reviewed the pass the same day and ruled on D-006 and D-007; those
rulings are quoted verbatim in their entries. **Every other entry is still PROPOSED**,
and the review explicitly was not a blanket approval of the v2 API (see R-001).

---

## R-001 · Review ruling on R0–R8 (Ruhaan, 2026-10-10)

> R0–R8 reviewed. The backend implementation and testing results are accepted as the
> integration baseline, subject to the remaining external and deployment blockers.

> Keep all v2 endpoints PROPOSED until the shared interface review is complete; do
> not treat this message as blanket API approval.

> The objective now is controlled team integration, not further model development.

Consequences recorded here: the code on `ruhaan` is the baseline Het and Neil
integrate against; every v2 endpoint stays PROPOSED until Het and Neil have reviewed
the registry; no model development is in scope.

---

## D-001 · v2 lives under `/api/v2`; contract v1.0 is untouched

**Status:** PROPOSED · **Owner:** Ruhaan · **Affects:** Neil, Het

All new endpoints are under `/api/v2`. The seven frozen endpoints keep their paths,
schemas, error envelope and CORS policy. v2 has its own OpenAPI document at
`/api/v2/openapi.json`, published as `contracts/v2/openapi.json`.

## D-002 · A separate application database; the research warehouse is left alone

**Status:** PROPOSED · **Owner:** Ruhaan · **Needs:** Het's acknowledgement

`data/**` (CGMacros / MetaboNet warehouse, `GLUCOTWIN_DB_URL`) is Het's code per its
own headers and is not modified. Real users are stored in a new database,
`GLUCOTWIN_APP_DB_URL`, defined in `backend/app/v2/db/tables.py` and migrated with
Alembic. The two never share a table.

*Why:* different owners, different sensitivity, different lifecycle. Research rows
are regenerable from public data; user rows are not.

## D-003 · v2 is a second ASGI application, not routers on the frozen app

**Status:** PROPOSED · **Owner:** Ruhaan · **Affects:** Het (start command)

`backend/app/main.py` and every v1 route are checksummed in the freeze manifest, so
nothing can be added to them. `backend/app/v2/asgi.py` dispatches `/api/v2/*` to the
v2 application and everything else to the unmodified v1 application object.

**Start command changes** from `backend.app.main:app` to `backend.app.v2.asgi:app`.
Verified: v1 responses are byte-identical with v2 mounted
(`tests/v2/test_platform_integrity.py`).

## D-004 · Identity: built-in email + password with opaque session tokens

**Status:** PROPOSED · **Owner:** team decision (master §17 lists the identity provider as open)

Working default so every other feature could be built and tested with real ownership:
scrypt password hashes, random bearer tokens stored only as SHA-256, server-side
revocation, 14-day expiry. Isolated in `deps.current_user`; swapping to a hosted IdP
(e.g. Cognito) replaces token verification and nothing else.

*Not included, and needed before real users:* email verification, password reset,
login rate limiting, MFA. See BLOCKERS B-005.

## D-005 · A new user gets a newly minted participant id, never a research one

**Status:** PROPOSED · **Owner:** Ruhaan

`participant_bindings` maps each account to `gtp_<random>`. The frozen artifact has no
calibration for that id and therefore reports `generalized_fallback`, with its own
explanation in `limitations`. A user is never mapped onto a CGMacros participant.

## D-006 · Live timestamps are projected onto the local wall clock before inference

**Status:** **CONDITIONALLY APPROVED** by Ruhaan, 2026-10-10 — condition implemented; see "Compliance"

**Ruling (verbatim):**

> **D-006 — CONDITIONAL APPROVAL.** I approve local wall-clock projection as necessary
> for compatibility with the frozen fs-1.3 features. I do not approve using an
> account-level fixed UTC offset as the unrestricted production solution. Use IANA
> timezone-aware, observation-time-specific conversion, preserve true UTC timestamps
> in storage, document the policy, and test DST boundaries. Do not modify frozen model
> files or feature definitions. If the current implementation cannot safely support
> this, keep production forecasting gated and report the blocker.

**The incompatibility.** fs-1.3 was trained on CGMacros, which stores
participant-local wall-clock time under a `+00:00` label. Two frozen features read
that stored clock directly: `g_dev_from_night` (readings with stored hour 2–5) and
the time-of-day components of every retrieval vector. Given genuine UTC instants they
would read the UTC hour.

**The policy now implemented** (`services/history.py`):

1. **Storage is true UTC, always.** Every `observed_at`, `occurred_at` and `as_of` in
   the database is the real instant. The local-clock form exists only inside the
   input handed to the engine, and in the snapshot of that input.
2. **Conversion is IANA and per observation.** `project_local_clock(instant, zone)`
   gives each observation the UTC offset its zone had *at that observation's own
   instant*. There is no account-level fixed offset.
3. **The engine's history never spans an offset change.** If the zone's offset
   changed inside the 8-day lookback, the history shown to the engine starts at the
   change (`engine_lookback_start`).

**Why rule 3 is required for rule 2 to be safe.** Local wall-clock time is not a
timeline across a daylight-saving change: an hour repeats (fall back) or is skipped
(spring forward). Per-observation local times spanning a change would be out of order
or would mis-state every elapsed time across it by an hour — and the frozen feature
code assumes an ascending series and takes slopes, gaps and 120-minute response
windows from timestamp differences. Restricting the history to one side of the change
makes both properties exact: each timestamp carries its own correct local clock
reading, and every difference between two of them is the true elapsed time. The code
asserts this invariant and refuses to forecast if it is ever violated.

**Cost, stated plainly.** For up to 8 days after an offset change (twice a year in
most DST zones), a forecast uses a shorter history: fewer or no admissible prior
meals, and possibly no overnight baseline. The engine handles this exactly as it
handles any new account — missing inputs are imputed and disclosed — and the response
says so: `context_quality.history_truncated_at_offset_change`, `offset_change_at`,
and an added `limitations` entry. The timing of a meal from before the change still
reaches the model as true elapsed minutes through the frozen `context_override`. In
the first minutes after a change, before any post-change reading exists, the forecast
is refused with `insufficient_premeal_context` rather than anchored on a pre-change
reading.

**No frozen file or feature definition was modified.** Two response fields are
corrected so they are true for a live account, as before: `meal_timestamp` is the
real UTC instant, and the research-dataset clock caveat is replaced by a statement of
the policy. The frozen response schema still validates.

**Compliance with the ruling**

| condition | where met | evidence |
|---|---|---|
| IANA timezone-aware | `ZoneInfo(account timezone)` | `test_offset_transitions_are_located_to_the_second` (incl. a 30-minute zone) |
| observation-time-specific | `project_local_clock` per row | `test_each_observation_is_converted_with_its_own_offset` |
| true UTC preserved in storage | nothing projected is stored except the engine-input snapshot | `test_storage_keeps_true_utc_regardless_of_projection` |
| DST boundaries tested | spring forward and fall back, America/New_York 2026 | `tests/v2/test_dst_clock_policy.py` (22 tests) |
| no fixed account-level offset | removed | the invariant check in `build_engine_inputs` |
| frozen files unmodified | — | `test_every_frozen_source_file_still_matches_the_freeze_manifest` |
| policy documented | this entry, `FS13_INPUT_MAPPING.md` | — |

**A defect this rework found and fixed.** The first implementation computed the
offset with `ZoneInfo.utcoffset(utc_datetime)`, which reads its argument as *local*
wall time. Away from a transition that returns the right answer; within hours of one
it returned the offset from the wrong side. It now converts into the zone first.

**Remaining limitations (not gated, but real):**

* The zone is the account's current `timezone`. A person who travels, or who changes
  their account timezone, has earlier observations read on the new zone's clock.
  Per-observation source zones are stored only for file imports.
* The cut is at offset *changes*, so a zone that changes its rules is handled, but a
  zone-database update that retroactively moves a transition is not replayed.

**Production gate.** Not required by this entry any more: the fixed-offset approach
is gone and the per-observation policy is safe by construction. Production
forecasting for real users remains blocked for other reasons (BLOCKERS B-001, B-004).

## D-007 · Personalization: Level A retained; Level B not approved for enablement

**Status:** **RULED** by Ruhaan, 2026-10-10 — Level A approved to remain; **Level B NOT APPROVED**

**Ruling (verbatim):**

> **D-007 — NOT APPROVED FOR ENABLEMENT.** Retain Level A historical-response
> personalization. Keep Level B calibration disabled by default. Evaluate it
> separately through leakage-safe chronological replay before requesting approval.

* **Level A (on, retained).** Scored, eligible completed meals are passed to the
  engine as the user's history. The frozen `r_hist_*` and `q_retr_*` features
  individualize the forecast with no fitted parameter.
* **Level B (off, not approved).** The frozen `PersonalizationModel.fit` run on a
  user's own eligible residuals, supplied through a shallow copy of the loaded
  artifact. The code and its tests remain so the evaluation below can be run; the
  default is `GLUCOTWIN_PERSONALIZATION_POLICY=history_only`.
  **Do not set `history_plus_frozen_calibration` in any deployed environment.**
  `GET /api/v2/status` reports the active policy, and every stored prediction records
  the policy that produced it, so an accidental enablement is visible.
* **Level C.** Not implemented. Not authorized.

**Before approval can be requested for Level B** — not started; out of scope while
the objective is team integration:

1. A leakage-safe chronological replay on the development folds: for each
   participant, walk their meals in time order; at each meal fit the offset only from
   episodes whose 120-minute window ended strictly before it; compare forecast error
   with and without the offset.
2. Report it per participant, including who it harms, with participant-clustered
   intervals — not a single pooled number.
3. The 394-episode reference set must not be used for the decision
   (freeze §20, rule 3).
4. State the result against a threshold declared before measurement.

## D-008 · Wearable heart rate and medication records are not model inputs

**Status:** PROPOSED · **Owner:** Ruhaan

fs-1.3 has two inputs these could feed: `hr_mean_30min_bpm` and
`medication_exposure_recent`. Neither is supplied for app users.

* Heart rate: the feature was trained on CGMacros's Fitbit minute-level stream.
  WHOOP exposes no such stream, Fitbit gates intraday data, and HealthKit samples have
  different cadence and provenance. Equivalence is unverified, so the feature is left
  missing and the frozen pipeline imputes it — disclosed in `limitations` and in
  `context_quality.missing_features`.
* Medication: the briefs say medication information must not silently modify
  forecasts. The feature is 0.0 for every app user.

Evidence: `test_medications_and_wearables_do_not_reach_the_model`. Full table:
`FS13_INPUT_MAPPING.md`.

## D-009 · What makes a completed meal eligible as personal history

**Status:** PROPOSED · **Owner:** Ruhaan

An episode is scored after its 120-minute window **plus a 30-minute grace period**
for late sensor uploads (`GLUCOTWIN_OUTCOME_GRACE_MINUTES`). It is eligible only if:
all four horizons were observed (±10 min, the frozen target tolerance); at least 20
native readings fall in the window (the frozen retrieval minimum); no other meal was
logged inside the window; no reading in the window was corrected or quality-flagged;
the meal was eaten as logged; the inputs were user-confirmed and have not since been
revised; and the user consents to personal-history use.

This is **stricter than training**, where overlapping-meal episodes were retained and
tagged. Chosen deliberately: master §11.7 says confounded episodes must not be
silently accepted. Episodes are re-scored automatically when late or corrected
readings arrive. Missing data is retried for 72 hours.

**Consequence to know:** only a meal that was *forecast* has an episode, so only
forecast meals become history. The frontend should request a forecast for every
confirmed meal, including ones logged after the fact.

**Known limitation:** when the most recent previous meal is ineligible, its timing
still reaches the model (through the frozen `context_override`), but
`prior_meal_carbs_g` then reflects the most recent *eligible* meal instead, or is
missing. Recorded in the snapshot as `prior_meal_timing_from_override`.

## D-010 · One sensor per forecast

**Status:** PROPOSED · **Owner:** Ruhaan

When several devices reported in the lookback, a forecast uses exactly one series —
the device with the most recent reading inside the 15-minute baseline tolerance.
Mixing two sensors would interleave their independent calibration errors into every
slope and variability feature. The choice is stored in the snapshot and returned as
`context_quality.cgm_device`. Scans and fingersticks are stored but kept out of the
model series.

## D-011 · Alerts respect quiet hours; medication reminders do not

**Status:** PROPOSED · **Owner:** Neil (UX) + Ruhaan

Forecast alerts are suppressed during quiet hours, during the cooldown, and for
retrospective forecasts; each suppression is recorded with its reason. Medication
reminders are **not** held back by quiet hours: Neil's brief says prescribed-time
reminders must not be silently suppressed without a documented exception policy, and
none exists yet. In-app is the only channel that actually delivers; push and email
are stored as preferences and every attempt is recorded as `channel_unavailable`.

## D-012 · Photo uploads are raw request bodies; only a sanitized JPEG is kept

**Status:** PROPOSED · **Owner:** Ruhaan · **Affects:** Neil, Het

`POST /api/v2/meals/images` takes the image bytes as the body with `Content-Type:
image/*` (no multipart dependency). The server re-encodes to JPEG, dropping EXIF and
GPS, and stores and sends to OpenAI only that. HEIC/AVIF are rejected with a specific
error. When Het provides a bucket, a pre-signed direct-to-S3 flow can replace the
proxy upload without changing the meal lifecycle.

## D-013 · OpenAI model is configuration with no default

**Status:** PROPOSED · **Owner:** team decision (master §17)

`GLUCOTWIN_OPENAI_VISION_MODEL` and `OPENAI_API_KEY` must both be set or analysis
returns `503 vision_not_configured`. No model name is hard-coded: the approved model,
cost ceiling and retention policy are open team decisions. A per-user cap of 40
analyses per 24 hours is enforced server-side. Requests set `store=False`.

## D-014 · Demonstration replay: release staged data over simulated time

**Status:** PROPOSED · **Owner:** Ruhaan

The hackathon demonstration replays a historical research recording. Two designs
were considered for keeping the future out of a forecast:

* **(A) Stage and release — chosen.** Future observations are not in the application
  database at all. They stay in a bundle file and are ingested, through the ordinary
  ingestion path, only as the replay position passes them.
* **(B) Load everything, restrict by replay time.** Every query that touches glucose,
  meals, outcomes or history would need a replay-time filter.

(A) is safer and simpler: no v2 query can see the future, without any of them
knowing a replay exists, so there is nothing to forget. (B) fails silently the first
time one query omits the filter.

For the demo account only, "now" is the replay position
(`core.clock_override`, set per request). So a 2020 meal is forecast at its 2020
instant, prospectively, with **historical timestamps preserved** — nothing is
re-dated to today. Ordinary accounts stay on the wall clock; sessions always use the
wall clock; background workers skip demo accounts. D-006 applies unchanged (the
recording is carried as UTC, so its offset is zero).

Every replay step calls the public v2 endpoints through an in-process client. The
only direct writes are the staged-glucose release and the replay-state row.

Routes live under `/api/v2/demo`, are registered only when `GLUCOTWIN_DEMO_MODE` is
enabled, are authenticated, and act only on the caller's own demo account.

## D-015 · Demonstration data is chosen by a model-free rule, on out-of-fit meals

**Status:** PROPOSED · **Owner:** Ruhaan

The demo participant is selected by a fixed rule that reads timestamps and macro
completeness only (`backend/app/v2/demo/source.py::SELECTION_RULE`). No glucose
value, forecast or forecast error is consulted, so the example cannot have been
picked for looking accurate. The rule, the ranking and the result are recorded in
the committed manifest.

The replay window is restricted to meals at or after the participant's Day-8 fit
cutoff, computed with the frozen pipeline's own split. The shipped artifact was
fitted only on episodes at or before `cutoff − 120 min`, so every demo forecast is
for a meal outside its fit. Those meals are, however, previously inspected Day-8
reference episodes: **the demo is not independent validation and must not be
described as evidence of accuracy.**

The alert threshold (140 mg/dL) *was* chosen after looking at forecasts — it is the
highest of 180/160/140 that any real forecast in the window reaches — and is
labelled a user-selected sample setting. No forecast was altered.

A research bundle is replayed only through the frozen artifact with its checksum
enforced, and only with Level B off; otherwise the replay refuses to run.

---

## Open decisions (nobody has decided these)

| # | decision | who | blocks |
|---|---|---|---|
| O-2 | Level B: run the chronological replay evaluation, then request approval (D-007) | Ruhaan | calibration offsets for app users |
| O-10 | Shared interface review of the v2 registry by Het and Neil (R-001) | Het, Neil, Ruhaan | moving any v2 endpoint from PROPOSED to APPROVED |
| O-3 | Identity provider | team | real user sign-up |
| O-4 | Approved OpenAI model, cost ceiling, image retention period | team | live meal analysis |
| O-5 | A real-time CGM source (see BLOCKERS B-001) | team | prospective forecasts for real users |
| O-6 | Is there going to be a native iOS app? | team | HealthKit / Apple Watch |
| O-7 | Clinical reviewer for medication education | team | showing it as anything but a draft |
| O-8 | Grace-period rule for a "missed" dose | Neil | a missed label in the UI |
| O-9 | Privacy policy / consent text owner; HIPAA posture | team | real users |
