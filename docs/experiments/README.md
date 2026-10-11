# Accuracy Experiments — development validation and final result

**Owner:** Ruhaan Shah · **Baseline HEAD:** `6a49ca3` · **Contract:** v1.0 (unchanged)
**Dataset:** CGMacros v1.0.0, provenance `official`

## 0 · Evaluation integrity — read first

**The 394-episode Day-8 holdout is no longer a pristine test set.** Its scores were
inspected and published at commit `6a49ca3` and in `docs/validation.md`. Using it to
choose features, hyperparameters or model families would be selection on the test set.

So **all model selection in this document was done on the 1,171-episode DEVELOPMENT
POOL** (the Day-8 train + validation folds) using rolling, participant-aware, purged
validation. The 394 reserved episodes were read exactly once, after the architecture
was frozen, and are reported below as a **previously-seen reference set** — not as
independent evidence.

### Development validation protocol

Per participant, episodes are ordered by meal time and cut into 4 contiguous
chronological blocks. Fold *k* trains on blocks `0..k-1` and validates on block *k*
(expanding window). A 120-minute embargo purges training episodes whose response
window could reach the validation block. Block 0 is never validated (no history).

| Fold | Train | Validate | Participants validated |
|---|---|---|---|
| 1 | 307 | 299 | 45 |
| 2 | 604 | 285 | 45 |
| 3 | 885 | 276 | 45 |

Every candidate is scored on **identical** validation cases, so out-of-fold
predictions are directly comparable and can be paired. Uncertainty uses a
**participant-clustered bootstrap** (participants, not episodes, are the resampling
unit — meals from one person are not independent).

Reproduce:

```bash
python -m ml.experiments --db-url sqlite:///data/local/cgmacros_v2.db --stage models
python -m ml.experiments --db-url sqlite:///data/local/cgmacros_v2.db --stage ablation
python -m ml.experiments --db-url sqlite:///data/local/cgmacros_v2.db --stage personalization
```

## 1 · Model comparison (development validation, out-of-fold)

| model | MAE | RMSE | @30 | @60 | @90 | @120 | peak | ttp | iAUC | fold spread |
|---|---|---|---|---|---|---|---|---|---|---|
| persistence | 34.56 | 49.26 | 22.70 | 37.93 | 39.41 | 38.19 | 52.86 | 68.2 | 3153 | 5.96 |
| cohort_mean | 31.01 | 41.71 | 19.70 | 32.96 | 36.42 | 34.95 | 34.46 | 32.0 | 2405 | 4.40 |
| A ridge | 29.71 | 39.86 | 18.33 | 31.23 | 35.24 | 34.02 | 32.25 | 32.3 | 2267 | 4.37 |
| B ridge_personal | 26.89 | 37.04 | 17.31 | 27.20 | 31.94 | 31.11 | 29.70 | 30.1 | 1990 | 4.88 |
| C gbm | 27.14 | 37.09 | 18.11 | 28.45 | 31.08 | 30.91 | 28.01 | 33.6 | 2015 | 5.12 |
| D gbm_personal | 25.57 | 35.42 | 17.17 | 25.76 | 29.47 | 29.88 | 26.76 | 31.8 | 1843 | 5.62 |
| E blend_personal | 25.12 | 34.61 | 16.57 | 25.38 | 29.25 | 29.27 | 27.10 | 30.7 | 1810 | 5.26 |

Paired participant-clustered bootstrap against the baseline **B ridge_personal**
(positive Δ = candidate better):

| candidate | Δ MAE | 95% CI | excludes 0 |
|---|---|---|---|
| C gbm | −0.248 | [−1.630, +1.193] | **no** |
| D gbm_personal | +1.318 | [+0.195, +2.652] | yes |
| **E blend_personal** | **+1.771** | **[+1.069, +2.626]** | **yes** |

**Three findings worth stating plainly:**

1. **Boosting alone does not beat the linear baseline.** `C gbm` (27.14) is
   indistinguishable from `B ridge_personal` (26.89); its CI spans zero. The win comes
   from *personalization*, not from model capacity.
2. **Boosting and the linear model are complementary.** The 50/50 blend (25.12) beats
   both halves, and beats the better half (`D gbm_personal`, 25.57) as well. Averaging
   two differently-biased estimators is doing the work.
3. **Fold spread (~5 mg/dL) exceeds every between-model difference.** Absolute error
   depends more on which meals are being predicted than on which model predicts them.

## 2 · Feature ablation (Ridge + personalization, development validation)

Control = all 16 features, MAE 26.889. Positive Δ means removing the group HURT.

| removed group | MAE | Δ vs control |
|---|---|---|
| glucose context | 27.326 | +0.437 |
| macros | 27.276 | +0.387 |
| carbs only | 27.108 | +0.219 |
| prior meal | 26.959 | +0.070 |
| baseline glucose | 26.949 | +0.060 |
| medication flag | 26.889 | -0.000 |
| data recency | 26.869 | -0.020 |
| circadian | 26.868 | -0.021 |
| heart rate | 26.799 | -0.090 |

**What carries the signal:** pre-meal glucose context (+0.44 when removed) and the
macronutrients (+0.39; carbohydrate alone +0.22).

**What does not measurably contribute on this dataset:** heart rate (−0.09), circadian
phase (−0.02), CGM recency (−0.02) and the medication flag (**exactly 0.000** — CGMacros
records no medications at all, so the feature is zero-variance and its Ridge coefficient
is exactly 0.0, verified).

**No feature was removed.** Every apparent gain is ≤0.09 mg/dL against a fold-to-fold
spread of ~5 mg/dL, so dropping features on that evidence would be fitting noise. The
feature set is unchanged from the baseline, which also avoids a `FEATURE_SET_VERSION`
churn. The medication flag is retained because it is provably inert here and will carry
real signal on a medicated cohort (e.g. MetaboNet).

## 3 · Personalization experiments (development validation)

All on the blend. Shrinkage factor is `n/(n+k)`.

| variant | MAE | Δ vs k=6 | 95% CI | excludes 0 |
|---|---|---|---|---|
| k=3 | 24.96 | +0.158 | [+0.064, +0.260] | yes |
| k=6 (previous) | 25.12 | — | — | — |
| k=12 | 25.44 | −0.322 | [−0.471, −0.188] | yes |
| k=20 | 25.81 | −0.690 | [−0.985, −0.420] | yes |
| recency-weighted, 3-day half-life | 25.18 | −0.057 | [−0.125, +0.012] | no |
| recency-weighted, 7-day half-life | 25.14 | −0.024 | [−0.062, +0.011] | no |

**Monotone in k**, so less shrinkage is better here — expected once participants have
~20 training episodes, because the raw mean residual is then reasonably estimated and
heavy shrinkage discards real signal. `shrinkage_k` moved 6.0 → **3.0**.

The k=3 gain is small (+0.16) but harmed **no** participant by more than 0.37 mg/dL
while helping 6 by more than 0.5.

**Recency weighting bought nothing.** Both half-lives have CIs spanning zero. Over a
~10-day recording there is not enough drift for recency to matter; it was implemented,
measured and rejected.

## 4 · Final model

**Selected: `blend_personalized`** — a fixed 50/50 average of the regularized Ridge and
a conservative `HistGradientBoostingRegressor`, then corrected by shrunk
per-participant residual offsets (k=3).

| | |
|---|---|
| Booster | `max_depth=3`, `learning_rate=0.05`, `max_iter=300`, `min_samples_leaf=25`, `max_leaf_nodes=8`, `l2_regularization=1.0` |
| Blend weight | **fixed** 0.5/0.5, not tuned — a learned weight would need another validation level |
| Library | `sklearn.ensemble.HistGradientBoostingRegressor` — already pinned, handles NaN natively, no new binary dependency near the deadline |

Conservative tree settings because the binding constraint is ~900 independent meal
episodes, not model capacity.

## 5 · Final evaluation on the 394 reserved episodes

> **Previously-seen reference set.** Reported once, after freezing. Not independent
> evidence.

| model | MAE | RMSE | @30 | @60 | @90 | @120 | peak | ttp | iAUC |
|---|---|---|---|---|---|---|---|---|---|
| persistence | 32.58 | 45.70 | 23.83 | 36.68 | 36.34 | 33.45 | 51.80 | 65.18 | 3030.9 |
| cohort mean | 28.08 | 38.15 | 20.22 | 29.72 | 31.65 | 30.74 | 32.22 | 28.40 | 2136.5 |
| generalized (Ridge) | 26.47 | 35.93 | 18.23 | 27.77 | 30.66 | 29.24 | 29.95 | 32.94 | 1990.2 |
| personalized (previous baseline) | 23.97 | 32.27 | 17.06 | 24.74 | 27.55 | 26.53 | 26.49 | 29.80 | 1701.3 |
| **blend_personalized (SELECTED)** | 23.22 | 31.61 | 16.74 | 24.29 | 26.33 | 25.51 | 25.31 | 32.41 | 1653.5 |

### Did it transfer? Partly.

| | Development validation | Reference set |
|---|---|---|
| Baseline MAE | 26.89 | 23.97 |
| Selected MAE | 25.12 | **23.22** |
| Δ | **+1.77** | **+0.75** |
| 95% CI (participant-clustered) | [+1.069, +2.626] | **[−0.014, +1.450]** |
| Excludes zero | **yes** | **no** |
| P(selected better) over resamples | — | 0.973 |

**The reference-set improvement is NOT statistically resolvable** with 44 participants:
the CI just includes zero. Direction and magnitude are consistent with development
validation and P(better) is 0.973, but **no significance is claimed on the reference
set.** The selection rests on the development result, which is the only legitimate
selection signal available.

The gain also shrank (1.77 → 0.75). The most likely reason is sample size: development
folds train on 307–885 episodes while the final model trains on 1,171, and the
booster's advantage over the linear model is largest when the linear model has least
data. Mild optimism from selecting among seven candidates on the same folds is a second
contributor.

### Per-participant, reference set

29 of 44 improved, 15 worsened. Δ range −5.90 to +5.84, median +0.78.
Worst: `cgmacros_018` (−5.90), `cgmacros_038` (−3.39), `cgmacros_042` (−2.65).
Best: `cgmacros_039` (+5.84), `cgmacros_031` (+4.91), `cgmacros_044` (+4.71).

### The tradeoff, stated explicitly

| metric | previous baseline | selected | change |
|---|---|---|---|
| MAE | 23.97 | **23.22** | **−0.75 better** |
| RMSE | 32.27 | **31.61** | −0.66 better |
| Peak MAE | 26.49 | **25.31** | −1.18 better |
| iAUC MAE | 1701.3 | **1653.5** | −47.8 better |
| **Time-to-peak MAE** | **29.80 min** | **32.41 min** | **+2.61 WORSE** |

**Time-to-peak got worse.** Every glucose-level metric improved, but peak *timing*
degraded by 2.6 minutes. The four-knot representation is the hard limit here — a
piecewise-linear curve can only peak at 0/30/60/90/120 — and the smoother blended
curve shifts which knot wins more often. Denser knots are the real fix; that was not
attempted, so no claim is made about it.

### Interpretability cost

The Ridge family admits an **exact** additive decomposition
(`intercept + Σ contributions == prediction`). The booster does not. The served
response therefore:

* decomposes the Ridge half exactly and **scales each contribution by the 0.5 blend
  weight**, so every number is the mg/dL that feature moves the *served* prediction;
* states in `limitations` that the boosted half is **not** attributed and the drivers
  are a partial explanation.

`python -m ml.train --family ridge` still produces the fully-explainable model, at a
cost of ~0.75 mg/dL MAE. That is a real tradeoff for the demo to weigh, not a
regression.

## 6 · Inference latency

Measured end to end through the API against the real database.

| stage | before | after |
|---|---|---|
| `get_history` | 663 ms | — |
| `build_features` | 2 ms | 2 ms |
| `predict_deltas` (Ridge) | 2.1 ms | 2.1 ms |
| `predict_deltas` (blend) | 40.1 ms | 40.1 ms |
| **end-to-end median** | **697 ms** | **206 ms** |
| end-to-end p95 | 721 ms | 297 ms |

The bottleneck was never the model: it was materializing a participant's entire history
(~2,900 CGM + ~14,600 heart-rate + ~29,500 activity rows). The prediction path now uses
`get_history_as_of` with a bounded 24-hour lookback, which filters in SQL. **3.4×
faster, and a second independent leakage layer** — a post-meal row is never read at all.

Equivalence verified on **60 real meals spanning all participants: 60/60 byte-identical
forecasts**, so the speedup changes no prediction.

Training cost: 69 s (Ridge) / 74 s (blend) for 1,171 episodes; peak RSS ~0.17 GB.

## 7 · Not attempted, and why

- **LightGBM / CatBoost / XGBoost** — a new binary dependency hours before a deadline,
  with no measured advantage over the already-pinned sklearn booster at this sample size.
- **Denser trajectory knots** (Phase 6) — the known fix for time-to-peak. Deferred;
  the blend was frozen and committed first so the repository always holds a working,
  tested improvement.
- **Deep learning** — ~900 training episodes.
- **Tuned blend weight / large hyperparameter sweeps** — would need another validation
  level to choose honestly.
