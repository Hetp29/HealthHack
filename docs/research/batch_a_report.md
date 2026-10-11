# Batch A — Few-shot personalization leaderboard

**Owner:** Ruhaan Shah · **From:** `0af6a39` · **Control:** fs-1.3 production model
**Contract:** v1.0 unchanged · **Production inference:** UNTOUCHED (all code in `research/batch_a/`)

## Verdict

**No Batch A candidate beats fs-1.3.** The best, `A5 + kernel-ridge OOF residual`, is
+0.141 mg/dL cold-start with a 95% CI of [−0.021, +0.311] that **includes zero**.
Nothing is promoted. fs-1.3 is retained.

The most valuable output of this batch is not a new model — it is that **the two
controls validate the existing fs-1.3 personalization design**, and one candidate
revealed a serious instability failure mode.

---

## 1 · Setup

| | |
|---|---|
| Development pool | 1,171 episodes, 45 participants |
| Reserved reference set | 394 episodes — **never loaded** |
| Within-person folds | rolling purged, train 307/604/885, validate 299/285/276 |
| Cold-start folds | 4 participant-held-out folds, train 838/904/893/878 |
| Support sets | median 12 admissible historical meals; 46 episodes (one per participant) have none; 80% have ≥5 |
| Fits per candidate | 3 within-person folds + 4 cold-start folds, identical episodes for all |

Every candidate is the fs-1.3 family (Ridge + booster blend, then shrunk participant
calibration) differing only in what is appended, removed, or added on top.

### Honest scope note on the two "neural" candidates

No deep-learning framework is installed, and installing one hours before a submission
deadline to fit ~900 training episodes is not a defensible trade. So:

- **A1 DeepSets** has a **fixed** item embedding (hand-specified permutation-invariant
  pooling: mean, max, min and inverse-distance-weighted mean of per-item differences,
  observed deltas and ages) and a **learned** MLP aggregator. A full DeepSets would also
  learn the item embedding by backpropagation.
- **A2 attention** is single-head attention whose scores come from a **learned diagonal
  metric and learned temperature**, fitted by L-BFGS-B on the training fold — i.e. a
  learned-kernel weighted average, not a transformer block.

Both are the strongest versions runnable here and are labelled as such rather than
overclaimed.

---

## 2 · Within-person (rolling purged folds)

| candidate | MAE | RMSE | @30 | @60 | @90 | @120 | peak | ttp | iAUC | Δ MAE | 95% CI | excl 0 | worst ppt | fit s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C2 control: no personal context | **24.145** | 33.16 | 16.48 | 24.83 | 27.81 | 27.46 | 25.94 | 29.15 | 1741 | +0.107 | [-0.439, +0.616] | no | -6.21 | 17 |
| A0 CONTROL fs-1.3 | **24.251** | 32.80 | 17.00 | 24.29 | 27.77 | 27.94 | 25.92 | 30.23 | 1743 | — | — | — | — | 16 |
| A4 + kernel retrieval (explicit) | **24.326** | 32.95 | 16.99 | 24.64 | 27.74 | 27.93 | 25.99 | 29.95 | 1757 | -0.074 | [-0.204, +0.047] | no | -1.31 | 17 |
| A5 + kernel-ridge OOF residual | **24.377** | 32.97 | 17.04 | 24.39 | 28.00 | 28.08 | 26.16 | 30.05 | 1761 | -0.125 | [-0.287, +0.033] | no | -1.34 | 46 |
| A2 + attention (learned metric) | **24.394** | 33.09 | 17.13 | 24.44 | 28.07 | 27.93 | 26.18 | 30.82 | 1757 | -0.143 | [-0.308, +0.010] | no | -1.94 | 53 |
| A3 + learned-metric retrieval | **24.493** | 33.22 | 17.12 | 24.70 | 28.11 | 28.04 | 26.13 | 30.01 | 1772 | -0.241 | [-0.439, -0.061] | **yes** | -2.27 | 73 |
| A7 hybrid: retrieval + OOF residual | **24.530** | 33.15 | 17.04 | 24.80 | 28.15 | 28.13 | 26.34 | 30.02 | 1779 | -0.278 | [-0.472, -0.081] | **yes** | -2.14 | 48 |
| C1 control: shuffled neighbours | **24.586** | 33.61 | 16.70 | 24.69 | 28.68 | 28.28 | 26.55 | 30.70 | 1779 | -0.335 | [-0.730, +0.058] | no | -5.00 | 17 |
| A6 + GP OOF residual | **24.918** | 33.55 | 17.37 | 24.89 | 28.78 | 28.63 | 26.29 | 30.15 | 1776 | -0.666 | [-1.040, -0.281] | **yes** | -3.40 | 45 |
| A1 + DeepSets (fixed phi) | **25.567** | 34.66 | 17.06 | 26.13 | 29.85 | 29.22 | 27.34 | 29.85 | 1917 | -1.315 | [-1.886, -0.771] | **yes** | -7.18 | 19 |

### Few-shot MAE by observed own-history depth

| candidate | none | 1-2 | 3-4 | 5-9 | 10plus |
|---|---|---|---|---|---|
| A0 CONTROL fs-1.3 | — | — | 18.91 | 29.07 | 23.40 |
| A1 + DeepSets (fixed phi) | — | — | 19.66 | 29.61 | 24.86 |
| A2 + attention (learned metric) | — | — | 18.20 | 29.57 | 23.48 |
| A3 + learned-metric retrieval | — | — | 18.91 | 29.80 | 23.55 |
| A4 + kernel retrieval (explicit) | — | — | 19.17 | 29.58 | 23.39 |
| A5 + kernel-ridge OOF residual | — | — | 18.55 | 28.83 | 23.60 |
| A6 + GP OOF residual | — | — | 18.55 | 28.27 | 24.35 |
| A7 hybrid: retrieval + OOF residual | — | — | 18.73 | 29.44 | 23.66 |
| C1 control: shuffled neighbours | — | — | 16.48 | 29.31 | 23.78 |
| C2 control: no personal context | — | — | 17.65 | 28.91 | 23.31 |

---

## 3 · Cold-start (entire participants held out)

| candidate | MAE | RMSE | @30 | @60 | @90 | @120 | peak | ttp | iAUC | Δ MAE | 95% CI | excl 0 | worst ppt | fit s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A5 + kernel-ridge OOF residual | **22.944** | 31.16 | 16.61 | 23.44 | 25.86 | 25.87 | 24.98 | 29.82 | 1635 | +0.141 | [-0.021, +0.311] | no | -1.22 | 71 |
| A7 hybrid: retrieval + OOF residual | **23.033** | 31.40 | 16.58 | 23.57 | 26.01 | 25.97 | 25.09 | 29.46 | 1647 | +0.052 | [-0.161, +0.267] | no | -2.36 | 75 |
| A0 CONTROL fs-1.3 | **23.085** | 31.44 | 16.54 | 23.55 | 26.22 | 26.02 | 25.32 | 29.08 | 1648 | — | — | — | — | 28 |
| A2 + attention (learned metric) | **23.093** | 31.57 | 16.48 | 23.64 | 26.28 | 25.97 | 25.30 | 29.13 | 1658 | -0.008 | [-0.201, +0.163] | no | -2.32 | 84 |
| A3 + learned-metric retrieval | **23.104** | 31.59 | 16.54 | 23.62 | 26.25 | 26.01 | 25.52 | 28.87 | 1652 | -0.019 | [-0.184, +0.138] | no | -1.94 | 136 |
| A4 + kernel retrieval (explicit) | **23.177** | 31.65 | 16.49 | 23.66 | 26.37 | 26.18 | 25.40 | 28.87 | 1658 | -0.092 | [-0.233, +0.039] | no | -1.98 | 25 |
| A6 + GP OOF residual | **23.605** | 31.78 | 17.20 | 24.14 | 26.67 | 26.41 | 24.48 | 31.27 | 1661 | -0.520 | [-1.024, -0.067] | **yes** | -7.04 | 72 |
| C1 control: shuffled neighbours | **24.059** | 32.59 | 17.52 | 25.23 | 27.13 | 26.36 | 26.29 | 30.47 | 1756 | -0.974 | [-1.467, -0.505] | **yes** | -5.88 | 26 |
| C2 control: no personal context | **25.401** | 34.15 | 18.05 | 27.70 | 28.70 | 27.15 | 27.37 | 30.88 | 1902 | -2.316 | [-3.379, -1.374] | **yes** | -13.72 | 23 |
| A1 + DeepSets (fixed phi) | **26.662** | 65.04 | 18.23 | 28.12 | 30.46 | 29.84 | 27.59 | 29.73 | 1896 | -3.577 | [-7.333, -1.165] | **yes** | -112.98 | 28 |

### Few-shot MAE by observed own-history depth

| candidate | none | 1-2 | 3-4 | 5-9 | 10plus |
|---|---|---|---|---|---|
| A0 CONTROL fs-1.3 | 23.10 | 27.43 | 29.68 | 23.31 | 21.58 |
| A1 + DeepSets (fixed phi) | 23.77 | 53.22 | 30.90 | 25.95 | 23.03 |
| A2 + attention (learned metric) | 23.24 | 27.86 | 30.12 | 23.25 | 21.49 |
| A3 + learned-metric retrieval | 23.30 | 27.66 | 29.97 | 23.23 | 21.56 |
| A4 + kernel retrieval (explicit) | 23.20 | 27.42 | 29.78 | 23.35 | 21.70 |
| A5 + kernel-ridge OOF residual | 23.29 | 27.24 | 29.09 | 23.03 | 21.52 |
| A6 + GP OOF residual | 24.14 | 27.79 | 28.76 | 23.44 | 22.39 |
| A7 hybrid: retrieval + OOF residual | 23.40 | 27.23 | 29.44 | 23.02 | 21.62 |
| C1 control: shuffled neighbours | 23.67 | 27.02 | 28.86 | 24.56 | 22.91 |
| C2 control: no personal context | 24.32 | 26.63 | 28.40 | 25.61 | 24.85 |

---

## 4 · What the controls established

These are the findings worth keeping from Batch A.

**C2 — no personal context** (drop both `q_retr_*` and `r_hist_*`):

| regime | Δ vs fs-1.3 | 95% CI | verdict |
|---|---|---|---|
| within-person | **+0.107** | [−0.439, +0.616] | removing personal context does **not** hurt |
| cold-start | **−2.316** | [−3.379, −1.374] | removing it costs **2.3 mg/dL** |

This is the cleanest statement yet of where fs-1.3's personalization earns its keep.
Within-person it is redundant, because the fitted per-participant calibration offset
already captures per-person level. Cold-start — where no fitted offset exists — it is
worth 2.3 mg/dL. The feature groups are not decoration.

**C1 — shuffled neighbours** (retrieval recomputed from *another* participant's history):

| regime | Δ vs fs-1.3 | 95% CI | verdict |
|---|---|---|---|
| within-person | −0.335 | [−0.730, +0.058] | spans zero (features redundant here anyway) |
| cold-start | **−0.974** | [−1.467, −0.505] | **CI excludes zero** |

Swapping in someone else's neighbours costs ~1 mg/dL cold-start. So retrieval carries
genuinely **participant-specific** signal, not cohort-level regularity. This is the
positive control the gate required, and it passes.

---

## 5 · Failure modes

**A1 DeepSets — catastrophic instability on unseen participants.** Cold-start RMSE
**65.04** against the control's 31.44, worst-participant deterioration **−112.98 mg/dL
MAE**, and few-shot MAE of **53.22** at 1–2 prior meals. A 2,196-parameter MLP fitted on
~900 episodes produced wildly out-of-range predictions for participants it had never
seen. This is the clearest demonstration in the project that parameter count, not
architecture family, is the binding constraint at this sample size — and it is exactly
why I have repeatedly declined TCN/GRU/neural-CDE.

**A6 GP residual — significantly worse in both regimes** (−0.666 within-person,
−0.520 cold-start, both CIs excluding zero) and the worst peak-timing of any candidate
(31.27 min cold-start).

**A6 GP — resource failure, reported not hidden.** With marginal-likelihood kernel
optimisation enabled, this candidate alone ran past 8.5 minutes without completing a
single regime, and the run was killed. Exact GP inference is O(n³) per optimiser
iteration, times four outputs, inside an inner CV, inside the outer folds. It was rerun
with **fixed kernel hyperparameters and training capped at 400 rows**. That is still a
GP residual learner, but an unbounded one was not affordable here and the result should
be read with that constraint in mind. My initial resource estimate for this candidate
was simply wrong.

**Learned metrics did not transfer.** A2 (8 parameters) and A3 (7 parameters) both fit
their metric by minimising *training-fold* retrieval MAE and both came out statistically
indistinguishable from the unlearned kernel in cold-start (−0.008 and −0.019). A3 was
significantly *worse* within-person. Seven to eight learned parameters is already too
many to fit reliably here.

---

## 6 · Parameter counts, runtime, latency

| candidate | parameters | fit time (all folds) |
|---|---|---|
| A0 CONTROL fs-1.3 | 0 | 44 s |
| A1 + DeepSets (fixed phi) | 2196 | 47 s |
| A2 + attention (learned metric) | 8 | 136 s |
| A3 + learned-metric retrieval | 7 | 209 s |
| A4 + kernel retrieval (explicit) | 0 | 41 s |
| A5 + kernel-ridge OOF residual | n_train (kernel) | 117 s |
| A6 + GP OOF residual | <=400 (kernel, fixed hyperparams) | 116 s |
| A7 hybrid: retrieval + OOF residual | n_train (kernel) | 122 s |
| C1 control: shuffled neighbours | 0 | 42 s |
| C2 control: no personal context | 0 | 40 s |

Inference latency is unchanged for every candidate except A1/A2/A3 (which add an encoder
pass, microseconds) and A5/A6/A7 (which add a kernel evaluation against the residual
training set, ~1 ms). None was promoted, so production latency remains 359 ms median.

---

## 7 · Hyperparameter search recorded

| candidate | searched |
|---|---|
| A1 DeepSets | one architecture (32, 16), alpha=1.0, early stopping — no sweep |
| A2 attention | 8 parameters by L-BFGS-B, bounds 0.2x–5x metric, 0.1–10 temperature, maxiter 40 |
| A3 learned metric | 7 parameters by L-BFGS-B, same bounds, maxiter 40 |
| A5 kernel ridge | fixed alpha=10, RBF gamma=1/d — no sweep |
| A6 GP | fixed kernel (see §5), training capped at 400 rows |
| A7 hybrid | no additional search |

10 candidates × 2 regimes = 20 scored runs. No unbounded search. Zero candidate
exceptions (`failures_*.json` are both empty); the one failure was the resource overrun
in §5.

---

## 8 · Conclusion and recommendation

**Retain fs-1.3.** Batch A produced no promotable model, which is itself a useful
result: the existing retrieval + fingerprint design is not leaving obvious few-shot
headroom on the table, and the controls prove it is doing real participant-specific
work rather than decoration.

**Recommendation on Batch B:** the A1 result is direct evidence against it. A
2,196-parameter MLP over pooled set statistics destabilised badly on unseen participants
at this sample size; a TCN, GRU, or masked-CGM encoder has far more parameters and a
harder objective, on the same ~900 training episodes. I would run Batch B only to
document the negative result, not expecting a promotion — and I would rather spend the
remaining time on submission-critical work. That is a recommendation, not a refusal: say
the word and I will run it.
