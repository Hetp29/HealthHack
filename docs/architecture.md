# Architecture

**Owner:** Ruhaan Shah · **Contract:** v1.0

---

## Shape of the system

```
┌──────────────────────┐
│  frontend/  (Neil)   │  React + TypeScript + Vite + Tailwind + Recharts
│  Overview · Predict  │  VITE_API_BASE_URL
│  Model Insights      │
└──────────┬───────────┘
           │  HTTPS / JSON · contract v1.0 · CORS allowlist
           ▼
┌──────────────────────────────────────────────────────────┐
│  backend/  (Ruhaan)              FastAPI + Pydantic v2   │
│                                                          │
│  routes/      participants · predict · model             │
│  schemas.py   typed models, extra="forbid"               │
│  errors.py    uniform {code, message, detail} envelope   │
│  services/    prediction.py   artifact + metrics lifecycle│
│               data_access.py  DataRepository Protocol ◀──┼── the seam
└───────┬──────────────────────────────────┬───────────────┘
        │                                  │
        ▼                                  ▼
┌───────────────────────┐      ┌─────────────────────────────┐
│  ml/  (Ruhaan)        │      │  data/  (Het)               │
│  features · targets   │      │  adapters · storage         │
│  train · personalize  │      │  fixtures · mapping         │
│  inference · evaluate │      │  SQLite (source of truth)   │
│  explain · metrics    │      │  provenance + quality report│
│  artifacts/*.joblib   │      └─────────────────────────────┘
└───────────────────────┘                  ▲
        ▲                                  │
        └── ml/devfixture.py ──────────────┘
            TEMPORARY labelled stand-in, satisfies the same Protocol
```

---

## The two seams that matter

Three people building in parallel against an unreleased dataset works only if the
boundaries are narrow and written down. There are exactly two.

### 1. `contracts/` — the HTTP seam

`contracts/openapi.yaml` is the single source of truth. `build_schemas.py` derives
self-contained JSON Schema files from it, so the two copies cannot drift, and
`tests/test_contracts.py` fails if the committed output is stale.

Neil builds against those files and against `contracts/examples/*.json` — including
two fixtures produced by an actually trained model — without needing the backend
running or the ML artifacts present.

### 2. `DataRepository` — the data seam

A `typing.Protocol` with five methods, declared in
`backend/app/services/data_access.py`. Any object with those methods satisfies it;
there is nothing to subclass and nothing to import from `backend/`.

Two implementations:

- `ml/devfixture.py::SyntheticDevRepository` — the labelled temporary fixture, in
  memory, deterministic. Default today.
- `data/storage/repository.py::SqliteDataRepository` — Het's canonical layer.
  Selected by `GLUCOTWIN_DATA_BACKEND=sqlite`; the branch already exists and fails
  with a message naming the missing module until it lands.

Swapping them is one environment variable. `tests/test_data_contract.py` is the
acceptance suite for either one.

---

## Request path: `POST /api/predict`

```
 1  Pydantic validates the body          extra="forbid", AwareDatetime,
                                         macro ranges, as_of <= meal_timestamp
                                         ↳ failure → 422 invalid_request / naive_datetime
                                                       / future_context_override
 2  Resolve the participant              repository.get_participant()
                                         ↳ None → 404 participant_not_found
 3  Require the model artifact           ↳ absent/stale → 503 model_not_trained
 4  Fetch history                        repository.get_history()
 5  Truncate + extract features          history.truncated_at(meal_timestamp)
                                         ↳ no baseline → 422 insufficient_premeal_context
 6  Predict four horizon deltas          Ridge pipeline (fitted transforms reused)
 7  Apply calibration, or fall back      personalized | generalized_fallback
 8  Reconstruct the 25-point curve       linear interpolation, clip + disclose
 9  Derive peak / ttp / iAUC             from the sampled points only
10  Attribute drivers                    exact Ridge decomposition at the peak horizon
11  Assemble limitations                 fallback reason first, then caveats
12  Validate the response                through ForecastResponse before it leaves
```

**Step 2 precedes step 3 deliberately.** An unknown participant must return 404 rather
than be masked by a 503 about an untrained model.

**Step 12 exists** so a contract-shape regression in the ML layer fails server-side in
tests rather than reaching the frontend as malformed JSON.

---

## Liveness versus readiness

| | Endpoint | Depends on the model? |
|---|---|---|
| Liveness | `GET /health` | **No** — 200 whenever the process serves |
| Readiness | `GET /api/model/info` | Yes — `model_version` is `"none"` with no artifact |

A missing artifact is recorded, not fatal. Crashing at startup would turn a recoverable
"you haven't run `ml.train` yet" into a dead deployment, and the operator would lose
the endpoint that explains why.

---

## Ownership

| Path | Owner | Notes |
|---|---|---|
| `contracts/**` | Ruhaan | Sole approver. Consumers ACK before implementing. |
| `ml/**` | Ruhaan | Includes the temporary `devfixture.py`. |
| `backend/**` | Ruhaan | |
| `data/**` | Het | Implements `DataRepository`. |
| `frontend/**` | Neil | Consumes `contracts/`. |
| `tests/test_data_*.py` | Het | |
| `tests/` (rest) | Ruhaan | |
| `docs/data_provenance.md` | Het | |
| `docs/` (rest), `README.md`, `presentation/**` | Ruhaan | |

Cross-owner changes need a documented handoff. A contract change needs a version bump,
regenerated schemas and examples, updated tests, and explicit ACKs — all in one commit.

---

## Technology choices

| Choice | Why | Alternative rejected |
|---|---|---|
| FastAPI + Pydantic v2 | Typed models generate the OpenAPI surface and enforce `extra="forbid"` at the edge | Flask — would need hand-written validation |
| scikit-learn Ridge | Exact additive attribution, and appropriate for 135 episodes × 16 features | Gradient boosting — would fit noise and cost interpretability at this sample size |
| Pipeline-encapsulated transforms | Train/serve consistency by construction rather than by duplication | Preprocessing before the split — the classic leakage route |
| joblib artifact + JSON manifest | Reproducible, checksummed, human-inspectable provenance | Pickling bare estimators — no provenance |
| SQLite behind an abstracted interface | Runs locally with no service; swappable for PostgreSQL if a cloud DB turns out to be permitted | A cloud DB up front — unknown AWS permissions |
| `typing.Protocol` for the data seam | Structural typing, zero coupling between `data/` and `backend/` | ABC inheritance — forces an import dependency |

---

## Provenance, end to end

Every record carries `data_source`; every forecast echoes it; every artifact manifest
records it alongside the training episode counts and a SHA-256 checksum; the metrics
report names its `dataset_source` and evaluation protocol. The UI shows it on the
prediction screen.

Today that value is `synthetic_dev` everywhere, and it is *supposed* to be visible.
When the organizer dataset lands it becomes `official` and nothing else about the
display logic changes.

---

## Deliberate non-goals

Scope held at P0/P1 on purpose: no voice assistant, no animated avatar, no deep
learning, no photo-to-nutrition inference (that needs a reviewed, licensed service),
no uncertainty bands until their coverage can actually be measured, and no clinical
decision support of any kind.
