# Het's accuracy experiments (CGMacros, real data)

A self-contained experiment, separate from `ml/`. It does **not** change the production
model, `ml/artifacts`, Contract v1.0, the API or the database schema. It reads
`data/glucotwin.db` and the CGMacros photos, and writes only to `data/local/het_experiments/`
(gitignored).

## Question

Can we beat the current approach (Ridge on feature set fs-1.0) on **real** CGMacros data,
and do the meal photos add accuracy?

## Setup

- **Data:** 1,652 meal episodes from 45 participants. Each needs a Dexcom baseline within
  15 min before the meal and readings within ±10 min of 30/60/90/120 min.
- **Same target as `ml/targets.py`:** the rise from baseline at each of the four horizons.
- **Evaluation protocols** (whole meals are the split unit; every fitted step, photo PCA
  included, uses the training fold only):
  - **new person:** 9 folds of 5 whole participants. The model has never seen the person
    it is predicting.
  - **Day-8:** each person's latest 25% of meals are held out, mirroring
    `ml/config.py EvaluationConfig`.
- **Leakage guards:**
  - every glucose, heart-rate and activity feature reads only data at or before the meal;
  - "personal response" features use only earlier meals whose 120-min window had already
    finished;
  - only the photo taken *at* the meal is used. The "after" photo, taken about 16 min
    later, is not, because it would not exist when a forecast is requested.
- **Photos:** a frozen CLIP ViT-B/32 (LAION-2B) model. Each photo becomes a 16-component PCA
  of its 512-d embedding, plus 22 zero-shot food-type scores (e.g. "white rice",
  "beans or lentils"). No image model is trained.
- **Model:** LightGBM, one model per horizon, with fixed hyperparameters (not tuned on test
  data).

## Results

MAE is the average prediction error on the glucose rise, in mg/dL (lower is better). The 95% CI
comes from resampling participants. "Within 20" is the share of predictions within 20 mg/dL of
the true value.

### Feature ablation

| model | new person MAE (95% CI) | within 20 | R² @60 | Day-8 MAE (95% CI) | within 20 | R² @60 |
|---|---|---|---|---|---|---|
| glucose stays flat | 33.6 (29.6–37.9) | 46% | −0.52 | 32.2 (28.4–36.7) | 46% | −0.58 |
| cohort mean rise | 29.4 (26.7–32.5) | 45% | −0.01 | 27.6 (25.3–30.6) | 48% | 0.00 |
| **current approach** (Ridge, fs-1.0) | 27.6 (25.3–30.4) | 48% | 0.13 | 26.8 (24.7–29.3) | 49% | 0.10 |
| LightGBM, same fs-1.0 features | 25.1 (22.5–27.8) | 54% | 0.23 | 24.2 (21.9–26.9) | 54% | 0.18 |
| + longer glucose context (3 h/24 h/7 d) and activity | 22.6 (20.5–25.1) | 57% | 0.37 | 21.3 (19.4–23.4) | 58% | 0.38 |
| **+ personal response history** | **21.7** (19.4–24.1) | 59% | 0.43 | 20.6 (18.6–22.6) | 60% | 0.44 |
| + labs (A1c, BMI, HOMA-IR, …) | 21.8 (19.6–24.2) | 58% | 0.43 | **20.3** (18.5–22.3) | 61% | 0.45 |
| + meal photo | 22.0 (19.8–24.5) | 59% | 0.42 | 20.5 (18.6–22.5) | 61% | 0.45 |

**Takeaways**
- Error is about 21–24% lower than the current approach.
- Explained variance (R² at 60 min) rises from about 0.1 to 0.45.
- The gains come from the model family (Ridge → LightGBM), longer glucose context and
  activity, and the person's own earlier meal responses.
- When macros are already logged, the photo adds nothing measurable.

### Photo instead of logging ("snap your meal")

In this scenario, every feature derived from logged macros is removed, for the current meal
and for earlier meals.

| scenario | new person MAE | Day-8 MAE |
|---|---|---|
| no meal info (glucose + time only) | 23.4 | 21.3 |
| **photo only, no logging** | **22.7** | **21.1** |
| logged macros | 21.7 | 20.6 |
| logged macros + photo | 21.9 | 20.8 |

**Takeaways**
- For a new person, a photo alone recovers about 40% of the benefit of logging macros by hand.
- For Day-8, the gain from both photos and macros is small. Most of the predictive signal is
  pre-meal glucose context and personal history, not meal composition.
- These photo differences are within the confidence intervals: treat them as suggestive, not
  proven.

## Reproduce

```bash
pip install -r requirements-dev.txt -r experiments/het/requirements.txt
# requires data/glucotwin.db and the extracted CGMacros folder (docs/data_setup.md)
python -m experiments.het.embed_photos              # ~7 min on an M-series Mac (CLIP download ~600 MB once)
python -m experiments.het.episodes                  # ~2 min -> episodes.parquet
python -m experiments.het.train_eval                # ~4 min, ablation tables
python -m experiments.het.train_eval --suite photo_only
```

## Not done here (Ruhaan owns `ml/`)

Porting the winning feature groups into `ml/features.py` is a feature-set version bump
(fs-1.0 → fs-1.1), and the model family change belongs in `ml/train.py`. Both are Ruhaan's
decision.
