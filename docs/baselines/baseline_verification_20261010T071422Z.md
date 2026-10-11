# Baseline Verification Report — 20261010T071422Z

**Result: BASELINE VERIFIED.** All checks reproduce exactly.

| Item | Value |
|---|---|
| Branch | `ruhaan` |
| HEAD | `6a49ca32e955b29b141706e599cecac5fb44a9b8` |
| Working tree | clean (no modified, staged or untracked tracked-path files) |
| Contract | v1.0 (`contracts/openapi.yaml` + `schemas/` byte-identical to freeze `388d24e`) |
| Verified at (UTC) | 20261010T071422Z |

## 1 · Model artifact provenance

| Field | Value |
|---|---|
| Path | `ml/artifacts_cgmacros/model.joblib` (9,177 bytes) |
| SHA-256 | `e1d9a58793cb61c5a3849b9ceb8560c25470b7c719bd97f795ea15f0660074cc` |
| Matches manifest | **yes** — artifact unmodified since the baseline commit |
| model_version | `v0.1.0` |
| feature_set_version | `fs-1.1` (16 features, 8 optional) |
| contract_version | `1.0.0` |
| trained_at | `2026-10-10T06:39:56.771137Z` |
| data_source | `official` |
| holdout | `day8` — fitted on 1,171 episodes (938 train + 233 validation) |
| chosen_alpha | 30: 0.1 · 60: 100.0 · 90: 100.0 · 120: 100.0 |

> Note: `ml.evaluate` does **not** load this artifact. It refits from the training
> fold, which is the honest design — the artifact is the *deployed* model. The
> artifact's integrity is verified separately by checksum above.

## 2 · Dataset version

| Item | Value |
|---|---|
| Database | `data/local/cgmacros_v2.db` (568,381,440 bytes) |
| schema_version / storage_version | `1.0` / `3` |
| participants | 45 |
| cgm (native Dexcom G6 Pro) | 126,259 |
| meals | 1,698 |
| quarantine rows | 16 (8 distinct records × 2 idempotent runs) |
| ingestion runs | 90 (45 participant files × 2 runs), 45 distinct input SHA-256 |

## 3 · Test suite

```
$ python -m pytest -q
575 passed, 5 warnings in 297.36s
```

Matches the previously reported 575. Full output: `pytest_20261010T071422Z.log`.

## 4 · Day-8 evaluation reproduction

```
$ python -m ml.evaluate --source sqlite \
    --db-url sqlite:///data/local/cgmacros_v2.db \
    --protocol day8_holdout --out <fresh dir>
```

Wall time 67s. Written to a fresh directory; **baseline artifacts were not overwritten.**

| Model | MAE | Expected | RMSE | @30 | @60 | @90 | @120 |
|---|---|---|---|---|---|---|---|
| persistence | **32.58** | 32.58 ✓ | 45.70 | 23.83 | 36.68 | 36.34 | 33.45 |
| cohort mean | **28.08** | 28.08 ✓ | 38.15 | 20.22 | 29.72 | 31.65 | 30.74 |
| generalized | **26.47** | 26.47 ✓ | 35.93 | 18.23 | 27.77 | 30.66 | 29.24 |
| personalized | **23.93** | 23.93 ✓ | 32.31 | 17.00 | 24.69 | 27.49 | 26.56 |

Curve metrics (n = 394): peak MAE **26.69** · time-to-peak MAE **30.08 min** ·
iAUC MAE **1700.7 mg/dL·min** — all match.
Personalization: **35 improved / 9 worsened** — matches.

**Byte-level check.** With `evaluated_at` removed, the rerun report hashes
identically to the committed baseline:
`sha256 = a9330192046a2dbcfef770f19468a0df…` for both. Reproduction is exact, not
merely within rounding tolerance.

## 5 · Splits, safeguards and configuration — unchanged

| Item | Value | Same as baseline |
|---|---|---|
| protocol (internal / served) | `day8_holdout` / `within_person_chronological` | yes |
| fold sizes | train 938 · validation 233 · test 394 | yes |
| split_unit | `meal_episode` | yes |
| embargo | 120 min | yes |
| Day-8 rule | 7 d history · ≥70% coverage · overlap excluded from test | yes |
| validation_fraction | 0.20 | yes |
| personalization | min_episodes 4 · k 6.0 · cap 60 mg/dL | yes |
| random_seed | 20261011 | yes |
| exclusions | 1,698 candidates → 1,651 accepted, 47 excluded (1 incomplete macros, 20 insufficient pre-meal context, 26 insufficient target coverage), 246 overlap-tagged, 480 Day-8 eligible | yes |
| dataset_source | `official` | yes |

Leakage safeguards active and asserted at run time by `_assert_day8_separation`
(disjoint folds, embargo enforced, every test episode Day-8 eligible and
non-overlapping). No code, split, seed or eligibility rule was changed to obtain
this match.

## BASELINE VERIFIED
