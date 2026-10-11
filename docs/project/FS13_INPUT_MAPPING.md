# fs-1.3 input mapping for live accounts

How each of the 39 frozen model inputs is obtained for an application user. The
feature definitions are **not changed** — `ml/features.py::build_features` computes
every one of them, exactly as at freeze time. This table records only *what history
that function is given* and what that means for each input.

Implemented in `backend/app/v2/services/history.py`. Each stored prediction keeps all
39 values, in frozen order, in `feature_snapshots.feature_values`
(`null` = missing at prediction time; the frozen pipeline's fitted imputer fills it).

## Rules applied to every input

| rule | where enforced |
|---|---|
| Nothing observed after the meal instant is read | SQL `observed_at <= meal time`, then the frozen `history.truncated_at` |
| One sensor per forecast | `services/glucose.py::select_model_series` |
| Lookback 8 days at most | mirrors the frozen `PredictionService._prediction_history` |
| Clock = local wall clock, IANA zone, converted per observation | `project_local_clock`; DECISIONS D-006 |
| History never spans a UTC-offset change | `engine_lookback_start`: the lookback starts at the change when one falls inside the 8 days |
| Storage stays true UTC | only the engine input is projected |
| Only `record_kind = cgm` readings | scans and fingersticks are stored but not used |
| Readings are never interpolated or filled | a gap stays a gap |

## The 39 inputs

Source "CGM" means the user's stored glucose observations in mg/dL (mmol/L converted
at ingestion with the original preserved). "Missing → imputed" means the value is
left `NaN` and the frozen fitted pipeline substitutes its training-fold median and
sets a missing-indicator, which the response discloses in `limitations`.

| # | feature | source | window (before meal) | null behaviour | notes |
|---|---|---|---|---|---|
| 1 | `premeal_glucose_mg_dl` | CGM | last reading ≤ 15 min | **forecast refused** (422) | Minute 0 is always a measured reading |
| 2 | `cgm_slope_30min_mg_dl_per_min` | CGM | 30 min | missing → imputed (< 2 readings) | |
| 3 | `cgm_mean_60min_mg_dl` | CGM | 60 min | missing → imputed | |
| 4 | `cgm_std_60min_mg_dl` | CGM | 60 min | missing → imputed | |
| 5 | `minutes_since_last_cgm` | CGM | — | never null once #1 exists | |
| 6 | `carbs_g` | confirmed meal | — | required | user-confirmed, contract-bounded |
| 7 | `protein_g` | confirmed meal | — | required | |
| 8 | `fat_g` | confirmed meal | — | required | |
| 9 | `fiber_g` | confirmed meal | — | required | |
| 10 | `calories_kcal` | confirmed meal | — | required | |
| 11 | `tod_sin` | meal time on local clock | — | never null (timezone is mandatory) | |
| 12 | `tod_cos` | meal time on local clock | — | never null | |
| 13 | `minutes_since_prior_meal` | previous confirmed meal | capped 720 min | missing → imputed (no earlier meal in 8 days) | see note A |
| 14 | `prior_meal_carbs_g` | previous **admissible** meal | — | missing → imputed | see note A |
| 15 | `hr_mean_30min_bpm` | **not supplied** | — | always missing → imputed | see note B |
| 16 | `medication_exposure_recent` | **not supplied** | — | always 0.0 | see note C |
| 17 | `g_slope_10` | CGM | 10 min | missing → imputed | needs ≥ 2 readings in 10 min |
| 18 | `g_slope_15` | CGM | 15 min | missing → imputed | |
| 19 | `g_slope_60` | CGM | 60 min | missing → imputed | |
| 20 | `g_accel` | derived from 18 and 2 | — | missing if either is | |
| 21 | `g_rise_fraction` | CGM | last 6 readings | missing → imputed (< 3) | |
| 22 | `g_cv_60` | CGM | 60 min | missing → imputed | |
| 23 | `g_range_120` | CGM | 120 min | missing → imputed | |
| 24 | `g_dev_from_night` | CGM | readings at 02:00–05:59 **local**, in the 8-day lookback | missing → imputed (no overnight data) | clock-dependent; see D-006 |
| 25 | `r_hist_peak_delta` | eligible completed meals + their CGM | 8 days | missing when no admissible meal | Level A history |
| 26 | `r_hist_per_carb` | same | 8 days | same | |
| 27 | `r_hist_ttp` | same | 8 days | same | |
| 28 | `r_hist_recovery` | same | 8 days | same | |
| 29 | `r_hist_n` | same | 8 days | 0, never null | true admissible count |
| 30–33 | `q_retr_delta_30/60/90/120` | same | 8 days | missing below 5 admissible meals | frozen retrieval gate |
| 34 | `q_retr_n` | same | 8 days | never null | true count below the gate, 5 at or above |
| 35 | `q_retr_weight` | same | 8 days | 0, never null | |
| 36 | `q_retr_mean_distance` | same | 8 days | missing below the gate | |
| 37 | `q_retr_peak_delta` | same | 8 days | missing below the gate | |
| 38 | `q_retr_ttp` | same | 8 days | missing below the gate | |
| 39 | `q_retr_curve_spread` | same | 8 days | missing below the gate | |

### Note A — previous-meal context

The engine is shown: (a) every earlier confirmed meal whose 120-minute window is
still open — these cannot be retrieved under the frozen strict rule but supply
"time since previous meal"; and (b) completed meals that were scored **eligible**
(DECISIONS D-009), provided the user consents to personal-history use.

If the most recent earlier meal is not in that set, its timing is still supplied,
through the engine's own `context_override.minutes_since_prior_meal`. Its carbohydrate
cannot be supplied that way, so `prior_meal_carbs_g` then comes from the most recent
*admissible* meal, or is missing. The snapshot records
`prior_meals.prior_meal_timing_from_override = true` when this happens.

### Note B — heart rate is deliberately not supplied

`hr_mean_30min_bpm` was trained on CGMacros's minute-level Fitbit stream. No live
source is a verified equivalent: WHOOP's API exposes no continuous stream, Fitbit
gates intraday data behind a separate approval, and HealthKit samples arrive at a
different cadence from different hardware. Supplying a non-equivalent signal would
change forecasts in an unmeasured way. The feature is left missing for every app
user. Changing this needs a measured equivalence check and a governance decision.

### Note C — medication is deliberately not supplied

`medication_exposure_recent` would be set to 1.0 by any medication event in the prior
12 hours. The briefs require that medication information never silently modify a
forecast, so no medication event is shown to the engine and the feature is 0.0. A
logged dose changes nothing about a forecast or an alert (tested).

## Never model inputs

This includes the **simulated** wearable data in the demonstration accounts
(`data_class: demo`, providers `demo_*`): it is stored and displayed only. With every
simulated source switched on, all 39 feature values and every forecast are identical
to a run without them.

Questionnaire answers, diagnosis, HbA1c, weight, wearable sleep / HRV / recovery /
steps / workouts, medication names and doses, meal photographs and image embeddings,
AI confidence labels, and the account identity. None has a slot in the 39-column
vector and the orchestrator does not read them.

## Distribution shifts to be aware of

These are not defects in the wrapper; they are ways live inputs differ from the
training data, and reported accuracy does not transfer across them automatically.

1. **Macros.** Training used the dataset's *consumed-meal* macro values. Live values
   are a user-confirmed estimate, often starting from a photo, entered before or
   while eating. Expect more input error than the evaluation saw.
2. **No heart rate.** Every training row with heart-rate data had it; live rows never
   do. The imputed-median path is exercised far more than in training.
3. **Sensor.** Training used Dexcom G6 Pro at 5-minute cadence. A 15-minute source
   (Libre exports) leaves short-window slope features missing and can never satisfy
   the 20-reading retrieval rule (BLOCKERS B-011).
4. **Stricter history.** Live history excludes confounded episodes that training
   retained (D-009), so `r_hist_n` runs lower for the same person.
5. **Clock.** Each observation is read on its own correct local clock, and elapsed
   times are exact. For up to 8 days after a daylight-saving change the history is
   shorter (it starts at the change), so history-dependent inputs are sparser and
   `g_dev_from_night` may be missing. Travel and account-timezone changes are not
   tracked per observation (D-006).
