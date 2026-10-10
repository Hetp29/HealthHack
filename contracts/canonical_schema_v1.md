# Canonical Data Schema — version 1.0 (FROZEN)

**Owner / sole approver:** Ruhaan Shah · **Primary implementer:** Het
**Source of authority:** `GlucoTwin_MASTER_Build_Specification.docx` v1.0,
*"Frozen cross-team interface: version 1.0"* and *"Data model and storage design"*.

Development fixtures and the organizer dataset must arrive at **identical canonical
records** through one stable repository interface. The adapter absorbs format
differences; the canonical schema never bends to accommodate a source file.

---

## Global rules

- **Timestamps.** RFC3339 / ISO 8601 with an explicit UTC offset. The data layer
  normalizes to UTC and preserves the source timezone on the participant record when
  provided. Display may localize; storage does not. Offset-naive timestamps are a
  rejection, not a guess.
- **Units.** Glucose `mg/dL`. Heart rate `bpm`. Macronutrients `g`. Energy `kcal`.
  Doses keep their original units with an explicit `dose_unit`. No implicit conversion.
- **Identifiers.** `participant_id` and `meal_id` are opaque strings. No consumer parses
  them; no real patient identifier ever appears in one.
- **Missingness.** A missing optional value is `null`. Never a silent `0`, never an
  empty string, never a sentinel like `-1`.
- **Rejections are counted, not deleted.** Every rejected row lands in a quarantine with
  a reason, and the counts appear in the quality report.
- **Provenance.** Every record carries a `data_source`. Every import records a source
  manifest with a SHA-256 checksum.

`data_source` ∈ `synthetic_dev` | `official` | `mixed` | `unknown`.

---

## Records

### `participants`

| Field | Type | Req | Rules |
|---|---|---|---|
| `participant_id` | string | ✓ | Primary key. Opaque. |
| `display_name` | string | ✓ | Synthetic label, e.g. `Participant 001`. Never a real identifier. |
| `timezone` | string \| null | — | IANA name (e.g. `America/New_York`) when the source supplies one. |
| `data_source` | enum | ✓ | Provenance label. |

### `cgm`

| Field | Type | Req | Rules |
|---|---|---|---|
| `participant_id` | string | ✓ | Composite PK with `timestamp`. |
| `timestamp` | datetime (UTC) | ✓ | 5-minute nominal cadence. Gaps are allowed and expected. |
| `glucose_mg_dl` | float | ✓ | Reject negative and non-finite. Reject outside a documented plausible range. |
| `data_source` | enum | ✓ | |

Cadence is **nominal**, not guaranteed. Consumers must never assume a gap-free grid.

### `heart_rate` — optional modality

| Field | Type | Req | Rules |
|---|---|---|---|
| `participant_id` | string | ✓ | Composite PK with `timestamp`. |
| `timestamp` | datetime (UTC) | ✓ | |
| `heart_rate_bpm` | float | ✓ | Reject non-finite and physiologically impossible values. |
| `resting_heart_rate_bpm` | float \| null | — | |
| `data_source` | enum | ✓ | |

The modality may be entirely absent for a participant. That must not crash the
prediction pipeline — it degrades to a missing-indicator feature.

### `meals`

| Field | Type | Req | Rules |
|---|---|---|---|
| `meal_id` | string | ✓ | Primary key. |
| `participant_id` | string | ✓ | FK → `participants`. |
| `timestamp` | datetime (UTC) | ✓ | Meal start. |
| `carbs_g` | float \| null | — | Optional **at ingestion**. |
| `protein_g` | float \| null | — | |
| `fat_g` | float \| null | — | |
| `fiber_g` | float \| null | — | |
| `calories_kcal` | float \| null | — | |
| `image_ref` | string \| null | — | Opaque reference held by the data layer. |
| `data_source` | enum | ✓ | |

Macros are optional at ingestion but **required for prediction**: `POST /api/predict`
mandates all five. A stored meal with missing macros cannot be forecast without a
reviewed estimate. Nutrition is **not** inferred from meal images unless a reviewed,
licensed service is in use.

`image_ref` is an opaque handle, never a filesystem path resolved from client input —
path traversal on the upload/serve path is an explicit invariant test.

### `medications` — optional modality

| Field | Type | Req | Rules |
|---|---|---|---|
| `id` | string | ✓ | Primary key. |
| `participant_id` | string | ✓ | FK → `participants`. |
| `timestamp` | datetime (UTC) | ✓ | Administration event. |
| `medication_class` | string | ✓ | Class label as supplied. |
| `dose` | float \| null | — | Original magnitude, unconverted. |
| `dose_unit` | string \| null | — | Required whenever `dose` is present. |
| `data_source` | enum | ✓ | |

**No clinical-effect assumption.** A medication name is never mapped to a
pharmacodynamic effect. The model uses only *exposure presence/recency*, and the UI
offers no dosing, diagnostic, or treatment advice.

### `activity` — optional, not required for MVP

| Field | Type | Req |
|---|---|---|
| `participant_id` | string | ✓ |
| `timestamp` | datetime (UTC) | ✓ |
| `activity_value` | float \| null | — |
| `activity_unit` | string \| null | — |
| `data_source` | enum | ✓ |

### `ingestion_runs`

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | Primary key. |
| `source` | string | Source directory or file identifier. |
| `schema_version` | string | Canonical schema version, e.g. `1.0`. |
| `created_at` | datetime (UTC) | |
| `records` | int | Accepted record count. |
| `rejected` | int | Quarantined record count. |
| `error_summary` | string \| null | Aggregated reasons, no row-level sensitive values. |
| `sha256` | string | Checksum of the ingested source. |

---

## Storage

SQLite for the local MVP, behind an abstracted storage interface so PostgreSQL can be
substituted if an AWS database turns out to be permitted. CSV/Parquet are allowed for ML
export; **SQLite is the source of truth for app metadata.** No AWS database provisioning
until account permissions are actually known.

- Index `(participant_id, timestamp)` on every event table.
- Idempotent upsert on the natural event key, so a re-run produces no duplicate rows.
- Foreign keys enforced from every event table to `participants`.
- Queries filter strictly by `participant_id`; cross-participant contamination is a P0 defect.
- `init_db` never drops tables by default.

---

## Repository interface

The backend consumes exactly one read interface. The authoritative Python declaration —
with full type signatures and the error contract — is the `DataRepository` Protocol in
`backend/app/services/data_access.py`. Implement that and the backend binds with no
change on the ML/backend side.

```
list_participants() -> list[ParticipantSummary]
get_participant(participant_id) -> ParticipantSummary | None
get_history(participant_id, start=None, end=None) -> ParticipantHistory
get_meals(participant_id, start=None, end=None) -> list[MealRecord]
export_model_examples(cutoff=None) -> list[MealRecord]   # ML export, deterministic order
```

Contract on every method:

- **Deterministic ordering.** Ascending `timestamp`, then ascending `meal_id` as a
  tie-breaker. Two calls with the same arguments return identical sequences.
- **`None` bounds mean unbounded.** `start` and `end` are inclusive when supplied.
- **Unknown participant.** `get_participant` returns `None`; it does not raise. The
  backend converts that to `404 participant_not_found`.
- **Absent optional modality.** Return an empty list, not `None`.
- **Offset-aware datetimes only**, in UTC, on both input and output.

---

## Changing this schema

A new organizer column is handled by **config-driven mapping in the adapter**
(original column → canonical field), not by editing this document. Only a genuine gap in
the canonical model justifies a v1.1 bump, which requires Ruhaan's approval, a migration
note, and updated tests in the same commit.

If the organizer dataset lacks something this schema expects — meal images, post-meal
ground truth, macro completeness — **report the gap explicitly. Do not invent values.**
