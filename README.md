# GlucoTwin

**Personalized postprandial glucose response forecasting.**
Rutgers HealthHack 2026 — Challenge 3: *Predict My Glucose Response*.

Given a participant's continuous-glucose history and a meal's macronutrients,
GlucoTwin forecasts the next two hours of glucose: the 30/60/90/120-minute values, the
full five-minute trajectory, the peak and when it arrives, the two-hour excursion, and
a model-grounded explanation of **why** this person is predicted to respond this way.

> **Research prototype on synthetic data.** Not a medical device, not for clinical
> decision-making. It gives no medication dosing, diagnostic or treatment advice.
> Prospective clinical validation would be required for any care-related use.

---

## The problem

Two people can eat the identical meal and see entirely different glucose responses.
Population-average nutrition guidance cannot tell them apart, so advice that is right
on average is wrong for individuals — and the individual is who has to live with it.

GlucoTwin models the response **per person**, from that person's own recent history,
and is explicit about when it cannot.

## The approach

Three models, each a falsifiable step up from the last:

| | Model | What it establishes |
|---|---|---|
| **0** | Persistence — glucose does not move | The floor. Beating it means the model learned *something*. |
| **1** | Generalized Ridge, per horizon | Beating a cohort-mean benchmark means it learned about **this meal**. |
| **2** | Shrunk per-participant calibration | Beating Model 1 on strictly later meals means it learned about **this person**. |

Each is evaluated on held-out meals the model has never seen, split chronologically, and
reported with its sample count. The personalization result is **mixed — 3 participants
improved, 2 worsened** — and is reported that way, because a personalization claim that
cannot fail is not a finding.

Full measured results: **[`docs/validation.md`](docs/validation.md)**.

---

## Quickstart

Requires Python 3.12.

```bash
git clone https://github.com/Hetp29/HealthHack.git
cd HealthHack

python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

python -m ml.train    --source synthetic_dev           # ~15 s → ml/artifacts/model.joblib
python -m ml.evaluate --protocol all                   # ~30 s → ml/artifacts/metrics.json

uvicorn backend.app.main:app --reload                  # http://localhost:8000
```

`ml/artifacts/` is gitignored, so training is a required setup step. Until you run it
the API stays up and returns `503 model_not_trained` from `/api/predict` — deliberately,
so you can see *why* prediction is unavailable rather than meeting a dead process.

### Check it works

```bash
curl localhost:8000/health
# {"status":"ok","version":"1.0.0"}

curl -s -X POST localhost:8000/api/predict \
  -H 'Content-Type: application/json' \
  -d '{"participant_id":"synthetic_p002",
       "meal_timestamp":"2026-10-05T12:30:00Z",
       "meal":{"carbs_g":85,"protein_g":24,"fat_g":18,"fiber_g":7,"calories_kcal":600},
       "context_override":null}'
```

Returns 25 points at minutes 0–120, the four horizon values, peak/time-to-peak/iAUC,
ranked drivers, and the caveats that apply to that specific forecast.

Interactive docs: <http://localhost:8000/docs>

### Run the tests

```bash
python -m pytest -q          # 385 passed
```

The suite is self-sufficient on a clean clone: `tests/conftest.py` trains the model
once if `ml/artifacts/` is empty, so `pytest` is a reliable signal even before you have
run `ml.train` yourself. An existing artifact is left alone.

**Verified from a clean clone** at commit `b949fb8`: 385 passed, then
`ml.train` + `ml.evaluate` + `uvicorn` reproduced every number in this README exactly.

---

## Repository layout

```
contracts/      FROZEN cross-team interface v1.0 -- openapi.yaml is the source of truth
  openapi.yaml            all 7 endpoints, 27 schemas
  schemas/                generated JSON Schema, for any validator
  examples/               example payloads, incl. two from a real trained model
  canonical_schema_v1.md  canonical records, units, timestamp rules

ml/             modelling core
  features.py             prediction-time features, truncated at the meal
  targets.py              supervised targets + meal-episode assembly
  baselines.py            Model 0 and the cohort-mean benchmark
  train.py                Model 1 + Model 2, artifact + manifest
  personalization.py      shrunk per-participant calibration
  inference.py            artifact -> 25-point forecast
  evaluate.py             leakage-safe chronological evaluation
  explain.py              exact Ridge attribution
  metrics.py              frozen curve math
  devfixture.py           TEMPORARY labelled synthetic fixture

backend/        FastAPI service
  app/schemas.py          typed models, extra="forbid"
  app/errors.py           uniform {code, message, detail} envelope
  app/routes/             participants · predict · model
  app/services/           prediction.py · data_access.py (the DataRepository seam)

data/           canonical data layer                        [owner: Het]
frontend/       React + TypeScript dashboard                [owner: Neil]
tests/          385 tests
docs/           architecture · methodology · validation · deployment · handoff
presentation/   demo script
```

---

## API — contract v1.0

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness. 200 whenever the process serves. |
| GET | `/api/participants` | Participant roster. |
| GET | `/api/participants/{id}/history` | Observed CGM / meals / heart rate. |
| POST | `/api/predict` | Forecast one meal. |
| POST | `/api/compare` | The same meal under two participant contexts. |
| GET | `/api/model/metrics` | **Measured** held-out results. |
| GET | `/api/model/info` | Model provenance and readiness. |

`/health` reports liveness only. For readiness read `/api/model/info` — `model_version`
is the literal string `"none"` when no artifact is loaded.

Errors use one envelope, `{code, message, detail}`, with `detail` always an object.
Branch on `code`: `participant_not_found` (404), `invalid_request` /
`naive_datetime` / `future_context_override` / `insufficient_premeal_context` (422),
`model_not_trained` / `data_layer_unavailable` / `not_implemented` (503).

Full reference and the consumer guide: **[`contracts/README.md`](contracts/README.md)**.

---

## What the model guarantees

**No forecast reads the future.** `build_features` begins with
`history.truncated_at(meal_timestamp)`, so post-meal observations are *absent* from the
object the feature extractors see — not merely unused. A window bug cannot reach forward
in time. `tests/test_leakage.py` buries 400 mg/dL post-meal readings in the history and
asserts the feature vector is byte-identical.

**Splits are by whole meal episode, never by CGM row.** Five-minute rows inside one
120-minute window are not independent; splitting them would put part of an episode's
answer into training. Chronology is *asserted* at run time, and the guard is itself
tested against a deliberately bad split.

**Transforms cannot leak across folds.** Imputation, scaling and the Ridge penalty are
pipeline steps, so they are fitted on training rows only and reused at serve time —
identical train-time and serve-time behaviour by construction. Proven by a test that
predicts one row alone and inside a batch and requires the same answer.

**Minute 0 is a measurement, not a prediction.** If no CGM reading exists within 15
minutes before the meal, the request is refused with `422
insufficient_premeal_context` rather than served with an invented baseline.

**Explanations are exact, not approximate.** For the Ridge family,
`intercept + Σ contributions == prediction` to floating-point tolerance, asserted in
tests. No LLM, no surrogate, no SHAP approximation. If a model family has no additive
decomposition, `drivers` is `[]` with an honest note — an empty list is correct, a
plausible-sounding invented one is a defect.

**Uncertainty is absent rather than fabricated.** `uncertainty_status` is
`not_calibrated` and all bounds are `null`. 33 held-out episodes cannot support a
credible coverage estimate, and a band computed from training residuals would look
authoritative while meaning nothing.

**Honest degradation is a feature.** A participant with too little history returns
`generalized_fallback` with the reason — actual episode count and threshold — as
`limitations[0]`. A participant with no wearable data still gets a forecast, with the
imputation disclosed.

---

## Measured results

`within_person_chronological` · 102 train / 33 held-out episodes · 6 participants ·
`synthetic_dev` fixtures · errors in mg/dL.

| Model | MAE | RMSE | @30 | @60 | @90 | @120 |
|---|---|---|---|---|---|---|
| Model 0 · persistence | 24.91 | 32.84 | 18.98 | 30.04 | 27.58 | 23.04 |
| Benchmark · cohort mean | 17.88 | 21.78 | 12.44 | 19.12 | 20.83 | 19.12 |
| Model 1 · generalized | 9.07 | 11.75 | 5.90 | 7.70 | 10.83 | 11.85 |
| **Model 2 · personalized** | **8.75** | **10.96** | 5.97 | 7.39 | 9.78 | 11.83 |

Curve metrics (personalized): peak MAE **8.42** mg/dL, iAUC MAE **656** mg/dL·min,
time-to-peak MAE **19.85** min. Cold-start (`cohort_holdout`, unseen participants):
generalized MAE **9.96**.

> **These numbers are measured on synthetic development fixtures, because the organizer
> dataset had not been released.** They establish that the pipeline is leakage-safe,
> chronologically split and reproducible. They establish **nothing** about accuracy on
> real human physiology — the fixture's response curves were written by us. No external
> validity is claimed.

Reproduce: `python -m ml.train --source synthetic_dev && python -m ml.evaluate --protocol all`.
Two fresh cycles produce a byte-identical report.

**Where it is weak**, stated plainly: time-to-peak MAE is ~20 minutes and the
cohort-mean benchmark beats both trained models on that one metric. The model learns
four knots (30/60/90/120) and a piecewise-linear curve attains its maximum *at* a knot,
so predicted peak timing can only be 0/30/60/90/120. More knots would fix it; we have
not measured that, so we do not claim it. Full discussion in
[`docs/validation.md`](docs/validation.md).

---

## Dataset provenance

| | |
|---|---|
| **Current source** | `synthetic_dev` — deterministic in-memory fixtures from `ml/devfixture.py` |
| **Participants** | 6 synthetic, 7 days, 5-minute CGM, heart rate, meals, medications |
| **Real patient data** | **None.** No identifiable data of any kind is used or committed. |
| **Organizer dataset** | Not yet released at the time of writing. Het's adapter maps it to the same canonical records; `data_source` becomes `official` and nothing in the display logic changes. |

The fixture is deliberately heterogeneous so the pipeline meets its edge cases: a steep
carbohydrate responder, a flat responder, a late peaker, one participant with **no
heart-rate data at all**, one **sparse logger** who triggers the generalized fallback,
plus scattered dropout and a 90-minute sensor outage per participant.

Every record carries `data_source`; every forecast echoes it; every artifact manifest
records it with a SHA-256 checksum. The fixture is a scaffold for building and testing
the pipeline — **nothing learned from it is presented as physiology.**

---

## Cloud

**Not yet deployed.** AWS Workshop Studio permissions had not been enumerated at the
time of writing, and the master plan is explicit that cloud selection waits on *verified*
available services rather than assumed ones.

The local path above is the verified one and is a complete fallback. `Dockerfile` and
`docker-compose.yml` exist and are **labelled unverified** — the development machine has
no Docker daemon, so they have not been built or run. Deployment target, verification
steps and the smoke checklist: [`docs/deployment.md`](docs/deployment.md).

Nothing about AWS is claimed in the presentation unless it is deployed and reproducible.

---

## Documentation

| Document | Contents |
|---|---|
| [`contracts/README.md`](contracts/README.md) | Frozen API, hard invariants, error codes, consumer guide |
| [`contracts/canonical_schema_v1.md`](contracts/canonical_schema_v1.md) | Canonical records, units, timestamps, repository interface |
| [`docs/architecture.md`](docs/architecture.md) | System shape, the two seams, request path, ownership |
| [`docs/methodology.md`](docs/methodology.md) | Features, targets, the three models, curve math, explanations |
| [`docs/validation.md`](docs/validation.md) | Protocols, measured results, personalization, limitations |
| [`docs/deployment.md`](docs/deployment.md) | Local run, container, AWS decision process |
| [`docs/handoff.md`](docs/handoff.md) | What Het and Neil need from this branch |
| [`presentation/demo_script.md`](presentation/demo_script.md) | Four-minute demo script |

---

## Team

| Owner | Scope |
|---|---|
| **Ruhaan Shah** | ML, backend, contracts, deployment, integration, submission |
| **Het Patel** | Canonical data layer, ingestion, provenance, quality reporting |
| **Neil** | Frontend dashboard, prediction journey, model insights |

Branches: `ruhaan`, `het`, `neil`. `main` must always run.

## Third-party components

FastAPI, Uvicorn, Pydantic (MIT) · scikit-learn, NumPy, pandas, joblib (BSD-3-Clause) ·
python-dateutil (Apache-2.0 / BSD-3-Clause) · pytest, httpx, PyYAML, jsonschema (MIT).
Pinned in `requirements.txt` / `requirements-dev.txt`. No third-party dataset is
redistributed in this repository.

**Repository:** <https://github.com/Hetp29/HealthHack>
