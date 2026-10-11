# GlucoTwin — shared decision log

One log for all three workstreams (master §6). Newest entries at the bottom.

**Status vocabulary**

* **PROPOSED** — implemented as the working default so work is not blocked, but not
  yet approved by the person who owns the decision. Changeable.
* **APPROVED** — the named owner has agreed. Nobody is recorded as approving anything
  they have not actually approved.
* **SUPERSEDED** — replaced; kept for history.

Every entry below was written during Ruhaan's R0–R8 implementation pass on
2026-10-10 and is **PROPOSED**. Each names who needs to sign it off.

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

## D-006 · Live timestamps are projected onto the account's local clock before inference

**Status:** PROPOSED — **needs Ruhaan's explicit sign-off; this is the model-compatibility gate**

**The incompatibility.** fs-1.3 was trained on CGMacros, which stores
participant-local wall-clock time under a `+00:00` label. Two frozen features read
that stored clock directly: `g_dev_from_night` (readings with stored hour 2–5) and
the time-of-day components of every retrieval vector. Given genuine UTC instants they
would read the UTC hour: for a user in New York the "overnight" baseline would come
from 21:00–01:00 local, and a retrieval query built from local time would be compared
with prior meals described in UTC.

**What is done.** No frozen file is changed. Before calling the engine, the
orchestrator shifts every timestamp in the history by one fixed offset — the
account's UTC offset at the meal instant — and passes
`time_basis="source_local_unknown_tz"`, the basis the model was trained under. A
uniform shift preserves every elapsed time and ordering exactly, so slopes, gaps and
window boundaries are unchanged; only the clock reading moves.
(`services/history.py::project_local_clock`.)

**Known approximation.** Observations on the far side of a DST change inside the
8-day lookback have a clock reading one hour off. Elapsed times are still exact.

**Two response fields are corrected** so they are true for a live account:
`meal_timestamp` is restored to the real UTC instant, and the dataset caveat ("dates
are shifted… ordering keys rather than real UTC instants") is replaced with a
statement of what was done. The schema is unchanged and still validates against the
frozen JSON Schema.

**Alternative rejected:** passing real UTC with `time_basis="utc"`. `tod_sin/cos`
would be right but the two clock-reading features above would be silently wrong.

**Evidence:** `test_time_of_day_is_read_on_the_account_local_clock` — same UTC data
in New York, UTC and Tokyo: clock features follow local time, all 36 other features
are bit-identical.

## D-007 · Personalization: Level A on; Level B implemented but off by default

**Status:** PROPOSED — **needs Ruhaan's governance decision**

* **Level A (on).** Scored, eligible completed meals are passed to the engine as the
  user's history. The frozen `r_hist_*` and `q_retr_*` features individualize the
  forecast with no fitted parameter. This is what the code comments in
  `ml/features.py` describe as cold-start personalization.
* **Level B (off; `GLUCOTWIN_PERSONALIZATION_POLICY=history_plus_frozen_calibration`).**
  The frozen `PersonalizationModel.fit` — same class, same config: `n/(n+3)`,
  minimum 4 episodes, cap 60 mg/dL — is run on the user's own eligible residuals and
  the resulting offset is supplied through a shallow copy of the loaded artifact. The
  artifact file and the loaded object are not modified.
* **Level C.** Not implemented. Not authorized.

**Why B is gated rather than on:** the artifact contains calibrations for its 45
training participants only. Producing one for a new user uses the frozen estimator
unchanged, but yields a parameter the artifact does not contain, reported under the
frozen version string. There is also a real statistical difference: training
offsets were fitted on in-sample residuals, a live user's are prospective. Whether
that is "applying the frozen policy" or "a new calibration" is a governance question,
so it does not get switched on by an engineering default. Every stored prediction
records the policy that produced it.

**To enable:** set the environment variable. Tests for both settings pass.

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

---

## Open decisions (nobody has decided these)

| # | decision | who | blocks |
|---|---|---|---|
| O-1 | Sign off D-006 (clock projection) | Ruhaan | production use of any live forecast |
| O-2 | Enable or reject Level B (D-007) | Ruhaan / governance | calibration offsets for app users |
| O-3 | Identity provider | team | real user sign-up |
| O-4 | Approved OpenAI model, cost ceiling, image retention period | team | live meal analysis |
| O-5 | A real-time CGM source (see BLOCKERS B-001) | team | prospective forecasts for real users |
| O-6 | Is there going to be a native iOS app? | team | HealthKit / Apple Watch |
| O-7 | Clinical reviewer for medication education | team | showing it as anything but a draft |
| O-8 | Grace-period rule for a "missed" dose | Neil | a missed label in the UI |
| O-9 | Privacy policy / consent text owner; HIPAA posture | team | real users |
