# Comprehensive Advanced Accuracy Research — Final Report

**Owner:** Ruhaan Shah · **Branch:** `ruhaan` · **From:** `bf87f19`
**Production model:** **fs-1.3, UNCHANGED** · **Contract:** v1.0, unchanged
**Environment:** 8 CPUs, 8 GB RAM, **no CUDA GPU** · CPU PyTorch 2.2.2, torchcde 0.2.5, torchdiffeq

## Bottom line

**No candidate from any of the eight batches is recommended for production promotion on
glucose accuracy.** fs-1.3 remains the best model on both development-validation
regimes. Two trajectory-reconstruction candidates are Pareto-optimal on *curve quality*
and are offered as optional, independent of glucose MAE.

`ml/`, `backend/` and `contracts/` are byte-identical to the start of this mission
(`git diff --stat bf87f19 HEAD -- ml/ backend/ contracts/` is empty). All four model
artifacts verify against their manifests. 646 tests pass.

---

## 1 · Research summary

| | |
|---|---|
| Model families evaluated | **8 batches / 14 families** — shape-constrained trajectories, causal CNN, dilated TCN, GRU, GRU-D, LSTM, Neural CDE, Neural ODE, self-supervised pretraining (forecast + masked), MetaboNet transfer, mixture-of-experts, learned gating, ARX/NARX, Kalman state |
| Distinct configurations tested | **~95** across this mission, on top of ~50 in earlier missions |
| Validation runs | 7 locked folds (3 within-person rolling purged + 4 participant-held-out), each candidate on identical episodes |
| Participants / episodes | 45 participants, 1,171 development episodes (394 reference episodes never loaded) |
| Compute | ~95 minutes wall clock, CPU only, peak RAM well under 8 GB |
| Candidates promoted | **0** |

### Multiplicity, stated plainly

Across this and prior missions roughly 145 configurations have been scored on the same
seven folds. Exactly one candidate produced a confidence interval excluding zero in a
favourable direction: **I3 retrieval-aware gating, +0.033 mg/dL cold-start
[+0.002, +0.072]**. At 0.033 mg/dL against a 5.5 mg/dL fold spread, after ~145 looks,
that is an exploratory interval and nothing more. I am not reporting it as a result.

---

## 2 · Complete leaderboard

### Batch C — shape-constrained trajectory reconstruction (cold-start)

| candidate | horizon MAE | traj MAE | peak | ttp | iAUC | >3 turns | overshoot |
|---|---|---|---|---|---|---|---|
| BASE linear (fs-1.3) | 23.076 | 21.112 | 25.38 | 29.00 | 1647.2 | 0.0% | 0.00 |
| C1 PCHIP | 23.076 | 21.175 | 25.38 | 29.00 | 1652.8 | 0.0% | 0.00 |
| C2 curvature spline | 23.076 | 21.106 | 25.14 | 28.14 | 1651.2 | 0.0% | 0.70 |
| C3 unimodal surge | 23.216 | 21.254 | 25.76 | 29.02 | 1660.3 | 0.0% | 0.78 |
| C4 gated unimodal/flexible | 23.192 | 21.149 | 25.47 | 28.52 | 1654.9 | 0.0% | 0.68 |
| C5 learned anchored correction | 23.076 | 20.794 | 25.01 | 27.82 | 1646.4 | 55.9% | 2.05 |
| C6 direct peak-time + unimodal | 23.759 | 21.785 | 26.30 | 26.78 | 1663.0 | 0.0% | 0.44 |

**C2 curvature-penalised spline** and **C6 direct peak-time + unimodal** are the two
Pareto-optimal candidates. C2 preserves horizons to 4.8e-14 (so glucose MAE is identical
by construction) while improving ttp 29.00 → 28.14 and peak 25.38 → 25.14, with **0%**
of curves exceeding three turning points. C6 gives the best timing measured anywhere in
this project, **26.78 min**, perfectly unimodal, at a cost of +0.68 horizon MAE.

**C5 learned anchored correction rejected**: best trajectory/peak/iAUC of any candidate
but **55.9%** of curves exceed three direction changes, versus the base model's 0%.

### Batch B1 — non-neural sequence representations (from the prior checkpoint)

| representation | cold-start Δ | 95% CI |
|---|---|---|
| + B1a raw lags | -0.134 | [-0.389, +0.089] |
| + B1b sequence PCA-6 | -0.179 | [-0.325, -0.050] |
| + B1c spectral-6 | -0.207 | [-0.314, -0.106] |
| + B1d sequence kernel-12 | -0.163 | [-0.318, -0.029] |
| + B1e shape descriptors | -0.103 | [-0.226, +0.013] |

### Batches D + E — learned temporal representations

**Within-person** (3 rolling folds):

| candidate | MAE | RMSE | bias | Δ vs fs-1.3 | worst ppt | params |
|---|---|---|---|---|---|---|
| fs-1.3 blend (reference) | 24.173 | 32.44 | -0.15 | — | — | — |
| CTRL static-only MLP | 24.012 | 32.89 | -3.49 | +0.161 | -14.12 | 1444 |
| CTRL shuffled sequence | 24.880 | 34.04 | -2.41 | -0.707 | -11.29 | 6420 |
| D1 CausalCNN-32 | 24.447 | 33.31 | -2.69 | -0.274 | -13.28 | 9124 |
| D2 TCN-16 rf63 | 24.959 | 34.29 | -3.46 | -0.785 | -9.21 | 6420 |
| D4 TCN repr -> fs-1.3 | 24.305 | 32.76 | -0.05 | -0.131 | -5.56 | 6420 |
| E1 GRU-16 | 24.186 | 33.05 | -3.20 | -0.013 | -14.45 | 2932 |
| E3 GRU-D-16 | 24.105 | 32.85 | -3.42 | +0.068 | -10.60 | 2918 |
| E6 LSTM-16 | 25.510 | 35.00 | -3.95 | -1.336 | -16.43 | 3268 |

**Cold-start** (4 participant-held-out folds):

| candidate | MAE | RMSE | bias | Δ vs fs-1.3 | worst ppt | params |
|---|---|---|---|---|---|---|
| fs-1.3 blend (reference) | 23.076 | 31.25 | -0.22 | — | — | — |
| CTRL static-only MLP | 24.004 | 33.26 | -5.20 | -0.928 | -7.69 | 1444 |
| CTRL shuffled sequence | 23.928 | 33.20 | -5.05 | -0.852 | -6.13 | 6420 |
| D1 CausalCNN-32 | 23.702 | 32.95 | -4.48 | -0.626 | -6.03 | 9124 |
| D2 TCN-16 rf63 | 23.721 | 32.94 | -4.33 | -0.645 | -6.37 | 6420 |
| D4 TCN repr -> fs-1.3 | 23.196 | 31.47 | -0.35 | -0.120 | -2.46 | 6420 |
| E1 GRU-16 | 23.869 | 33.09 | -4.51 | -0.793 | -6.54 | 2932 |
| E3 GRU-D-16 | 23.800 | 33.15 | -4.83 | -0.725 | -5.98 | 2918 |
| E6 LSTM-16 | 23.715 | 32.77 | -4.25 | -0.639 | -5.81 | 3268 |

### Batch F — Neural CDE / Neural ODE (cold-start, 4 folds)

| candidate | MAE | Δ vs fs-1.3 | bias | params |
|---|---|---|---|---|
| fs-1.3 blend (reference) | 23.076 | — | — | — |
| CTRL static-only (no path) | 24.011 | -0.936 | -4.13 | 1444 |
| F1 CDE-8 linear | 23.843 | -0.768 | -4.54 | 3052 |
| F2 CDE-8 rectilinear | 23.961 | -0.885 | -5.01 | 3052 |
| F6 ODE-8 (control) | 24.076 | -1.001 | -4.61 | 2292 |
| F5 CDE-8 keep 25% | 24.083 | -1.007 | -4.87 | 3052 |

### Batches G + H — self-supervised pretraining and MetaboNet transfer (cold-start)

| configuration | MAE | Δ vs fs-1.3 | Δ vs RANDOM encoder |
|---|---|---|---|
| fs-1.3 blend (reference) | 23.076 | — | — |
| H2 MetaboNet forecast | 23.090 | -0.014 | +0.044 |
| H4 combined forecast | 23.125 | -0.049 | +0.009 |
| G6a CTRL random encoder | 23.134 | -0.058 | +0.000 |
| G6b CTRL shuffled objective | 23.158 | -0.082 | -0.023 |
| G2 CGMacros masked | 23.160 | -0.084 | -0.026 |
| G1 CGMacros forecast | 23.167 | -0.091 | -0.033 |
| G5 CGMacros forecast + fine-tune | 23.179 | -0.103 | -0.044 |

### Batches I + J — gating and classical dynamics

Note: J1/J2/J4 need the 37 pre-meal lag columns, so every candidate in this run — the
I0 control included — sits on a 76-column base (fs-1.3's 39 + 37 lags). Deltas are valid
paired comparisons against that control; absolute MAEs are not comparable to fs-1.3's.

| candidate | within-person Δ | cold-start Δ | cold-start 95% CI |
|---|---|---|---|
| I1 linear soft gate | +0.086 | +0.012 | [-0.024, +0.052] |
| I3 retrieval-aware gate | -0.002 | +0.033 | [+0.002, +0.072] |
| I6a tree gate | +0.087 | +0.018 | [-0.032, +0.071] |
| I6b hard threshold gate | -0.007 | +0.010 | [-0.089, +0.111] |
| I2 calibration gate | -0.000 | +0.000 | [+0.000, +0.000] |
| I5 2-expert regime | -0.771 | -0.625 | [-0.974, -0.305] |
| I6 3-expert regime | -0.448 | -0.952 | [-1.357, -0.552] |
| J1 regularized ARX | -6.461 | -6.547 | [-8.270, -5.078] |
| J2 NARX (boosted) | -4.308 | -3.570 | [-4.823, -2.451] |
| J4 Kalman state -> fs-1.3 | -0.003 | -0.027 | [-0.078, +0.027] |

---

## 3 · The ten questions, answered

**1. Do CNNs learn useful information beyond handcrafted glucose features?**
**No.** The best CNN is −0.274 within-person and −0.626 cold-start. The decisive evidence
is the controls: within-person, a **static-only MLP with no sequence input at all is the
best neural model (+0.161)**, and feeding the TCN a **shuffled** sequence (−0.707) does
*better* than the real one (−0.785). The single-fold screen suggested every architecture
beat fs-1.3; the controls showed that gain was the MLP head over static features.

**2. Does a GRU improve glucose-state modelling?** **No.** GRU-16 is −0.013
within-person and −0.793 cold-start. The matched LSTM control (−1.336 / −0.639) confirms
this is not GRU-specific but general to recurrent modelling at this sample size.

**3. Does missingness-aware recurrence help?** **Marginally and inconsistently.** GRU-D
is the only recurrent model above the reference within-person (+0.068) but −0.725
cold-start. With **99.6% of native gaps at exactly 5 minutes** and 0.10% over 15 minutes,
there is almost no irregularity for a decay mechanism to exploit.

**4. Does a Neural CDE benefit irregular CGM observations?** **The mechanism works; the
dataset does not reward it.** The CDE beats both its controls — the static-only MLP
(+0.17) and a Neural ODE evolving freely from a path summary (+0.23) — and it *degrades*
when the path is thinned to 25% of observations. So it genuinely uses the path. But it is
still 0.77 behind fs-1.3. Notably, the single-fold screen said thinning to 25% was
**best** (+0.506); across four folds it was **worst** (−1.007). Multi-fold evaluation
inverted that conclusion.

**5. Does self-supervised pretraining improve downstream forecasting?** **No, and the
controls are unambiguous.** A **random untrained encoder (23.134) beats every
CGMacros-pretrained encoder** (forecast 23.167, masked 23.160, fine-tuned 23.179), and
the **shuffled-objective control (23.158) matches the real forecast objective (23.167)**.
When random ≈ shuffled ≈ trained, the representation is contributing nothing regardless
of how it was obtained. Fine-tuning was the worst variant, consistent with overfitting.

**6. Can MetaboNet representations transfer to CGMacros?** **No meaningfully.** MetaboNet
pretraining is nominally the best of the pretraining group (23.090, +0.044 over random)
but that is well inside noise. More fundamentally: MetaboNet has **carbohydrates only —
no protein, fat, fibre or calories in any of its 15 studies** — so meal-response labels
cannot be pooled at all, only a CGM encoder can transfer, and the encoder is exactly what
question 5 shows to be inert. Units (mg/dL) and cadence (99.9% 5-min) are compatible;
the populations are not (type 1 diabetes vs healthy/prediabetes/T2D).

**7. Can learned mixture-of-experts gating outperform fixed blending?** **No.** The best
soft gate is +0.087 within-person with a CI spanning zero. The audit explains why: learned
gate weights sit at **mean 0.483–0.496 with 0% extreme assignments**, i.e. they reproduce
the fixed 50/50 blend. Expert *specialization* is significantly **harmful** and worsens
with more experts (2-expert −0.625, 3-expert −0.952 cold-start) — data fragmentation at
~900 episodes, with worst-participant deterioration reaching −4.91.

**8. Can shape-constrained trajectories improve time-to-peak without damaging glucose
accuracy?** **Yes — this is the one affirmative answer in the mission.** C2 improves ttp
by 0.86 min and peak by 0.24 while preserving horizon predictions to 4.8e-14, with zero
oscillation. C6 improves ttp by 2.22 min, also with zero oscillation, but costs +0.68
horizon MAE. C1 PCHIP does nothing, exactly as predicted: it is monotone-preserving, so
it cannot create an off-knot peak.

**9. What additional information is still unavailable to the model?** Insulin time series
(CGMacros has one fasting value per participant), gastric-emptying or tracer measurements,
meal-photo-derived portion correction, medication exposure (CGMacros records **none**), a
true local timezone (timestamps are date-shifted offset-naive wall clock), and any
post-meal context such as activity or a second meal.

**10. Which weaknesses are data limitations rather than algorithm choice?** Essentially
all of them, and Batch J makes the case quantitatively. ARX and NARX — lagged glucose plus
the exogenous meal and nothing else — lose **4.3 to 6.5 mg/dL**. The neural models, which
were *also* given fs-1.3's engineered features, came within 0.1–1.3. So the signal lives
in the engineered features, and fourteen model families failed to extract more from the
same 45 participants and 1,171 episodes. The binding constraint is cohort size.

A second, convergent piece of evidence: **every** direct neural model shows large
systematic negative bias (−4.1 to −5.2 mg/dL) against fs-1.3's −0.22 — they
systematically under-predict excursions, the signature of regression to the mean under
insufficient data.

---

## 4 · Best-model recommendation

| category | winner |
|---|---|
| Best within-person | **fs-1.3** (24.173; no candidate beat it) |
| Best cold-start | **fs-1.3** (23.076; closest candidate D4 TCN-representation at −0.120) |
| Best peak timing | **C6 direct peak-time + unimodal** (26.78 min vs 29.00), at +0.68 horizon MAE |
| Best full-trajectory accuracy | **C5** (20.794) — but rejected, 55.9% oscillating curves |
| Best overall balanced | **fs-1.3**, optionally with the **C2** curvature-spline reconstruction |
| Deserving promotion | **None on glucose accuracy.** C2 is the only candidate I would offer, and only because it is free: horizons are preserved by construction, so it cannot degrade glucose MAE. |

---

## 5 · Failed and rejected hypotheses

| hypothesis | outcome |
|---|---|
| PCHIP smoothing improves trajectories | **Rejected** — identical peak/ttp to linear, marginally worse trajectory MAE |
| Unconstrained learned curve correction | **Rejected** — 55.9% of curves oscillate implausibly |
| Raw lags / PCA / spectral / sequence kernels | **Rejected** (prior checkpoint) — three significantly worse cold-start |
| Causal CNN, dilated TCN | **Rejected** — shuffled sequence does as well within-person |
| GRU, GRU-D, LSTM | **Rejected** — LSTM control shows it is not architecture-specific |
| Neural CDE, Neural ODE | **Rejected** — mechanism verified, magnitude insufficient |
| Self-supervised forecast / masked pretraining | **Rejected** — a random encoder beats it |
| MetaboNet transfer | **Rejected** — labels unpoolable, encoder inert |
| Mixture-of-experts (2 and 3 experts) | **Rejected** — significantly harmful, data fragmentation |
| Learned soft / tree / hard gating | **Rejected** — gates reproduce the fixed blend |
| ARX / NARX / Kalman state | **Rejected** — 4.3–6.5 mg/dL worse, or no effect |

---

## 6 · Reproduction

```bash
python -m research.batch_c.runner      --db-url sqlite:///data/local/cgmacros_v2.db
python -m research.neural.screen       --db-url sqlite:///data/local/cgmacros_v2.db
python -m research.neural.runner_de    --db-url sqlite:///data/local/cgmacros_v2.db
python -m research.batch_f.screen      --db-url sqlite:///data/local/cgmacros_v2.db
python -m research.batch_f.runner      --db-url sqlite:///data/local/cgmacros_v2.db
python -m research.batch_gh.runner     --db-url sqlite:///data/local/cgmacros_v2.db
python -m research.batch_ij.runner     --db-url sqlite:///data/local/cgmacros_v2.db
python -m pytest -q
```

Results land under `research/*/results/`.

**Determinism:** seeds fixed for torch and numpy. Full bitwise determinism is **not**
claimed — CPU BLAS reduction order varies between runs. Repeated runs agree to ~1e-3
validation MAE, far below every effect size reported.

**Numerical record:** zero non-finite predictions and zero divergences across every
neural and CDE configuration (`de_failures.json` is empty; all F configs completed 4/4
folds with zero non-finite batches). The only resource failure in the whole mission was a
GP residual learner in an earlier batch, documented there.

---

## 7 · What I would do with more time

Not another architecture. The convergent evidence from Batch J (classical temporal models
lose 4–6.5 mg/dL), Batches D/E/F (fourteen families within ~1 mg/dL but none ahead), and
Batches G/H (a random encoder beats pretraining) all point at cohort size. **More
participants** is the experiment that would move this number; a fifteenth model family
would not.
