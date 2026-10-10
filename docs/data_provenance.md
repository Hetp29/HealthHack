# Data provenance, validation and quality

**Owner:** Het Patel · **Schema version:** 1.0 (storage layout v2) · **Status:** synthetic fixtures plus both
organizer datasets (CGMacros, MetaboNet) ingested. Full audit: `docs/data_audit/`.

## 1. Data sources

| `data_source` | What it is | Where | Status |
|---|---|---|---|
| `synthetic_dev` | Fictional participants from our own hand-written simulator | `data/fixtures/synthetic_dev/` | In use |
| `official` — CGMacros | Organizer-supplied (Das et al., Sci Data 2025); 45 participants, non-T1D | `data/official_raw/` (gitignored) → `data/glucotwin.db` | In use (primary) |
| `official` — MetaboNet | Organizer-supplied (Wolff et al., JDST 2026); 1,188 T1D subjects from 15 studies | `data/official_raw/` (gitignored) → `data/metabonet.db` | Audited in full; 8-day windows ingested |

Participants are namespaced by dataset (`cgmacros_NNN`, `metabonet_<source>_<id>`) and carry `source_dataset`.
Dataset-specific transformations and exclusions: `docs/data_audit/data_quality.md`.

**`synthetic_dev` is not real data.** The people are invented. Simulator parameters were chosen by hand and not fitted
to any human data. Any metric computed on this data shows that the software works. It is **not** clinical evidence and
**not** a validation claim.

## 2. Provenance guarantees

- Every stored row has a `data_source` value and the `ingestion_run_id` of the run that last wrote it.
- Every import adds a row to `ingestion_runs` with: `run_id`, `source` (relative path), `schema_version`, `created_at`
  (UTC), `records` (accepted count), `rejected`, `warnings`, `error_summary` (reason codes and counts only, no data
  values), `sha256`, `status`, `mapping_name` and `report_path`.
- `sha256` is a checksum over the relative path and SHA-256 of every input file, in sorted order. The mapping config's
  own SHA-256 is saved in the run report.
- Every run writes a JSON report to `data/reports/runs/<run_id>.json`. It contains totals, per-modality counts
  (read / accepted / accepted-with-warnings / rejected / duplicates / inserted / updated / unchanged), rejection and
  warning counts by code, batch-level warnings, a quarantine sample and a quality summary.
- Rejected rows go to the `quarantine` table with their source file, row number, reason codes and the mapped row as
  JSON. They are counted, never silently deleted.
- A failed run is rolled back completely. It is still logged in `ingestion_runs` with `status = failed`.

## 3. Canonical rules applied at ingestion

- Timestamps are RFC3339 and stored as UTC. A timestamp **without a timezone offset is rejected**. The only exception
  is when a mapping config explicitly names the source timezone (`assume_timezone`). Even then, local times that are
  ambiguous or never exist because of daylight-saving changes are rejected rather than guessed.
- Units: glucose mg/dL, heart rate bpm, nutrition g, energy kcal. The only conversions are ones a mapping declares
  (mmol/L→mg/dL ×18.0182, mg→g ×0.001, kJ→kcal ÷4.184), and each is recorded in the run report. **Medication doses are
  never converted.**
- A missing optional value stays `null`, never `0`. A real `0` (for example, 0 g of fat in an apple) is a value.
  `"nan"` is **not** treated as missing by default. It is rejected as a non-finite number unless the dataset profile
  shows it is the source's marker for missing data, and the mapping says so.
- Identifiers are opaque strings. Official IDs get a mapping-defined prefix (default `official_`), so they can never
  collide with fixture IDs.

## 4. Validation rules (`data/validation.py`, thresholds in `Thresholds`)

Each row ends up in one of three outcomes: **rejected** (quarantined), **accepted with warnings**, or **accepted**.

| Modality | Rejected (impossible or unusable) | Accepted with warning (unusual but possible) |
|---|---|---|
| any | missing participant ID; missing, unparseable, timezone-less or DST-ambiguous/nonexistent timestamp; non-numeric or non-finite number; unknown participant (foreign-key check); conflicting duplicates in the source | — |
| CGM | glucose ≤ 0; glucose > 1500 mg/dL (sensor, sentinel or unit error) | glucose < 40 or > 400 mg/dL (outside the typical CGM reporting range) |
| Heart rate | HR or resting HR < 20 or > 300 bpm | HR < 35 or > 220; resting HR > 120 |
| Meals | missing `meal_id`; any macro < 0 or > 1000 g; kcal < 0 or > 10000 | no nutrition logged; incomplete nutrition; carbs > 250 g; kcal > 3000; fiber > carbs; kcal differs from Atwater 4C+4P+9F by more than 35% **and** more than 75 kcal; unsafe `image_ref` (absolute path or `..`) is set to null |
| Medications | missing `medication_class`; dose < 0; dose without `dose_unit` | dose = 0; unit without a dose |
| Activity | value < 0; value without a unit; empty record | unit without a value |
| Participants | missing ID or display name | invalid IANA timezone is set to null |

**Checks on the whole batch** (reported, not rejected): CGM values with a per-participant median below 30 (looks like
mmol/L mislabeled as mg/dL); one participant and medication class recorded with different dose units; more than one
activity unit; several meals for one participant at the same instant; out-of-order source rows (counted, then stored
in sorted order); mapped modalities whose files are missing; modalities not mapped at all.

**Duplicates.** If rows share a natural key and are identical, they collapse to one row, and the count of dropped
copies is reported. If they share a key but have different values, every copy is quarantined
(`conflicting_duplicate_in_source`). The data layer does not choose a winner.

**Natural keys** (used for upserts): cgm `(participant_id, timestamp)`, heart_rate `(participant_id, timestamp)`,
meals `meal_id`, medications `(participant_id, timestamp, medication_class)`, activity
`(participant_id, timestamp, activity_unit)`. Re-importing identical data changes nothing. A changed value counts as an
update and re-stamps `ingestion_run_id`.

The thresholds are deliberately cautious and listed in one place, so the ML owner can review them. They flag extreme
but possible values rather than deleting them.

## 5. Synthetic fixture generation (`data/fixtures/generator.py`)

- Seed `20261011`, generator version 1.0.0, local dates 2026-10-01 to 2026-10-07. That leaves 2026-10-08 free for
  "Day 8" demos.
- Six phenotypes with distinct glucose baselines (88–121 mg/dL), carbohydrate sensitivity (0.60–1.55 mg/dL per g),
  time to peak (35–75 min), evening-meal effect, dawn rise, activity sensitivity and resting heart rate. Participants
  are spread across five US timezones, including Phoenix, which has no daylight-saving time.
- Glucose = baseline + dawn bump + gamma-shaped carbohydrate response (slowed by fat and reduced by fiber) + small
  protein response − activity effect + AR(1) drift + sensor noise. Values are rounded and clipped to 40–400 mg/dL.
- Realism and edge cases: per-participant CGM clock offsets (0–4 min), ~1.5% random dropouts plus one signal-loss
  block per participant (up to 5 h, a simulated sensor change), and meal logging times that differ from true times by
  0–8 min. Logged macros carry ~8% self-report noise. 12% of meals are missing fiber and 8% are missing calories.
  p005 logs snacks as carbs only, and one p003 meal has no nutrition logged at all. Snacks 45–90 min after lunch create
  overlapping meal responses. p005 has no heart-rate or activity data; p006 is a sparse logger (eats normally, logs
  3 meals in the week). Medications exist only for p002 (biguanide
  500 mg twice daily, with occasional skipped doses) and p004 (basal insulin 14 units nightly).
- **Medications have no simulated effect on glucose.** The contract forbids linking a medication name to a clinical
  effect, so a model trained on this data cannot learn one.
- There are no images. `image_ref` is null everywhere.
- **Hidden variables are kept separate.** The generating parameters and true meal times and macros are in
  `data/fixtures/simulator_truth/simulator_truth.json`, outside the folder the model reads. A test ensures no hidden
  field appears as a column in the model-readable files. Nothing outside the tests may read that file.
- Output is deterministic. The same seed produces byte-identical files, and a test checks the committed fixtures
  against a fresh run.

Observed in the committed fixtures (from `python -m data.quality`): 11,774 CGM readings, 9,680 heart-rate readings,
162 meals, 20 medication events and 3,360 activity windows. CGM coverage is 95.7–98.3% of a 5-minute grid. Median
glucose per participant ranges from 98 to 152 mg/dL.

## 6. When the official dataset arrives (procedure)

1. Store the files outside git, in `data/official_raw/` or elsewhere. Record the source, time received, SHA-256 and
   permitted use in the run log and in this document.
2. Profile the data **before changing anything**: `python -m data.adapters.profile --path <dir>`. This gives the file
   inventory, checksums, row counts, per-column null and NaN counts, types, ranges, timestamp spans with and without a
   timezone offset, image file count, unit hints, and column names that look like leakage risks (`post`, `peak`, `auc`,
   `target`, `day 8`, …).
3. Copy `data/mapping/official_template.toml` to `official.toml` and map source columns to canonical fields. Declare
   units, timestamp format and the timezone policy, and add the source's NaN marker to `null_tokens` only if the
   profile shows it is used for missing values.
4. Run the profile again with `--mapping` to get a validation dry run: participant, CGM and meal counts, timestamp
   coverage, missing data by modality, image and label availability, duplicate and invalid counts, and unit warnings.
5. Send the profile to Ruhaan **before** importing. If the official data conflicts with schema v1.0, stop and file a
   mapping and impact note. Do not change the schema without his approval.
6. Import it: `python -m data.adapters.ingest --source official --path <dir> --mapping data/mapping/official.toml`.
   Anything the dataset lacks (images, post-meal ground truth, macros) is reported as a gap and never filled with
   invented values.

## 7. Known discrepancies and implementation choices (for Ruhaan's review)

1. **Column name.** The master spec's table list says `participants.source`, but the canonical field and the contract
   (`canonical_schema_v1.md`) say `data_source`. We store `data_source`, as the frozen contract requires.
2. **Extra internal tables and columns.** We added `quarantine`, `schema_meta`, and `ingestion_run_id` on every row,
   plus `data_source`, `warnings`, `status`, `mapping_name` and `report_path` on `ingestion_runs`. They are needed for
   the quarantine and provenance rules. No API-facing field changed.
3. **Medication `id`.** If the source provides no ID, it is `med_` + SHA-256 of
   (participant, UTC timestamp, class), truncated, so re-imports stay idempotent.
4. **Activity key.** Activity uses `(participant_id, timestamp, activity_unit)`, with a deterministic `id`. A value
   with no unit is rejected, because it can't be interpreted.
5. **Unknown participant.** Resolved against `backend/app/services/data_access.py`: the backend-facing
   `SqliteDataRepository` returns `None` from `get_participant` and an **empty** history / meal list for an unknown
   id, as the Protocol requires. (The older pydantic `data.repository.DataRepository`, kept for data-layer tooling,
   still raises `ParticipantNotFoundError`.)
6. **Meal IDs from official data.** If the official data has no meal ID, `generate_meal_id = true` builds
   `<participant_id>_meal_<UTC YYYYMMDDTHHMMSS>`.
7. **Dependencies.** SQLAlchemy and pyarrow are not in the root `requirements.txt` (owned by Ruhaan). They are
   listed in `data/requirements.txt`, and we've asked for them to be added to the root file.
8. **Storage layout v2 (internal).** Added `cgm.sensor`, `participants.source_dataset` and a `cgm_secondary` table
   (second simultaneously worn sensor). No API-facing field changed. A local DB from layout v1 must be rebuilt;
   `init_db` refuses to open it and says how.
9. **Floating local time for official data.** Both datasets give offset-naive, date-shifted wall-clock times with no
   timezone. They are stored as UTC-labelled wall clock under an explicit `assume_timezone="UTC"` policy, and
   `participant.timezone` is null. This needs Ruhaan's acknowledgement; see `docs/data_audit/data_quality.md`.
