# Batch C — Shape-constrained trajectory reconstruction

**Owner:** Ruhaan Shah · **From:** `bf87f19` · **Control:** fs-1.3 linear reconstruction
**Contract:** v1.0 unchanged · **Production:** UNTOUCHED (`research/batch_c/`)

## Verdict

Two Pareto-optimal candidates, kept rather than collapsed into one winner:

- **C2 curvature-penalised spline** — horizon predictions preserved to 4.8e-14, so glucose
  MAE is *identical by construction*; time-to-peak improves 29.00 → **28.14 min**, peak
  25.38 → **25.14**, and it is at **0% >3-turn oscillation**. A free improvement.
- **C6 direct peak-time + unimodal** — the best timing of any candidate,
  29.00 → **26.78 min** (−2.22) with **0% oscillation**, but it costs **+0.68 horizon MAE**.
  A deliberate timing-for-accuracy trade.

**C5 learned anchored correction is rejected** on exactly the grounds the earlier FPCA
overlay was, and more severely: it has the best trajectory/peak/iAUC numbers of any
candidate but **55.9% of its curves have more than three direction changes** versus
FPCA's 17.8% and the base model's **0%**.

Efficiency note: all reconstructions consume the same base horizon predictions, so the
fs-1.3 base was fitted once per fold and every reconstruction compared against identical
predictions — exactly paired, at one base fit per fold instead of one per candidate.

---

## 1 · Within-person (rolling purged folds)

| candidate | horizon MAE | traj MAE | peak | ttp | iAUC | mean turns | >1 turn | **>3 turns** | overshoot | max horizon dev |
|---|---|---|---|---|---|---|---|---|---|---|
| BASE linear (fs-1.3) | 24.173 | 21.951 | 25.85 | 30.15 | 1735.3 | 1.26 | 30.7% | **0.0%** | 0.00 | 0.0e+00 |
| C1 PCHIP | 24.173 | 22.166 | 25.85 | 30.15 | 1741.8 | 1.26 | 30.7% | **0.0%** | 0.00 | 1.7e-14 |
| C2 curvature spline | 24.173 | 22.079 | 25.65 | 28.98 | 1739.5 | 1.47 | 44.5% | **0.0%** | 0.87 | 4.7e-14 |
| C3 unimodal surge | 24.166 | 22.247 | 26.58 | 30.71 | 1753.6 | 0.82 | 0.0% | **0.0%** | 0.86 | 4.4e+01 |
| C4 gated unimodal/flexible | 24.196 | 22.111 | 25.99 | 29.93 | 1741.5 | 1.27 | 30.7% | **0.0%** | 0.86 | 3.6e+01 |
| C5 learned anchored correction | 24.173 | 21.795 | 25.48 | 28.76 | 1738.8 | 4.82 | 93.1% | **67.4%** | 2.85 | 1.2e-14 |
| C6 direct peak-time + unimodal | 24.307 | 22.372 | 27.20 | 29.01 | 1747.9 | 1.00 | 0.0% | **0.0%** | 0.35 | 5.1e+01 |

## 2 · Cold-start (participants held out)

| candidate | horizon MAE | traj MAE | peak | ttp | iAUC | mean turns | >1 turn | **>3 turns** | overshoot | max horizon dev |
|---|---|---|---|---|---|---|---|---|---|---|
| BASE linear (fs-1.3) | 23.076 | 21.112 | 25.38 | 29.00 | 1647.2 | 1.10 | 24.6% | **0.0%** | 0.00 | 0.0e+00 |
| C1 PCHIP | 23.076 | 21.175 | 25.38 | 29.00 | 1652.8 | 1.10 | 24.6% | **0.0%** | 0.00 | 1.8e-14 |
| C2 curvature spline | 23.076 | 21.106 | 25.14 | 28.14 | 1651.2 | 1.35 | 38.1% | **0.0%** | 0.70 | 4.8e-14 |
| C3 unimodal surge | 23.216 | 21.254 | 25.76 | 29.02 | 1660.3 | 0.78 | 0.0% | **0.0%** | 0.78 | 2.7e+01 |
| C4 gated unimodal/flexible | 23.192 | 21.149 | 25.47 | 28.52 | 1654.9 | 1.13 | 24.6% | **0.0%** | 0.68 | 2.4e+01 |
| C5 learned anchored correction | 23.076 | 20.794 | 25.01 | 27.82 | 1646.4 | 3.99 | 90.7% | **55.9%** | 2.05 | 1.2e-14 |
| C6 direct peak-time + unimodal | 23.759 | 21.785 | 26.30 | 26.78 | 1663.0 | 1.00 | 0.0% | **0.0%** | 0.44 | 6.3e+01 |

---

## 3 · Findings

**A framing correction I owe the record.** When I rejected the FPCA overlay I quoted
"17.8% of curves with >3 direction changes" against an implicit assumption that the
baseline was smooth. Measured here, the **base linear reconstruction is at 0.0%** on that
metric — but **24.6% of base curves already have more than ONE turning point**, because
for a quarter of episodes the four predicted knots are themselves non-monotone. So
oscillation is not purely a smoothing artifact; part of it is the knot predictions. The
FPCA rejection still stands (17.8% vs 0% on the >3 metric), but the baseline was not as
clean as that framing implied.

**C1 PCHIP does nothing, exactly as the gate predicted.** Metrics are identical to linear
to 14 decimal places on peak and time-to-peak, and trajectory MAE is marginally *worse*
(21.112 → 21.175 cold-start). PCHIP is monotone-preserving: between two adjacent knots it
introduces no new extremum, so it cannot produce an off-knot peak. It only rounds corners,
and rounding corners slightly degrades the fit. Confirmed empirically rather than assumed.

**C2 is the clean win.** Penalising discrete second-difference roughness subject to exact
knot equality (solved through the KKT system, so anchors hold to machine precision) buys
−0.86 min of timing and −0.24 mg/dL of peak error for 0.70 mg/dL of mean overshoot and no
>3-turn curves at all. Mean turning points rise 1.10 → 1.35.

**C3 unimodal is the only perfectly plausible family** (0% above one turning point, mean
0.78) and correctly represents a *negative* net response rather than forcing an excursion.
But it cannot pass through four arbitrary knots: max horizon deviation 27 mg/dL, and
horizon MAE degrades 23.076 → 23.216. It does not improve timing on its own (29.02).

**C6 is where the timing actually comes from.** Supplying a *directly predicted* peak time
— trained on observed time-to-peak from native CGM — and then fitting amplitude and shape
with that time held fixed gives the best time-to-peak in the project, 26.78 min, with
perfect unimodality and the lowest overshoot of any shape-changing method (0.44). The cost
is real and reported: horizon MAE +0.68, peak MAE +0.92.

**C4 gating added nothing.** Its gate routes to the flexible reconstruction only when the
predicted knots are already multimodal (24.6% of cases), so it inherits the base's
turning-point profile and lands between C2 and C3 on every metric without beating either.

---

## 4 · Numerical stability

Zero non-finite values across every candidate and both regimes. Horizon-preserving methods
hold their anchors to 1.2e-14 or better. Maximum overshoot beyond the predicted knot
envelope is 2.85 mg/dL (C5) and 0.87 mg/dL (C2).

---

## 5 · Reproduction

```bash
python -m research.batch_c.runner --db-url sqlite:///data/local/cgmacros_v2.db
```

Runtime ~5 minutes for both regimes, 7 candidates, 7 folds. Results in
`research/batch_c/results/batch_c_leaderboard.csv`.

## 6 · Promotion recommendation

**C2 is the only candidate I would recommend promoting**, and only if you want it: it is
strictly free on glucose accuracy (horizons preserved by construction) and improves two
curve metrics with no >3-turn oscillation. **C6 should be held as a research candidate**
— its timing gain is the largest measured, but trading +0.68 mg/dL of horizon MAE for
−2.22 min of timing is a product decision, not a modelling one.

Neither is promoted here; production remains fs-1.3 pending your approval.
