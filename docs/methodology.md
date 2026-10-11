# Model Methodology

**Owner:** Ruhaan Shah · **Contract:** v1.0 · **Model:** `v0.1.0` · **Feature set:** `fs-1.0`

How GLIMPSE turns a participant's history and a meal's composition into a 25-point
glucose trajectory, and why each decision was made that way. Measured results live in
[`validation.md`](validation.md).

---

## The problem

Given a participant, a meal timestamp `t`, and the meal's macronutrients, predict
interstitial glucose over `(t, t+120]` minutes: the four required horizons
(30/60/90/120), the full five-minute trajectory, and the derived summaries (peak, peak
delta, time to peak, incremental AUC).

The constraint that shapes everything: **only information available at `t` may be
used.** A forecast that reads post-meal CGM is not a forecast.

---

## What the model predicts, and why

The model predicts the **delta from the measured pre-meal baseline**, not absolute
glucose. Minute 0 of the returned curve is the last CGM reading at or before `t` — a
measurement — and every later point is `baseline + predicted delta`.

Three reasons:

1. **The baseline comparison becomes clean.** Model 0 (persistence) is exactly
   "delta = 0". No tuning, no ambiguity about what the floor is.
2. **Model capacity goes to the excursion.** Predicting absolute glucose would spend
   most of the model's explanatory power re-learning that `synthetic_p004` runs around
   118 mg/dL and `synthetic_p003` around 92 — which the baseline feature already states.
3. **The curve stays anchored in observation.** A user sees a trajectory that starts
   at their actual reading. If minute 0 were predicted, a small model error there would
   visibly contradict the CGM trace next to it.

A direct consequence: if no CGM reading exists within 15 minutes before the meal, the
request is **refused** with `422 insufficient_premeal_context` rather than served with
an imputed baseline. Inventing minute 0 would fabricate the curve's anchor.

---

## Features

Nineteen candidate signals were considered; **16** are used. Every window looks strictly
backwards from `t`.

| Group | Features |
|---|---|
| Pre-meal glucose | `premeal_glucose_mg_dl`, `cgm_slope_30min_mg_dl_per_min`, `cgm_mean_60min_mg_dl`, `cgm_std_60min_mg_dl`, `minutes_since_last_cgm` |
| Meal composition | `carbs_g`, `protein_g`, `fat_g`, `fiber_g`, `calories_kcal` |
| Timing | `tod_sin`, `tod_cos`, `minutes_since_prior_meal`, `prior_meal_carbs_g` |
| Wearable | `hr_mean_30min_bpm` |
| Medication | `medication_exposure_recent` |

Time of day is encoded as a sin/cos pair so that 23:55 and 00:05 are adjacent rather
than 1430 units apart, and localized to the participant's timezone when the data layer
supplies one.

Medication is **presence and recency only** — "was there any logged administration in
the prior 12 hours". A medication class is never mapped to a pharmacodynamic effect,
and the UI offers no dosing, diagnostic or treatment advice.

### Three features deliberately removed

These removals materially improved the system, and the reasoning generalizes:

**`net_carbs_g` (= carbs − fiber) and `fat_protein_g` (= fat + protein).** For a
*linear* model these are exact linear combinations of columns already present. They
add literally zero information and actively damage the attribution: Ridge distributes
the shared signal across collinear columns, and with them present `carbs_g` was
acquiring a **negative** contribution on high-carbohydrate meals. Removing them
dropped the maximum off-diagonal correlation from ~1.0 to 0.97 and made the drivers
read correctly. Interaction terms belong in a tree-based family, if one is ever
justified by held-out performance — not in a linear model.

**`hr_above_resting_bpm` (= mean HR − resting HR).** Resting heart rate is a
near-constant *participant trait*, so this difference is largely a participant-identity
proxy. Letting the **cohort** model read participant identity would flatter the
`cohort_holdout` protocol and blur the generalized-versus-personalized comparison the
entire evaluation rests on. Stable per-person offsets are the personalization layer's
job, and keeping identity out of Model 1 is what makes that division meaningful.

### Missingness

A missing optional feature is `NaN`, never zero. Imputation is **not** done in the
feature code: it is a *fitted* transformation and belongs inside the pipeline so the
fold boundary is respected. `SimpleImputer(strategy="median", add_indicator=True)`
fills from training-fold medians and appends a binary indicator column, so "heart rate
was unavailable" is information the model can use rather than an average masquerading
as an observation. `synthetic_p005` has no wearable data at all and its forecasts
surface an `hr_mean_30min_bpm_unavailable` driver.

---

## Leakage protection

Structural, not conventional. `build_features` begins with:

```python
past = history.truncated_at(meal_timestamp)
```

Post-meal observations are **absent** from the object the extractors see, not merely
unused. A bug in a window calculation therefore cannot reach forward in time; the
worst it can do is use too little history.

Other guards:

- `ml/schema.py` constructors reject offset-naive datetimes outright. An offset-naive
  timestamp compared against a meal time would shift every window boundary by the
  local UTC offset and silently corrupt the guarantee.
- The meal being forecast is excluded from its own `prior_meal_*` features (`<`, not `<=`).
- `context_override.as_of` must be `<= meal_timestamp`; a later value is
  `422 future_context_override`. The override lets a clinician explore a hypothetical
  pre-meal state; it can never inject a post-meal observation.
- `tests/test_leakage.py` attacks all of this with 400 mg/dL post-meal readings and
  asserts the feature vector is byte-identical.

---

## Targets

A **meal episode** is the unit of learning and of evaluation: one meal, its strictly
pre-meal features, and its post-meal CGM targets.

- The target at horizon `h` is the CGM reading **nearest** `t+h` within ±10 minutes —
  a real measurement, not an interpolation.
- An episode needs targets at all four horizons to be used; otherwise it is excluded
  with the reason recorded.
- A sparse observed curve on the five-minute grid (±2.5 min tolerance) is retained for
  measuring peak/time-to-peak/iAUC error.
- Episodes with another logged meal inside `(t, t+120]` are **tagged**. They are
  included by default and excludable via `--exclude-overlapping` for a sensitivity
  check, because their observed response genuinely blends two meals.

---

## The three models

### Model 0 — persistence

Predicted delta is identically zero: glucose does not move. Required by the master
specification as the benchmark. Not a strawman — over two hours with real sensor noise
it is a hard floor for small meals, and any model that cannot beat it has learned
nothing.

### Benchmark — cohort mean delta

Predicts each horizon's training-fold mean delta, ignoring the meal entirely. Included
because it separates two very different claims: *"the model knows a meal raises
glucose"* (beats persistence) from *"the model knows **this** meal"* (beats the cohort
mean). Reporting only against persistence would flatter the result.

### Model 1 — generalized cohort model

One Ridge regression per horizon, each inside a pipeline:

```
SimpleImputer(median, add_indicator=True) → StandardScaler → Ridge(alpha)
```

- **Ridge, not OLS**, because of the residual collinearity (pre-meal glucose vs 60-min
  mean, r = 0.97; carbs vs calories, r = 0.85) that is inherent to the domain.
- **Ridge, not gradient boosting.** With 135 episodes and 16 features, a boosted
  ensemble would be fitting noise, and it would cost the exact additive attribution
  that `drivers` depends on. The master specification permits
  `HistGradientBoostingRegressor` "when enough independent meals" exist; that
  condition is not met and the switch must be justified by held-out performance, not
  by a desire for a more impressive stack.
- **Alpha chosen by chronological inner CV** (`TimeSeriesSplit` over episodes already
  sorted by meal time), never random k-fold — which would let a later episode help
  select the penalty used to predict an earlier one. All four horizons selected
  `alpha = 10.0`.
- **Transforms are pipeline steps**, so `fit` computes medians and scales from the
  fitting rows only and `predict` reuses them. Train-time and serve-time transforms are
  identical *by construction*, not by careful duplication.
  `tests/test_reproducibility.py` proves it: predicting one row alone matches
  predicting it inside a batch, which could not hold if anything were refit at
  inference.

### Model 2 — shrunk per-participant calibration

The cohort model learns how an *average* person responds. Individuals deviate
systematically, and that deviation is estimable from their own earlier meals.

Per participant, per horizon:

```
offset = clip( mean(training residuals) × n/(n+k),  ±max_offset )
```

with `k = 6`, `max_offset = 60 mg/dL`, and **no offset at all** below
`min_episodes = 4`.

**The shrinkage is the whole point.** With one training meal the raw mean residual is
almost entirely noise, and applying it would make predictions worse while *looking*
like personalization. At `k = 6`: one episode retains 14% of the raw offset, twenty
retain 77%. The correction earns its weight as evidence accumulates.

Two further guards:

- Below `min_episodes` the response is `generalized_fallback` and the reason — with the
  actual episode count and threshold — is `limitations[0]`. The API never implies
  personalization that did not happen.
- The cap stops one extreme training meal from producing an implausible correction.

Offsets are fitted on **training episodes only**. In `ml/evaluate.py` they come from the
training fold and are scored on strictly later meals. In the deployed artifact (fitted
on all data) they come from in-sample residuals, which are mildly optimistic — which is
precisely why no performance claim is ever made from the artifact, only from the
held-out evaluation.

---

## Curve reconstruction

Knots at minute 0 (delta exactly 0, measured) and the four learned horizons; linear
interpolation between them onto the 25-point five-minute grid. Predictions are clipped
to 40–400 mg/dL, and **clipping is disclosed in `limitations` whenever it actually
engages**, because a silently bounded curve presented as raw model output would hide
that the input was far outside the training distribution.

**The honest limitation:** a piecewise-linear function attains its maximum at a knot,
so `time_to_peak_minutes` can only be 0/30/60/90/120. Measured time-to-peak MAE is
~20 minutes, near the floor this parameterization allows. We did not fit a smooth
curve with overshoot between knots: that would manufacture timing precision the model
does not have. The fix is more knots (15/45/75/105 are already supported via a
configurable horizon set), justified by held-out time-to-peak error — a measured
improvement we have not made, so no claim is made about it.

### Derived summaries (frozen definitions)

All computed from the 25 sampled points, so they can never disagree with the curve:

- **peak** — maximum glucose over the points.
- **peak delta** — peak minus baseline. May be ≤ 0.
- **time to peak** — minute of the peak; **earliest minute wins on ties**.
- **incremental AUC** — five-minute trapezoidal integration of
  `max(glucose − baseline, 0)`. Clamping each endpoint to zero *before* integrating is
  what makes this strictly non-negative and distinct from signed net excursion: a dip
  below baseline contributes nothing rather than subtracting area. Verified against
  hand-calculated arrays (triangle → exactly 2400 mg/dL·min).

---

## Explanations

For a Ridge model behind a centering scaler the decomposition is **exact**:

```
predicted_delta = intercept + Σⱼ coefⱼ × scaled_xⱼ
```

`StandardScaler` centers on the training mean, so `scaled_xⱼ` *is* this meal's
standardized deviation from the average training meal. Therefore `intercept` is the
typical cohort excursion at that horizon, and each `coefⱼ × scaled_xⱼ` is exactly how
many mg/dL this meal's value of feature `j` moves the prediction away from it. The
terms sum to the prediction with no residual; `verify_decomposition` asserts it and
`tests/test_explain.py` checks it at every horizon.

No language model, no surrogate, no SHAP approximation, no hand-written narrative.
Drivers are reported at the horizon where the forecast peaks, and that horizon is
stated in `limitations`. The personalization offset appears as its own
`participant_calibration` driver, so the personal correction is visible rather than
folded invisibly into the total. If a future model family has no additive
decomposition, `drivers` must be `[]` with an honest note — an empty list is correct,
a plausible-sounding invented one is a defect.

**Three things a driver does not claim:** causality (it is an observational
association inside this model), transferability (coefficients are cohort-level, not
this individual's physiology), and independence (correlated features split credit in
ways that reflect the fit, not separable mechanisms).

---

## Configuration

Every threshold is in `ml/config.py` with its rationale. The ones that change behaviour
most:

| Setting | Value | Effect |
|---|---|---|
| `baseline_tolerance_minutes` | 15 | How stale a CGM reading may be and still anchor minute 0. |
| `targets.tolerance_minutes` | 10 | How far from `t+h` a reading may be and still count as that target. |
| `min_episodes` | 4 | Below this, no personalization. |
| `shrinkage_k` | 6 | Larger ⇒ stronger pull toward the cohort model when history is sparse. |
| `test_fraction` | 0.25 | Share of each participant's latest meals held out. |
| `random_seed` | 20261011 | Seeds every stochastic step. |
