# Apple Health bridge — interface specification

**Status: the native half does not exist.** There is no iOS application in this
project, and nobody is assigned to build one (BLOCKERS B-003). This document
specifies the interface so the unsupported component is isolated and unambiguous. It
is not a claim that Apple Health or Apple Watch integration works.

## Why a bridge is needed

HealthKit is an on-device database. It can be read only by a native iOS application
that holds the HealthKit entitlement and that the user has granted permission to,
type by type. A web browser — which is what Glimpse is — has no access to it at all,
and neither does a server. Apple Watch data reaches us only by way of the paired
iPhone's Health store.

So the integration has two halves:

```
┌──────────────── iPhone ────────────────┐         ┌──────── GlucoTwin API ────────┐
│ Native app (NOT BUILT)                 │  HTTPS  │ IMPLEMENTED AND TESTED        │
│  • requests HealthKit read permission  │ ──────▶ │ POST /api/v2/cgm/readings     │
│  • runs anchored queries per type      │         │ POST /api/v2/wearables/…      │
│  • uploads new samples, keeps anchors  │         │  validate · dedupe · store    │
└────────────────────────────────────────┘         └───────────────────────────────┘
```

## Server half (done)

Both endpoints require the user's bearer token and the `necessary_processing`
consent, and are idempotent, so a bridge may safely re-send after a failed upload.

### Glucose — `POST /api/v2/cgm/readings`

```json
{
  "source": "healthkit_bridge",
  "device_id": "<HKSourceRevision bundle id or product type>",
  "readings": [
    {"observed_at": "2026-09-20T15:55:00Z", "value": 112, "unit": "mg/dL",
     "record_kind": "cgm", "source_record_id": "<HKSample UUID>"}
  ]
}
```

* `HKQuantityTypeIdentifier.bloodGlucose`. Send the unit the sample is read in and
  say which it is; the server converts and keeps the original.
* `record_kind`: `cgm` for a continuous sensor's samples, `fingerstick` for a meter.
  Decide from the sample's source, not from the value.
* `observed_at` = the sample's `startDate`, as an instant with an offset.
* Up to 5000 readings per request.

### Wearable samples — `POST /api/v2/wearables/observations`

```json
{
  "source": "healthkit_bridge",
  "observations": [
    {"metric": "heart_rate", "value": 72, "unit": "bpm",
     "observed_at": "2026-09-20T15:50:00Z", "source_record_id": "<HKSample UUID>"}
  ]
}
```

| metric | HealthKit type | unit sent | `observed_at` / `observed_end_at` |
|---|---|---|---|
| `heart_rate` | `heartRate` | `bpm` | sample start |
| `resting_heart_rate` | `restingHeartRate` | `bpm` | sample start |
| `steps_daily` | `stepCount`, summed per local day by a statistics query | `count` | local midnight → next midnight |
| `sleep_duration` | `sleepAnalysis`, asleep stages summed per session | `min` | session start → end |
| `hrv_sdnn` | `heartRateVariabilitySDNN` | `ms` | sample start |
| `workout` | `HKWorkout` | `min` (duration) | workout start → end |

Apple reports HRV as **SDNN**; Fitbit and WHOOP report **RMSSD**. They are different
statistics and are stored as different metrics. Do not relabel one as the other.

`source_record_id` must be stable for the same sample. For per-sample types use the
`HKSample` UUID. For daily aggregates use a deterministic key such as
`steps:2026-09-20` — re-sending with a new value updates the stored one, which is how
a step total that grows through the day stays correct.

## Native half (to be built) — requirements

1. **Entitlement and usage strings.** HealthKit capability; `NSHealthShareUsageDescription`
   stating plainly what is read and why. Read-only: request no write access.
2. **Type-scoped consent.** Request only the types the user chose in onboarding
   (`wearable_permissions`). HealthKit does not reveal whether read access was denied
   — an unauthorized type simply returns no samples — so the app must treat "no data"
   and "not permitted" as the same visible state and never claim a type is connected
   until samples have actually been uploaded and acknowledged.
3. **Anchored queries.** One `HKAnchoredObjectQuery` per type, persisting the anchor
   only after the server acknowledges the batch. This gives exactly-once delivery
   from the app's side; the server's idempotency covers the rest.
4. **Deletions.** Anchored queries also report deleted samples. There is no delete
   endpoint yet; add one (`DELETE` by `source_record_id`) when this is built.
5. **Background delivery.** `enableBackgroundDelivery` is best-effort and throttled
   by iOS. It cannot guarantee a reading reaches the server within 15 minutes.
6. **Authentication.** The app signs in with the same account and stores the session
   token in the Keychain.
7. **No health data in logs or analytics.**

## What this bridge can and cannot deliver

* It **can** supply history for outcome scoring and personal history.
* Whether it can supply glucose fresh enough for a live forecast depends on the app
  that writes glucose into Health. Some CGM apps are reported to write with a delay
  of hours. **Measure this with the actual sensor before promising live forecasts
  through Apple Health** (BLOCKERS B-001).
* Wearable samples uploaded here are stored and displayed only. They are not inputs
  to the forecast (DECISIONS D-008).

## Connection status

Uploading through the bridge creates a `healthkit_bridge` entry in `GET /devices`. It
becomes `connected` only once real samples have been stored.
