# Promotion Gate Report — retrieval integrated, FPCA overlay declined

**Owner:** Ruhaan Shah · **From:** `c5827b7` · **Contract:** v1.0 unchanged
**Feature set:** fs-1.2 → **fs-1.3** · **Artifact:** `ml/artifacts_cgmacros_fs13/`

## Verdicts

| Decision | Outcome |
|---|---|
| Promote retrieval | **DONE.** Integrated into `ml/features.py` as fs-1.3. |
| Test FPCA shape overlay separately | **DONE — and DECLINED.** See §5; the plausibility audit is the reason. |
| Replace horizon predictors with trajectory heads | Not approved, not attempted. |

---

## 1 · Phase 1 — baseline protected

646 tests passed before any change. All three prior artifacts verified against their
manifests (`fs-1.1 ridge k=6`, `fs-1.1 blend k=3`, `fs-1.2 blend k=3`) and the fs-1.2
artifact copied aside before work began. **No artifact was overwritten**; fs-1.3 went to
a new directory. Research results reproduced: cold-start baseline 23.971 vs 23.972
reported, retrieval 23.185 exact.

## 2 · Phase 2 — integration

Retrieval is now computed inside `build_features`, sharing ONE causal history pass with
the fs-1.2 fingerprint (`_prior_observed_responses`), so the two can never disagree about
which history is admissible. That pass enforces:

- same participant only;
- strict `meal.timestamp < meal_timestamp` — a meal never retrieves itself;
- `meal.timestamp + 120 min < meal_timestamp` — the whole response was observable at
  prediction time. A meal 30 minutes earlier is **excluded**; the same meal 4 hours
  earlier is included;
- a baseline reading must exist at or before that earlier meal;
- ≥20 native grid points, else the curve is not characterized well enough to average.

Timezone semantics respected: retrieval compares a participant's own timestamps with each
other, all sharing one `time_basis`, so the raw clock reading is the correct comparable.

**Insufficient-history fallback (required by Phase 2).** The few-shot breakdown showed
retrieval *hurts* with very thin history — averaging 1–2 "most similar" meals is noisier
than the fingerprint mean over everything. Features are therefore withheld below
`retrieval_min_history`, and the model falls back to generalized + fingerprint.
`q_retr_n` still reports the true count, so the fallback is observable, not silent.

Threshold selected on development folds only:

| `retrieval_min_history` | within-person Δ | cold-start Δ | cold-start 95% CI |
|---|---|---|---|
| 1 | +0.172 (ns) | +0.689 | [+0.211, +1.140] |
| 3 | +0.256 (ns) | +0.756 | [+0.276, +1.206] |
| **5 (selected)** | +0.109 (ns) | **+0.857** | **[+0.390, +1.339]** |
| 8 | +0.037 (ns) | +0.550 | [+0.135, +0.979] |

Non-monotone, as expected: too low admits noisy retrieval, too high discards usable
history. Cold-start is the selection signal because it is the regime retrieval exists for
and the only one with significance.

## 3 · Research ↔ production equivalence

Production recomputes retrieval from raw CGM; the research harness read each episode's
pre-sampled `observed_curve`. Agreement over all 1,171 dev episodes:

| feature | correlation | mean abs diff |
|---|---|---|
| `q_retr_delta_30/60/90/120` | 0.989 / 0.996 / 0.995 / 0.992 | 1.47 / 1.31 / 1.34 / 1.99 |
| `q_retr_n` | 0.998 | 0.008 |
| `q_retr_peak_delta` | 0.994 | 1.40 |
| `q_retr_ttp` | 0.962 | 2.48 |
| `q_retr_curve_spread` | 0.995 | 0.60 |

Identical NaN rates (3.9%), `q_retr_n` exact agreement 99.2%, zero-retrieved agreement
**100%**. Residual differences come from grid-sampling versus raw reconstruction and are
expected rather than defects. Outcome-level reproduction is the stronger check, and it
holds — see §4.

## 4 · Phase 3 — paired development evaluation

Candidate A is the full fs-1.3 feature set with the `q_retr_*` columns **dropped**, so A
and B share one feature pass and identical episodes. Any difference is the retrieval
columns alone.

### Within-person rolling purged folds (307/604/885 train)

| candidate | MAE | RMSE | traj MAE | peak | ttp | iAUC | Δ MAE | 95% CI | excl 0 |
|---|---|---|---|---|---|---|---|---|---|
| A baseline (no retrieval) | **24.360** | 33.20 | 22.24 | 26.42 | 30.74 | 1747 | — | — | — |
| B + retrieval | **24.189** | 32.85 | 22.05 | 26.03 | 30.84 | 1750 | +0.172 | [-0.157, +0.488] | no |
| C + retrieval + FPCA shape | **24.189** | 32.85 | 21.81 | 25.77 | 29.51 | 1749 | +0.172 | [-0.157, +0.488] | no |

### Cold-start — entire participants held out

| candidate | MAE | RMSE | traj MAE | peak | ttp | iAUC | Δ MAE | 95% CI | excl 0 |
|---|---|---|---|---|---|---|---|---|---|
| A baseline (no retrieval) | **23.942** | 32.51 | 21.95 | 26.23 | 30.34 | 1743 | — | — | — |
| B + retrieval | **23.253** | 31.70 | 21.28 | 25.44 | 29.37 | 1665 | +0.689 | [+0.216, +1.144] | **yes** |
| C + retrieval + FPCA shape | **23.253** | 31.70 | 20.98 | 25.16 | 27.83 | 1662 | +0.689 | [+0.216, +1.144] | **yes** |

**Retrieval reproduced its research advantage after integration:** cold-start +0.689
[+0.216, +1.144], CI excluding zero (research: +0.786 [+0.277, +1.285]). Within-person
+0.172 with a CI spanning zero (research: +0.234, also spanning zero). Same direction,
same significance pattern, in both regimes.

### Few-shot, cold-start — MAE by observed own-history depth

| model | none | 1–2 | 3–4 | 5–9 | 10+ |
|---|---|---|---|---|---|
| A baseline | 23.43 | 26.84 | 28.28 | 24.65 | 22.81 |
| B + retrieval | 23.19 | 28.14 | 29.05 | **23.59** | **21.75** |

The gain is concentrated at 5–9 and 10+ prior meals and is **worse** at 1–4. That is the
finding that motivated the fallback gate, and it is mechanistically coherent: there is
nothing useful to retrieve from one or two meals.

## 5 · Phase 4 — FPCA shape overlay: evaluated, then DECLINED

The overlay passed five of the six verification requirements:

| Requirement | Result |
|---|---|
| Horizon predictions unchanged at 30/60/90/120 | **PASS** — max deviation 3.55e-15 |
| Trajectory internally consistent with peak/ttp/iAUC | **PASS** — metrics recomputed from the model's own 25-point curve |
| No unavailable future observations | **PASS** — basis fitted per fold on training trajectories only |
| Peak-timing improvement persists | **PASS** — ttp 30.74→29.51 within-person, 30.34→27.83 cold-start |
| Model available without the overlay | **PASS** — separate candidate, production untouched |
| **Numerically stable and physiologically plausible** | **FAIL** |

The plausibility audit is why this is declined. On a held-out fold the overlay curves are
finite, pin minute 0 correctly, stay inside 59–348 mg/dL with 0% needing clipping, and
**56.5% of peaks land off the four knots** — the structural ceiling really is removed.
But:

> **17.8% of curves show more than three direction changes** (median 2; a clean
> rise-and-fall has 1).

Nearly one curve in five oscillates in a way no postprandial glucose response does. The
curve is the primary thing a user and a judge look at, so trading ~1.5 minutes of
time-to-peak error for visibly implausible wiggle in 18% of displayed predictions is a
bad exchange. **Not promoted.**

The fix is identified, not guessed: constrain the basis (shape-constrained or
monotone-then-decreasing components, a second-derivative penalty, or fewer components).
That is the recommended next experiment.

## 6 · Reference-set result (previously inspected; reported once, never used for selection)

| model | MAE | RMSE | @30 | @60 | @90 | @120 | peak | ttp | iAUC |
|---|---|---|---|---|---|---|---|---|---|
| persistence | 32.58 | 45.70 | 23.83 | 36.68 | 36.34 | 33.45 | 51.80 | 65.18 | 3030.9 |
| cohort mean | 28.08 | 38.15 | 20.22 | 29.72 | 31.65 | 30.74 | 32.22 | 28.40 | 2136.5 |
| generalized | 21.63 | 29.54 | 15.51 | 21.70 | 24.72 | 24.58 | 25.19 | 28.93 | 1528.0 |
| personalized | 21.96 | 29.60 | 16.05 | 22.00 | 25.05 | 24.72 | 24.62 | 28.76 | 1531.7 |
| **blend_personalized (PRODUCTION)** | 20.55 | 28.35 | 15.73 | 21.19 | 22.83 | 22.44 | 22.79 | 28.02 | 1477.6 |

Against fs-1.2 on the same 394 episodes:

| metric | fs-1.2 | fs-1.3 | change |
|---|---|---|---|
| MAE | 21.90 | **20.55** | **−1.35** |
| RMSE | 30.08 | **28.35** | −1.73 |
| Peak MAE | 24.29 | **22.79** | −1.50 |
| iAUC MAE | 1566.2 | **1477.6** | −88.6 |
| Time-to-peak MAE | 26.93 | 28.02 | **+1.09 worse** |

Four metrics improved materially; **time-to-peak regressed 1.09 minutes.** Reported
rather than buried — and it is precisely the gap the overlay would have closed, which is
why the overlay's plausibility failure matters.

Participant-clustered bootstrap, all CIs excluding zero: vs `personalized` +1.409
[+0.659, +2.129]; vs `generalized` +1.079 [+0.149, +1.962]; vs `cohort_mean` +7.536.

`generalized` vs `personalized` remains −0.330 [−1.036, +0.395], CI spanning zero: with
retrieval and the fingerprint both in the feature set, the **fitted** calibration offset
is still not demonstrably useful on its own.

## 7 · Operational

| | |
|---|---|
| Contract v1.0 | unchanged — `openapi.yaml` and `schemas/` byte-identical to the freeze |
| API | `/api/model/info` 200 (fs-1.3, official), `/api/model/metrics` 200, `POST /api/predict` 200 with 0 schema errors, 404/422 correct |
| Determinism | identical output across 3 identical requests |
| Latency | **359 ms median / 372 ms p95** — unchanged from fs-1.2's 353 ms, because retrieval reuses the 8-day window fs-1.2 already fetched |
| Tests | 646 passed |
| Artifact | `ml/artifacts_cgmacros_fs13/model.joblib`, fs-1.3, blend, k=3, `data_source=official` |

## 8 · Recommended next action

**Shape-constrained trajectory basis.** The overlay's only failure was plausibility, and
the measured peak-timing benefit is real. A basis constrained to unimodal rise-and-fall
would likely keep the ~1.5-minute ttp gain while eliminating the oscillation — and
time-to-peak is now the one metric that regressed at fs-1.3.

I would still not spend remaining time on TCN/GRU/neural-CDE at ~900 training episodes.
