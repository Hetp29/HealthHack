# GlucoTwin — blocker register

One register for all three workstreams (master §6). Blocking issues first.

**Legend:** EXT = needs an external account, credential or approval · TEAM = needs a
team decision · HET / NEIL / RUHAAN = waiting on that person's work.

Last updated 2026-10-10, after Ruhaan's review of R0–R8.

**Nothing in this register is verified against a real external system.** OpenAI, Dexcom, LibreView, Fitbit, WHOOP, Apple Health, PostgreSQL, S3, Docker and the Glimpse frontend have each been exercised only by mocks, fixtures, offline rendering or not at all. None may be described as verified until a real integration test against it has passed.

---

## B-001 · No approved source of real-time CGM data — EXT + TEAM — **highest impact**

The frozen model refuses to forecast unless a CGM reading exists within **15 minutes**
before the meal. That is a frozen rule (`baseline_tolerance_minutes`) and is not
negotiable in the wrapper. For a meal being eaten *now*, the reading must therefore
have reached GlucoTwin within about 15 minutes of being taken.

None of the paths available today delivers that:

| path | what it can do | why it cannot anchor a live forecast |
|---|---|---|
| Dexcom developer API | history, outcome scoring | Dexcom delays API data (on the order of an hour in the US). Production access to other people's accounts also needs Dexcom's approval. |
| LibreView | — | Abbott offers no third-party API. |
| File export (Clarity / LibreView) | history, outcome scoring, personal history | The user exports by hand, after the fact. |
| Apple Health bridge | depends on the writing app | No native iOS app exists (B-003). CGM apps are also reported to write to Health with a delay — **verify before relying on it**. |
| Manual entry | live forecast | Works today, but is a fingerstick-style workflow, not a CGM connection. |

**What works without resolving this:** everything retrospective — import history, log
meals after the fact, forecast-versus-actual, and building the personal history that
future forecasts use. Prospective forecasting works end to end in tests and with
manual or demo readings.

**Needs:** a team decision on the real-time source (a Dexcom real-time partnership, a
user-run Nightscout bridge, or accepting manual entry for the prototype).

## B-002 · No provider credentials exist — EXT

No developer application is registered with Dexcom, Fitbit or WHOOP, and no
`OPENAI_API_KEY` is present. The adapters are complete and tested against mock
responses in each provider's documented shape, and **none has ever contacted its
provider.** Nothing is, or should be described as, connected.

To unblock each: register the application, set the redirect URI to
`{GLUCOTWIN_PUBLIC_API_BASE_URL}/api/v2/devices/{provider}/callback`, and provide the
client id and secret through the secret store.

## B-003 · No native iOS app, so no HealthKit or Apple Watch — TEAM

The Glimpse frontend (on `origin/neil`) is a TanStack Start web app with no native
wrapper. A browser cannot read HealthKit. The server half of the bridge exists and is
tested (`POST /cgm/readings` and `POST /wearables/observations` with source
`healthkit_bridge`); the specification for the native half is
`HEALTHKIT_BRIDGE_SPEC.md`. Nobody owns building it.

## B-004 · The frozen artifact has no delivery path to a deployment — HET

`ml/artifacts_*/` is gitignored, so the fs-1.3 artifact is not in the repository and
a clean clone cannot build the image.

**Fixed on this branch:** the `Dockerfile` no longer trains anything and has no
synthetic fallback. It carries only `ml/artifacts_cgmacros_fs13/`, runs
`python -m backend.app.v2.verify_artifact` at build time (no artifact or wrong
checksum → no image) and again before the container starts serving (→ the container
exits). That start-time gate also protects the frozen v1 `/api/predict`, which cannot
check for itself. `.dockerignore` excludes every other model directory.

**Still needed from Het:** an immutable artifact store (e.g. a versioned, write-once
S3 prefix) and a pipeline step that places the five files in the build context.

**Not verified:** the image has never been built — there is no Docker daemon on the
development machine. The guards are tested as Python and as static checks on the
Dockerfile text, not by running a container.

## B-005 · Things real users need that are not built — RUHAAN / TEAM

* Email verification, password reset, MFA.
* Login and upload rate limiting. Nothing in the app throttles requests; put a limit
  at the gateway (HET) until it is built.
* A hosted identity provider, if the team wants one (DECISIONS O-3).
* Data export for a user.

## B-006 · Fitbit's API may be closing to new applications — EXT

Google has been moving Fitbit developers to a Google Health API. The adapter targets
the legacy Fitbit Web API. **Check the current state before registering an
application**; if the legacy API is closed, the adapter needs porting. This was not
verifiable from the development machine.

## B-007 · PostgreSQL has never been exercised — HET

The schema and migration are tested on SQLite. The migration is confirmed to *render*
valid PostgreSQL DDL (`TIMESTAMP WITH TIME ZONE`, 24 tables) in Alembic's offline
mode, but has never run against a PostgreSQL server — none was available. First run
`alembic upgrade head` against a scratch RDS instance and run the test suite with
`GLUCOTWIN_APP_DB_URL` pointed at it.

## B-008 · The S3 image store is written but not exercised — HET

`S3ImageStore` implements the same interface as the tested local store, but there is
no bucket to run it against and `boto3` is not pinned. Verify against the real bucket.

## B-009 · The frontend does not consume v2 yet — NEIL

Glimpse calls the seven v1 endpoints only; it has no login, no upload and no bearer
header (`apiClient.ts` supports GET/POST only). Until it is wired to v2, "the frontend
can consume the result" is demonstrated by the generated example payloads and the
HTTP-level tests, not by the real UI. Everything Neil needs is in `HANDOFF_NEIL.md`.

## B-010 · File-import parsers are unverified against genuine exports — RUHAAN

The Dexcom Clarity and LibreView parsers follow the published column layouts and pass
fixture tests. Neither has been run on an export from a real account. One real file
of each kind is enough to close this.

## B-011 · Libre data cannot activate retrieval personalization — model limitation

LibreView exports the historic series at a 15-minute interval: 8 readings in a
120-minute window. The frozen retrieval rule needs 20. A Libre user's completed meals
are scored but never become retrieval history. This is a property of the frozen model
and is not worked around (interpolating readings would fabricate data).

## B-012 · Durable scheduling is not provisioned — HET

Three workers exist as idempotent one-shot commands (`workers/outcome_scoring.py`,
`workers/reminders.py`, `workers/device_sync.py`). They need a real scheduler. The
API already scores outcomes and materializes reminders on demand, so nothing breaks
without them — but push/email reminders for someone who does not have the app open
cannot exist until both a scheduler and a delivery channel do.

## B-013 · No clinical review of medication education — TEAM

`GET /medication-education` returns class-level text with MedlinePlus citations and
`review_status: "not_clinically_reviewed"`. It should be shown as a draft until
someone qualified has reviewed it.

---

## B-014 · Level B calibration is unevaluated and not approved — RUHAAN

Ruled NOT APPROVED FOR ENABLEMENT (DECISIONS D-007). It stays off. Approval may be
requested only after a leakage-safe chronological replay on the development folds.
Not started; out of scope during team integration.

## B-015 · Shared interface review has not happened — HET, NEIL, RUHAAN

Every v2 endpoint is PROPOSED (DECISIONS R-001). Het and Neil have not yet reviewed
the registry. Until they have, field names can still change.

---

## Resolved

| | resolved | how |
|---|---|---|
| D-006 fixed-offset clock projection | 2026-10-10 | Replaced by IANA per-observation conversion with a transition-bounded lookback; 22 DST tests. See DECISIONS D-006. |
| Dockerfile trained a synthetic model | 2026-10-10 | Training removed; build-time and start-time checksum gates. See B-004 for what remains. |
