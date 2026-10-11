# GlucoTwin Contracts — version 1.0 (FROZEN)

**Owner / sole approver:** Ruhaan Shah
**Consumers:** Het (data layer), Neil (frontend)
**Source of authority:** `GlucoTwin_MASTER_Build_Specification.docx` v1.0, sections
*"Frozen cross-team interface: version 1.0"* and *"Frozen REST API: version 1.0"*.

This directory is the contract. If an implementation disagrees with these files, the
implementation is wrong. Build against these files; you do not need access to the
backend source, a running server, or the ML artifacts to start work.

---

## What is in here

| Path | Purpose |
|---|---|
| `openapi.yaml` | **Single source of truth.** OpenAPI 3.0.3 description of all 7 endpoints and 27 schemas. |
| `schemas/*.schema.json` | Self-contained JSON Schema (draft 2020-12) files, **generated** from `openapi.yaml`. Validate payloads with any standard validator. |
| `examples/*.json` | Concrete example payloads, **generated**, every cross-field invariant satisfied. |
| `canonical_schema_v1.md` | The canonical data-record contract (fields, units, timestamp rules) that the data layer must produce. |
| `build_schemas.py` | Regenerates `schemas/` from `openapi.yaml`. |
| `build_examples.py` | Regenerates `examples/`. |

Regenerate after any contract edit, and commit the regenerated output in the same commit:

```bash
python contracts/build_schemas.py
python contracts/build_examples.py
python -m pytest tests/test_contracts.py -v
```

Do not hand-edit anything under `schemas/` or `examples/` — it will be overwritten.

---

## The 7 endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | `{status, version}`. 200 whenever the process serves. Does **not** imply the model is ready. |
| GET | `/api/participants` | Roster. May be an empty list. |
| GET | `/api/participants/{id}/history?start=&end=` | Observed CGM / meals / heart rate, chronologically ascending. |
| POST | `/api/predict` | Returns `ForecastResponse`. |
| POST | `/api/compare` | P1. Two `ForecastResponse` objects plus a mandatory caveat. Returns 503 `not_implemented` until built. |
| GET | `/api/model/metrics` | **Measured** evaluation results, or an explicitly empty report. |
| GET | `/api/model/info` | Model provenance and readiness. Use this for readiness, not `/health`. |

### Readiness, not liveness

`GET /health` returns 200 even with no trained model, so that a load balancer can tell
"process up" from "model ready". To decide whether prediction will work, read
`GET /api/model/info` → `model_version`. It is the literal string `"none"` when no
artifact is loaded, and `metrics_available` is `false` until `ml.evaluate` has run.

---

## Hard invariants (tested in `tests/test_contracts.py`)

1. **`points` has exactly 25 entries**, at minutes `0, 5, 10, …, 120` inclusive, in
   strictly ascending minute order. Not 24, not 26.
2. **Minute 0 is the measured pre-meal baseline glucose**, not a model output.
3. **`glucose_at_minutes` is derived from `points`** by exact minute lookup. Keys are the
   strings `"30"`, `"60"`, `"90"`, `"120"`. It is never generated independently, so it can
   never disagree with the curve.
4. **`metrics` is derived from the 25 sampled points**:
   - `peak_mg_dl` = max glucose over the points.
   - `peak_delta_mg_dl` = `peak_mg_dl` − baseline (minute 0). May be ≤ 0.
   - `time_to_peak_minutes` = minute of the peak, **earliest minute wins on ties**.
   - `incremental_auc_mg_dl_min` = five-minute trapezoidal integration of
     `max(glucose − baseline, 0)` over minutes 0…120, in mg/dL·min.
     **Strictly non-negative. This is positive iAUC, NOT signed net excursion.**
5. **`lower_mg_dl` / `upper_mg_dl` are `null`** unless `uncertainty_status` is
   `empirical_holdout`. When they are `null`, clients must render
   "uncertainty not calibrated" and must not draw a band.
6. **`limitations` is never empty.** It always contains the research-use disclaimer.
7. **`drivers` may legitimately be `[]`.** An empty list with an honest note in
   `limitations` is correct behavior when the loaded model family has no attribution.
   A fabricated or static driver list presented as attribution is a P1 defect.
8. **All datetimes are RFC3339 with an explicit offset**, normalized to UTC (`Z`).
   Offset-naive input is rejected `422` with code `naive_datetime`.
9. **A missing optional value is `null`**, never a silent `0`.

> **On the example numbers.** The master specification's sample payload shows an
> illustrative `incremental_auc_mg_dl_min` of `3200`. Integrating the master's own
> illustrative curve with the frozen trapezoidal rule gives `3015.0`, which is what
> `examples/predict_response.json` contains. The **rule** is frozen; the master's sample
> number was an illustrative placeholder, and the spec states those numbers are "never
> validation claims". Treat `3015.0` as the arithmetically correct value for that curve,
> and treat neither number as a performance result.

---

## Error envelope

Every non-2xx response — including FastAPI/Pydantic validation failures, which are
translated — uses the same shape:

```json
{ "code": "participant_not_found",
  "message": "Unknown participant identifier.",
  "detail": { "participant_id": "nope_p999" } }
```

`detail` is always a JSON **object** (never a bare string or array), may be empty, and
never carries stack traces or participant-level physiological values. Branch on `code`:

| `code` | HTTP | Meaning |
|---|---|---|
| `invalid_request` | 422 | Schema/range violation (negative macros, non-finite numbers, …). |
| `naive_datetime` | 422 | A datetime arrived without a UTC offset. |
| `future_context_override` | 422 | `context_override.as_of` was later than `meal_timestamp`. |
| `insufficient_premeal_context` | 422 | No CGM observation close enough before the meal to establish a baseline. |
| `participant_not_found` | 404 | Unknown `participant_id`. |
| `model_not_trained` | 503 | No artifact loaded. Run `python -m ml.train`. |
| `data_layer_unavailable` | 503 | The data repository could not be reached. |
| `not_implemented` | 503 | Endpoint reserved by the contract but not yet built (`/api/compare`). |
| `internal_error` | 500 | Unexpected failure. Message is generic by design. |

---

## `prediction_status` and the fallback reason

`prediction_status` is exactly one of:

- **`personalized`** — the participant had at least the configured minimum number of
  training meal episodes, and a shrunk participant-specific correction was applied.
- **`generalized_fallback`** — the cohort-level model was used alone.

There is **no separate `fallback_reason` field**; adding one would be a contract change.
When status is `generalized_fallback`, **the first entry of `limitations` states the
reason** in plain language, including the actual episode count and threshold. See
`examples/predict_response_generalized_fallback.json`.

Frontend rule: display `prediction_status`, `data_source`, and `model_version` on every
prediction screen, and surface `limitations[0]` prominently when the status is a fallback.

---

## `context_override` — an explicit implementation choice

The master specification fixes `context_override` as a present, nullable member of the
predict request but does not enumerate its fields. **This is the v1.0 definition**, and
it is flagged as our implementation choice rather than organizer-given:

```json
{ "premeal_glucose_mg_dl": 112.0,
  "recent_heart_rate_bpm": 74.0,
  "minutes_since_prior_meal": 300,
  "as_of": "2026-10-08T12:28:00Z" }
```

Every member is optional and defaults to `null`, in which case the server derives that
value from stored history **truncated at `meal_timestamp`**. The object is additive-only;
new members require a contract bump.

**Leakage guard:** `as_of` must be ≤ `meal_timestamp`. A later value is rejected `422`
`future_context_override`. The override exists so a clinician can explore a
hypothetical pre-meal state; it can never be used to inject a post-meal observation.

---

## Using these contracts

### Neil (frontend)

Generate TypeScript types from the OpenAPI document rather than hand-writing them:

```bash
npx openapi-typescript ../contracts/openapi.yaml -o src/types/api.ts
```

Bind the UI against `examples/*.json` as DEV MOCK fixtures (centralized in one file,
clearly tagged), then swap to the live API. The two prediction examples give you both
render states: `predict_response.json` (personalized) and
`predict_response_generalized_fallback.json` (fallback, 3 limitations).
`metrics_response.json` is deliberately the **empty** metrics report — build the "no
validated metrics yet" state from exactly that payload.

### Het (data layer)

`canonical_schema_v1.md` is your target. The record shapes the API exposes
(`CgmPoint`, `MealRecord`, `HeartRatePoint`, `ParticipantSummary`) are in
`openapi.yaml`; your `DataRepository` must be able to produce them. The Python Protocol
the backend will call is declared in `backend/app/services/data_access.py` —
implement that interface and the backend binds to it with no changes on my side.

Validate your output directly:

```python
import json
from jsonschema import Draft202012Validator
schema = json.load(open("contracts/schemas/history_response.schema.json"))
Draft202012Validator(schema).validate(my_history_payload)
```

---

## Changing the contract

Do not. If you must:

1. Open a change proposal stating the field, the current shape, the proposed shape, the
   concrete justification, and the blast radius for the other two owners.
2. Ruhaan approves or rejects. Silent changes are a P0 defect.
3. On approval: bump `info.version`, edit `openapi.yaml` **only**, regenerate `schemas/`
   and `examples/`, update this README, update `tests/test_contracts.py`, and attach a
   migration note — all in one commit.
4. Announce the new version and get explicit ACKs from both consumers.

## ACK register

Both consumers must acknowledge contract v1.0 before implementing against it.

| Owner | Scope acknowledged | ACK |
|---|---|---|
| Het | Canonical records, units, timestamp normalization, `DataRepository` interface | ☐ pending |
| Neil | Endpoint paths, response shapes, enums, error codes, null-bound rule | ☐ pending |
