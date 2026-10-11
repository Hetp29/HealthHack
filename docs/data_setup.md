# Data setup: reproducing the canonical databases

**Owner:** Het Patel · storage layout v3 · contract v1.0 (unchanged)

The organizer datasets and every database built from them stay **local**. None of them is in git: GitHub's 100 MB
file limit, unverified licence terms, and the master spec's "never commit large unlicensed datasets" rule all forbid
it. Each machine downloads the datasets and rebuilds the databases deterministically.

## 1. Get the data

Download both organizer files (SharePoint links in the wellness-challenge channel) into `data/official_raw/`
(gitignored):

```
data/official_raw/Type1Diabetes.parquet                                                   # MetaboNet, 1.28 GB
data/official_raw/cgmacros-a-scientific-dataset-for-personalized-nutrition-and-diet-monitoring-1.0.0/   # CGMacros, 0.66 GB
```

## 2. Build

```bash
git pull origin het
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt            # includes SQLAlchemy 2.0.43 + pyarrow 17.0.0

unzip -q data/official_raw/cgmacros-*/CGMacros_dateshifted365.zip -d data/official_raw/cgmacros_extracted

# CGMacros: the primary modeling dataset
python -m data.adapters.cgmacros --db-url sqlite:///data/glucotwin.db

# MetaboNet (optional): full streaming audit, then 8-day windows of eligible subjects
python -m data.adapters.metabonet audit
python -m data.adapters.metabonet ingest --db-url sqlite:///data/metabonet.db

# Audit docs (optional; regenerates docs/data_audit/)
python -m data.audit --metabonet-subjects data/reports/profiles/metabonet_subjects.csv

# Synthetic fixtures (tests / offline demo)
python -m data.seed --db-url sqlite:///data/synthetic_dev.db
```

Re-running any import is safe. Identical input writes nothing new: rows come back as `unchanged` and are never
duplicated.

## 3. Measured resources

Measured on an 8 GB Apple-silicon Mac, Python 3.12, with `/usr/bin/time -l`:

| Step | Wall time | Peak RAM | Output |
|---|---|---|---|
| CGMacros import (45 participants, 2.10M rows) | 2.6 min | 0.19 GB | `data/glucotwin.db` 0.57 GB |
| MetaboNet audit (132.8M rows streamed) | 2.1 min | 1.0 GB | `data/reports/profiles/*` |
| MetaboNet import (1,087 subjects × 8 days, 2.96M rows) | 4.5 min | 1.0 GB | `data/metabonet.db` 0.79 GB |

Disk:

| Item | Size |
|---|---|
| Raw downloads | 1.9 GB |
| Unzipped CGMacros | 0.67 GB |
| Both databases | 1.36 GB |
| **Total** | **≈ 4 GB; keep ≥ 4.5 GB free** |

Building CGMacros only needs about 2.5 GB.

## 4. Point the backend / training at it

```bash
export GLUCOTWIN_DATA_BACKEND=sqlite
export GLUCOTWIN_DB_URL=sqlite:///data/glucotwin.db      # or sqlite:///data/metabonet.db
```

`ml/train.py` still needs a `sqlite` source branch. That change is owned by Ruhaan. It should return
`data.storage.repository.SqliteDataRepository(os.environ["GLUCOTWIN_DB_URL"])`.

## 5. Time semantics (read before building time-of-day features)

The organizer datasets record **offset-naive, date-shifted local wall-clock times with no timezone**. The data layer
does not treat them as UTC:

| What | Where |
|---|---|
| Original text exactly as in the source | `source_timestamp` column on every event row |
| Timezone status | `participants.time_basis = "source_local_unknown_tz"`, `timezone = NULL` |
| Contract-required offset-aware `timestamp` | Source wall clock with a `+00:00` label. It is an **ordering key, not a real UTC instant**. Order and durations within a participant are exact; comparisons across participants or with real clocks are meaningless. |
| Correct local time of day | `SqliteDataRepository.local_minutes_of_day(pid, ts)` or `data.timeutil.local_minutes_of_day(ts, time_basis, tz)`. Returns `None` for real-UTC data with no timezone instead of guessing. |

Synthetic fixtures use `time_basis = "utc"` with real IANA timezones.

## 6. Preserved, not exposed

CGMacros `Amount Consumed` (per meal) and `bio.csv` A1c (per participant) are in the data-layer-only
`source_attributes` table (`SqliteDataRepository.get_source_attributes(pid)`). They will be considered after the first
real-data models are evaluated. No API field was added.
