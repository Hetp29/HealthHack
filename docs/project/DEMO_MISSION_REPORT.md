# Demo mission report — deterministic replay, verification, integration readiness

**Date:** 2026-10-10 · **Owner:** Ruhaan · **Branch:** `ruhaan`
**Starting commit:** `00b0149997a1961d652970196923fa4b613376f7`

Everything stated as verified below was executed on this machine. Nothing here has
run on a deployed server, in a container, or on PostgreSQL, and no teammate's branch
was touched.

## Result

From a clean application database, with the frozen artifact and the data bundle in
place:

```bash
export GLIMPSE_DEMO_PASSWORD='…'
alembic upgrade head && python -m scripts.demo_seed
```

initializes a repeatable demonstration. The existing v2 API then serves real
frozen-model forecasts, real historical outcomes, the personalization progression,
server-generated alerts, a questionnaire and fictional medication records for it.
No API response is hard-coded: every forecast, outcome and alert is produced by the
application as the replay advances.

## What was built

| piece | where |
|---|---|
| Per-request replay clock for demo accounts only | `core.clock_override`, `deps.current_user`, `V2Context.now()` |
| Replay state (one table, migration `0002`) | `db/tables.py::demo_replay_state` |
| Bundle format, checksum enforcement, model-free selection rules | `demo/source.py` |
| Replay engine: initialize, advance, reset, state, fingerprint | `demo/replay.py` |
| Fictional profile, kept apart from research data | `demo/profile.py` |
| Labelled test fixture for tests and published examples | `demo/fixture.py` |
| Gated routes `/api/v2/demo/replay[/advance|/reset]` | `routers/demo.py` |
| Commands | `scripts/demo_select.py`, `demo_seed.py`, `demo_reset.py`, `demo_verify.py`, `smoke_demo.py` |
| Committed selection record | `backend/app/v2/demo/manifest.json` |

Nothing existing was reimplemented. The replay calls the public endpoints for
registration, consent, onboarding, alert settings, medications, doses, meals,
forecasts and outcomes. The only direct writes are releasing staged readings through
the ordinary ingestion service and the replay-state row.

Changes to existing behaviour, all backward compatible:

* `GET /status` gains `demo_mode`.
* `current_user` now also carries `is_demo`; sessions explicitly use the wall clock.
* The global outcome-scoring and reminder worker passes skip demo accounts.
* `Dockerfile` copies `scripts/` so the commands exist in the image.

No published v2 endpoint changed shape. Contract v1.0 is untouched.

## Verification

| check | result |
|---|---|
| `python -m pytest -q -rsxX` | **994 passed, 1 skipped, 0 failed (703 original freeze tests + 291 v2; the skip is the opt-in real-OpenAI call)** |
| `python -m research.freeze.verify_artifacts` | **PASS** |
| `model.joblib` / `contracts/openapi.yaml` SHA-256 | unchanged (`12ed0602…7415fe` / `be836ddd…200f4`) |
| 23 frozen source files vs the manifest | all match (asserted by a test) |
| `python -m contracts.v2.build_contract --check` | **pass** |
| `alembic upgrade head` / `check` / `downgrade base` (SQLite, incl. `0002`) | **pass** |
| `python -m scripts.demo_verify` — real data, frozen model, scratch DB | **27 of 27 checks passed** |
| `python scripts/smoke_demo.py` — live server over HTTP | **25 of 25 steps passed against 127.0.0.1** |
| `python scripts/smoke_v2.py` — existing 21-step smoke, same server | **21 of 21 steps passed; the demo account was left intact** |

`demo_verify` checks, on real output: the bundle and model are the selected and
frozen ones; stored readings equal the source exactly (2,594 of them, no conversion,
correction or relabelling); the store never held anything later than the replay
position; the backend's `admissible_prior_meals` sequence is `[0, 1, 1, 2, 3, 4, 5,
5, 6]` with retrieval active exactly from five; every forecast is prospective at its
historical instant, carries the frozen checksum, has 25 points and reproduces exactly
from its snapshot; no forecast's stored input history contains anything after its
own instant; eligibility and reasons match the manifest; two independent runs and a
reset produce an identical state fingerprint.

## Required report fields

**MISSION STATUS:** Complete for Ruhaan-owned demo engineering, verified locally.
Not integrated with, or run on, anything belonging to Het or Neil.

**BRANCH:** `ruhaan`

**STARTING COMMIT:** `00b0149997a1961d652970196923fa4b613376f7`

**FINAL COMMIT / PUSH STATUS:** recorded in the push record that accompanies this
report (a commit cannot contain its own SHA).

**MODEL INTEGRITY:** Unchanged. Artifact SHA-256
`12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe`; freeze
verification passes; all 23 frozen source files match; Contract v1.0 byte-identical.
No retraining, no feature change, no tuning. Level B off.

**DEMO DATA SOURCE:** CGMacros v1.0.0, from the local research warehouse built by the
repository's pipeline. Nothing downloaded. Glucose: Dexcom G6 Pro primary series.

**SELECTED PARTICIPANT:** `cgmacros_001`, by a fixed rule that reads timestamps and
macro completeness only. 35 of 44 participants were feasible; nine tie and the
lowest id wins. Not chosen for forecast error — no forecast was consulted.

**SELECTED MEALS:** The participant's nine meals from the Day-8 fit cutoff onward:
`2020-05-08T13:26` to `2020-05-10T09:27` (recording time). All nine are outside the
artifact's fit; all are previously inspected reference episodes.

**EPISODE ELIGIBILITY:** Seven eligible, two ineligible (`intervening_meal`: meals 2
and 7). Verified empirically by running the real scoring service, and it matches
the model-free prediction in the manifest exactly.

**REPLAY DESIGN:** Stage-and-release. Future readings are not in the database; they
are ingested from the bundle only as the replay position passes them. For the demo
account alone, "now" is the replay position. Historical timestamps are preserved.
21 events: 9 forecasts, 9 outcome reveals, 3 scripted dose actions. Forward-only,
deterministic, idempotent. DECISIONS D-014.

**SEED COMMAND:** `python -m scripts.demo_seed [--to CHECKPOINT]`

**RESET COMMAND:** `python -m scripts.demo_reset [--to CHECKPOINT]`
(or `POST /api/v2/demo/replay/reset`)

**VERIFY COMMAND:** `python -m scripts.demo_verify` (scratch database), and
`python scripts/smoke_demo.py <url>` (live server).

**FORECAST REPRODUCIBILITY:** All 9 forecasts reproduce exactly from their stored
snapshots, in-process and over HTTP. Two independent replays and a reset give the
same fingerprint.

**PERSONALIZATION DEMONSTRATION:** Backend-reported admissible prior meals
`0, 1, 1, 2, 3, 4, 5, 5, 6`; retrieval off through four, on at five (meal 7) and
after. Shows a meal not counting while its window is open, and two meals excluded as
ineligible. Level A only; `calibration_applied` is false on every forecast.

**ALERT DEMONSTRATION:** The existing server-side engine raised three alerts at a
140 mg/dL user-selected sample setting (meals 4, 5 and 8). No forecast reaches 160 or
180. The clean example is meal 5: crossing at minute 35, forecast peak 151.8 mg/dL.
For meal 8 the pre-meal glucose was already above the level — shown as the engine
reports it. No second evaluator was written; no forecast was altered.

**MEDICATION DEMONSTRATION:** Fictional and labelled: "Demozide (fictional)" current
with 08:00/20:00 reminders, "Samplitol (fictional)" discontinued. Scripted explicit
actions taken / skipped / taken; one reminder left pending for a live tap. Verified
that the dose actions do not change any forecast.

**QUESTIONNAIRE DEMONSTRATION:** 13 answers on the established question ids,
completed, both consents explicit. HbA1c and fasting glucose are `"unknown"` rather
than invented beside real glucose data.

**OPENAPI CHANGES:** Three gated routes added under `/demo/replay`; `GET /status`
gains `demo_mode`. Published contract regenerated (44 paths). Nine new example files,
generated from a labelled test fixture, not from research data. No existing v2 schema
changed. v1 unchanged.

**TEST RESULTS:** see the table above. 29 new demo tests: 24 on the fixture bundle
(run anywhere) and 5 on the real recording (skipped where the bundle or artifact is
absent).

**HTTP SMOKE RESULTS:** see the table above.

**HANDOFF TO HET:** `docs/project/DEMO_HANDOFF_HET.md`.

**HANDOFF TO NEIL:** `docs/project/DEMO_HANDOFF_NEIL.md`.

**REMAINING BLOCKERS:**

* B-016 — the artifact and the 87 KB data bundle are not in git; Ruhaan holds the
  only copies. Het and Neil cannot run the real demo until they are delivered.
* B-017 — the demo has run on one machine only: not in a container, not on
  PostgreSQL, not on a deployed server. Single worker required.
* B-018 — no demo UI exists.
* B-004 — the image has never been built.
* B-007 — PostgreSQL unverified, now including migration `0002`.
* B-015 — the shared interface review has not happened; all v2 routes, including the
  new ones, are PROPOSED.
* Unchanged and still unverified against anything real: OpenAI, Dexcom, Fitbit,
  WHOOP, Apple Health, S3, the frontend. None is used by the demo.

**WHAT IS REAL VS SIMULATED:**

| real | simulated or fictional |
|---|---|
| Glucose readings (CGMacros, value-for-value) | The passage of time (replay position) |
| Meal times and macronutrients (CGMacros records) | The demo account, its name and questionnaire answers |
| Forecasts (frozen fs-1.3, checksum enforced) | Medications, reminders and dose history |
| Outcomes, errors, eligibility decisions | The 140 mg/dL alert level (a sample setting) |
| Personalization states reported by the backend | The moment each scripted dose action happens |
| Alerts (existing server-side engine) | — |
| The API calls that produce all of the above | — |

Not real, and not claimed: a live sensor, a connected device, a real user, an
independent accuracy result.

## Known limitations of the demo

1. One participant, nine meals: it shows the system working, not how accurate it is.
2. Out of the model's fit, but previously inspected — not independent validation.
3. The alert level was chosen after seeing forecasts, and is labelled accordingly.
4. Eight days of earlier sensor history are released at the start without the meals
   from that period, so the first forecast has no "previous meal" context.
5. The recording's dates are date-shifted research timestamps under a UTC label.
6. Advancing is serialized by an in-process lock: one worker only.
7. A hand-logged meal during the demo becomes part of that run; reset restores the
   scripted journey.

---

## Addendum — optional RETURNING USER scenario (2026-10-10)

**Starting commit:** `7797121291191b4d06f4813754c98bb578645ce7`

**Question:** can the eight days of pre-demo sensor history be accompanied by
authentic meal records? **Yes.**

| | |
|---|---|
| Historical meals found before the first presented meal | **31** (`2020-05-01 14:23` → `2020-05-08 09:58`) |
| Valid macros / baseline reading / ≥ 20 readings / all horizons / window complete | 31 of 31 on every check |
| Eligible under the v2 policy | **27** |
| Excluded | 4 — another meal inside the 120-minute window (meals 11, 12, 21, 25) |
| Admissible at the first presented meal | 27 (gate: 5) |

Checked from timestamps and macro completeness only; no forecast or error consulted;
no meal chosen or dropped — the backfill is every earlier meal in the recording.

**Implemented** as a second demo mode on the existing replay engine: a mode
parameter, a per-mode bundle and manifest, a separate account, and a re-entrant
lock. History is **replayed** through forecast → outcome → eligibility, never
inserted. The engine's architecture did not change.

**NEW USER preserved.** Its end-state fingerprint
(`5a17f51a9d066a09d4ebb275b4a4b7a8db270b2d09f33bdd57012eb2715adad5`) is identical
before and after; its bundle checksum and manifest plan are unchanged; all its
earlier tests pass unmodified.

**RETURNING USER verified.** The application's own admissible counts match the plan
for all 40 meals; retrieval first activates at meal 6, after five eligible meals; at
the first presented meal 27 are admissible and retrieval is on; all 40 forecasts
reproduce exactly; nothing later than the replay position was ever stored.

**Caveat carried everywhere:** the 31 history meals precede the Day-8 cutoff and
were in the artifact's fit. Their forecasts are in-sample. The nine presented meals
were not in the fit. Neither is independent validation.

| check | result |
|---|---|
| `python -m pytest -q -rsxX` | **1006 passed, 1 skipped, 0 failed (703 original + 303 v2; the skip is the opt-in real-OpenAI call)** |
| `python -m research.freeze.verify_artifacts` | **PASS; artifact and Contract v1.0 checksums unchanged** |
| `python -m contracts.v2.build_contract --check` | **pass** |
| `python -m scripts.demo_verify` (both scenarios) | **60 of 60 checks (28 NEW USER + 32 RETURNING USER); both fingerprints match** |
| `smoke_demo.py` NEW USER, live server | **25 of 25 steps** |
| `smoke_demo.py --scenario returning_user`, live server | **26 of 26 steps** |
| `smoke_v2.py`, same server | **21 of 21 steps; both demo accounts left intact** |

**Contract:** no OpenAPI schema changed. Additive fields on the replay state are
flagged for the shared interface review (DECISIONS D-016). Two example payloads added.

---

## Addendum 2 — simulated connected-health sources (2026-10-10)

**Starting commit:** `72dc33653ab955039595f35920896bf2e407f718`

Four simulated wearable sources (Apple Health, Apple Watch, Fitbit, WHOOP) can be
switched on in either demo account. Their values are generated in code from a fixed
seed, stored through the existing wearable ingestion service with `data_class: demo`,
and released by the existing replay clock. The glucose remains the genuine CGMacros
recording. Nothing contacts a vendor; no token or OAuth exchange exists.

**Reused, not rebuilt:** the wearable store and ingestion service, the metric/unit
table, the device-connection table, the replay clock and release hook, the demo
router and its authorization. **Added:** one module (`demo/sim_devices.py`), three
demo routes, one connection status (`demo_simulated`, migration `0003`), an optional
`provider` filter on the existing observations endpoint.

**Both scenarios preserved.** With every simulated source on, the NEW USER end-state
fingerprint is still `5a17f51a…adad5` and the RETURNING USER one is still
`cd96f4d8…9a0f` — forecasts, feature snapshots, outcomes and glucose are identical.

| check | result |
|---|---|
| `python -m pytest -q -rsxX` | **1020 passed, 1 skipped, 0 failed (703 original + 317 v2; the skip is the opt-in real-OpenAI call)** |
| `python -m research.freeze.verify_artifacts` | **PASS; artifact and Contract v1.0 checksums unchanged** |
| `python -m contracts.v2.build_contract --check` | **pass** |
| `alembic upgrade head` / `check` / `downgrade base` (SQLite, incl. `0003`) | **pass** |
| `python -m scripts.demo_verify` (both scenarios, third pass with devices on) | **72 of 72 checks (34 NEW USER + 38 RETURNING USER); both fingerprints match** |
| `smoke_demo.py` NEW USER, live server | **33 of 33 steps** |
| `smoke_demo.py --scenario returning_user`, live server | **34 of 34 steps** |
| `smoke_v2.py`, same server | **21 of 21 steps** |

**Limitations.** The wearable values are invented and have no physiological link to
the real glucose beside them. This is not an Apple, Fitbit or WHOOP integration and
none of those is verified. Migration `0003`, like the others, has not run on
PostgreSQL.
