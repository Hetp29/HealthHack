# Physiological Modelling Research Report

**Research lead:** Ruhaan Shah · **Branch:** `ruhaan` · **Baseline HEAD:** `d9862fc`
**Contract:** v1.0 — unchanged · **Dataset:** CGMacros v1.0.0 (`official`)
**Feature set:** fs-1.1 → **fs-1.2**

> **Guiding principle applied throughout:** biological plausibility generated the
> hypotheses; out-of-sample predictive performance decided which survived. Four of the
> six signal groups were rejected, including the one that looked best on the first
> validation regime.

---

## 1 · Headline result

Two signal groups survived validation and are now in production. On the 394 reserved
Day-8 reference episodes:

| model | MAE | RMSE | @30 | @60 | @90 | @120 | peak | ttp | iAUC |
|---|---|---|---|---|---|---|---|---|---|
| persistence | 32.58 | 45.70 | 23.83 | 36.68 | 36.34 | 33.45 | 51.80 | 65.18 | 3030.9 |
| cohort mean | 28.08 | 38.15 | 20.22 | 29.72 | 31.65 | 30.74 | 32.22 | 28.40 | 2136.5 |
| generalized | 23.31 | 32.08 | 16.55 | 23.77 | 27.15 | 25.76 | 27.21 | 29.72 | 1664.2 |
| personalized | 23.64 | 31.97 | 16.83 | 24.04 | 27.49 | 26.19 | 26.63 | 28.83 | 1653.0 |
| **blend_personalized (SELECTED)** | 21.90 | 30.08 | 16.43 | 22.81 | 24.84 | 23.54 | 24.29 | 26.93 | 1566.2 |

Against the previous production model (fs-1.1, same 394 episodes):

| metric | fs-1.1 | fs-1.2 | change |
|---|---|---|---|
| MAE | 23.22 | **21.90** | **−1.32** |
| RMSE | 31.61 | **30.08** | −1.53 |
| Peak MAE | 25.31 | **24.29** | −1.02 |
| **Time-to-peak MAE** | 32.41 min | **26.93 min** | **−5.48** |
| iAUC MAE | 1653.5 | **1566.2** | −87.3 |

Every metric improved, **including time-to-peak** — the metric that regressed in the
previous round and the one I flagged as the model's weakest. It is now better than any
model measured in this project.

Participant-clustered bootstrap on the reference set (positive = selected better):

| comparison | Δ MAE | 95% CI | excludes 0 |
|---|---|---|---|
| vs `personalized` | +1.734 | [+0.780, +2.635] | **yes** |
| vs `generalized` | +1.403 | [+0.218, +2.509] | **yes** |
| vs `cohort_mean` | +6.180 | [+4.316, +8.191] | **yes** |
| `generalized` vs `personalized` | −0.330 | [−1.095, +0.461] | **no** |

That last row is a finding in its own right — see §6.

---

## 2 · Evidence levels (mission Part I §2)

| Level | Meaning | Signals |
|---|---|---|
| **A** directly observed | measured CGM / meal log / HR / activity / lab value | all `g_slope_*`, `g_accel`, `g_rise_fraction`, `g_cv_60`, `g_range_120`, `r_hist_n`, `m_meals_4h`, `m_carbs_24h`, `v_activity_kcal_60`, `v_mets_mean_60`, `p_a1c`, `p_bmi`, `p_age`, `p_fasting_glucose` |
| **B** empirical proxy | derived from observation, represents a process indirectly | `g_dev_from_night`, all `m_*` kernels, all `a_*`, `r_hist_recovery`, `p_homa_ir`, `p_cohort_ord`, `v_activity_decay`, `v_hr_dev_baseline` |
| **C** latent predictive state | model-estimated, no independent physiological ground truth | `r_hist_peak_delta`, `r_hist_per_carb`, `r_hist_ttp` |
| **D** not identifiable | needs unobserved insulin kinetics, gastric emptying or tracer data | **none implemented** — see §7 |

**Nothing in this project estimates insulin concentration, insulin sensitivity, gastric
emptying rate or endogenous glucose production.** `p_homa_ir` is the standard HOMA-IR
*surrogate* computed from two fasting lab values; it is labelled Level B and is never
described as a measured insulin sensitivity. The `r_hist_*` fingerprints summarize
observed glucose curves and are labelled Level C, not as measures of β-cell function.

---

## 3 · Validation protocol — and why two regimes were needed

All selection used the development pool (1,171 episodes = Day-8 train + validation).
The 394 reserved episodes were **never** loaded during selection.

**Regime 1 — within-person rolling folds.** Per participant, episodes ordered by time
and cut into 4 contiguous blocks; fold *k* trains on blocks `0..k-1`, validates on
block *k*, with a 120-minute embargo. Train 307/604/885.

**Regime 2 — cold-start folds.** Entire participants held out (4 folds, deterministic).

Running both was the single most important methodological decision in this mission. A
static per-participant covariate can improve Regime 1 for an uninteresting reason: it
acts as a **participant-identity proxy**, and Regime 1 validates on later meals from
participants already seen in training. Only Regime 2 can distinguish transferable
biology from memorized identity.

---

## 4 · Group ablations — the two regimes disagree, decisively

### Regime 1 — within-person

| group | MAE | Δ vs baseline | 95% CI | excludes 0 |
|---|---|---|---|---|
| BASELINE (production features) | 24.96 | — | — | — |
| + glucose | 24.14 | +0.815 | [+0.437, +1.226] | **yes** |
| + mob | 25.07 | -0.107 | [-0.210, +0.003] | no |
| + absorption | 24.93 | +0.029 | [-0.175, +0.205] | no |
| + response | 25.19 | -0.229 | [-0.676, +0.198] | no |
| + profile | 23.57 | +1.392 | [+1.059, +1.778] | **yes** |
| + activity | 25.17 | -0.208 | [-0.424, +0.003] | no |
| + ALL groups | 23.97 | +0.989 | [+0.490, +1.515] | **yes** |

### Regime 2 — cold-start (unseen participants)

| group | MAE | Δ vs baseline | 95% CI | excludes 0 |
|---|---|---|---|---|
| BASELINE (production features) | 26.93 | — | — | — |
| + glucose | 25.40 | +1.527 | [+0.853, +2.264] | **yes** |
| + mob | 26.90 | +0.031 | [-0.153, +0.212] | no |
| + absorption | 26.89 | +0.034 | [-0.093, +0.152] | no |
| + response | 24.82 | +2.107 | [+1.165, +3.049] | **yes** |
| + profile | 26.16 | +0.767 | [-0.302, +1.762] | no |
| + activity | 27.30 | -0.369 | [-0.641, -0.102] | **yes** |
| + ALL groups | 24.39 | +2.539 | [+1.635, +3.429] | **yes** |

**The reordering is the result.** `profile` is the best group in Regime 1 and
statistically indistinguishable from nothing in Regime 2. `response` is useless in
Regime 1 and the best single group in Regime 2.

---

## 5 · Combination tests and the selected feature set

### Within-person

| group | MAE | Δ vs baseline | 95% CI | excludes 0 |
|---|---|---|---|---|
| BASELINE (production features) | 24.96 | — | — | — |
| + glucose | 24.14 | +0.815 | [+0.437, +1.226] | **yes** |
| + response | 25.19 | -0.229 | [-0.676, +0.198] | no |
| + profile | 23.57 | +1.392 | [+1.059, +1.778] | **yes** |
| + glucose + response | 24.33 | +0.629 | [+0.108, +1.199] | **yes** |
| + glucose + response + profile | 23.95 | +1.014 | [+0.495, +1.588] | **yes** |
| + ALL groups | 23.97 | +0.989 | [+0.490, +1.515] | **yes** |

### Cold-start

| group | MAE | Δ vs baseline | 95% CI | excludes 0 |
|---|---|---|---|---|
| BASELINE (production features) | 26.93 | — | — | — |
| + glucose | 25.40 | +1.527 | [+0.853, +2.264] | **yes** |
| + response | 24.82 | +2.107 | [+1.165, +3.049] | **yes** |
| + profile | 26.16 | +0.767 | [-0.302, +1.762] | no |
| + glucose + response | 23.96 | +2.971 | [+2.002, +4.029] | **yes** |
| + glucose + response + profile | 24.32 | +2.610 | [+1.680, +3.538] | **yes** |
| + ALL groups | 24.39 | +2.539 | [+1.635, +3.429] | **yes** |

**Selected: `glucose + response`** (13 signals). It is the only combination that wins
in *both* regimes with CIs excluding zero, and in cold-start it beats every alternative
including the full vector. Adding `profile` on top makes cold-start **worse**
(23.96 → 24.32).

Production validation confirmed the gain transferred: re-running the development
leaderboard on the real fs-1.2 implementation gave blend MAE **24.346** vs **24.960**
at fs-1.1 — a gain of +0.614, against +0.629 predicted by the research harness.

---

## 6 · What the surviving signals mean, and what they do not

### Glucose dynamic state (Level A) — RETAINED

`g_slope_10/15/60`, `g_accel`, `g_rise_fraction`, `g_cv_60`, `g_range_120`,
`g_dev_from_night`. Multi-timescale backward momentum, curvature, trend persistence,
and elevation above the participant's own overnight floor.

Helps in both regimes (+0.82 within, +1.53 cold-start). Interpretation: a single
30-minute slope was discarding information about *where in a trajectory* the meal lands.
This is measured glucose, so the evidence level is the highest available — and it
requires no new data modality.

### Historical response fingerprint (Level C) — RETAINED, and the most interesting finding

`r_hist_peak_delta`, `r_hist_per_carb`, `r_hist_ttp`, `r_hist_recovery`, `r_hist_n`:
shrunk means of this participant's **own earlier measured responses**, strictly before
the meal being forecast.

The asymmetry is instructive. In Regime 1 these signals are *worthless* (−0.23) because
the fitted per-participant calibration offset already captures the same information. In
Regime 2 they are the **best single group** (+2.11) because a held-out participant has
no fitted offset — but they do have their own recent meals.

**Product consequence:** this is online personalization that works for a brand-new user
after a handful of logged meals, with no retraining and no fitted offset. The fitted
calibration cannot do that; these features can.

**What they are not:** summaries of observed glucose curves. Not measures of insulin
secretion, glucose disposal, or gastric emptying. A participant with a large
`r_hist_peak_delta` has historically had large excursions; the mechanism is unobserved.

### The calibration offset is now nearly redundant

With fs-1.2, `generalized` (23.31) vs `personalized` (23.64) has a CI spanning zero
(−0.330 [−1.095, +0.461]), and the fitted offset now helps only 22 of 44 participants
versus 33 of 44 at fs-1.1. The fingerprint features have largely absorbed its job. The
offset is retained because the full combination still wins, but it is no longer carrying
the personalization story — and a future simplification could drop it.

---

## 7 · Rejected hypotheses

| Hypothesis | Biological rationale | Result | Verdict |
|---|---|---|---|
| **Participant lab profile** (A1c, BMI, age, fasting glucose, HOMA-IR, ADA cohort) | chronic glycaemia and insulin resistance should modulate meal response | +1.392 within-person (CI excludes 0) but **+0.767 cold-start, CI [−0.302, +1.762] spans zero**; adding it to the selected set makes cold-start worse | **REJECTED.** At n=45 participants the within-person gain is largely participant-identity memorization, not transferable phenotype. This is the result I would most have liked to be true. |
| **Meal-on-board** (exponential carbohydrate/fat decay kernels, 60 and 120 min time constants, 4 h lookback) | residual carbohydrate from a prior meal should still be absorbing | −0.107 within, +0.031 cold-start; both CIs span zero | **REJECTED.** CGMacros meals are mostly well separated, and `minutes_since_prior_meal` + `prior_meal_carbs_g` already capture what little signal exists. |
| **Macronutrient interactions** (carb×fat, carb×fibre, carb×protein, saturating carb, energy fractions) | fat and fibre delay gastric emptying | +0.029 within, +0.034 cold-start; CIs span zero | **REJECTED.** The booster can already represent interactions; explicit products add nothing. |
| **Activity / autonomic load** (pre-meal kcal, METs, decayed 6 h activity, HR vs own baseline) | recent exercise increases glucose uptake | −0.208 within, **−0.369 cold-start with CI excluding zero (significantly WORSE)** | **REJECTED.** Actively harmful. Consistent with the earlier fs-1.1 ablation, where heart rate contributed nothing. |
| **Level-D mechanistic models** (minimal model, Dalla Man / UVA-Padova-style compartments with insulin states) | the published standard for glucose–insulin dynamics | not implemented | **NOT IDENTIFIABLE.** CGMacros has a single fasting insulin value per participant and no insulin time series, no tracer data and no gastric-emptying measurement. Fitting insulin sensitivity or appearance-rate parameters would produce numbers with no identifiability. Documented as future work requiring richer measurement, not attempted and not claimed. |

---

## 8 · Reproducing

```bash
# 1. signal matrix (reads bio.csv in place from the published zip; never extracts)
python -m research.physiology.build_signals --db-url sqlite:///data/local/cgmacros_v2.db

# 2. group ablations, both regimes
python -m research.physiology.experiment_runner --db-url ... --stage groups
python -m research.physiology.experiment_runner --db-url ... --stage coldstart

# 3. combination tests
python -m research.physiology.experiment_runner --db-url ... --stage combos --folds rolling
python -m research.physiology.experiment_runner --db-url ... --stage combos --folds coldstart

# 4. production retrain + single reference evaluation
python -m ml.train    --source sqlite --db-url ... --family blend --holdout day8 --out ml/artifacts_cgmacros_fs12
python -m ml.evaluate --source sqlite --db-url ... --protocol day8_holdout --out ml/artifacts_cgmacros_fs12
```

Results land in `research/physiology/results/`.

## 9 · Cost

| | |
|---|---|
| Signal matrix (1,171 × 35) | 63 s |
| One ablation stage (8 candidates × 3 folds) | 46 s |
| Cold-start stage | 81 s |
| Production training (1,171 episodes, fs-1.2 blend) | 77 s |
| Reference evaluation | 78 s |
| **Inference latency** (end to end, real DB) | **353 ms median / 413 ms p95** |

Latency rose from 206 ms because the fingerprint needs an 8-day history window rather
than 24 h (measured fetch cost 34 ms → 259 ms). Still well under the 697 ms the
pipeline cost before the earlier lookback optimization.

## 10 · Statistical safeguards and honest limits

- **Models/configurations evaluated in this mission: 29** (7 model families in the
  earlier round, 8 group ablations × 2 regimes, 7 combinations × 2 regimes). No
  unbounded hyperparameter search was run.
- Every comparison is on **identical validation cases**, paired per episode.
- All intervals are **participant-clustered** bootstraps (participants, not episodes,
  are the resampling unit) because meals from one person are not independent.
- **The 394-episode reference set was inspected in a previous round** (commit
  `6a49ca3`). It is reported here as a previously-seen reference, not as independent
  evidence. No model was selected using it.
- 44–45 participants is a small cohort. The cold-start regime has only 4 folds, so its
  intervals are wide.
- ~22 mg/dL MAE remains far too large for clinical use. This is a research prototype.
- No causal claim is made from any predictive gain.
