# Evaluation Protocol (fixed before results were inspected)

**Owner:** Ruhaan Shah · **Generated:** `2026-10-10T06:38:03.881001Z` · **Contract:** v1.0
**Dataset provenance:** `official` · **Participants discovered:** 45

Regenerate with:

```bash
python -m ml.protocol_report --source sqlite --db-url sqlite:///data/local/cgmacros_v2.db
```

> This document contains **no error metrics**, by design. Ruling D1 requires the held-out protocol to be fixed before any performance is seen, so it cannot be adjusted to improve a score. The command that produces it never fits a model.

## 1 · Filtering cascade

| Stage | Count |
|---|---|
| Candidate logged meals in the database | 1698 |
| Became usable episodes | 1651 |
| Excluded | 47 |
| &nbsp;&nbsp;↳ `incomplete_macros` | 1 |
| &nbsp;&nbsp;↳ `insufficient_premeal_context` | 20 |
| &nbsp;&nbsp;↳ `insufficient_target_coverage` | 26 |
| Tagged: another meal inside the 120-min window | 246 |
| **Day-8 context-eligible** | **480** |

Nothing is dropped silently: every excluded meal carries a recorded reason.

## 2 · Inclusion requirements

**Every usable episode** must have:

- a measured CGM baseline within 15 minutes **before** the meal (minute 0 of the curve is a measurement, never imputed; without one the episode is refused);
- an observed reading within ±10 minutes of all 4 horizons (t+30/60/90/120);
- all five macronutrients present (the forecast contract requires them).

**A Day-8 test episode** must additionally have:

- at least **7 days** of prior CGM history;
- at least **70%** of the nominal 5-minute readings present in that window (CGMacros has real sensor gaps; demanding 100% would discard good episodes);
- **no other logged meal inside its 120-minute response window** (enforced: `exclude_overlap_from_test=True`). An overlapping episode's observed response blends two meals, so an error measured against it is not attributable to the meal being forecast. Such episodes stay in training, tagged.

## 3 · Split methodology — `day8_holdout` (PRIMARY)

Per participant:

1. **test candidates** = Day-8-eligible episodes, minus overlapping ones;
2. **cutoff** = the earliest test-candidate meal time;
3. **pool** = episodes at or before `cutoff − 120 min` (the **embargo/purge**);
4. **validation** = the latest 20% of that pool;
5. **train** = the remainder of the pool.

A participant with no test candidate contributes its whole pool to training: it still informs the cohort model but cannot be scored on a Day-8 meal.

**Why the embargo.** A training episode at `t'` owns the response window `(t', t'+120]`. If `t' + 120 > cutoff`, that window shares CGM observations with a held-out window, so the training target would partially contain the held-out answer. Purging the last 120 minutes before the cutoff makes `t' + 120 ≤ cutoff` for every training episode.

Train, validation and test are therefore **disjoint by construction** — the protocol never trains on all episodes and scores an overlapping subset.

## 4 · Fold sizes

| Fold | Episodes | Participants |
|---|---|---|
| train | 938 | 45 |
| validation | 233 | 44 |
| **test (scored)** | **394** | **44** |
| purged by embargo / ineligible-and-after-cutoff | 86 | — |
| total usable | 1651 | 45 |

Contract note: `sample_counts.train_episodes` in `GET /api/model/metrics` reports 1171 — train **plus** validation — because contract v1.0 freezes that object's members and both folds are fitted or tuned on rather than scored. The breakdown lives here and in the evaluation detail file. No public field was added.

## 5 · Secondary protocols (reported separately, never the primary result)

- `within_person_chronological`: 1238 train / 413 test. Latest 25% of each participant's meals, **without** the Day-8 context requirement. Broader but weaker evidence.
- `cohort_holdout`: 1116 train / 535 test across 15 entirely held-out participants. Cold start; personalization is unavailable by construction.

## 6 · Leakage safeguards

| Guarantee | Mechanism |
|---|---|
| No feature reads the future | `build_features` starts with `history.truncated_at(meal_timestamp)`, so post-meal rows are *absent*, not merely unused |
| Interpolated CGM never used | the data layer keeps only native sensor readings; the source's 1-minute rows are forward-interpolated and would encode future glucose |
| Splits by whole meal episode | 5-minute rows inside one response window are not independent |
| Response windows never overlap folds | the 120-minute embargo, asserted at run time by `_assert_day8_separation` |
| Folds disjoint, test eligibility enforced | same assertion; the run fails rather than reporting a contaminated number |
| Transforms fitted per fold | imputation medians, scaler statistics and the Ridge penalty are pipeline steps fitted on training rows only |
| Calibration from training meals only | per-participant offsets come from the training pool, all strictly before the cutoff |

## 7 · Regularization selection

The Ridge penalty is chosen by **MAE on the explicit validation fold** — the latest pre-cutoff slice of each participant's own pool. It is participant-aware and chronological within person.

A globally sorted `TimeSeriesSplit` is **not** used on real data: the organizer timestamps are date-shifted per participant (observed recording years here span 2019–2023), so a global sort is not a chronology at all and would silently degenerate into participant blocks.

**The test fold is never used for tuning, model selection, calibration or preprocessing.**

## 8 · Timestamp semantics

`time_basis = 'source_local_unknown_tz'`, `timezone = None`.

This dataset records offset-naive local wall-clock times with no stated timezone, and the dates are shifted. Time-of-day and within-participant elapsed time are exact, but the timestamps are ordering keys rather than real UTC instants, so they are not comparable across participants or with a real clock.

Time-of-day features are computed through the data layer's `local_minutes_of_day`, which reads the source wall clock directly for this basis and returns *missing* rather than guessing when the local clock is unknowable.

## 9 · Per-participant counts

| participant | train | validation | test | day-8 eligible | overlap-tagged |
|---|---|---|---|---|---|
| `cgmacros_001` | 25 | 6 | 8 | 8 | 4 |
| `cgmacros_002` | 22 | 6 | 6 | 6 | 7 |
| `cgmacros_003` | 18 | 5 | 9 | 9 | 0 |
| `cgmacros_004` | 30 | 8 | 11 | 11 | 11 |
| `cgmacros_005` | 24 | 6 | 10 | 10 | 6 |
| `cgmacros_006` | 20 | 5 | 10 | 10 | 2 |
| `cgmacros_007` | 9 | 0 | 0 | 0 | 0 |
| `cgmacros_008` | 17 | 4 | 8 | 8 | 0 |
| `cgmacros_009` | 24 | 6 | 10 | 10 | 4 |
| `cgmacros_010` | 26 | 6 | 10 | 10 | 5 |
| `cgmacros_011` | 22 | 6 | 9 | 9 | 1 |
| `cgmacros_012` | 18 | 4 | 10 | 10 | 0 |
| `cgmacros_013` | 21 | 5 | 10 | 10 | 2 |
| `cgmacros_014` | 21 | 5 | 11 | 11 | 3 |
| `cgmacros_015` | 26 | 7 | 11 | 11 | 11 |
| `cgmacros_016` | 13 | 3 | 8 | 8 | 0 |
| `cgmacros_017` | 26 | 7 | 10 | 10 | 9 |
| `cgmacros_018` | 19 | 5 | 13 | 13 | 4 |
| `cgmacros_019` | 25 | 6 | 10 | 10 | 8 |
| `cgmacros_020` | 24 | 6 | 9 | 9 | 5 |
| `cgmacros_021` | 20 | 5 | 9 | 9 | 2 |
| `cgmacros_022` | 24 | 6 | 10 | 10 | 4 |
| `cgmacros_023` | 24 | 6 | 11 | 11 | 2 |
| `cgmacros_026` | 18 | 4 | 10 | 10 | 4 |
| `cgmacros_027` | 19 | 5 | 8 | 8 | 1 |
| `cgmacros_028` | 18 | 5 | 9 | 9 | 1 |
| `cgmacros_029` | 17 | 4 | 8 | 8 | 0 |
| `cgmacros_030` | 15 | 4 | 8 | 8 | 0 |
| `cgmacros_031` | 31 | 8 | 8 | 8 | 17 |
| `cgmacros_032` | 12 | 3 | 5 | 5 | 0 |
| `cgmacros_033` | 18 | 5 | 9 | 9 | 0 |
| `cgmacros_034` | 30 | 8 | 8 | 8 | 11 |
| `cgmacros_035` | 16 | 4 | 7 | 7 | 0 |
| `cgmacros_036` | 17 | 4 | 8 | 8 | 0 |
| `cgmacros_038` | 22 | 6 | 10 | 10 | 5 |
| `cgmacros_039` | 20 | 5 | 8 | 8 | 2 |
| `cgmacros_041` | 28 | 7 | 11 | 11 | 5 |
| `cgmacros_042` | 14 | 3 | 5 | 5 | 1 |
| `cgmacros_043` | 34 | 8 | 10 | 10 | 12 |
| `cgmacros_044` | 24 | 6 | 9 | 9 | 5 |
| `cgmacros_045` | 18 | 4 | 8 | 8 | 1 |
| `cgmacros_046` | 17 | 4 | 8 | 8 | 3 |
| `cgmacros_047` | 17 | 4 | 6 | 6 | 0 |
| `cgmacros_048` | 19 | 5 | 11 | 11 | 2 |
| `cgmacros_049` | 16 | 4 | 7 | 7 | 0 |

## 10 · What is NOT claimed

- Every technically eligible meal is **not** an independent sample: meals from one participant share physiology, and consecutive meals share CGM context.
- No uncertainty interval is reported, because none has measured coverage.
- No causal physiological effect is claimed from any feature attribution.
