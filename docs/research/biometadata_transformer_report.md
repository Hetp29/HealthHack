# Final Biological Metadata and Transformer Research — Report

**Owner:** Ruhaan Shah · **Branch:** `ruhaan` · **From:** `477334f`
**Production model:** **fs-1.3, UNCHANGED** · **Contract:** v1.0, unchanged
**Environment:** 8 CPUs, 8 GB RAM, no CUDA GPU · CPU PyTorch 2.2.2

## Bottom line

Both tracks are negative. **No candidate is recommended for promotion.**

Neither hypothesis survived. Previously underused biological measurements add
nothing to fs-1.3 that generalises to an unseen participant, and attention-based
Transformers do not beat fs-1.3 in either validation regime. The single candidate
with any inferential claim — a stacked microbiome-plus-cohort residual correction
at **+0.039 mg/dL cold-start** — is 0.17% of the reference error, is *negative*
within-person, and arrives after ~202 cumulative configurations on the same seven
folds. Under conservative promotion standards that is not a result.

The two most informative findings in the mission are both from negative controls:

* **Phase 3, within-person: the SHUFFLED microbiome control (+0.184) was the best
  model of all**, ahead of every genuine microbiome feature. A gain that gets
  *larger* when you destroy the biology is a participant-identity proxy.
* **Phase 4: the shuffled-objective pretraining control matched or beat all three
  genuine pretraining objectives, and random frozen embeddings beat real
  pretrained ones within-person.** This independently reproduces Batch G's
  conclusion using a completely different architecture.

---

## Deliverable 1 · Verified biological metadata dictionary

Verified against `DataDictionary.pdf` (87 pages, extracted and read) and against
the files themselves. Field meanings are not inferred from names.

### `bio.csv` — 45 participants × 24 columns, **no missing values**

| field | source column | unit (verified) | level | notes |
|---|---|---|---|---|
| m_age | Age | years | A | 18–69, median 51 |
| m_bmi | BMI | kg/m² | A | 20.69–49.09, median 30.04 |
| m_a1c | A1c PDL (Lab) | **% (NGSP)** | A | **unit corrected — see below** |
| m_fasting_glucose | Fasting GLU - PDL (Lab) | mg/dL | A | 79–218, median 109 |
| m_fasting_insulin | Insulin | mcU/mL | A | dictionary confirms mcU/mL; 2.5–46.4 |
| m_triglycerides | Triglycerides | mg/dL | A | 40–1150 (extreme upper outlier) |
| m_cholesterol / m_hdl | Cholesterol, HDL | mg/dL | A | — |
| m_ldl | LDL (Cal) | mg/dL | A | **calculated, not measured** |
| m_chol_hdl_ratio | Cho/HDL Ratio | ratio | A | — |
| m_homa_ir | *derived* | index | **B** | glucose×insulin/405. A **surrogate**, not a clamp-measured insulin sensitivity |
| m_cohort_ord | *derived* | ordinal 0/1/2 | **B** | ADA thresholds on a measured lab A1c |
| m_sex_female | Gender | binary | A | 29 F / 16 M |

**Measurement timing:** the dictionary states every bio field was "taken at start
of study", i.e. strictly before every prediction episode. No temporal leakage.
Participant-identity leakage is a different matter and is handled in Phase 2–5.

#### The HbA1c unit ambiguity is RESOLVED: the values are NGSP %, and the dictionary is wrong

The data dictionary says, in one sentence: *"Numeric: 4.6 - 8.5 … Range of hbA1c
reading taken at start of study **in mmol/mol**"*. The stated unit and the stated
range contradict each other, and the range is the reliable half:

* **4.6–8.5 mmol/mol is biologically impossible.** The non-diabetic reference
  range is ~20–42 mmol/mol, so all 45 participants would sit far *below* the
  healthy floor — in a cohort whose fasting lab glucose reaches 218 mg/dL.
* Read as **NGSP %** the values are coherent: Spearman **ρ = 0.875** against
  fasting lab glucose, and the ADA thresholds split the cohort
  **15 normal / 16 prediabetes / 14 diabetes**, matching the three-group
  recruitment CGMacros describes. 12 of the 16 participants with fasting glucose
  ≥126 mg/dL also have A1c ≥6.5%.
* The observed median of 5.9% converts to 41 mmol/mol — which lands exactly where
  a mmol/mol reading of "5.9" could never be.

Treated as % throughout, and asserted by `tests/test_biological_metadata.py`.

### `microbes.csv` — 45 participants × 1979 taxa, **BINARY presence/absence**

The dictionary is explicit and identical for every taxon column: *"Binary: 0 = No,
1 = Yes — Indicator variable of presence of bacteria in microbiome sample"*.
Verified: the set of distinct values across all 45 × 1979 cells is exactly {0, 1}.
Observed richness runs 84–537 taxa, median 180. 15 taxa are present in no
participant; none is present in all 45.

**This single fact determines what Phase 3 could legitimately do:**

| requested | status | reason |
|---|---|---|
| Richness | **done** | well defined on incidence data |
| Shannon / Simpson / evenness | **DECLINED** | all are functions of abundance proportions. Incidence fixes every present taxon at the same value, so these collapse to a re-parameterisation of richness. Computing them would be log(richness) in a costume. |
| Relative abundance of groups | **substituted** | not available. Implemented honestly as a **prevalence fraction** and named `prev_`, never `abundance_` |
| CLR / ALR log-ratios | **DECLINED** | a 0/1 vector is not a composition: there is no total to close against, and every zero is structural rather than a rounded-down small abundance, so the usual pseudo-count repair repairs nothing |
| Jaccard kernel | **done** | the natural similarity for incidence vectors (Bray-Curtis and Aitchison are not applicable) |

### `gut_health_test.csv` — 47 rows × 22 scored columns, **ordinal**

Dictionary: *"Factor: 1 = Not Optimal, 2 = Average, 3 = Good"*, and for Active
Microbial Diversity, *"This score is your percentile for the total count of active
microbial species"*. These are **vendor percentile-derived ordinal scores, not
measurements in any physical unit**. Verified: distinct values are exactly
{1, 2, 3}.

---

## Deliverable 2 · Participant linkage and coverage report

| source | subjects | identical to the 45 CGM participant directories? |
|---|---|---|
| CGM participant directories | 45 | — |
| `bio.csv` | 45 | **yes** |
| `microbes.csv` | 45 | **yes** |
| `gut_health_test.csv` | **47** | **no — superset** |

Subject ids are **not** 1–45: 24, 25, 37 and 40 are absent and the ids run to 49.
`gut_health_test.csv` adds **subjects 24 and 25, which have no CGM data at all**;
both are dropped. The usable intersection is **45 participants**.

**Are metadata available for all 45 participants? Not quite — three gaps:**

| table | complete | missing |
|---|---|---|
| `bio.csv` | **45/45** on every field used | none |
| `microbes.csv` | 44/45 | **subject 48: all 1979 cells NaN** — no sample |
| `gut_health_test.csv` | 42/45 | **subjects 26, 28, 48: all 22 cells NaN** |

Subject 48's missing microbiome is preserved as **NaN, never imputed to zero**:
"no sample" and "no bacteria" are different claims, and conflating them would put
a fabricated richness of 0 into a model. The Jaccard matrix excludes that
participant (44 × 44) rather than giving them a similarity to anyone.

---

## Deliverable 3 · Metabolic feature leaderboard (Phase 2)

25 configurations × 2 regimes. Reference MAE: 23.548 within-person, 23.085
cold-start. Note these absolutes differ from earlier missions because
`rolling_folds(n=3)` yields 2 validation folds; all comparisons here are paired on
identical episodes, so the Δ column is the valid quantity.

### Within-person

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt |
|---|---|---|---|---|---|
| A fs-1.3 (reference) | 23.548 | **ref** | — | — | — |
| C group:core4 | 23.363 | +0.185 | [+0.010, +0.365] | **YES** | -1.28 |
| B m_fasting_glucose | 23.372 | +0.175 | [+0.015, +0.356] | **YES** | -1.29 |
| B m_a1c | 23.385 | +0.163 | [-0.010, +0.344] | no | -1.40 |
| B m_cohort_ord | 23.395 | +0.153 | [+0.049, +0.272] | **YES** | -0.68 |
| C group:glycemic | 23.404 | +0.144 | [-0.044, +0.346] | no | -1.31 |
| C group:all13 | 23.427 | +0.121 | [-0.150, +0.388] | no | -3.49 |
| B m_cholesterol | 23.444 | +0.104 | [-0.031, +0.242] | no | -1.50 |
| B m_chol_hdl_ratio | 23.475 | +0.072 | [-0.185, +0.301] | no | -2.21 |
| C group:lipid | 23.477 | +0.071 | [-0.168, +0.289] | no | -2.26 |
| B m_ldl | 23.483 | +0.065 | [-0.110, +0.225] | no | -1.56 |
| NC shuffled metadata:all13 | 23.489 | +0.059 | [-0.188, +0.294] | no | -1.79 |
| E cohort interaction (low capacity) | 23.494 | +0.054 | [-0.182, +0.284] | no | -3.47 |
| B m_homa_ir | 23.501 | +0.047 | [-0.067, +0.161] | no | -1.20 |
| B m_age | 23.505 | +0.043 | [-0.081, +0.177] | no | -1.05 |
| C group:anthropometric | 23.524 | +0.024 | [-0.107, +0.160] | no | -2.12 |
| B m_bmi | 23.541 | +0.006 | [-0.080, +0.093] | no | -0.78 |
| C group:insulin_axis | 23.548 | -0.001 | [-0.094, +0.095] | no | -0.97 |
| F hierarchical cohort shrinkage (k=3) | 23.579 | -0.031 | [-0.066, +0.002] | no | -0.37 |
| B m_sex_female | 23.580 | -0.032 | [-0.070, +0.005] | no | -0.34 |
| B m_hdl | 23.588 | -0.040 | [-0.118, +0.035] | no | -1.09 |
| D regularised linear correction (alpha=1000) | 23.600 | -0.052 | [-0.085, -0.022] | **YES** | -0.50 |
| B m_fasting_insulin | 23.602 | -0.054 | [-0.147, +0.044] | no | -1.03 |
| B m_triglycerides | 23.609 | -0.061 | [-0.263, +0.128] | no | -2.54 |
| NC shuffled metadata:core4 | 23.649 | -0.101 | [-0.241, +0.037] | no | -1.48 |

### Cold-start — **the regime that decides**

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt |
|---|---|---|---|---|---|
| A fs-1.3 (reference) | 23.085 | **ref** | — | — | — |
| B m_fasting_glucose | 23.056 | +0.029 | [-0.175, +0.208] | no | -2.16 |
| F hierarchical cohort shrinkage (k=3) | 23.075 | +0.010 | [-0.004, +0.024] | no | -0.12 |
| D regularised linear correction (alpha=1000) | 23.096 | -0.011 | [-0.067, +0.024] | no | -1.23 |
| B m_fasting_insulin | 23.097 | -0.012 | [-0.150, +0.115] | no | -2.06 |
| B m_a1c | 23.106 | -0.021 | [-0.293, +0.207] | no | -3.70 |
| C group:insulin_axis | 23.130 | -0.045 | [-0.222, +0.109] | no | -2.08 |
| B m_sex_female | 23.141 | -0.056 | [-0.134, +0.015] | no | -0.68 |
| B m_hdl | 23.152 | -0.067 | [-0.156, +0.011] | no | -0.91 |
| C group:glycemic | 23.162 | -0.077 | [-0.400, +0.198] | no | -4.59 |
| B m_homa_ir | 23.165 | -0.080 | [-0.252, +0.059] | no | -2.25 |
| C group:core4 | 23.180 | -0.095 | [-0.384, +0.153] | no | -3.38 |
| B m_bmi | 23.181 | -0.096 | [-0.202, +0.008] | no | -0.97 |
| B m_cohort_ord | 23.189 | -0.104 | [-0.305, +0.061] | no | -2.67 |
| B m_cholesterol | 23.201 | -0.116 | [-0.331, +0.062] | no | -4.07 |
| B m_age | 23.204 | -0.119 | [-0.228, -0.024] | **YES** | -1.35 |
| B m_triglycerides | 23.238 | -0.153 | [-0.307, -0.036] | **YES** | -2.40 |
| C group:anthropometric | 23.240 | -0.155 | [-0.308, -0.019] | **YES** | -1.47 |
| E cohort interaction (low capacity) | 23.246 | -0.161 | [-0.435, +0.079] | no | -2.89 |
| B m_ldl | 23.436 | -0.351 | [-0.807, -0.033] | **YES** | -9.15 |
| NC shuffled metadata:core4 | 23.914 | -0.829 | [-1.325, -0.473] | **YES** | -7.40 |
| NC shuffled metadata:all13 | 24.415 | -1.329 | [-2.285, -0.634] | **YES** | -19.67 |
| C group:all13 | 25.835 | -2.750 | [-8.114, +0.012] | no | -127.46 |
| B m_chol_hdl_ratio | 28.467 | -5.382 | [-16.791, -0.051] | **YES** | -277.20 |
| C group:lipid | 33.872 | -10.787 | [-33.767, -0.079] | **YES** | -561.12 |

**Read this pair of tables together.** Within-person, three configurations show a
favourable interval excluding zero (fasting glucose +0.175, ADA cohort +0.153,
core4 +0.185). Cold-start, **not one configuration is favourable**, eight are
significantly worse, and the lipid panel is a catastrophe: `C group:lipid` loses
**10.787 mg/dL** with **−561 mg/dL** on its worst participant, and
`B m_chol_hdl_ratio` loses **5.382** with **−277** on its worst.

The negative controls explain the mechanism. `NC shuffled metadata` — where the
metadata→participant mapping is destroyed among training participants only —
loses **0.829** (core4) and **1.329** (all 13) cold-start, *significantly*. The
model is keying on a constant-per-participant column as an identity label, which
is free within-person and actively misleading on someone new.

This does not reproduce the earlier `+ profile` experiment, it supersedes it. That
run (physiology baseline, no personalisation) gave +1.392 [1.059, 1.778]
within-person and +0.767 [−0.302, 1.762] cold-start. Against fs-1.3 — which
already carries the participant's own measured response history through
`r_hist_*` and `q_retr_*` — the within-person gain shrinks to ~0.18 and the
cold-start gain disappears entirely. **A static lab panel adds nothing once the
model can already see how that person actually responded to food.**

---

## Deliverable 4 · Microbiome feature leaderboard (Phase 3)

15 configurations × 2 regimes.

### Within-person — where the control wins

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt |
|---|---|---|---|---|---|
| A fs-1.3 (reference) | 23.548 | **ref** | — | — | — |
| NC shuffled microbiome SVD4 | 23.363 | +0.184 | [-0.012, +0.367] | no | -2.15 |
| M9 prevalence, functional groups | 23.402 | +0.145 | [-0.037, +0.340] | no | -1.31 |
| M2 prevalence, 12 commonest genera | 23.410 | +0.138 | [-0.151, +0.402] | no | -2.73 |
| M1 richness | 23.475 | +0.073 | [-0.058, +0.227] | no | -0.89 |
| NC shuffled gut-health | 23.499 | +0.049 | [-0.243, +0.311] | no | -3.01 |
| M10 gut-health ordinal scores | 23.499 | +0.048 | [-0.202, +0.278] | no | -2.23 |
| M4 microbiome SVD4 | 23.502 | +0.046 | [-0.094, +0.185] | no | -0.81 |
| M8 jaccard fingerprint (top 10) | 23.519 | +0.029 | [-0.127, +0.203] | no | -1.13 |
| M4 microbiome SVD2 | 23.535 | +0.013 | [-0.139, +0.151] | no | -1.23 |
| NC shuffled jaccard neighbour | 23.540 | +0.008 | [-0.134, +0.147] | no | -1.50 |
| M7 microbiome residual correction | 23.561 | -0.014 | [-0.077, +0.041] | no | -0.63 |
| M5 selected taxa (k=10) | 23.567 | -0.019 | [-0.196, +0.169] | no | -1.54 |
| M6 jaccard neighbour response | 23.571 | -0.023 | [-0.177, +0.149] | no | -1.25 |
| M4 microbiome SVD8 | 23.635 | -0.087 | [-0.281, +0.097] | no | -2.12 |

**The top line is the finding.** `NC shuffled microbiome SVD4` (+0.184) is the
**best model in the table**, ahead of every genuine microbiome feature: M9
functional-group prevalence (+0.145), M2 commonest genera (+0.138), M1 richness
(+0.073), M4 SVD4 (+0.046). `NC shuffled gut-health` (+0.049) and the real
`M10 gut-health` (+0.048) are indistinguishable. Nothing excludes zero.

A representation whose apparent value *rises* when the biology is scrambled is not
carrying biology. Off-diagonal Jaccard similarity averages 0.201, i.e. microbiomes
are nearly unique per person — so these columns are an excellent participant
fingerprint and a poor physiological signal.

### Cold-start

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt |
|---|---|---|---|---|---|
| A fs-1.3 (reference) | 23.085 | **ref** | — | — | — |
| M9 prevalence, functional groups | 22.995 | +0.090 | [-0.187, +0.348] | no | -2.26 |
| M1 richness | 23.019 | +0.066 | [-0.198, +0.296] | no | -2.71 |
| M7 microbiome residual correction | 23.056 | +0.029 | [+0.009, +0.048] | **YES** | -0.13 |
| M4 microbiome SVD2 | 23.102 | -0.017 | [-0.304, +0.244] | no | -2.68 |
| M6 jaccard neighbour response | 23.113 | -0.028 | [-0.545, +0.366] | no | -7.23 |
| M4 microbiome SVD4 | 23.132 | -0.047 | [-0.380, +0.249] | no | -3.47 |
| M8 jaccard fingerprint (top 10) | 23.152 | -0.067 | [-0.477, +0.297] | no | -4.08 |
| M4 microbiome SVD8 | 23.304 | -0.219 | [-0.553, +0.067] | no | -3.62 |
| NC shuffled jaccard neighbour | 23.308 | -0.223 | [-0.558, +0.057] | no | -4.00 |
| NC shuffled microbiome SVD4 | 23.413 | -0.328 | [-0.523, -0.150] | **YES** | -3.65 |
| NC shuffled gut-health | 23.440 | -0.355 | [-0.672, -0.052] | **YES** | -3.31 |
| M2 prevalence, 12 commonest genera | 23.727 | -0.643 | [-1.159, -0.175] | **YES** | -6.81 |
| M5 selected taxa (k=10) | 23.736 | -0.652 | [-1.184, -0.155] | **YES** | -7.35 |
| M10 gut-health ordinal scores | 23.797 | -0.712 | [-1.463, -0.183] | **YES** | -11.62 |

Cold-start, five configurations are significantly worse. `M10 gut-health`
(**−0.712**) is worse than its own shuffled control (−0.355): the real vendor
scores hurt *more* than scrambled ones. M2 (−0.643) and M5 selected taxa (−0.652)
also fail significantly — 1979 columns and ~44 training participants is not a
selection problem that 10 picks can solve.

**M7 microbiome residual correction** is the one candidate with a favourable
interval excluding zero: **+0.029 [+0.009, +0.048]**, worst-participant −0.13. It
is 0.13% of the reference error. See the multiplicity audit.

The mechanism most likely to have worked — M6/M8 Jaccard neighbour transfer, which
indexes *other* participants' observed responses by microbiome similarity and is
the only route available to a cold-start participant — did not: −0.028 and −0.067,
with worst-participant deterioration of −7.23 and −4.08.

---

## Deliverable 5 · Transformer architecture leaderboard (Phase 4)

12 configurations × 2 regimes. All encoders 10k–22k parameters against ~900
training episodes and ~3,400 target values.

### Within-person

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt | bias | params |
|---|---|---|---|---|---|---|---|
| A fs-1.3 (reference) | 23.548 | **ref** | — | — | — | -1.19 | — |
| CTRL shuffled-pretrained frozen + fs-1.3 | 23.669 | -0.121 | [-0.403, +0.156] | no | -3.52 | -1.20 | 17248 |
| CTRL random frozen embeddings + fs-1.3 | 23.724 | -0.176 | [-0.505, +0.140] | no | -4.17 | -0.97 | 17248 |
| T7 frozen pretrained embeddings + fs-1.3 | 23.767 | -0.219 | [-0.538, +0.072] | no | -3.61 | -1.00 | 17248 |
| CTRL shuffled-objective pretrained | 24.003 | -0.456 | [-1.185, +0.212] | no | -7.87 | -6.21 | 19684 |
| T3 meal-conditioned transformer | 24.040 | -0.492 | [-1.200, +0.206] | no | -5.00 | -4.09 | 22020 |
| T6 MetaboNet pretrained (transfer) | 24.238 | -0.690 | [-1.523, +0.077] | no | -9.49 | -7.63 | 19684 |
| T1 temporal transformer | 24.254 | -0.706 | [-1.432, -0.019] | **YES** | -8.32 | -5.24 | 19684 |
| T8 historical-meal cross-attention | 24.296 | -0.748 | [-1.483, -0.045] | **YES** | -7.98 | -5.46 | 10084 |
| T5 forecast pretrained | 24.310 | -0.763 | [-1.507, -0.071] | **YES** | -8.40 | -5.63 | 19684 |
| T2 patch transformer (PatchTST-inspired) | 24.477 | -0.929 | [-1.734, -0.214] | **YES** | -8.05 | -5.39 | 20324 |
| T4 masked-CGM pretrained | 24.483 | -0.935 | [-1.797, -0.149] | **YES** | -11.63 | -6.37 | 19684 |

### Cold-start

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt | bias | params |
|---|---|---|---|---|---|---|---|
| A fs-1.3 (reference) | 23.085 | **ref** | — | — | — | -0.17 | — |
| T7 frozen pretrained embeddings + fs-1.3 | 23.097 | -0.012 | [-0.204, +0.151] | no | -1.43 | -0.15 | 17248 |
| CTRL random frozen embeddings + fs-1.3 | 23.130 | -0.045 | [-0.223, +0.119] | no | -1.52 | -0.13 | 17248 |
| CTRL shuffled-pretrained frozen + fs-1.3 | 23.145 | -0.060 | [-0.268, +0.123] | no | -2.46 | -0.13 | 17248 |
| T2 patch transformer (PatchTST-inspired) | 23.710 | -0.625 | [-1.211, -0.068] | **YES** | -6.59 | -4.40 | 20324 |
| T8 historical-meal cross-attention | 23.724 | -0.639 | [-1.173, -0.119] | **YES** | -5.88 | -3.69 | 10084 |
| T4 masked-CGM pretrained | 23.742 | -0.657 | [-1.296, -0.035] | **YES** | -7.91 | -5.24 | 19684 |
| CTRL shuffled-objective pretrained | 23.754 | -0.669 | [-1.233, -0.139] | **YES** | -6.38 | -4.11 | 19684 |
| T6 MetaboNet pretrained (transfer) | 23.777 | -0.692 | [-1.262, -0.146] | **YES** | -6.99 | -4.86 | 19684 |
| T5 forecast pretrained | 23.820 | -0.735 | [-1.292, -0.198] | **YES** | -6.31 | -4.02 | 19684 |
| T1 temporal transformer | 23.824 | -0.739 | [-1.297, -0.209] | **YES** | -6.07 | -4.06 | 19684 |
| T3 meal-conditioned transformer | 24.043 | -0.958 | [-1.631, -0.319] | **YES** | -7.56 | -4.54 | 22020 |

**No Transformer beats fs-1.3 in either regime.** All eight direct configurations
are significantly worse cold-start (−0.625 to −0.958) and five are significantly
worse within-person. The three frozen-embedding variants are statistically
indistinguishable from fs-1.3 cold-start (−0.012 to −0.060) — they neither help
nor hurt.

Specific answers:

* **T2 PatchTST-inspired patching** did not rescue attention: −0.929 within-person,
  −0.625 cold-start. Patching shortens the sequence but does not create signal.
* **T8 historical-meal cross-attention** is the most interesting failure. 96.1% of
  episodes had at least one admissible prior meal and a mean of 6.57 of 8 memory
  slots filled, so the mechanism had real history to attend to — and at 10,084
  parameters it is the smallest model tested. It still lost (−0.748 / −0.639).
  fs-1.3 already extracts that history through `r_hist_*` and `q_retr_*`, and
  learned attention over the same material did not improve on hand-built retrieval.
* **T6 MetaboNet transfer** gave no advantage over CGMacros pretraining or over no
  pretraining at all (−0.690 / −0.692). Consistent with Batch H.
* **Bias** is the common signature: every direct Transformer carries −3.69 to
  −7.63 mg/dL systematic bias against fs-1.3's −0.17, i.e. systematic
  under-prediction of excursions — regression to the mean under insufficient data,
  exactly as in Batches D/E/F.
* **Sensor identity** was implemented as an input channel as required, and the
  honest report is that it is **degenerate within CGMacros**: the primary CGM
  series is `dexcom_g6_pro` for 100% of readings and all 45 participants. It is
  retained because MetaboNet's devices are heterogeneous, so a transferred encoder
  has somewhere to put that variation. No gain is claimed on it, and none is
  possible by construction.

Nothing here is a foundation model, and nothing here is described as one.

---

## Deliverable 6 · Pretraining and negative-control comparisons

All four required controls were run. The result is unambiguous.

| comparison | within-person | cold-start |
|---|---|---|
| **Direct: shuffled-objective CONTROL** | **−0.456** | −0.669 |
| T4 masked-CGM pretrained | −0.935 | −0.657 |
| T5 forecast pretrained | −0.763 | −0.735 |
| T6 MetaboNet pretrained | −0.690 | −0.692 |
| T1 identical arch, NO pretraining | −0.706 | −0.739 |
| **Frozen: shuffled-pretrained CONTROL** | **−0.121** | −0.060 |
| **Frozen: RANDOM embeddings CONTROL** | **−0.176** | −0.045 |
| T7 frozen genuinely pretrained | −0.219 | −0.012 |

**Within-person the shuffled-objective control is the best direct pretrained model
and beats all three genuine objectives, and the ordering of the frozen trio is
exactly inverted from the one pretraining is supposed to produce: shuffled beats
random beats real.** Cold-start, the entire frozen spread is 0.048 mg/dL — noise.

This independently reproduces Batch G's finding (where a random GRU encoder beat
every CGMacros-pretrained GRU) with a different architecture, a different objective
implementation and a different pooling scheme. When random ≈ shuffled ≈ trained
across two architecture families, the representation is not the thing carrying the
signal.

Pretraining corpora, per fold: CGMacros windows drawn from **that fold's training
participants only** (15,117 windows from 33 participants in cold-start fold 0);
MetaboNet 30,000 windows, containing no CGMacros participant.

---

## Deliverable 7 · Biological-plus-Transformer combination results (Phase 5)

Admitted by the rule "only combinations justified by independent development
results": **M7** (the one cold-start interval excluding zero), **F** (the only
reliably harmless metabolic mechanism) and **M1+M9** (the two nominally positive
cold-start microbiome features, as a pair).

**The Transformer slot was NOT filled, because Phase 4 justified nothing.** T7 is
−0.012 cold-start and the *worst* of the three frozen variants within-person. Every
direct Transformer lost. Per the mission's own rule, there was nothing to combine.

### Cold-start

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt |
|---|---|---|---|---|---|
| A fs-1.3 (reference) | 23.085 | **ref** | — | — | — |
| P5 M1 + M9 biological pair | 22.997 | +0.088 | [-0.185, +0.344] | no | -2.09 |
| P5 M7 + F stacked | 23.046 | +0.039 | [+0.017, +0.061] | **YES** | -0.12 |
| M7 microbiome residual correction | 23.056 | +0.029 | [+0.009, +0.048] | **YES** | -0.13 |
| F hierarchical cohort shrinkage (k=3) | 23.075 | +0.010 | [-0.004, +0.024] | no | -0.12 |

### Within-person

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt |
|---|---|---|---|---|---|
| A fs-1.3 (reference) | 23.548 | **ref** | — | — | — |
| P5 M1 + M9 biological pair | 23.367 | +0.181 | [+0.006, +0.374] | **YES** | -1.14 |
| M7 microbiome residual correction | 23.561 | -0.014 | [-0.077, +0.041] | no | -0.63 |
| F hierarchical cohort shrinkage (k=3) | 23.579 | -0.031 | [-0.066, +0.002] | no | -0.37 |
| P5 M7 + F stacked | 23.589 | -0.041 | [-0.118, +0.027] | no | -0.94 |

**P5 M7 + F stacked is exactly additive**: M7 alone +0.029, F alone +0.010,
stacked **+0.039 [+0.017, +0.061]** — the two mechanisms are complementary rather
than redundant, and the worst-participant deterioration of **−0.12** is the
mildest measured anywhere in the project. Full metrics: RMSE 31.387 (vs 31.435),
bias −0.143 (vs −0.165), peak 25.290 (vs 25.318), ttp 29.107 (vs 29.081), iAUC
1644.3 (vs 1647.8), all 45 participants, 12.8 s fit.

It is still **0.17% of the reference error**, and it is **−0.041 within-person**.

`P5 M1 + M9` shows the familiar shape: +0.181 within-person with an interval
excluding zero, +0.088 cold-start with an interval that does not.

---

## Deliverable 8 · Updated total count of model configurations investigated

| | configurations |
|---|---|
| Prior research missions (as reported at `477334f`) | ~145 |
| Phase 2 metabolic | 25 |
| Phase 3 microbiome | 15 |
| Phase 4 Transformer | 12 |
| Phase 5 combination | 5 |
| **This mission** | **57** |
| **NEW CUMULATIVE TOTAL** | **~202** |

Each of the 57 was scored in both regimes, so this mission added **114 paired
comparisons** against fs-1.3 on the same seven locked folds.

---

## Deliverable 9 · Leakage and multiplicity audit

### Leakage

| vector | control |
|---|---|
| Temporal | Metadata were collected at study start, before every episode. T8's memory admits an earlier meal only if its **whole 120-minute response window elapsed strictly before** the current meal timestamp — the same filter `ml/features.py::_prior_observed_responses` applies to fs-1.3's fingerprint and retrieval. |
| Participant identity | The real threat here. Metadata are constant per participant, hence a fingerprint. Addressed by cold-start folds (validation participants never seen) **and** by training-fold-safe shuffled controls. |
| Transform fitting | Every metadata transform — median imputation, standardisation, SVD, taxon selection, Jaccard pool, cohort offsets — is fitted on the fold's **training participants only**, enforced at one chokepoint (`research/biometa/common.py::Attachment`). M5's taxon selection uses only training participants' mean 60-minute response. |
| Pretraining corpus | CGMacros windows come from the fold's training participants only. MetaboNet contains no CGMacros participant. |
| Reserved test set | The **394-episode reference set was never loaded** by any experiment in this mission. Every run reports "394 test episodes reserved and NOT loaded". |
| Raw data | All three metadata files are read **in place, read-only**. Nothing was extracted, moved or modified. |

### Multiplicity — and why the one surviving interval is not a result

This mission produced **7 favourable intervals excluding zero out of 114 paired
comparisons**. At α = 0.05 two-sided, chance alone predicts **≈5.7**. The observed
count is indistinguishable from the false-positive rate.

Worse, the 7 are concentrated exactly where identity leakage predicts them:
4 of the 7 are **within-person**, and one of those (`P5 M1 + M9`) is a
configuration whose own shuffled control outperformed it.

The strongest single claim available is `P5 M7 + F stacked`, +0.039 mg/dL
cold-start. Against ~202 cumulative configurations on the same folds, a 0.039
mg/dL effect with a 0.044-wide interval is an exploratory observation, not
evidence of superiority.

**All results in this report are marked exploratory. No statistically confirmed
superiority is claimed for any candidate.**

---

## Deliverable 10 · Best-model recommendation

| category | recommendation | measured evidence |
|---|---|---|
| Best within-person | **fs-1.3** (23.548) | no candidate beat it; the best biological candidate's own shuffled control beat it |
| Best cold-start | **fs-1.3** (23.085) | closest candidate +0.039 mg/dL, i.e. 0.17%, after ~202 configurations |
| Best peak timing | **fs-1.3** (25.290–25.318 peak, 29.04–29.11 ttp) | no candidate moved either metric beyond the third decimal |
| Best metabolic | **none** | 0 favourable cold-start; lipids lose up to 10.787 mg/dL |
| Best microbiome | **none promotable** | M7 +0.029, discounted for multiplicity |
| Best Transformer | **none** | all 8 direct configs significantly worse cold-start |
| **Deserving promotion** | **NONE. Keep fs-1.3.** | — |

**fs-1.3 is preserved and unchanged.** `git diff` against `477334f` over `ml/`,
`backend/` and `contracts/` is empty; all four model artifacts verify against
their manifests.

If the user wants the single most defensible candidate on the record, it is
`P5 M7 + F stacked`: +0.039 mg/dL cold-start, interval excluding zero,
worst-participant risk −0.12, 1.2 s extra fit time. I am **not** recommending it.
It is negative within-person, its effect is two orders of magnitude below the
fold-to-fold spread, and promoting it would mean selecting the best of ~202
configurations on folds those configurations have all already seen.

---

## Failed and rejected hypotheses

| hypothesis | outcome |
|---|---|
| Underused biological measurements contain information missing from fs-1.3 | **REJECTED.** 0 of 25 metabolic and 0 of 15 microbiome configurations improved cold-start with any defensible margin |
| Attention gives a modelling advantage over GRU/TCN/CDE | **REJECTED.** All 8 direct Transformers significantly worse cold-start; frozen variants indistinguishable |
| Self-supervised pretraining helps, if the architecture is right | **REJECTED a second time.** Shuffled-objective control matched or beat all genuine objectives; random frozen beat real pretrained within-person |
| MetaboNet transfer works with attention instead of recurrence | **REJECTED.** −0.690 / −0.692, no advantage over no pretraining |
| Microbiome similarity can substitute for missing personal history in cold start | **REJECTED.** M6 −0.028, M8 −0.067, worst participant −7.23 |
| Learned cross-attention over prior meals beats hand-built retrieval | **REJECTED.** T8 −0.748 / −0.639 despite 96.1% memory coverage |
| Vendor gut-health scores carry usable physiology | **REJECTED.** −0.712 cold-start, worse than its own shuffled control |
| Shannon/Simpson diversity | **NOT RUN** — binary scale cannot support it |
| Compositional log-ratios | **NOT RUN** — incidence data is not a composition |

---

## Reproduction

```bash
python -m research.biometa.metadata                  # verified dictionary
python -m research.biometa.runner_metabolic    --db-url sqlite:///data/local/cgmacros_v2.db
python -m research.biometa.runner_microbiome   --db-url sqlite:///data/local/cgmacros_v2.db
python -m research.transformer.runner          --db-url sqlite:///data/local/cgmacros_v2.db
python -m research.biometa.runner_combination  --db-url sqlite:///data/local/cgmacros_v2.db
python -m pytest -q
```

Results land in `research/biometa/results/` and `research/transformer/results/`.

**Runtime and memory:** Phase 2 ≈ 8 min, Phase 3 ≈ 6 min, Phase 4 ≈ 40 min
(792 s within-person + ~1500 s cold-start), Phase 5 ≈ 2 min. CPU only, peak RAM
well under 8 GB. Transformer parameter counts 10,084–22,020.

**Determinism:** seeds fixed for torch and numpy. Full bitwise determinism is not
claimed — CPU BLAS reduction order varies between runs. No numerical instability
occurred: zero non-finite predictions across all 57 configurations.

**Missingness robustness:** subject 48 (no microbiome) and subjects 26/28/48 (no
gut health) were carried as NaN through every microbiome configuration without
imputation to zero; all 45 participants appear in every cold-start result.

---

## What I would do with more time

Nothing in this direction. This mission tested the last two standing hypotheses
and both failed, in the specific way that points at the cohort rather than the
method: the biology is measured once per person and so can only act as an identity
label, and the architectures keep tying with their own scrambled controls. Adding
**more participants** remains the experiment that would move the number.
