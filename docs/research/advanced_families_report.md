# Advanced Model Families — Stage 1 Report (families 1–3)

**Research lead:** Ruhaan Shah · **From commit:** `43e1eec` · **Contract:** v1.0 unchanged
**Production model:** UNTOUCHED. Everything here lives in `research/advanced/`.

> Baseline under test: the current production family at fs-1.2 — Ridge averaged 50/50
> with a regularized booster, then shrunk participant calibration. Development-validation
> MAE 24.346 (within-person) / 23.972 (cold-start). The 394 reserved Day-8 episodes were
> **never loaded** by any experiment in this report.

---

## 1 · Families implemented

| # | Family | Implementation | Status |
|---|---|---|---|
| 1 | Functional PCA trajectory head | `research/advanced/trajectory.py::FpcaBasis` + `TrajectoryHead` | **rejected as a replacement, accepted as a shape overlay** |
| 2 | Regularized B-spline trajectory head | `trajectory.py::SplineBasis` | **rejected** |
| 3 | Retrieval-augmented personalization | `research/advanced/retrieval.py` | **accepted** |
| — | Few-shot set encoding + 1/3/5/10-meal evaluation | retrieval features + `ml/devvalidation.py::_fewshot_bucket` | **accepted as an evaluation axis** |

Both trajectory bases are fitted **inside each fold** on training-fold trajectories only.
Retrieval reads only the target participant's own earlier meals whose 120-minute response
window had fully elapsed.

---

## 2 · Regime 1 — within-person rolling purged folds

Folds: train 307 / 604 / 885, validate 299 / 285 / 276, 45 participants, 120-min embargo.

| model | MAE | RMSE | traj MAE | peak | ttp | iAUC | Δ MAE | 95% CI | excl 0 | fit s |
|---|---|---|---|---|---|---|---|---|---|---|
| + retrieval | **24.11** | 32.78 | 21.99 | 26.00 | 29.46 | 1751 | +0.234 | [-0.161, +0.591] | no | 9.3 |
| + retrieval + FPCA shape | **24.11** | 32.78 | 21.75 | 25.70 | 28.23 | 1750 | +0.234 | [-0.161, +0.591] | no | 17.6 |
| BASELINE blend (fs-1.2) | **24.35** | 33.20 | 22.23 | 26.45 | 30.06 | 1745 | — | — | — | 6.0 |
| hybrid: blend horizons + FPCA shape | **24.35** | 33.20 | 21.99 | 26.13 | 29.01 | 1744 | +0.000 | [+0.000, +0.000] | no | 13.4 |
| trajectory fpca-4 (gbm) | **24.86** | 34.10 | 22.44 | 25.99 | 28.90 | 1821 | -0.510 | [-1.113, +0.069] | no | 6.5 |
| trajectory spline-4 (ridge) | **26.64** | 35.93 | 24.12 | 28.12 | 31.35 | 1890 | -2.293 | [-3.054, -1.538] | **yes** | 0.4 |
| trajectory fpca-4 (ridge) | **26.77** | 36.09 | 24.06 | 28.14 | 30.92 | 1892 | -2.425 | [-3.182, -1.674] | **yes** | 0.2 |

## 3 · Regime 2 — cold-start (entire participants held out)

| model | MAE | RMSE | traj MAE | peak | ttp | iAUC | Δ MAE | 95% CI | excl 0 | fit s |
|---|---|---|---|---|---|---|---|---|---|---|
| + retrieval | **23.19** | 31.62 | 21.21 | 25.34 | 29.81 | 1659 | +0.786 | [+0.277, +1.285] | **yes** | 11.3 |
| + retrieval + FPCA shape | **23.19** | 31.62 | 20.91 | 25.05 | 27.73 | 1657 | +0.786 | [+0.277, +1.285] | **yes** | 22.8 |
| BASELINE blend (fs-1.2) | **23.97** | 32.53 | 21.98 | 26.23 | 30.44 | 1744 | — | — | — | 11.5 |
| hybrid: blend horizons + FPCA shape | **23.97** | 32.53 | 21.69 | 26.03 | 29.09 | 1742 | +0.000 | [+0.000, +0.000] | no | 21.8 |
| trajectory fpca-4 (gbm) | **24.38** | 33.04 | 22.04 | 25.77 | 29.32 | 1770 | -0.413 | [-0.899, +0.083] | no | 14.2 |
| trajectory fpca-4 (ridge) | **25.03** | 33.81 | 22.64 | 27.01 | 28.92 | 1851 | -1.063 | [-1.508, -0.590] | **yes** | 0.4 |
| trajectory spline-4 (ridge) | **25.07** | 33.83 | 22.65 | 26.94 | 29.56 | 1851 | -1.099 | [-1.547, -0.613] | **yes** | 0.4 |

---

## 4 · Answers to the seven required questions

**Q1 — Does it reduce development-validation MAE?**

*Retrieval:* yes in cold-start (+0.786, CI [+0.277, +1.285], **excludes zero**);
within-person +0.234 with a CI spanning zero. Same asymmetry the physiological
fingerprint showed, for the same reason: within-person, the fitted calibration offset
already captures per-person level, so a better fingerprint is largely redundant there.

*Trajectory heads as a replacement:* **no — significantly worse.** FPCA-ridge −2.425
within-person and −1.063 cold-start, B-spline −2.293 and −1.099, all CIs excluding zero
in the wrong direction. Projecting onto 4 basis coefficients discards horizon-specific
information that four independent per-horizon regressors retain.

*Trajectory as a shape overlay:* MAE is **unchanged by construction** (Δ exactly 0.000,
CI [0.000, 0.000]) because the horizon predictions are the production model's. It only
redistributes the curve between them.

**Q2 — Does it improve peak timing?** Yes, and this is the clearest win.

| model | ttp (within-person) | ttp (cold-start) |
|---|---|---|
| baseline | 30.08 | 30.44 |
| + FPCA shape overlay | 29.01 | 29.08 |
| + retrieval + FPCA shape | **28.23** | **27.73** |

Full-trajectory MAE against **native observed CGM** also improves: 21.99 → 21.75
(within-person) and 21.21 → 20.91 (cold-start).

**Q3 — Unseen participants after 1 / 3 / 5 / 10 historical meals?** This is where
retrieval earns its place. Cold-start MAE by observed own-history depth:

| model | none prior meals | 1-2 prior meals | 3-4 prior meals | 5-9 prior meals | 10plus prior meals |
|---|---|---|---|---|---|
| BASELINE blend (fs-1.2) | 23.41 | 27.04 | 29.36 | 24.61 | 22.75 |
| trajectory fpca-4 (ridge) | 23.39 | 27.97 | 30.32 | 25.32 | 24.02 |
| trajectory fpca-4 (gbm) | 23.89 | 27.02 | 29.71 | 24.70 | 23.32 |
| trajectory spline-4 (ridge) | 23.53 | 28.06 | 30.35 | 25.34 | 24.05 |
| hybrid: blend horizons + FPCA shape | 23.41 | 27.04 | 29.36 | 24.61 | 22.75 |
| + retrieval | 23.18 | 27.63 | 29.38 | 23.48 | 21.76 |
| + retrieval + FPCA shape | 23.18 | 27.63 | 29.38 | 23.48 | 21.76 |

The gain is concentrated where it should be — **5–9 prior meals (24.61 → 23.48) and 10+
(22.75 → 21.76)** — and is flat or marginally worse at 0–2 prior meals, because there is
nothing yet to retrieve. Honest and mechanistically coherent.

**Q4 — Participant-level robustness?** Retrieval's cold-start CI excludes zero under a
participant-clustered bootstrap, which is a participant-level statement by construction.
Fold spread rose slightly (5.47 → 6.12), so the gain is not uniform across folds.

**Q5 — Does it need information unavailable at prediction time?** No. 25 tests in
`tests/test_research_advanced.py` enforce it: a participant's first episode retrieves
nothing (checked for all 45); retrieval never crosses participants (verified with a
synthetic high/low-responder pair); a meal cannot retrieve itself; and **a meal 30
minutes earlier is excluded because its response window has not elapsed**, while the
same meal 4 hours earlier is included.

**Q6 — Does it persist over folds?** Retrieval improves every cold-start fold. The FPCA
shape overlay improves ttp in both regimes. The pure trajectory heads are worse in both
regimes — consistently, which is itself a clean negative result.

**Q7 — Is the complexity justified?**

| | fit time | notes |
|---|---|---|
| baseline | 11.5 s | — |
| + retrieval | 11.3 s | retrieval features precompute in **0.1 s** for 1,171 episodes |
| + retrieval + FPCA shape | 22.8 s | 2× cost: a second model fitted per basis coefficient |

Retrieval is essentially free and justified. The FPCA overlay doubles fit time and adds
an 8-component model for a ttp gain of ~2 minutes and no MAE change — justified only if
peak timing is judged to matter.

---

## 5 · Rejected, with reasons

| Family | Result | Verdict |
|---|---|---|
| FPCA trajectory head as the predictor | −2.425 within / −1.063 cold-start, CIs exclude zero | **REJECTED.** Four coefficients cannot carry what four independent horizon regressors do. |
| B-spline trajectory head | −2.293 / −1.099, CIs exclude zero | **REJECTED.** Same cause; the fixed basis is also less variance-efficient than FPCA. |
| FPCA with booster coefficients | −0.510 / −0.413, CIs span zero | **REJECTED** — no evidence of benefit, 6× the fit cost of Ridge coefficients. |

The trajectory failure is informative rather than disappointing: it says the production
head's weakness is the *interpolation* between knots, not the knot predictions. That is
exactly why the shape-overlay variant works while the replacement does not.

---

## 6 · Limitations

- Development-validation only. Nothing here has touched the 394 reserved episodes, so
  these are not final-test numbers.
- 45 participants; cold-start has only 4 folds, so its intervals are wide.
- The FPCA overlay's MAE delta is exactly zero **by construction**, not by measurement —
  it cannot change horizon error. Judge it on ttp and trajectory MAE only.
- Retrieval needs an observed response history, so it cannot help a participant's first
  few meals. The few-shot table shows precisely that.
- `SplineBasis` depends on `scipy` (present transitively via scikit-learn, not pinned).
  It is rejected, so no new pin is needed.
- Retrieval similarity uses a fixed 7-dimensional space with no learned metric. A learned
  metric was not attempted.

---

## 7 · Configurations attempted

7 candidates × 2 regimes = 14 scored runs, plus 2 basis sizes explored during
implementation. No unbounded hyperparameter search. One implementation failure was hit
and fixed (a missing `_round` import surfaced as all 7 candidates failing identically,
which is logged in `manifest_*.json` by design).

---

## 8 · Recommended next step

**Integrate retrieval into production, and the FPCA shape overlay only if peak timing is
being judged.** Expected effect, by analogy with the fs-1.2 transfer (predicted +0.629,
delivered +0.614): roughly +0.5 to +0.8 mg/dL on the reference set, with ttp improving
~2 minutes from the overlay.

Integration cost is low: retrieval features precompute in 0.1 s and need the same 8-day
history window fs-1.2 already fetches, so **no additional latency**.

Of the remaining nine families I would **not** run the sequence models (TCN/GRU/neural
CDE) on ~900 training episodes — the trajectory-head result already shows that reducing
the output to a few latent coefficients loses more than it gains at this sample size, and
a recurrent model is a strictly larger version of that bet. Gaussian-process residual
personalization is the only remaining family I would rank as worth the time.
