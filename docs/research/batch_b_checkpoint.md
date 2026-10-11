# Batch B Checkpoint — Gates B0 and B1

**Owner:** Ruhaan Shah · **From:** `991a415` · **Control:** fs-1.3
**Contract:** v1.0 unchanged · **Production inference:** UNTOUCHED (`research/batch_b/`)

## Verdict

**Gate B1 is negative, and B2 was not run.** No non-neural sequence representation adds
measurable value to fs-1.3, and three are **significantly harmful** on unseen
participants. This triggers the mission's own stopping criterion — "sequence
representations provide no measurable development-validation benefit" — and §5 explains
why the B1a result specifically also removes the architectural hypothesis behind B2.

fs-1.3 is retained unchanged.

---

## 1 · Gate B0 — the data opportunity, verified

### Sensor provenance and sampling

| | |
|---|---|
| Primary sensor | Dexcom G6 Pro — **126,259 native readings**, 45 participants |
| Secondary sensor | Libre Pro — 46,234 readings, 45 participants (not used here) |
| Inter-reading gaps | **99.6% exactly 5 minutes**; only 0.10% exceed 15 minutes |
| Per participant | min 1,373 / median 2,854 / max 2,860 readings |
| Recording span | min 7.2 / median 9.9 / max 19.1 days |

These are de-interpolated native readings. The source's 1-minute columns are
forward-interpolated between true readings and would leak future glucose; the data layer
discarded them, which is why this inventory counts 126,259 rather than ~687,000.

### Window design, chosen from evidence

| window | episodes ≥90% complete | consecutive episodes whose windows overlap |
|---|---|---|
| 60 min (12 readings) | 99% | — |
| 120 min (24 readings) | 98% | 14% |
| **180 min (36 readings) — SELECTED** | **97%** | — |
| 360 min (72 readings) | 95% | **63%** |

180 minutes keeps near-complete coverage without the 63% window overlap a six-hour
window produces. Realised mean coverage on the lattice was **0.963**, with 96% of
episodes ≥90% covered.

### Independence — stated explicitly, as the gate required

**1,171 episodes come from 45 participants.** Sliding windows are *not* independent
samples: consecutive episodes share pre-meal context (14% overlap even at 120 minutes)
and every window from one participant shares one physiology. The effective sample size
is nearer 45 than 1,171, which is why every interval in this project is a
participant-clustered bootstrap rather than an episode-level one.

### Leakage-safe pretraining budgets

| regime | permissible native readings |
|---|---|
| cold-start fold 0–3 | 92,015 / 96,491 / 94,999 / 95,272 (validation participants entirely unseen) |
| rolling fold 1 / 2 / 3 | 25,587 / 47,282 / 67,862 (each participant truncated at its validation cutoff minus the 120-minute embargo) |

Within-person pretraining is the tighter constraint: a representation fitted on a
participant's whole series would see glucose that lies in the future relative to that
participant's earlier validation episodes.

---

## 2 · Gate B1 — within-person (rolling purged folds)

| candidate | MAE | RMSE | @30 | @60 | @90 | @120 | peak | ttp | iAUC | Δ MAE | 95% CI | excl 0 | worst ppt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| + B1a raw lags | **23.967** | 32.69 | 16.50 | 24.58 | 27.32 | 27.47 | 26.08 | 29.25 | 1729 | +0.284 | [-0.026, +0.575] | no | -2.65 |
| + B1d sequence kernel-12 | **24.117** | 32.81 | 16.69 | 24.48 | 27.58 | 27.72 | 25.86 | 30.77 | 1742 | +0.135 | [-0.152, +0.389] | no | -2.44 |
| + B1e shape descriptors | **24.204** | 32.84 | 16.93 | 24.39 | 27.75 | 27.74 | 26.06 | 29.89 | 1744 | +0.048 | [-0.142, +0.233] | no | -2.05 |
| + B1b sequence PCA-6 | **24.244** | 32.81 | 16.91 | 24.36 | 27.81 | 27.89 | 25.95 | 30.59 | 1743 | +0.007 | [-0.182, +0.191] | no | -2.10 |
| B0 CONTROL fs-1.3 | **24.251** | 32.80 | 17.00 | 24.29 | 27.77 | 27.94 | 25.92 | 30.23 | 1743 | — | — | — | — |
| + B1c spectral-6 | **24.281** | 32.86 | 17.11 | 24.50 | 27.90 | 27.61 | 26.05 | 30.45 | 1759 | -0.029 | [-0.165, +0.108] | no | -1.28 |

## 3 · Gate B1 — cold-start (entire participants held out)

| candidate | MAE | RMSE | @30 | @60 | @90 | @120 | peak | ttp | iAUC | Δ MAE | 95% CI | excl 0 | worst ppt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B0 CONTROL fs-1.3 | **23.085** | 31.44 | 16.54 | 23.55 | 26.22 | 26.02 | 25.32 | 29.08 | 1648 | — | — | — | — |
| + B1e shape descriptors | **23.188** | 31.51 | 16.63 | 23.70 | 26.45 | 25.97 | 25.28 | 28.78 | 1658 | -0.103 | [-0.226, +0.013] | no | -1.14 |
| + B1a raw lags | **23.219** | 31.67 | 16.74 | 23.90 | 26.31 | 25.92 | 25.52 | 29.00 | 1658 | -0.134 | [-0.389, +0.089] | no | -2.13 |
| + B1d sequence kernel-12 | **23.248** | 31.70 | 16.57 | 23.81 | 26.42 | 26.19 | 25.51 | 29.67 | 1658 | -0.163 | [-0.318, -0.029] | **yes** | -1.72 |
| + B1b sequence PCA-6 | **23.264** | 31.64 | 16.61 | 23.71 | 26.45 | 26.28 | 25.41 | 29.24 | 1654 | -0.179 | [-0.325, -0.050] | **yes** | -1.23 |
| + B1c spectral-6 | **23.292** | 31.71 | 16.68 | 23.80 | 26.55 | 26.14 | 25.46 | 28.96 | 1670 | -0.207 | [-0.314, -0.106] | **yes** | -0.91 |

---

## 4 · What B1 shows

**Within-person:** the best candidate is `B1a raw lags` at +0.284 mg/dL, 95% CI
[−0.026, +0.575] — **includes zero**. Every other representation is within ±0.05 of the
control. Nothing is established.

**Cold-start:** every representation is *worse*, and three of the five significantly so:

| representation | Δ MAE | 95% CI | |
|---|---|---|---|
| B1b sequence PCA-6 | −0.179 | [−0.325, −0.050] | **significantly worse** |
| B1c spectral-6 | −0.207 | [−0.314, −0.106] | **significantly worse** |
| B1d sequence kernel-12 | −0.163 | [−0.318, −0.029] | **significantly worse** |
| B1a raw lags | −0.134 | [−0.389, +0.089] | spans zero |
| B1e shape descriptors | −0.103 | [−0.226, +0.013] | spans zero |

The pattern is coherent: fs-1.3 already carries **twelve** glucose-dynamic features
derived from this same window (pre-meal level, 10/15/30/60-minute slopes, acceleration,
rise persistence, 60-minute variability, 120-minute range, deviation from the
participant's own overnight floor). Adding 6–38 further columns describing the same
180 minutes supplies no new information and costs variance — and cold-start, where the
model has no fitted per-participant offset to lean on, that cost is measurable.

---

## 5 · Why B2 was not run

The gate authorises B2 "only if B1 reveals useful incremental sequence information, or
if a justified architectural hypothesis remains." Neither holds, and the reason is
specifically the **B1a raw-lags** result rather than a general prejudice against
sequence models.

**B1a handed the model the entire sequence verbatim** — all 37 lattice values plus an
observation mask. That is the most expressive possible representation of that window: a
learned encoder is a *function of* those 37 numbers and cannot recover information they
do not contain.

I checked whether the model actually used them rather than silently ignoring them,
because the whole argument rests on that:

| | sequence columns' share |
|---|---|
| Ridge, 60-min horizon | **34.6% of total \|coefficient\| mass** |
| Booster, 60-min horizon | **13.6% of positive permutation importance**; best sequence column ranked 20th of 77 |

Top features in both remained fs-1.3's own (`carbs_g`, `q_retr_delta_60`,
`g_dev_from_night`, `minutes_since_prior_meal`). So the raw sequence was supplied,
substantially attended to, and still produced no generalization gain.

The two remaining CNN-specific hypotheses also fail:

- *"A CNN would capture nonlinear sequence structure."* `B1d` is an RBF kernel over
  whole sequences — an explicitly nonlinear representation — and it was significantly
  **worse** cold-start. The gradient booster in the blend can also already form
  nonlinear functions of the B1a raw lags.
- *"Weight sharing reduces variance versus 37 free columns."* The cold-start harm
  pattern shows the binding constraint is variance from added parameters. A CNN adds
  more parameters than B1a's 38 columns, not fewer, and must learn its filters from the
  same ~900 training episodes.

I deliberately am **not** generalising the Batch A DeepSets failure here, per your
correction. This conclusion rests on the B1a information-content argument, which is
specific to sequence models and independent of that earlier result.

PyTorch was therefore not installed. Had B1 shown headroom I would have installed the
CPU wheel; spending the remaining time on an encoder whose input space has already been
shown to be exhausted is not a defensible trade this close to the deadline.

---

## 6 · Leakage audit

| control | mechanism |
|---|---|
| Sequence window | strictly `[t − 180 min, t]`; extraction reads only readings at or before `t` |
| Native readings only | interpolated 1-minute source rows were discarded at ingestion |
| Missing lattice points | explicit observation mask; **never zero-filled**. Interpolation for PCA/kernel inputs happens only *within* one already-observed pre-meal window and the mask stays a separate feature |
| Fitted representations | PCA components and kernel landmarks fitted on **training-fold episodes only**, inside each fold |
| Folds | the same purged rolling and participant-held-out folds the production model was selected on; 120-minute embargo |
| Reference set | the 394 reserved Day-8 episodes were never loaded |

---

## 7 · Resource use

| step | time |
|---|---|
| CGM series load + sequence extraction (1,171 × 37) | 1.6 s |
| Gate B1, both regimes, 6 candidates | 273 s |
| B0 inventory | ~30 s |
| Peak memory | well under 1 GB |

Representation fit times: raw lags 0.0 s, PCA 1.6 s, spectral 0.0 s, kernel 3.7 s,
shape descriptors 0.1 s.

---

## 8 · Recommendation on self-supervised pretraining (B3) and transfer (B4)

**Do not run them.** Both are downstream of an encoder that B1 says has nothing to
encode. B3 asks whether a *pretrained* representation of the pre-meal window beats a
randomly initialised one; B1a has already shown that the window's full contents add
nothing to fs-1.3, so the comparison would be between two encodings of exhausted
information. B4 compounds that with a cross-dataset transfer whose source has **zero
macronutrients in all 15 studies**, so only CGM dynamics could transfer — the exact
signal B1 found unhelpful.

The honest remaining experiment in this direction is not a bigger encoder but **more
participants**. At 45 participants the binding constraint is cohort size, not model
class, and every negative result in Batches A and B points the same way.

If you want the negative result documented for completeness I will run B2 on an
installed CPU PyTorch — it is perhaps 90 minutes including the install. I do not expect
it to change the conclusion, and I would rather spend that time on the stale
documentation and the cloud deployment.
