# Validation Report

**Owner:** Ruhaan Shah · **Contract:** v1.0 · **Model:** `v0.1.0` · **Feature set:** `fs-1.1`

---

# PART 1 — PRIMARY RESULT: real organizer data (CGMacros)

**Dataset:** CGMacros v1.0.0 (Das et al., *Sci Data* 12, 1557 (2025)) · provenance label
`official` · 45 participants · 1,698 logged meals · ~9–10 days per participant.

**Protocol:** `day8_holdout`. The full specification — fixed and written down *before*
any of these numbers were seen — is in
[`evaluation_protocol.md`](evaluation_protocol.md).

| Fold | Episodes |
|---|---|
| train | 938 |
| validation (selects the Ridge penalty) | 233 |
| **test (scored, genuinely held out)** | **394** |
| purged by the 120-min embargo / ineligible | 86 |
| total usable episodes | 1,651 |

Test episodes are restricted to Day-8-eligible meals: ≥7 days of prior CGM with ≥70%
coverage, a measured baseline within 15 min, observed readings at all four horizons, and
**no other meal inside the 120-minute response window**. Train/validation/test are
disjoint by construction and a 120-minute embargo guarantees no training response window
reaches a held-out one. The test fold was never used for fitting, tuning, calibration or
preprocessing — asserted at run time by `_assert_day8_separation`.

## Horizon errors (mg/dL), 394 held-out Day-8 meals across 44 participants

| Model | MAE | RMSE | @30 | @60 | @90 | @120 |
|---|---|---|---|---|---|---|
| Model 0 · persistence | 32.58 | 45.70 | 23.83 | 36.68 | 36.34 | 33.45 |
| Benchmark · cohort mean | 28.08 | 38.15 | 20.22 | 29.72 | 31.65 | 30.74 |
| Model 1 · generalized | 26.47 | 35.93 | 18.23 | 27.77 | 30.66 | 29.24 |
| **Model 2 · personalized** | 23.93 | 32.31 | 17.00 | 24.69 | 27.49 | 26.56 |

Relative to persistence, the personalized model reduces MAE by
26.6%.
Relative to the cohort-mean benchmark — which ignores the meal entirely — by
14.8%.
Personalization improves on the generalized model by
9.6%.

## Curve-derived errors

| Model | Peak MAE (mg/dL) | Time-to-peak MAE (min) | iAUC MAE (mg/dL·min) | n |
|---|---|---|---|---|
| persistence | 51.80 | 65.18 | 3030.9 | 394 |
| cohort mean | 32.22 | 28.40 | 2136.5 | 394 |
| generalized | 29.95 | 32.94 | 1990.2 | 394 |
| **personalized** | 26.69 | 30.08 | 1700.7 | 394 |

## Personalization works on real data — and this is the headline finding

**35 of 44 participants improved, 9 got worse.**

| | Participant | Generalized | Personalized | Δ MAE | n |
|---|---|---|---|---|---|
| best | `cgmacros_030` | 51.38 | 39.23 | **+12.15** | 32 |
| best | `cgmacros_001` | 30.20 | 18.30 | **+11.90** | 32 |
| best | `cgmacros_049` | 47.70 | 37.60 | **+10.11** | 28 |
| worst | `cgmacros_042` | 26.46 | 33.96 | **-7.50** | 20 |
| worst | `cgmacros_047` | 18.19 | 22.03 | **-3.84** | 24 |
| worst | `cgmacros_003` | 17.00 | 19.94 | **-2.94** | 36 |

Per-participant personalized MAE spans **13.3 to 52.1 mg/dL** (median 22.5), so the
aggregate hides very large between-person differences in predictability.

This is a **stronger** personalization result than the synthetic fixtures produced
(where it was 3 improved / 2 worsened and the aggregate gain was 3.5%). That direction
is worth stating plainly: the synthetic generator's smooth response kernel was *easier*
for the cohort model, so it understated what per-person calibration is worth. On real
physiology the per-person signal is much larger.

## Cold start — `cohort_holdout`, 535 episodes from 15 entirely unseen participants

| Model | MAE | RMSE |
|---|---|---|
| persistence | 43.52 | 59.88 |
| cohort mean | 36.34 | 50.80 |
| generalized | **35.34** | 49.88 |
| personalized | 35.34 | 49.88 |

Cold-start error is **much** worse (35.34 vs
26.47 MAE). Predicting a person we have never seen
is a substantially harder problem, and the personalized row is identical by
construction because a held-out participant has no calibration. Secondary protocol
`within_person_chronological` (Day-8 context rule relaxed, 413
test episodes) gives personalized MAE 23.59.

## What these numbers are, and are not

**Are:** a genuinely held-out evaluation on real human CGM observations, with a protocol
fixed before results were inspected, a purged chronological split, and a falsifiable
per-participant personalization comparison.

**Are not:**

1. **Accurate enough for any clinical use.** A ~24 mg/dL mean absolute error at a
   ~25 mg/dL 60-minute horizon is
   large relative to the clinical decisions people make about food. This is a research
   prototype.
2. **Independent samples.** 394 test episodes come from
   44 participants — roughly 9 meals each. Meals from one person share physiology and
   consecutive meals share CGM context, so the effective sample size is well below 394.
   No significance is claimed.
3. **Generalizable beyond this cohort.** 45 participants, ~10 days, one study.
4. **Time-to-peak capable.** 30
   minutes of error, and the cohort-mean benchmark beats both trained models on this
   metric (28.4 min). The
   model learns four knots and a piecewise-linear curve can only peak *at* a knot. More
   knots would likely help; we have not measured that, so we do not claim it.
5. **Calibrated in uncertainty.** All interval bounds are `null`.
6. **Free of a medication/activity confound.** CGMacros records **no** medications, so
   `medication_exposure_recent` is a zero-variance feature here (verified: coefficient
   exactly 0.0).

## Known data limitations carried from the source

- **Interpolated CGM was discarded, not used.** The source's 1-minute columns are
  forward-interpolated between true readings (Dexcom 5-min, Libre 15-min), so an
  interpolated minute encodes the *next* real reading. The data layer keeps only the
  126,259 native Dexcom readings. Had this been missed, every result above would be
  invalid.
- **Two sensors.** Dexcom G6 Pro is primary; Libre Pro is retained separately.
- **Timestamps are offset-naive, date-shifted local wall clock with no timezone.** Stored
  under a `+00:00` label as an *ordering key*, with `time_basis =
  "source_local_unknown_tz"`. Time-of-day and within-participant elapsed time are exact
  (verified byte-identical against the raw CSVs); absolute instants are not comparable
  across participants. Recording years span 2019–2023, which is why a globally sorted
  "chronological" CV would be meaningless and the Ridge penalty is selected on an
  explicit per-participant validation fold instead.
- **8 meals quarantined** for implausible fiber (1,020–2,830 g, probably milligrams) in
  participants 016 and 019. Not unit-guessed.
- **586 meals report `fiber_g = 0`**, which may mean "not estimated" rather than zero
  (ruling D4 — preserved as recorded; a sensitivity analysis is not yet done).
- **`LICENSE.txt` in the CGMacros package is 0 bytes**, so terms are unstated in the
  files received. Used under organizer permission for this challenge; the paper is cited.

## Reproducing

```bash
python -m data.storage.init_db --db-url sqlite:///data/local/cgmacros_v2.db
python -m data.adapters.cgmacros --path <CGMacros dir> --db-url sqlite:///data/local/cgmacros_v2.db
python -m ml.protocol_report --source sqlite --db-url sqlite:///data/local/cgmacros_v2.db
python -m ml.train    --source sqlite --db-url sqlite:///data/local/cgmacros_v2.db --holdout day8 --out ml/artifacts_cgmacros
python -m ml.evaluate --source sqlite --db-url sqlite:///data/local/cgmacros_v2.db --protocol all --out ml/artifacts_cgmacros
```

The artifact is fitted on the Day-8 training pool only (`--holdout day8`), so the
**served** model has never seen a held-out Day-8 response either — not just the
evaluation run.

---

# PART 2 — Synthetic development fixtures (SUPERSEDED)

> The section below was the primary result before the organizer data arrived. It is kept
> because it documents the leakage, reproducibility and protocol machinery, all of which
> still apply. **Its numbers are no longer the headline and must not be quoted as
> results.** Note also that the fixture default changed from 7 to 16 days so it can
> exercise the Day-8 protocol, so re-running it will not reproduce the figures below.

> ## Read this first
>
> Every number in this document was measured by running `python -m ml.evaluate` on
> **synthetic development fixtures**, because the organizer dataset had not been
> released when this was written.
>
> **What these numbers establish:** that the pipeline is leakage-safe, that the splits
> are chronological, that the models are reproducible, and that the reporting machinery
> computes what it claims to compute.
>
> **What they do NOT establish:** anything about accuracy on real human physiology.
> The fixture's response curves were written by us. A model that fits them well has
> demonstrated that it can learn a smooth meal-response relationship from pre-meal
> context — not that it has learned glucose metabolism. No external validity is
> claimed, and none should be inferred.
>
> When the organizer dataset arrives, this document is regenerated against it and the
> provenance line at the top changes to `official`.

---

## How to reproduce every number here

```bash
python -m ml.train    --source synthetic_dev
python -m ml.evaluate --protocol all
cat ml/artifacts/metrics.json
```

Reproducibility is verified, not assumed. Two fresh `train` + `evaluate` cycles
produce a byte-identical report (SHA-256 of the report with `evaluated_at` removed:
`13beeff5…`). `tests/test_reproducibility.py` asserts that the fitted coefficients
and the per-participant offsets are identical across runs, and that the fixture seed
does not depend on `PYTHONHASHSEED`.

---

## Evaluation protocols

Two protocols, answering two different questions. Reporting only one would overstate
what has been shown.

### `within_person_chronological` — primary

Per participant, meal episodes are sorted by time; the earliest 75% train and the
latest 25% are held out.

*Question answered:* given some history for this person, how well do we predict their
**next** meals? This is the only protocol that can support a personalization claim,
because each participant's calibration is estimated from meals that occurred strictly
before every meal it is scored on.

### `cohort_holdout`

Whole participants are held out (`synthetic_p005`, `synthetic_p006`).

*Question answered:* how well do we predict a person we have **never seen**? The
cold-start case. Personalization is impossible for a held-out participant by
construction, so every forecast there is `generalized_fallback` and the personalized
row is identical to the generalized row — the report says so explicitly rather than
quietly presenting a duplicate as a result.

### What both protocols enforce

| Guarantee | Mechanism |
|---|---|
| Splits are by **whole meal episode** | Five-minute CGM rows inside one 120-minute window are not independent. Splitting them would put part of an episode's answer into training. |
| No feature reads the future | `build_features` begins with `history.truncated_at(meal_timestamp)`, so post-meal rows are *absent*, not merely unused. |
| Every fitted transform is refit on the training fold | Imputation medians, scaler statistics, the Ridge penalty (inner `TimeSeriesSplit` CV) and the per-participant offsets are all computed inside `fit` on training rows only. |
| Chronology is **asserted**, not assumed | `_assert_no_leakage` fails the run if any held-out episode is not strictly later than every training episode for that participant. `tests/test_leakage.py` also proves the guard itself fires when handed a bad split. |
| Participants cannot cross-contaminate | Calibration is estimated per participant id; `tests/test_personalization.py` checks isolation directly. |

---

## Sample accounting

Nothing is dropped silently. Every excluded meal has a recorded reason.

| | Count |
|---|---|
| Candidate logged meals | 136 |
| Became usable episodes | 135 |
| Excluded | 1 — all `insufficient_target_coverage` (sensor gap near the horizons) |
| Tagged as overlapping | 9 (another meal inside the 120-minute window) |

**Primary protocol split:** 102 training episodes, 33 held-out episodes, 6 participants.

| Participant | Train | Test | Personalized | Note |
|---|---|---|---|---|
| `synthetic_p001` | 20 | 7 | yes | |
| `synthetic_p002` | 21 | 7 | yes | steep carbohydrate responder |
| `synthetic_p003` | 16 | 5 | yes | flat responder |
| `synthetic_p004` | 21 | 7 | yes | late peaker |
| `synthetic_p005` | 21 | 7 | yes | **no heart-rate data at all** |
| `synthetic_p006` | 3 | 0 | **no** | sparse logger → `generalized_fallback` |

`synthetic_p006` has too few episodes to be scored held-out *or* to be calibrated.
That is the intended behaviour, not a gap: it is the case that exercises the honest
fallback path end to end, including in the live demo.

---

## Primary results — `within_person_chronological`

33 held-out meal episodes · 132 horizon predictions · errors in mg/dL.

| Model | MAE | RMSE | MAE@30 | MAE@60 | MAE@90 | MAE@120 |
|---|---|---|---|---|---|---|
| Model 0 · persistence | 24.91 | 32.84 | 18.98 | 30.04 | 27.58 | 23.04 |
| Benchmark · cohort mean delta | 17.88 | 21.78 | 12.44 | 19.12 | 20.83 | 19.12 |
| Model 1 · generalized Ridge | 9.07 | 11.75 | 5.90 | 7.70 | 10.83 | 11.85 |
| **Model 2 · personalized** | **8.75** | **10.96** | 5.97 | 7.39 | 9.78 | 11.83 |

### Why two baselines, not one

Beating **persistence** only shows the model knows that *a meal raises glucose* — a
low bar. Beating the **cohort mean delta**, which predicts the average training
excursion while ignoring the meal entirely, is what shows the model knows something
about *this* meal. Reporting persistence alone would flatter the result, so both are
reported. The generalized model halves the cohort-mean error (17.88 → 9.07).

### Error grows with horizon

MAE rises monotonically from 5.90 mg/dL at 30 minutes to 11.85 at 120 for the
generalized model. That is the expected shape — the further from the measured
baseline, the less the pre-meal context constrains the trajectory — and it is worth
stating plainly rather than quoting a single pooled number.

---

## Curve-derived metrics

Computed from the 25-point trajectory, against the observed CGM curve. `n = 33`;
episodes whose sensor coverage is too sparse to characterize a peak are excluded from
these specific metrics by `summarize_observed`, which is why the curve `n` can fall
below the horizon `n`.

| Model | Peak MAE (mg/dL) | Time-to-peak MAE (min) | iAUC MAE (mg/dL·min) |
|---|---|---|---|
| persistence | 34.19 | 67.12 | 2622.3 |
| cohort mean delta | 19.60 | 17.73 | 1808.9 |
| generalized | 9.08 | 19.55 | 736.5 |
| personalized | **8.42** | 19.85 | **656.0** |

### Time-to-peak is the weak metric, and here is why

**Time-to-peak MAE is ~20 minutes and the cohort-mean benchmark beats both trained
models on it (17.73 vs 19.55/19.85).** This is a structural limitation, not a bug, and
it would be dishonest to bury it:

The model learns glucose change at **four knots** — 30, 60, 90 and 120 minutes — and
the curve between them is linearly interpolated. A piecewise-linear function attains
its maximum **at a knot**. So the predicted time-to-peak can only ever be one of
0/30/60/90/120 minutes, while the observed peak can fall anywhere on the five-minute
grid. Roughly 20 minutes of error is close to the floor this parameterization allows.

The cohort-mean benchmark "wins" here for an uninteresting reason: it predicts the same
shape every time, which happens to put its peak near the cohort's average peak time.
It is not better at timing; it is consistently average.

Fixing this properly means predicting more knots (15/45/75/105 are already supported
via a configurable horizon set) and justifying the extra targets with held-out
time-to-peak error. That is a measured improvement we have *not* made yet, so no claim
is made about it. We did **not** fit a smooth curve with overshoot between knots:
that would manufacture peak-timing precision the model does not have.

---

## Personalization — a mixed result

**This is the falsifiable claim, and the honest answer is "small net gain, not
uniform".**

Aggregate MAE improves 9.07 → 8.75 mg/dL (−0.32, a 3.5% relative reduction). Per
participant, on strictly later held-out meals:

| Participant | Generalized MAE | Personalized MAE | Δ MAE | n | Outcome |
|---|---|---|---|---|---|
| `synthetic_p001` | 9.62 | 7.46 | **+2.16** | 28 | improved |
| `synthetic_p004` | 12.24 | 11.40 | **+0.84** | 28 | improved |
| `synthetic_p003` | 9.18 | 8.98 | **+0.19** | 20 | improved |
| `synthetic_p005` | 7.25 | 7.36 | **−0.11** | 28 | worsened |
| `synthetic_p002` | 7.09 | 8.59 | **−1.50** | 28 | worsened |

**3 improved, 2 worsened.** Positive Δ means personalization helped.

Two further caveats we are not hiding:

- **At the 30-minute horizon personalization is slightly worse** (5.97 vs 5.90 mg/dL).
  The personal offset is a constant per horizon; early in the response, where the
  measured baseline still dominates, a constant shift has little to correct and can
  add noise.
- **`synthetic_p002` got worse despite having the largest calibration offset**
  (+8.31 mg/dL at 60 min, from a raw mean residual of +10.69 shrunk by a factor of
  0.78). A participant whose response is both steep *and* variable has a large mean
  residual that is nonetheless a poor predictor of any individual later meal. This is
  exactly the failure mode shrinkage is meant to damp, and with 21 training episodes
  the shrinkage factor is high enough that it does not damp it much.

### Fitted calibration offsets (60-minute horizon)

| Participant | Train episodes | Raw mean residual | Shrinkage factor | Applied offset |
|---|---|---|---|---|
| `synthetic_p001` | 20 | −7.68 | 0.769 | −5.91 |
| `synthetic_p002` | 21 | +10.69 | 0.778 | +8.31 |
| `synthetic_p003` | 16 | −4.33 | 0.727 | −3.15 |
| `synthetic_p004` | 21 | +1.76 | 0.778 | +1.37 |
| `synthetic_p005` | 21 | −0.45 | 0.778 | −0.35 |
| `synthetic_p006` | 3 | −9.72 | 0.333 | **not applied** |

`synthetic_p006` is the case the guard exists for. Its raw mean residual of
−9.72 mg/dL comes from **three** meals and is almost entirely noise. Applying even the
shrunk −3.24 would look like personalization while being a coin flip, so below the
4-episode threshold **no offset is applied at all** and the API returns
`generalized_fallback` with the reason in `limitations[0]`.

---

## Cold-start results — `cohort_holdout`

Held-out participants: `synthetic_p005`, `synthetic_p006`. 104 train / 31 test episodes.

| Model | MAE | RMSE |
|---|---|---|
| persistence | 27.72 | 32.78 |
| cohort mean delta | 14.33 | 18.48 |
| generalized | **9.96** | 12.40 |
| personalized | 9.96 | 12.40 |

The generalized model transfers to unseen participants with only modest degradation
(9.07 → 9.96 MAE). The personalized row is **identical by construction** — a held-out
participant has no training episodes, so no calibration exists — and the report's
`limitations` state this rather than presenting the duplicate as a second result.

---

## Uncertainty

`uncertainty_status` is `not_calibrated` on every forecast, `lower_mg_dl` and
`upper_mg_dl` are `null`, and `interval_coverage` is `null` in the metrics report.

This is a deliberate omission, not an oversight. Producing an interval is easy;
producing one whose stated coverage is *measured* requires a held-out calibration set
separate from the one used for model selection, and 33 held-out episodes is not enough
to estimate quantile coverage with any credibility. A ±1 SD band computed from
training residuals would look authoritative and mean nothing.

The plumbing exists (`UncertaintyStatus.empirical_holdout`, the `interval_coverage`
field), so intervals can be switched on once there is enough held-out data to measure
their coverage — and the API will only report them when that measurement exists.

---

## Known limitations

1. **Synthetic data.** The decisive limitation. See the banner at the top.
2. **Six participants, 135 episodes.** Small enough that per-participant results are
   noisy and a single unusual participant visibly moves the aggregate.
3. **One seven-day window.** Held-out meals come from the same week as the training
   meals, so no seasonal, adherence or sensor-drift shift is tested.
4. **Four-knot curve.** Peak timing resolves only to 0/30/60/90/120 minutes (see above).
5. **Collinear features.** `premeal_glucose_mg_dl` vs `cgm_mean_60min_mg_dl`
   (r = 0.97) and `carbs_g` vs `calories_kcal` (r = 0.85) are inherent to the domain.
   Ridge handles them for *prediction*, but collinear columns split credit in the
   driver attribution, so an individual contribution is less separable than it looks.
   Exact linear combinations were removed for this reason — see `docs/methodology.md`.
6. **Medication exposure is confounded with participant identity.** Only 2 of 6
   fixture participants take medication, and one of them is the flat responder. The
   feature's fitted coefficient partly encodes "which person is this", so its driver
   contribution should not be read as a medication effect. No clinical effect is
   claimed or assumed anywhere in the system.
7. **Time of day is UTC when no participant timezone is supplied.** The circadian
   features are then shifted by the participant's true offset. The API discloses this
   in `limitations` when it happens.
8. **Overlapping meals are included and tagged**, not excluded, in the primary report.
   9 episodes have another meal inside the response window, so their observed response
   blends two meals. Rerun with `--exclude-overlapping` for the sensitivity check.
9. **No prospective validation.** Nothing here is evidence of clinical utility.
   Prospective clinical validation would be required for any care-related use.

---

## Test evidence

```
$ python -m pytest -q
385 passed
```

| Suite | Protects |
|---|---|
| `tests/test_leakage.py` | 22 adversarial tests. Buries 400 mg/dL post-meal readings in the history and asserts the feature vector is unchanged; checks the `<= t` boundary to the second; proves the chronology guard fires on a deliberately bad split. |
| `tests/test_metrics.py` | Curve math against hand-calculated arrays — triangle iAUC = 2400 exactly, earliest-tie peak, dip-below-baseline contributes zero. |
| `tests/test_reproducibility.py` | Identical coefficients across runs; `PYTHONHASHSEED` independence; single-row and batched predictions agree, proving transforms are reused rather than refit. |
| `tests/test_personalization.py` | Shrinkage arithmetic, the threshold, the cap, participant isolation, unseen-participant fallback. |
| `tests/test_explain.py` | `intercept + Σ contributions == prediction` exactly, at every horizon and across participants including one with a missing modality. |
| `tests/test_evaluate.py` | Report validates against the frozen schema; sample counts add up; the model must beat **both** baselines. |
| `tests/test_contracts.py` / `test_api.py` | Live responses validated against `contracts/schemas/*.json`. |
