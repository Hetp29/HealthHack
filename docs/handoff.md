# Handoff — `ruhaan` branch

**Owner:** Ruhaan Shah · **Contract:** v1.0 (FROZEN) · **Branch:** `ruhaan`

What is ready for Het and Neil to build against, what is wanted back, and what is
still open.

---

## Status

| Deliverable | State |
|---|---|
| `contracts/` v1.0 — OpenAPI, JSON Schemas, examples, canonical schema | **Ready. Frozen.** |
| ML pipeline — features, targets, 3 models, evaluation, attribution | **Ready.** 385 tests pass |
| FastAPI backend — all 7 endpoints, error envelope, CORS | **Ready.** Verified under `uvicorn` |
| Measured validation report | **Ready.** `docs/validation.md` |
| `DataRepository` seam + acceptance suite | **Ready for Het** |
| Real-model response fixtures | **Ready for Neil** |
| Container | Written, **unverified** (no Docker daemon here) |
| AWS deployment | **Not started.** Permissions not yet enumerated |

```bash
python -m pytest -q      # 385 passed
```

---

## ACK required from both of you

Contract v1.0 is frozen. Please confirm in the team channel that you have read it and
are implementing against it. If anything in it blocks you, say so **now** — a change
after you have both built against it costs far more than a change today.

| Owner | Confirm you have read | ACK |
|---|---|---|
| Het | `contracts/canonical_schema_v1.md` + the `DataRepository` Protocol | ☐ |
| Neil | `contracts/README.md` — endpoints, enums, error codes, null-bound rule | ☐ |

---

## For Het

### Your target

`backend/app/services/data_access.py` is the authoritative declaration. It is a
`typing.Protocol`, so there is nothing to subclass and nothing to import from
`backend/`. Any object with these five methods satisfies it:

```python
list_participants()                        -> list[ParticipantSummary]
get_participant(participant_id)            -> ParticipantSummary | None
get_history(participant_id, start, end)    -> ParticipantHistory
get_meals(participant_id, start, end)      -> list[MealRecord]
export_model_examples(cutoff)              -> list[MealRecord]
```

Record types live in `ml/schema.py` — the typed mirror of
`contracts/canonical_schema_v1.md`. Import them from there:

```python
from ml.schema import CgmPoint, HeartRatePoint, MealRecord, MedicationEvent, \
                      ParticipantHistory, ParticipantSummary
```

Their constructors **reject offset-naive datetimes**. That is intentional: a naive
timestamp compared against a meal time would shift every feature window by the local
UTC offset and silently break the leakage guarantee.

### The rules that will bite if missed

1. **Deterministic ordering.** Ascending `timestamp`, then `meal_id` as tie-breaker.
   Two calls with the same arguments must return identical sequences — the
   chronological splits depend on it.
2. **`get_participant` returns `None`** for an unknown id; it does not raise. The
   backend turns `None` into `404 participant_not_found`; an exception becomes a 500.
3. **Absent optional modality is `[]`, never `None`.** `synthetic_p005` in my fixture
   has no heart-rate data at all and the pipeline handles it — please keep a
   participant like that in yours.
4. **`None` bounds mean unbounded**; `start`/`end` are inclusive when supplied.
5. **Strict participant filtering.** Cross-participant contamination is a P0 defect.

### Your acceptance suite, ready now

`tests/test_data_contract.py` already tests all of the above against my dev fixture.
Point the `candidate` fixture at your repository and it becomes your acceptance test.
It imports nothing from `data/`, so it runs today and keeps running.

```python
@pytest.fixture(scope="module")
def candidate() -> DataRepository:
    from data.storage.repository import SqliteDataRepository
    return SqliteDataRepository("sqlite:///data/glucotwin.db")
```

```bash
python -m pytest tests/test_data_contract.py -v
```

### Binding it

No change needed on my side. The `sqlite` branch exists in `build_repository` and
currently fails with a message naming the missing module. Once
`data/storage/repository.py::SqliteDataRepository` is importable:

```bash
export GLIMPSE_DATA_BACKEND=sqlite
export GLIMPSE_DB_URL=sqlite:///data/glucotwin.db
uvicorn backend.app.main:app
```

### What I need from you

1. **ACK of contract v1.0.**
2. **Fixture characteristics I rely on for edge cases** — please keep equivalents:
   one participant with no heart-rate data, one sparse logger (≤3 meals, to exercise
   the generalized fallback), scattered CGM dropout, and at least one contiguous
   sensor gap.
3. **The quality report**, especially **near-meal CGM coverage**. It directly sets my
   usable episode count: an episode needs a reading within 15 min before the meal and
   within ±10 min of all four horizons.
4. **When the organizer dataset lands:** the column inventory and mapping profile
   *before* ingestion, and an explicit statement of what is missing (meal images,
   post-meal ground truth, macro completeness). I will not infer absent values, and I
   need to know what to exclude rather than discover it in the metrics.
5. `ml/devfixture.py` retires once your layer lands — it survives only as a test
   fixture. Nothing in `data/` should depend on it.

---

## For Neil

### Bind against these files

Everything you need is in `contracts/`, with no running backend required.

```bash
npx openapi-typescript ../contracts/openapi.yaml -o src/types/api.ts
```

| Fixture | Renders |
|---|---|
| `examples/predict_response.model_generated.json` | **From a real trained model** — the personalized happy path |
| `examples/predict_response.model_generated_fallback.json` | **From a real trained model** — `generalized_fallback` |
| `examples/predict_response.json` | Hand-built shape fixture (illustrative numbers) |
| `examples/metrics_response.json` | The **empty** metrics report — build your "no validated metrics" state from exactly this |
| `examples/participants_response.json`, `history_response.json` | Roster and history |
| `examples/error_response.json` | The error envelope |

The two `model_generated` files are the ones to trust for realistic values — they came
out of `python -m ml.inference --emit-fixture`.

### Five rules that will cause a visible defect if missed

1. **`points` has exactly 25 entries**, minutes 0…120 by 5. Minute 0 is the *measured*
   pre-meal reading, not a prediction — worth styling differently from the forecast.
2. **If `lower_mg_dl` / `upper_mg_dl` are `null`, draw no band.** Show "uncertainty not
   calibrated". They are `null` on every forecast today, and that is correct, not a
   missing feature. A fabricated band is a P1 defect.
3. **`drivers` may legitimately be `[]`.** Show honest unavailable text. Never a static
   or hardcoded explanation presented as attribution.
4. **Surface `prediction_status`, `data_source` and `model_version` on every prediction
   screen.** When status is `generalized_fallback`, show `limitations[0]` prominently —
   it carries the reason, including the real episode count and threshold. There is no
   separate `fallback_reason` field by design.
5. **`glucose_at_minutes` and `metrics` are derived from `points`.** Never recompute
   them client-side; they cannot disagree with the curve and should not be made to.

Also: `heart_rate` can be `[]` for a participant with no wearable (try
`synthetic_p005`), and `data_source` is `synthetic_dev` today — please display it, the
provenance is meant to be visible.

### Live backend

```bash
pip install -r requirements-dev.txt
python -m ml.train --source synthetic_dev
python -m ml.evaluate --protocol all
uvicorn backend.app.main:app --reload --port 8000
```

`VITE_API_BASE_URL=http://localhost:8000`. CORS already allows `:5173` on both
`localhost` and `127.0.0.1`. Interactive docs at `/docs`.

Demo-worthy participants:

| Participant | Why |
|---|---|
| `synthetic_p002` | Steep carbohydrate responder — the most visually striking curve |
| `synthetic_p003` | Flat responder — same meal, barely moves. The personalization story in one screen |
| `synthetic_p005` | No heart-rate data — exercises the missing-modality disclosure |
| `synthetic_p006` | Sparse logger — the only `generalized_fallback` |

`synthetic_p002` vs `synthetic_p003` on the identical meal is the strongest 10 seconds
in the demo. `POST /api/compare` returns both at once with a mandatory caveat you must
display verbatim.

### Error states to build

| Trigger | Response |
|---|---|
| Unknown participant | `404 participant_not_found` |
| Negative / out-of-range macro | `422 invalid_request` |
| Timestamp without an offset | `422 naive_datetime` |
| Meal time with no nearby pre-meal CGM | `422 insufficient_premeal_context` |
| Before `ml.train` has been run | `503 model_not_trained` |

`detail` is always an object, so you can render it structurally. The `503` is easy to
reproduce — just point `GLIMPSE_MODEL_ARTIFACT` at a nonexistent path.

### What I need from you

1. **ACK of contract v1.0.**
2. Confirmation that the two `model_generated` fixtures bind cleanly to your types.
3. Four screenshots and a ~45 s happy-path clip for the deck.
4. Tell me immediately if any response shape is awkward to render. I would rather bump
   the contract once, now, than have you work around it.

---

## Open items I own

| Item | Status |
|---|---|
| **Three-person eligibility** vs the published 4–8 guidance | **Administrative blocker.** Needs an organizer answer; not an assumption of approval |
| Organizer dataset format and availability | Blocked on release. Adapter seam is ready |
| Day-8 ground truth and scoring protocol | Unknown. If truth is withheld we report a Days 1–7 blocked chronological holdout, **not** Day-8 accuracy |
| AWS available services | Not enumerated. Process in `docs/deployment.md` §4 |
| Container verification | Needs a machine with Docker |
| Public/private repo submission requirement | Needs confirmation before Sunday 09:00 |

## Contract changes

Don't, if it can be avoided. If it must change: tell me with the field, current shape,
proposed shape, justification and blast radius. I bump `info.version`, regenerate
`schemas/` and `examples/`, update `contracts/README.md` and `tests/test_contracts.py`,
and attach a migration note — all in one commit, then both of you re-ACK. A silent
change is a P0 defect.
