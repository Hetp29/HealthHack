# fs-1.3 — Formal Model Freeze v1

**Freeze ID:** `FS13_MODEL_FREEZE_V1`
**Status:** **FROZEN**
**Freeze timestamp (UTC):** `2026-10-10T23:20:51Z`
**Responsible owner:** Ruhaan Shah (ML + backend)
**Verification source commit:** `b5583dc34199fbb797643fc61f5ec5f1d6bce511`

This document records the verification of an existing model. **Nothing was
retrained, refitted, retuned or re-weighted to produce it.** The artifact's
SHA-256 was captured before and after every phase and is unchanged throughout.

---

## 1 · Model identifier

| | |
|---|---|
| Model ID | `glucotwin-fs13-blend-personalized` |
| Model version | `v0.1.0` |
| Model family | Personalized **0.5 × Ridge + 0.5 × HistGradientBoostingRegressor**, then shrunk per-participant calibration |
| Artifact family tag (from the artifact itself) | `blend`, `ridge_weight = 0.5` |
| Data source | `official` (CGMacros v1.0.0) |
| Trained at | `2026-10-10T09:32:09.702154Z` |

## 2 · Feature-set identifier

**`fs-1.3` — exactly 39 features, in this order.** Order is part of
the freeze: the stored coefficients correspond to a column layout, so a reordering
would serve confident nonsense rather than fail loudly.

| `premeal_glucose_mg_dl` | `cgm_slope_30min_mg_dl_per_min` | `cgm_mean_60min_mg_dl` |
| `cgm_std_60min_mg_dl` | `minutes_since_last_cgm` | `carbs_g` |
| `protein_g` | `fat_g` | `fiber_g` |
| `calories_kcal` | `tod_sin` | `tod_cos` |
| `minutes_since_prior_meal` | `prior_meal_carbs_g` | `hr_mean_30min_bpm` |
| `medication_exposure_recent` | `g_slope_10` | `g_slope_15` |
| `g_slope_60` | `g_accel` | `g_rise_fraction` |
| `g_cv_60` | `g_range_120` | `g_dev_from_night` |
| `r_hist_peak_delta` | `r_hist_per_carb` | `r_hist_ttp` |
| `r_hist_recovery` | `r_hist_n` | `q_retr_delta_30` |
| `q_retr_delta_60` | `q_retr_delta_90` | `q_retr_delta_120` |
| `q_retr_n` | `q_retr_weight` | `q_retr_mean_distance` |
| `q_retr_peak_delta` | `q_retr_ttp` | `q_retr_curve_spread` |

Verified identical between `ml/features.py::FEATURE_NAMES` and the artifact
manifest's `feature_names`, element by element.

## 3 · Artifact names and SHA-256 checksums

Directory: `ml/artifacts_cgmacros_fs13/`

| file | SHA-256 |
|---|---|
| `evaluation_detail_day8_holdout.json` | `ec8829178554a12058b568e9ec9cd286b3241b8612b20ec1077e8a87f34e47fe` |
| `metrics.json` | `d519359621baee9891faef761a2f15dfe7b8f5813a16da0cc37c98fb7b3444a3` |
| `metrics_day8_holdout.json` | `d519359621baee9891faef761a2f15dfe7b8f5813a16da0cc37c98fb7b3444a3` |
| `model.joblib` | `12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe` |
| `model_manifest.json` | `e1b4d4c1c3a8ad5f5c9b6979dca5cc953eca5192c67f97c03ca8b546686599d9` |

`metrics.json` and `metrics_day8_holdout.json` are byte-identical to one another —
the same evaluation serialized under two names.

## 4 · Inference source commit

**Verification commit:** `b5583dc34199fbb797643fc61f5ec5f1d6bce511`
**Freeze commit:** see §19.

Checksums of every file on the inference path at freeze time:

| file | SHA-256 (truncated) |
|---|---|
| `ml/config.py` | `fd93db3166861bbf41bbae991636250f…` |
| `ml/features.py` | `254f14200e899ad9cdd2c10e68c9feea…` |
| `ml/train.py` | `5ba79101afa5e6cc708b71449a0c8aa5…` |
| `ml/candidates.py` | `95e84b78cda5d7ac1d0ed978a0ec7a49…` |
| `ml/inference.py` | `ea5dca271a214ed5361e2c5ced0434f9…` |
| `ml/metrics.py` | `9f30b312f77765cb91e540ffc207c8ab…` |
| `ml/targets.py` | `cd760b9f5fece1e75d2a362d003c6af0…` |
| `ml/personalization.py` | `5acc9051375a71e35dc30813bdbf2b4f…` |
| `ml/provenance.py` | `5b1c1e224f072120963c1d5dde7599c5…` |
| `ml/explain.py` | `844542410f7f53521ad7ae15435ea627…` |
| `ml/schema.py` | `db44e74b66186eaa7c8ddbb30d32aeec…` |
| `ml/evaluate.py` | `a2275d5aa9f07d4e7eed83d77d0da757…` |
| `backend/app/main.py` | `0ae3da506640be81fb17032f6f06bb24…` |
| `backend/app/schemas.py` | `710fd56d3410d240923a876ce0c12bbc…` |
| `backend/app/config.py` | `de8c6ec8ccb18a9c25b4216b959fc8ee…` |
| `backend/app/errors.py` | `a40c8355d15c998ed3a0f04785faf9a6…` |
| `backend/app/dependencies.py` | `e16d466fb9a38ba4604bb307b05e8d8d…` |
| `backend/app/routes/predict.py` | `bb17c2557ffdd658db9ad04dae0ec81e…` |
| `backend/app/routes/model.py` | `102eb6b93ef453e233d6683ae4503eb6…` |
| `backend/app/routes/participants.py` | `d2ba8f6afdbcf6da55ac3ee91cbecc1b…` |
| `backend/app/services/prediction.py` | `1861a764fd1ff036a9cfd0a72fe5e3cc…` |
| `backend/app/services/data_access.py` | `02b423bb64b9dda95d397a57c589d10f…` |
| `contracts/openapi.yaml` | `be836dddaac5ed4cd78af7adac02bf9b…` |

## 5 · Training methodology

Day-8 holdout protocol, as recorded in the artifact and reproduced during this retest:

* **Eligibility.** A test-eligible meal needs ≥7 days of prior CGM history with
  ≥70% of nominal 5-minute readings present, a baseline reading within 15 minutes
  before the meal, and an observed reading within ±10 minutes of t+30/60/90/120.
* **Per-participant chronological split.** Test candidates are that participant's
  Day-8-eligible meals; the cutoff is the earliest such meal; the training pool is
  everything at or before `cutoff − 120 min`.
* **Embargo/purging.** A 120-minute embargo removes any training episode whose
  response window could overlap a held-out window.
* **Validation fold.** The latest 20% of each participant's pre-cutoff pool selects
  the Ridge penalty. The test set never tunes anything.
* **Split unit.** Whole meal episodes. Five-minute CGM rows are never split.
* **Fold-local fitting.** Imputation medians, scaler statistics and per-participant
  calibration offsets are fitted on the training fold only.

Episode accounting, reproduced exactly during this retest:

| | |
|---|---|
| Candidate meals | 1,698 |
| Accepted episodes | 1,651 |
| Excluded | 47 (1 incomplete macros, 20 insufficient pre-meal context, 26 insufficient target coverage) |
| Day-8 eligible | 480 |
| **Development pool (train + validation)** | **1,171** (938 + 233) |
| **Reference/test episodes** | **394** |
| Participants | 45 |
| Overlapping-meal episodes (tagged, retained) | 246 |

**The shipped artifact is fitted on all 1,171 development episodes** — the 394
reference episodes are excluded from its fit. See §10 for why this matters.

## 6 · Fixed ensemble configuration

| | |
|---|---|
| Ridge weight | **0.5** (read from the artifact, not inferred) |
| Gradient-boosting weight | **0.5** |
| Ridge α, shipped artifact | `{"30": 0.1, "60": 10.0, "90": 100.0, "120": 100.0}` |
| Ridge α, evaluation-harness fit | `{"30": 100.0, "60": 100.0, "90": 100.0, "120": 100.0}` |
| Booster | `HistGradientBoostingRegressor`, max_depth 3, lr 0.05, max_iter 300, min_samples_leaf 25, l2 1.0, max_leaf_nodes 8, early_stopping **false**, random_state 20261011 |
| Horizons | 30, 60, 90, 120 minutes |

## 7 · Personalization policy

| | |
|---|---|
| Shrinkage | `n / (n + k)` with **k = 3.0** |
| Minimum episodes for a personal offset | **4** |
| Maximum offset magnitude | **60.0 mg/dL** |
| Participants with a personal calibration | 45 of 45 |
| Reported effect at freeze | 22 improved, 22 worsened, 0 unchanged (of 44 scored) |

A participant below the 4-episode threshold receives the generalized cohort model.
The personalization is an additive, bounded, shrunk offset — never a per-person refit.

## 8 · Retrieval eligibility rules

A prior meal is admissible as fingerprint or retrieval evidence **only** if all of:

1. `meal.timestamp < query_timestamp` — strict, so a meal never sees itself;
2. `meal.timestamp + 120 min < query_timestamp` — the whole response window must
   have elapsed. **A window ending exactly at the query instant is EXCLUDED**;
3. a baseline CGM reading exists at or before that earlier meal;
4. at least **20** native CGM grid points inside its response window.

| | |
|---|---|
| `retrieval_min_history` | **5** — fewer than 5 admissible priors falls back |
| `retrieval_k` | **5** neighbours |
| `retrieval_min_curve_points` | **20** |
| `fingerprint_shrinkage_k` | **3.0** |

**The fallback is observable, not silent:** with fewer than 5 admissible priors the
`q_retr_*` value features are emitted as NaN and `q_retr_n` reports the true count.
All 16 enumerated cases are covered by `tests/test_freeze_retrieval_audit.py`.

## 9 · Development-validation metrics (freeze retest)

Measured at freeze time with the frozen fs-1.3 configuration on the 1,171-episode
development pool. **These are the numbers the freeze stands on** — the reference set
in §10 is not an independent test set.

| metric | within-person (2 purged rolling folds) | cold-start (4 participant-held-out folds) |
|---|---|---|
| Validation episodes | 768 | 1171 |
| Participants | 45 | 45 |
| **MAE (mg/dL)** | **23.548** | **23.085** |
| RMSE (mg/dL) | 32.045 | 31.435 |
| Bias (mg/dL) | -1.186 | -0.165 |
| MAE @ 30 min | 16.720 | 16.542 |
| MAE @ 60 min | 23.872 | 23.555 |
| MAE @ 90 min | 27.073 | 26.219 |
| MAE @ 120 min | 26.526 | 26.023 |
| Peak MAE (mg/dL) | 25.449 | 25.318 |
| Time-to-peak MAE (min) | 29.102 | 29.081 |
| iAUC MAE (mg/dL·min) | 1680.23 | 1647.75 |
| Per-fold MAE | 25.551 / 21.481 | 21.884 / 21.352 / 22.169 / 26.897 |

Per-participant error distribution (all 45 participants in both regimes):

| | within-person | cold-start |
|---|---|---|
| best participant MAE | 12.33 | 12.81 |
| median | 21.84 | 21.17 |
| p90 | 37.06 | 36.18 |
| **worst participant MAE** | **53.49** | **45.71** |

**Reproduction against prior reports** (tolerance **T8 = 0.001 mg/dL**, declared
before measurement): within-person 23.5478 vs previously reported
23.548 (|diff| **0.00022**, PASS);
cold-start 23.0850 vs 23.085 (|diff|
**0.00004**, PASS).

## 10 · Previously inspected reference-set metrics, and their limitations

> **Previously inspected reference-set reproducibility evaluation; not independent
> external validation.** The 394 Day-8 episodes were inspected during model
> development and their scores published. They are a reproducibility check only and
> must never be used for model selection.

### 10a · The recorded figures reproduce EXACTLY

Re-running the evaluation harness at freeze time reproduced `metrics.json` with
**every metric identical to 0.00000**, and `evaluation_detail_day8_holdout.json`
came back **byte-identical**. The only difference anywhere in the report was the
evaluation wall-clock timestamp.

| metric | recorded | retest | \|diff\| | tolerance | verdict |
|---|---|---|---|---|---|
| Overall MAE (mg/dL) | 20.55 | 20.55 | 0.00000 | 0.015 (T4) | **PASS** |
| Overall RMSE (mg/dL) | 28.35 | 28.35 | 0.00000 | 0.015 (T4) | **PASS** |
| Peak MAE (mg/dL) | 22.79 | 22.79 | 0.00000 | 0.015 (T4) | **PASS** |
| Time-to-peak MAE (min) | 28.02 | 28.02 | 0.00000 | 0.015 (T5) | **PASS** |
| iAUC MAE (mg/dL·min) | 1477.6 | 1477.6 | 0.00000 | 0.15 (T6) | **PASS** |
| MAE @ 30/60/90/120 | 15.73 / 21.19 / 22.83 / 22.44 | identical | 0.00000 | 0.015 (T4) | **PASS** |
| Ridge α per horizon | `{"30": 100.0, "60": 100.0, "90": 100.0, "120": 100.0}` | identical | — | exact (T7) | **PASS** |
| Episode & participant counts | 394 / 1,171 / 45 / 47 excluded | identical | — | exact (T3) | **PASS** |

The three baselines also reproduced exactly: persistence 32.58, cohort-mean-delta
28.08, generalized 21.63, personalized 21.96.

### 10b · A material distinction the retest surfaced

**The recorded 20.55 and the shipped artifact are two different fits.** This is not
a defect; it is a fact about what each number means, and conflating them would be
the easiest mistake to make here:

| | recorded `metrics.json` | shipped `model.joblib` |
|---|---|---|
| fitted on | **938** training-fold episodes | **1,171** development episodes |
| Ridge α | `{"30": 100.0, "60": 100.0, "90": 100.0, "120": 100.0}` | `{"30": 0.1, "60": 10.0, "90": 100.0, "120": 100.0}` |
| reference-set MAE | **20.55** | **20.4727** (newly measured) |
| reference-set RMSE | 28.35 | 27.8005 |
| reference-set bias | — | +0.8881 |

The evaluation harness (`ml/evaluate.py`) fits throwaway estimators on the
938-episode training fold — that is what produced the published figure. The deployed
artifact is fitted on all 1,171 development episodes, which still **excludes all 394
reference episodes**, so measuring it on them is meaningful. It scores slightly
better (20.473 vs 20.55), the expected direction for 233
additional training episodes.

**The declared tolerance spec therefore does not apply T4 to this pair**, and the
shipped artifact's reference-set performance is reported as a separately measured
quantity rather than as a reproduction. Tolerances were declared in
`research/freeze/TOLERANCES.md` (SHA-256 `2ddafab5445e7b017059f22b1803bf33…`)
**before** any comparison ran.

## 11 · Data provenance

| | |
|---|---|
| Dataset | CGMacros v1.0.0 — "A scientific dataset for personalized nutrition and diet monitoring" |
| Archive SHA-256 | matches the published `SHA256SUMS.txt`, verified at freeze time |
| `DataDictionary.pdf` | matches the published `SHA256SUMS.txt` |
| Warehouse | `data/local/cgmacros_v2.db` (gitignored; generated, never committed) |
| `data_source` label | `official`, resolved from participant provenance records |
| Time basis | `source_local_unknown_tz` — timestamps are date-shifted offset-naive wall clock and are **ordering keys, not true UTC instants** |
| CGM sensor | Dexcom G6 Pro (primary, 100% of readings); FreeStyle Libre Pro retained as a secondary series and **not used by fs-1.3** |
| Participants | 45 (ids are not 1–45: 24, 25, 37, 40 absent; ids run to 49) |
| Identifiable data | none. No raw dataset, generated database or patient-level file is committed. |

## 12 · Prediction-time availability limitations

All 39 features were audited individually in
`research/freeze/results/phase5_lineage.json`, and the leakage column is backed by an
experiment rather than prose: the same history was rebuilt with 36 post-meal CGM
points at 390 mg/dL, a 250 g meal at t+45 min, and 12 heart-rate points at 180 bpm
injected. **All 39 features were bit-for-bit invariant.** The chokepoint is
`ml/features.py::build_features → history.truncated_at(t)`, which *removes* future
observations so they are absent rather than merely unused.

**Confirmed not used in inference:**

| | |
|---|---|
| Post-prediction CGM | removed by the truncation chokepoint; verified invariant |
| Future meal events | same chokepoint; verified invariant |
| Incomplete prior responses | excluded by the §8 filter |
| `Amount Consumed` | never a feature — derived from an end-of-meal photograph taken a median 16 min **after** the meal |
| Meal photographs | fs-1.3 has no image feature; the vision research track was not promoted |
| Participant biological metadata (A1c, BMI, HOMA-IR, microbiome, gut health) | not promoted; no fs-1.3 feature reads them |
| Participant identity | no id, index or one-hot is a feature |

### The one genuine limitation, stated plainly

**Retrospective macronutrient availability.** The CGMacros dictionary defines
Calories/Carbs/Protein/Fat/Fiber as quantities *"in the consumed meal"*, and
`Amount Consumed` as the percentage eaten, derived from the end-of-meal photograph.
**The logged macros therefore describe what was actually eaten.** In deployment the
equivalent input is a participant's planned or logged-at-the-time meal, which is
prospectively available but may differ from what they finish.

This is a **known evaluation limitation of the dataset and of the task as
specified** — it is not a model-integrity failure, and fs-1.3 has always received
these five fields. Reported accuracy must **not** be presented as evidence that
exact consumed quantities are knowable before eating.

## 13 · Known model weaknesses

1. **Piecewise-linear trajectory.** The model learns deltas at four knots and
   interpolates linearly between them, so a predicted peak can only fall on a knot.
   This structurally caps time-to-peak accuracy near 29 minutes.
2. **No calibrated prediction intervals.** `uncertainty_status` is
   `not_calibrated` and the service says so in `limitations`. The `lower_mg_dl` /
   `upper_mg_dl` point fields are not calibrated coverage bounds.
3. **Wide per-participant spread.** Cold-start MAE runs
   12.8 to 45.7 mg/dL across the
   45 participants — the worst participant is roughly
   3.6× the best.
4. **Same-window evaluation.** Errors are on held-out meals from the same 7-day
   recording window, not an independent cohort or a later period.
5. **`q_retr_n` has branch-dependent meaning.** In the fallback branch it is the true
   admissible count; in the active branch it is the number of neighbours *retrieved*,
   i.e. `min(retrieval_k, admissible)`. The code's own label is accurate ("number of
   similar past meals retrieved") and the uncapped count is always available as
   `r_hist_n`. Recorded so the semantics are pinned rather than rediscovered.
6. **246 episodes contain a second meal inside the 120-minute window.** Their
   observed response blends two meals. They are retained and tagged, not dropped.
7. **`/api/compare` is barely exercisable on real CGMacros data.** It requires a
   measured pre-meal baseline for **both** participants at one shared timestamp, and
   only **7 of 990** participant pairs have CGM
   windows that overlap at all (recording years span 2019–2025). Where they do not,
   the service correctly returns a contract-valid `422
   insufficient_premeal_context` rather than fabricating a baseline. Verified working
   on an overlapping pair.
8. **23 of 1,698 warehouse meals fall outside contract v1.0 input bounds** (e.g. a row
   with `fiber_g = 480`). The service rejects them with a contract-valid 422 — correct
   enforcement, but a small number of source rows cannot be scored through the frozen
   interface.
9. **Not medical advice.** Research prototype; not for clinical decision-making.

## 14 · Runtime and dependency requirements

| | |
|---|---|
| Python | 3.12.10 (arm64, Darwin 25.5.0) |
| numpy | 1.26.4 |
| pandas | 2.2.2 |
| scikit-learn | **1.4.2** — the artifact is a pickle; a different minor version may fail to load it |
| scipy | 1.17.1 |
| joblib | 1.4.2 |
| FastAPI | 0.109.0 |
| Pydantic | 2.5.3 |
| SQLAlchemy | 2.0.43 |
| Model initialization | 259 ms |
| Peak traced memory at startup | 6.2 MB |
| Accelerator | none required; CPU only |

`torch`, `torchvision`, `open_clip_torch`, `torchcde` and `torchdiffeq` are
**research-only**. No production module imports them and fs-1.3 does not use them.

## 15 · API contract reference and checksum

| | |
|---|---|
| Contract | `contracts/openapi.yaml`, OpenAPI 3.0.3, `info.version: "1.0.0"` |
| SHA-256 | `be836dddaac5ed4cd78af7adac02bf9b7a1b77133143b84df34621f1be9200f4` |
| Byte-identical to its freeze commit `388d24e` | **yes** — `git diff 388d24e HEAD -- contracts/openapi.yaml` is empty |
| Endpoints | 7 — `/health`, `/api/participants`, `/api/participants/{id}/history`, `/api/predict`, `/api/compare`, `/api/model/metrics`, `/api/model/info` |

All seven were exercised against the real artifact and real warehouse and validated
against the frozen JSON Schemas in `contracts/schemas/` — not against the backend's
own Pydantic models, since checking a service against its own types proves only
internal consistency.

**Verified for a valid prediction:** exactly **25** points at minutes 0, 5, …, 120 in
strictly ascending order; **0** non-finite glucose values; `glucose_at_minutes` for
30/60/90/120 matching `points` by exact lookup to 0.000; peak magnitude matching
`max(points)` to 0.000; time-to-peak matching `argmax(points)` exactly; incremental
AUC within 0.300 of recomputation from the trajectory (tolerance 6.25); 9 explanatory
drivers, **all grounded in the 39 fs-1.3 features**; `uncertainty_status =
"not_calibrated"` with 6 declared limitations; `model_version = "v0.1.0"`;
`prediction_status = "personalized"`.

**Verified error handling** (all contract-valid `error_response` payloads): invalid
participant → 404 `participant_not_found`; malformed meal → 422; impossible numeric →
422; missing required field → 422; unknown extra field → 422 (`extra="forbid"`); naive
timestamp → 422; unanchored baseline → 422 `insufficient_premeal_context`.

`contract_version` is deliberately **not** a field of `model_info_response` — that
schema has exactly six fields and the service forbids extras, so the contract version
is carried by `GET /health` (`version: "1.0.0"`, verified).

## 16 · Exact reproduction commands

```bash
# Phase 1 -- artifact and source integrity
python -m research.freeze.verify_artifacts

# Phases 2 and 3a -- development regimes + shipped artifact on the 394
python -m research.freeze.retest --db-url sqlite:///data/local/cgmacros_v2.db

# Phase 3b -- reproduce the recorded reference metrics. --out MUST point at a
# scratch directory: the harness writes a metrics report, and the frozen artifact
# directory must never be overwritten.
python -m ml.evaluate --source sqlite --db-url sqlite:///data/local/cgmacros_v2.db \
    --protocol day8_holdout --out /tmp/freeze_eval

# Phase 5 -- per-feature lineage and leakage audit
python -m research.freeze.lineage

# Phases 6 and 7 -- API contract, determinism, latency
python -m research.freeze.verify_api --db-url sqlite:///data/local/cgmacros_v2.db

# Phase 8 -- full suite with skip accounting
python -m pytest -q -rsxX
```

Tolerances: `research/freeze/TOLERANCES.md`.
Machine-readable manifest: `docs/governance/fs13_integrity_manifest.json`.

## 17 · Complete test-suite results

| | |
|---|---|
| **Passed** | **703** |
| Failed | **0** |
| **Skipped** | **0** |
| xfail / xpass | 0 |
| Warnings | 5 |
| Runtime | 201.84 s |

| | count |
|---|---|
| Pre-existing tests at `b5583dc` | 666 |
| **New freeze-verification tests** | **37** |
| — `tests/test_freeze_retrieval_audit.py` (Phase 4, the 16 cases) | 23 |
| — `tests/test_freeze_integrity.py` (artifact, schema, contract, config) | 14 |

No test is skipped, so none is counted as passing vacuously. The 5 warnings are
third-party deprecations in `starlette.testclient` and `httpx` (the `app=` shortcut);
none originate in project code.

The new tests add no behaviour and touch no artifact. They exist so a future edit that
silently changes the frozen model fails CI rather than shipping: they assert the
artifact checksum, the 39 feature names *and order*, the 0.5 blend weight, the
retrieval gate of 5, the personalization policy, and contract byte-identity.

## 18 · Freeze timestamp (UTC)

`2026-10-10T23:20:51Z`

## 19 · Responsible owner and approval

| role | person | status |
|---|---|---|
| Model owner / verifier | **Ruhaan Shah** (ML + backend) | verification executed and documented |
| Independent reviewer | — | **not obtained** |
| Team sign-off | — | **not obtained** |

The repository had no governance or approval convention before this document, so
`docs/governance/` is established here. **No approval is recorded from anyone who has
not actually approved this freeze.** The two unsigned rows above are deliberately
blank: this document certifies that the verification gates passed, not that any third
party has reviewed them.

**Freeze commit SHA:** the commit that adds this document —
`git log -1 --format=%H -- docs/governance/FS13_MODEL_FREEZE_V1.md`.

## 20 · Change-control policy

**The current fs-1.3 artifact must remain unchanged.** Its SHA-256 is
`12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe` and `tests/test_freeze_integrity.py` fails if
it changes.

Any future change to **any** of the following constitutes a **new model version**
requiring its own full verification against these same gates:

* model weights or the serialized artifact;
* feature definitions, feature count, or **feature ordering**;
* preprocessing, imputation, or scaling;
* retrieval eligibility policy, `retrieval_k`, or `retrieval_min_history`;
* personalized calibration (shrinkage k, minimum episodes, offset cap);
* the Ridge/GBM blend weights or either estimator's hyperparameters;
* trajectory generation or interpolation;
* the shape, units or semantics of any output field.

Rules:

1. **A new version does not silently replace fs-1.3.** It is a new artifact directory
   with a new version identifier and its own freeze document.
2. **A future version may be researched independently** — that is encouraged — but
   promotion requires measured, controlled evidence on the development folds, not a
   favourable exploratory interval.
3. **The 394-episode reference set must not be used for model selection.** It has been
   inspected and is a reproducibility check only.
4. **Contract v1.0 is frozen.** An output change that requires a contract change is a
   new contract version and breaks every client built against v1.0.
5. **Research candidates are not promoted by this document.** Across ~221
   configurations, none demonstrated reliable superiority. Specifically **not
   promoted**: A8 enriched composition, C2 curvature-spline reconstruction, C6 direct
   peak-time, M7 microbiome residual correction, M7+F stacked, every Transformer
   variant, and every frozen-vision-embedding candidate.

---

## Verification gate summary

| phase | gate | result |
|---|---|---|
| 1 | Artifact and source integrity | **PASS** |
| 2 | Development-validation retest, both regimes | **PASS** (T8: 0.00022 / 0.00004 mg/dL) |
| 3a | Shipped artifact on the 394 reference episodes | **PASS** (counts exact; artifact unchanged) |
| 3b | Recorded reference metrics reproduction | **PASS** (exact — only the timestamp differs) |
| 4 | Retrieval and personalization audit, 16 cases | **PASS** (23 tests) |
| 5 | Leakage and prediction-time lineage, 39 features | **PASS** (39/39 invariant) |
| 6 | API contract verification, 7 endpoints | **PASS** (all contract-valid) |
| 7 | Determinism and performance | **PASS** (10/10 byte-identical; median 286 ms, p95 404 ms) |
| 8 | Full test suite | **PASS** (703 / 0 failed / 0 skipped) |

**No blocker was found. fs-1.3 is FROZEN.**
