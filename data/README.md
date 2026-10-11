# GlucoTwin data layer (`data/`)

**Owner:** Het Patel · **Contract:** canonical schema v1.0 (`contracts/canonical_schema_v1.md`) ·
**Storage:** SQLite via SQLAlchemy · **Sources:** `synthetic_dev` fixtures, **CGMacros** and **MetaboNet** (organizer datasets).
Dataset audit: [`docs/data_audit/summary.md`](../docs/data_audit/summary.md). Setup, resources and time semantics: [`docs/data_setup.md`](../docs/data_setup.md).

This package loads, validates and stores the data, records where it came from, and provides one read
interface (`DataRepository`). It does **not** construct targets, engineer features, train models or serve HTTP.

## Quick start (clean clone)

```bash
pip install -r requirements-dev.txt -r data/requirements.txt   # Python 3.12; adds SQLAlchemy 2.0.43
python -m data.seed                                   # create data/glucotwin.db + load synthetic_dev fixtures
python -m pytest tests/test_data_*.py -q              # data-layer tests (temp DBs only)
```

`python -m data.seed` is safe to re-run: identical input writes nothing new. Use `--reset --yes` to drop and rebuild.

## Organizer datasets (CGMacros, MetaboNet)

Place the downloads in `data/official_raw/` (gitignored), then:

```bash
pip install -r requirements-dev.txt -r data/requirements.txt          # adds SQLAlchemy + pyarrow
unzip -q data/official_raw/cgmacros-*/CGMacros_dateshifted365.zip -d data/official_raw/cgmacros_extracted

python -m data.adapters.cgmacros --db-url sqlite:///data/glucotwin.db     # 45 participants, ~5 min
python -m data.adapters.metabonet audit                                   # streams 132.8M rows, ~5 min
python -m data.adapters.metabonet ingest --db-url sqlite:///data/metabonet.db [--max-subjects 100]
python -m data.audit --metabonet-subjects data/reports/profiles/metabonet_subjects.csv   # -> docs/data_audit/
```

Each dataset goes to its own SQLite file, so populations are never mixed by accident (T1D MetaboNet
vs the non-T1D CGMacros). Both use the same schema, pipeline and repository. Source-specific behaviour
is in the adapter docstrings (`data/adapters/cgmacros.py`, `data/adapters/metabonet.py`), and the
transformations and exclusions are in `docs/data_audit/data_quality.md`.

## Binding the backend (Ruhaan's Protocol)

`data/storage/repository.py::SqliteDataRepository` implements the `DataRepository` Protocol in
`backend/app/services/data_access.py` and returns `ml.schema` record types. It is checked by running
`tests/test_data_contract.py`'s own test classes against it (`tests/test_data_sqlite_protocol.py`).

```bash
export GLIMPSE_DATA_BACKEND=sqlite
export GLIMPSE_DB_URL=sqlite:///data/glucotwin.db      # CGMacros
uvicorn backend.app.main:app
```

```python
from data.storage.repository import SqliteDataRepository
repo = SqliteDataRepository("sqlite:///data/glucotwin.db")
repo.list_participants()                          # ml.schema.ParticipantSummary (timezone None for official data)
repo.get_history("cgmacros_001")                  # ml.schema.ParticipantHistory, all modalities; DISPLAY
repo.get_history_as_of("cgmacros_001", meal.timestamp, lookback=timedelta(days=7))   # nothing after as_of
repo.get_cgm_secondary("cgmacros_001")            # Libre Pro readings (second sensor)
repo.get_participant_metadata("cgmacros_001")     # source_dataset, run id, cgm_sensors
```

## Commands

| Purpose | Command |
|---|---|
| Create schema (never drops by default) | `python -m data.storage.init_db [--db-url sqlite:///...]` |
| Load fixtures (idempotent) | `python -m data.adapters.ingest --source synthetic_dev` |
| Validate only, write nothing | `python -m data.adapters.ingest --source synthetic_dev --dry-run` |
| Profile a new dataset (first step on arrival) | `python -m data.adapters.profile --path <dir> [--mapping <toml>]` |
| Import the official dataset | `python -m data.adapters.ingest --source official --path <dir> --mapping data/mapping/official.toml` |
| Data-quality report over the DB | `python -m data.quality [--out data/reports/quality.json]` |
| CSV export for ML (canonical columns) | `python -m data.export --out data/local/export [--cutoff 2026-10-07T23:59:59Z]` |
| Regenerate fixtures (deterministic) | `python -m data.fixtures.generator` |
| Reset + reload local demo DB | `python -m data.seed --reset --yes` |

Database location: `--db-url` > `$GLIMPSE_DB_URL` > `sqlite:///data/glucotwin.db` (relative paths resolve
against the repo root). `*.db` files are gitignored.

## Using it from the backend / ML (Ruhaan)

```python
from datetime import timedelta
from data.repository import DataRepository, ParticipantNotFoundError, DataLayerUnavailableError

repo = DataRepository()                      # or DataRepository("sqlite:///path.db")

repo.list_participants()                     # -> list[ParticipantSummary]   (GET /api/participants rows)
repo.get_participant("synthetic_p001")       # -> ParticipantSummary | None   (None -> 404)
repo.get_history(pid, start, end)            # -> ParticipantHistory          (GET .../history body; DISPLAY only)
repo.get_meals(pid, start, end)              # -> list[MealRecord]
repo.export_model_examples(cutoff)           # -> list[MealRecord], all participants, (timestamp, meal_id) order

# Prediction-time inputs: everything with timestamp <= as_of, never later.
ctx = repo.get_prediction_context(pid, as_of=meal_timestamp, lookback=timedelta(hours=24))
ctx.cgm, ctx.heart_rate, ctx.meals, ctx.medications, ctx.activity

# Extra per-modality readers (same ordering/bounds rules):
repo.get_cgm / get_heart_rate / get_medications / get_activity (pid, start=None, end=None)
repo.get_participant_record(pid)             # full row incl. timezone
repo.list_ingestion_runs(); repo.quality_report()
```

FastAPI wiring sketch (Ruhaan owns the routes; this only shows the shapes line up):

```python
summary = repo.get_participant(pid)
if summary is None: -> 404 participant_not_found
return repo.get_history(pid, start, end).model_dump(mode="json")   # validates against history_response.schema.json
```

**Rules every method follows.** Results are in ascending `timestamp` order with a deterministic tie-breaker
(`meal_id` for meals). `None` bounds mean unbounded, and given bounds are inclusive. A missing modality returns
`[]`. Datetimes are timezone-aware UTC on the way in and out, and a timestamp without a timezone (`start`, `end`,
`as_of`, `cutoff`) raises `ValueError`. Every query filters on exactly one `participant_id`. For an unknown participant,
`get_participant` returns `None` and the per-participant readers raise `ParticipantNotFoundError`. An uninitialized DB
raises `DataLayerUnavailableError`, which maps to `503 data_layer_unavailable`.

**Leakage note.** `get_history` is meant for charts. If the window goes past a meal, it includes the glucose after that meal.
Build model inputs only with `get_prediction_context(pid, as_of)`. `export_model_examples` returns the observed meals and
nothing else. Targets, inclusion rules and data splits are decided by the ML owner.

## Example records (real output from the committed fixtures)

```json
{"participant_id":"synthetic_p001","display_name":"Participant 001","data_source":"synthetic_dev","days_available":7.0}
{"participant_id":"synthetic_p001",
 "cgm":[{"timestamp":"2026-10-03T16:00:00Z","glucose_mg_dl":93.0},{"timestamp":"2026-10-03T16:05:00Z","glucose_mg_dl":102.0}],
 "meals":[],
 "heart_rate":[{"timestamp":"2026-10-03T16:00:00Z","heart_rate_bpm":67.0,"resting_heart_rate_bpm":64.1}]}
{"meal_id":"synthetic_p001_m008","participant_id":"synthetic_p001","timestamp":"2026-10-02T22:41:00Z",
 "carbs_g":52.9,"protein_g":29.0,"fat_g":17.5,"fiber_g":5.8,"calories_kcal":510.0,"image_ref":null}
{"id":"synthetic_p002_med001","participant_id":"synthetic_p002","timestamp":"2026-10-01T13:01:00Z",
 "medication_class":"biguanide","dose":500.0,"dose_unit":"mg","data_source":"synthetic_dev"}
```

## Layout

```
data/
  schema.py            canonical typed records (pydantic) — API-shaped + storage records
  timeutil.py          RFC3339 parsing, UTC normalization, DST-safe localization
  validation.py        row rules + thresholds (reject / accept-with-warning / accept)
  quality.py           data-quality summary (per participant, per modality)
  repository.py        DataRepository — the read interface
  seed.py, export.py   convenience CLIs
  storage/             tables.py (SQLAlchemy schema), db.py, init_db.py, upsert.py
  adapters/            pipeline.py (single ingest path), mapping.py, readers.py,
                       ingest.py (CLI), profile.py (dataset profiler), __init__.py
                       (import_fixture / import_official)
  mapping/             synthetic_dev.toml (identity), official_template.toml (placeholder)
  fixtures/            generator.py, synthetic_dev/ (committed CSVs + manifest),
                       simulator_truth/ (hidden generator params — NEVER a model input)
  reports/             run + profile reports (gitignored)
```

Fixtures (`data/fixtures/synthetic_dev/`): 6 fictional participants and 7 days (2026-10-01 to 10-07 local time). Each
participant has nominal 5-minute CGM readings, heart rate (**p005 has none**), meals (**p006 is a sparse logger: 3 logged
meals**), medications (p002 and p004 only) and 15-minute step counts (p005 has none). These edge cases match the IDs
`tests/test_data_contract.py` relies on. **The simulator truth folder is for tests only.** No model, feature or API code
may read it.

Provenance, validation thresholds, simulator assumptions and the procedure for the official dataset are in
`docs/data_provenance.md`.
