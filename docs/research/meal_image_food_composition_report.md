# Meal Images and Food Composition — Final Research Report

**Owner:** Ruhaan Shah · **Branch:** `ruhaan` · **From:** `53a118e`
**Production model:** **fs-1.3, UNCHANGED** · **API contract:** v1.0, unchanged
**Environment:** 8 CPUs, 8 GB RAM, no CUDA GPU · CPU PyTorch 2.2.2, torchvision 0.17.2

## Bottom line

**Meal photographs do not improve glucose prediction here, and no candidate is
recommended for promotion.**

The photographs are real, plentiful and correctly linked — 3,454 images, 1,126 of
the 1,171 development episodes (96.2%) carry a photograph verified as taken at the
meal timestamp. The experiment was well-powered and it came back negative:

* **Across 19 configurations × 4 scope-and-regime combinations = 72 paired
  comparisons, ZERO have a favourable confidence interval excluding zero**, and
  21 are significantly *worse*.
* **Every single frozen-vision-embedding candidate has a negative mean effect**
  across the four combinations — ResNet-18 −0.253, EfficientNet-B0 −0.192,
  CLIP ViT-B/32 −0.205, and the wider PCA32 variant −0.336 mg/dL.
* The only candidate positive in all four combinations, **A8 enriched
  composition (mean +0.128)**, **uses no image data at all**. It is algebra on the
  macronutrients that fs-1.3 already receives — and it is still not significant.

The one branch the data simply cannot support is USDA enrichment, and that is a
property of CGMacros rather than a shortfall of effort: **the dataset contains no
food identifier of any kind.**

---

## Deliverable 1 · Authentic image inventory and checksums

Images were already extracted on disk; nothing needed restoring, and the archive
was not modified. Provenance verified:

| object | SHA-256 status |
|---|---|
| `CGMacros_dateshifted365.zip` | **matches** the published `SHA256SUMS.txt` |
| `DataDictionary.pdf` | **matches** the published `SHA256SUMS.txt` |
| archive contents | 91 directories, 48 CSVs, **3,454 JPGs** — and nothing else |

### Inventory checkpoint

```
IMAGE_COUNT:                      3454  (0.65 GB, all RGB, 0 corrupt)
LOGGED_MEALS:                     1706
MEALS_WITH_IMAGES:                1644  (96.4%)
MEALS_WITHOUT_IMAGES:               62
PARTICIPANTS_WITH_IMAGES:           45  (all of them)
UNIQUE_IMAGE_MEAL_LINKS:          1644
MULTI_IMAGE_MEALS:                   0  (exactly one photo per meal row)
VERIFIED_PREDICTION_TIME_IMAGES:  1633
UNCERTAIN_IMAGES:                   16
POST_PREDICTION_FOLLOWUP_IMAGES:  1548  (median +16 min AFTER the meal)
IMAGE_QUALITY:                    900x1600 (1743), 1152x2048 (831), 1153x2048 (170)
CORRUPT_FILES:                       0
EXACT_DUPLICATE_GROUPS:              3  (8 files)
IMAGES_ON_DISK_NOT_REFERENCED:     260
REFERENCED_BUT_MISSING_ON_DISK:      3
FOOD_TEXT_FIELDS:                 NONE
COMPOSITION_FIELDS:               Calories, Carbs, Protein, Fat, Fiber, Amount Consumed
ELIGIBLE_EPISODE_COUNT:           1126 of 1171 development episodes (96.2%)
LEAKAGE RISKS:                    see Deliverable 9
```

---

## Deliverable 2 · Exact image-to-meal linkage audit

**The linkage is read from the data, not inferred.** Each per-participant CSV has
an `Image path` column, and the warehouse carries it forward as `meals.image_ref`
(a `cgmacros://` URI). Linkage is therefore authoritative.

Photograph filenames also encode a capture time
(`00000005-PHOTO-2020-5-1-14-23-0.jpg`). That is used **only** to corroborate
timestamp evidence for the availability audit. **It is never a model feature** —
the leading sequence number correlates with study day and would be a pure
shortcut predictor. No EXIF, no directory name and no filename field enters any
candidate.

### The decisive timing finding

The data dictionary states that `Image Path` "indicates where the photo associated
with the **start or end** of this meal is located". The inventory confirms both
populations exist and separates them:

| class | n | treatment |
|---|---|---|
| **Verified at meal time** (filename time within 60 s of the meal timestamp) | **1,633** | the only images used |
| Uncertain timing | 16 | excluded |
| **Post-prediction follow-up** (median **+16 min** after the meal) | **1,548** | **excluded** |

The 1,548 follow-up photographs are excluded for two independent reasons: they
postdate the prediction, and they are the pictures of what was left on the plate
from which `Amount Consumed` was derived. Using them would be reading the future.

**`Amount Consumed` is never used as a feature** for the same reason — the
dictionary defines it as "% of meal consumed", which is only knowable after eating.

**An honest caveat I cannot engineer away:** the dictionary describes Calories,
Carbs, Protein, Fat and Fiber as quantities "in the **consumed** meal", so the
logged macros already reflect what was actually eaten. That is a pre-existing
property of CGMacros and of the task as specified — fs-1.3 has always received
these — not something this mission introduces. It is recorded here because it
bounds what "prediction-time" can mean for any macro-derived feature, A8 included.

---

## Deliverable 3 · Food-data schema and composition availability

**The complete meal-level schema**, confirmed across all 45 extracted
per-participant files and all 48 archive members:

```
Timestamp, Libre GL, Dexcom GL, HR, Calories (Activity)/Steps, METs,
Meal Type, Calories, Carbs, Protein, Fat, Fiber, Amount Consumed, Image path
```

| requested identifier | present? |
|---|---|
| Food names | **ABSENT** |
| Ingredient lists | **ABSENT** |
| Descriptions / free-text notes | **ABSENT** |
| Restaurant or packaged-food identifiers, brand, UPC | **ABSENT** |
| Food categories | **ABSENT** (only `Meal Type`, an occasion label) |
| Image annotations / label files | **ABSENT** — the archive holds only directories, CSVs and JPGs |
| Macronutrients, calories, serving amount | **PRESENT** (estimated) |

The identifier search was run over every column of every tabular file and every
archive member, with deliberately generous matching; the only hits were
microbiome pathway names and the data dictionaries' own "Description" column,
which describes *fields*, not foods.

### Phase Four determination: USDA matching is not possible

**`USDA_MATCHING_POSSIBLE: False` · `GI_ASSIGNMENT_POSSIBLE: False`**

A FoodData Central lookup requires a name, an ingredient description or a product
identifier. CGMacros has none. Deriving a food name from the photograph and then
treating the resulting USDA row as source composition would manufacture exactly
the ground truth the enrichment is supposed to supply, so **it was not done**. For
the same reason no glycaemic index is assigned: there is no identified food to
look one up for, and attaching a generic GI to an unidentified mixed meal would
be inventing a measurement.

**"Enriched composition" in this report therefore means algebraic transforms of
the source-observed macros only** — macronutrient fractions, available
carbohydrate (carbs − fibre), fat-to-carb and protein-to-carb ratios, fibre
fraction of carbohydrate, energy density, carbohydrate energy fraction. Nothing
is imported, nothing is estimated from an external table, nothing is fabricated.

---

## Deliverable 4 · Vision feature leaderboard

### Encoder provenance

| backbone | architecture | weights | licence | CPU |
|---|---|---|---|---|
| `resnet18` | ResNet-18 | torchvision IMAGENET1K_V1 | BSD-3-Clause (torchvision); weights trained on ImageNet-1k | cached ms/img |
| `efficientnet_b0` | EfficientNet-B0 | torchvision IMAGENET1K_V1 | BSD-3-Clause (torchvision); weights trained on ImageNet-1k | cached ms/img |
| `clip_vit_b32` | CLIP ViT-B/32 | open_clip laion2b_s34b_b79k | MIT (open_clip); weights trained on LAION-2B | cached ms/img |
| `random_resnet18` | ResNet-18 | NONE -- randomly initialised. CONTROL A2 ONLY. | n/a | cached ms/img |

Weights are the published checkpoints from their official hosts, downloaded and
frozen; none are trained here. Preprocessing comes from each checkpoint's own
transform rather than a reimplementation. Embeddings are cached by SHA-256 of the
image bytes (3454 images, 1,126 unique among the eligible set).
`random_resnet18` is an untrained backbone used **only** as control A2 and is
never described as pretrained.

**Sanity check that the encoder sees food at all:** over the 1,126 meal
photographs, 73.3% of ResNet-18 top-1 predictions fall inside the prespecified
ImageNet food groups, carrying mean probability mass 0.675 — versus **0.039** for
the random-weight control. Top classes are plausible foods (burrito, guacamole,
carbonara, espresso, mashed potato). The vision pipeline works; the signal just
does not help.

### FULL scope — the seven established folds, all 1,171 episodes

Cold-start:

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt | ppts worse |
|---|---|---|---|---|---|---|
| A0 fs-1.3 baseline | 23.085 | **ref** | — | — | — | — |
| E7b composition + Meal Type + food posteriors | 22.985 | +0.100 | [-0.125, +0.326] | no | -1.77 | 20/45 |
| A8 + enriched composition (source macros only) | 23.013 | +0.071 | [-0.096, +0.237] | no | -1.59 | 20/45 |
| A7 + source Meal Type one-hot | 23.060 | +0.025 | [-0.041, +0.087] | no | -0.49 | 20/45 |
| A1 + image-availability indicator | 23.093 | -0.008 | [-0.026, +0.007] | no | -0.26 | 25/45 |
| C5/E3 CLIP food-text similarity | 23.110 | -0.025 | [-0.167, +0.122] | no | -1.58 | 25/45 |
| E2 ImageNet food-class posteriors | 23.153 | -0.068 | [-0.204, +0.060] | no | -1.21 | 28/45 |
| E7 ResNet-18 + enriched composition | 23.183 | -0.098 | [-0.321, +0.119] | no | -1.64 | 24/45 |
| C4 ResNet-18 PCA8 | 23.198 | -0.113 | [-0.255, +0.034] | no | -1.45 | 25/45 |
| A4 CONTROL embeddings permuted among training meals | 23.199 | -0.114 | [-0.287, +0.051] | no | -2.12 | 25/45 |
| C + CLIP ViT-B/32 (PCA16) | 23.229 | -0.144 | [-0.383, +0.083] | no | -2.12 | 26/45 |
| E6 ResNet-18 + Meal Type | 23.248 | -0.164 | [-0.348, +0.018] | no | -1.92 | 23/45 |
| C6/E5 image-similar own-meal retrieval | 23.272 | -0.187 | [-0.357, -0.020] | **YES** | -2.07 | 29/45 |
| A3 + ResNet-18 embeddings (PCA16) | 23.321 | -0.236 | [-0.402, -0.073] | **YES** | -1.79 | 31/45 |
| C + EfficientNet-B0 (PCA16) | 23.326 | -0.241 | [-0.422, -0.058] | **YES** | -1.69 | 28/45 |
| A5 CONTROL embeddings permuted within participants | 23.434 | -0.349 | [-0.554, -0.148] | **YES** | -1.70 | 36/45 |
| A2 CONTROL random-weight backbone (PCA16) | 23.467 | -0.382 | [-0.575, -0.197] | **YES** | -1.82 | 35/45 |
| C4 ResNet-18 PCA32 | 23.497 | -0.412 | [-0.613, -0.214] | **YES** | -2.01 | 33/45 |
| A6 CONTROL macronutrients only (no CGM) | 28.725 | -5.640 | [-7.237, -4.256] | **YES** | -18.59 | 40/45 |

Within-person:

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt | ppts worse |
|---|---|---|---|---|---|---|
| A0 fs-1.3 baseline | 23.548 | **ref** | — | — | — | — |
| A8 + enriched composition (source macros only) | 23.338 | +0.210 | [-0.021, +0.445] | no | -2.06 | 17/45 |
| E7 ResNet-18 + enriched composition | 23.461 | +0.087 | [-0.338, +0.461] | no | -5.78 | 19/45 |
| E7b composition + Meal Type + food posteriors | 23.505 | +0.043 | [-0.239, +0.335] | no | -2.30 | 19/45 |
| C5/E3 CLIP food-text similarity | 23.534 | +0.013 | [-0.216, +0.210] | no | -2.46 | 21/45 |
| A1 + image-availability indicator | 23.549 | -0.001 | [-0.022, +0.021] | no | -0.19 | 23/45 |
| C6/E5 image-similar own-meal retrieval | 23.593 | -0.046 | [-0.275, +0.169] | no | -2.13 | 20/45 |
| A7 + source Meal Type one-hot | 23.606 | -0.059 | [-0.180, +0.064] | no | -0.84 | 26/45 |
| C + EfficientNet-B0 (PCA16) | 23.609 | -0.061 | [-0.423, +0.279] | no | -2.88 | 26/45 |
| C + CLIP ViT-B/32 (PCA16) | 23.693 | -0.146 | [-0.517, +0.159] | no | -4.70 | 23/45 |
| E2 ImageNet food-class posteriors | 23.701 | -0.154 | [-0.382, +0.074] | no | -1.78 | 23/45 |
| C4 ResNet-18 PCA8 | 23.722 | -0.174 | [-0.473, +0.131] | no | -2.20 | 26/45 |
| A3 + ResNet-18 embeddings (PCA16) | 23.767 | -0.219 | [-0.670, +0.184] | no | -6.90 | 24/45 |
| E6 ResNet-18 + Meal Type | 23.804 | -0.256 | [-0.675, +0.117] | no | -5.67 | 24/45 |
| A2 CONTROL random-weight backbone (PCA16) | 23.823 | -0.275 | [-0.525, -0.025] | **YES** | -3.17 | 31/45 |
| C4 ResNet-18 PCA32 | 23.860 | -0.312 | [-0.732, +0.066] | no | -5.97 | 26/45 |
| A5 CONTROL embeddings permuted within participants | 23.877 | -0.330 | [-0.569, -0.102] | **YES** | -2.43 | 28/45 |
| A4 CONTROL embeddings permuted among training meals | 23.948 | -0.400 | [-0.706, -0.103] | **YES** | -3.50 | 26/45 |
| A6 CONTROL macronutrients only (no CGM) | 27.437 | -3.889 | [-5.557, -2.480] | **YES** | -24.63 | 35/45 |

### COVERED scope — folds rebuilt on the 1,126 image-eligible episodes

Cold-start:

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt | ppts worse |
|---|---|---|---|---|---|---|
| A0 fs-1.3 baseline | 23.022 | **ref** | — | — | — | — |
| E7b composition + Meal Type + food posteriors | 22.915 | +0.107 | [-0.130, +0.343] | no | -1.42 | 20/45 |
| A8 + enriched composition (source macros only) | 22.924 | +0.098 | [-0.061, +0.257] | no | -0.93 | 20/45 |
| C5/E3 CLIP food-text similarity | 22.974 | +0.048 | [-0.105, +0.201] | no | -1.44 | 20/45 |
| A7 + source Meal Type one-hot | 22.975 | +0.047 | [-0.017, +0.113] | no | -0.60 | 19/45 |
| A1 + image-availability indicator | 23.022 | -0.000 | [-0.000, +0.000] | no | +0.00 | 0/45 |
| C4 ResNet-18 PCA8 | 23.055 | -0.033 | [-0.181, +0.110] | no | -1.73 | 19/45 |
| E2 ImageNet food-class posteriors | 23.060 | -0.038 | [-0.185, +0.104] | no | -1.48 | 26/45 |
| C + CLIP ViT-B/32 (PCA16) | 23.074 | -0.052 | [-0.281, +0.155] | no | -1.99 | 23/45 |
| E7 ResNet-18 + enriched composition | 23.089 | -0.067 | [-0.283, +0.155] | no | -1.83 | 20/45 |
| A4 CONTROL embeddings permuted among training meals | 23.100 | -0.078 | [-0.255, +0.079] | no | -1.61 | 25/45 |
| E6 ResNet-18 + Meal Type | 23.127 | -0.105 | [-0.293, +0.073] | no | -2.34 | 22/45 |
| A3 + ResNet-18 embeddings (PCA16) | 23.185 | -0.163 | [-0.334, +0.002] | no | -1.98 | 30/45 |
| C6/E5 image-similar own-meal retrieval | 23.189 | -0.167 | [-0.346, +0.002] | no | -2.97 | 25/45 |
| C + EfficientNet-B0 (PCA16) | 23.214 | -0.192 | [-0.367, -0.020] | **YES** | -2.01 | 27/45 |
| A2 CONTROL random-weight backbone (PCA16) | 23.286 | -0.264 | [-0.446, -0.082] | **YES** | -1.83 | 29/45 |
| C4 ResNet-18 PCA32 | 23.294 | -0.272 | [-0.474, -0.068] | **YES** | -2.60 | 30/45 |
| A5 CONTROL embeddings permuted within participants | 23.316 | -0.294 | [-0.495, -0.103] | **YES** | -2.29 | 29/45 |
| A6 CONTROL macronutrients only (no CGM) | 28.723 | -5.701 | [-7.313, -4.279] | **YES** | -18.33 | 40/45 |

Within-person:

| candidate | MAE | Δ | 95% CI | excl 0 | worst ppt | ppts worse |
|---|---|---|---|---|---|---|
| A0 fs-1.3 baseline | 23.363 | **ref** | — | — | — | — |
| C5/E3 CLIP food-text similarity | 23.210 | +0.153 | [-0.023, +0.322] | no | -1.80 | 18/45 |
| C6/E5 image-similar own-meal retrieval | 23.224 | +0.139 | [-0.021, +0.292] | no | -1.55 | 16/45 |
| A8 + enriched composition (source macros only) | 23.228 | +0.135 | [-0.092, +0.344] | no | -2.07 | 17/45 |
| A4 CONTROL embeddings permuted among training meals | 23.349 | +0.014 | [-0.255, +0.254] | no | -2.02 | 20/45 |
| A1 + image-availability indicator | 23.363 | +0.000 | [-0.000, +0.000] | no | +0.00 | 0/45 |
| A7 + source Meal Type one-hot | 23.396 | -0.033 | [-0.156, +0.082] | no | -0.88 | 20/45 |
| E7b composition + Meal Type + food posteriors | 23.401 | -0.037 | [-0.290, +0.209] | no | -1.45 | 23/45 |
| E2 ImageNet food-class posteriors | 23.516 | -0.153 | [-0.330, +0.022] | no | -1.63 | 27/45 |
| E7 ResNet-18 + enriched composition | 23.517 | -0.154 | [-0.621, +0.242] | no | -4.71 | 24/45 |
| A2 CONTROL random-weight backbone (PCA16) | 23.541 | -0.178 | [-0.495, +0.116] | no | -3.52 | 27/45 |
| A5 CONTROL embeddings permuted within participants | 23.626 | -0.263 | [-0.476, -0.065] | **YES** | -2.38 | 31/45 |
| C + EfficientNet-B0 (PCA16) | 23.638 | -0.275 | [-0.632, +0.071] | no | -3.08 | 27/45 |
| C4 ResNet-18 PCA8 | 23.703 | -0.340 | [-0.662, -0.031] | **YES** | -3.47 | 28/45 |
| C4 ResNet-18 PCA32 | 23.712 | -0.349 | [-0.842, +0.079] | no | -5.93 | 25/45 |
| A3 + ResNet-18 embeddings (PCA16) | 23.758 | -0.395 | [-0.876, +0.016] | no | -5.58 | 25/45 |
| C + CLIP ViT-B/32 (PCA16) | 23.842 | -0.479 | [-0.830, -0.152] | **YES** | -4.10 | 34/45 |
| E6 ResNet-18 + Meal Type | 23.862 | -0.498 | [-0.966, -0.082] | **YES** | -4.90 | 28/45 |
| A6 CONTROL macronutrients only (no CGM) | 27.496 | -4.133 | [-5.842, -2.718] | **YES** | -24.72 | 38/45 |

---

## Deliverable 5 · Food-composition feature leaderboard

No USDA features exist (Deliverable 3), so this is the source-derived
food-information family, ranked by consistency of sign across all four
scope-and-regime combinations:

| candidate | covered/cold | covered/within | full/cold | full/within | positive in | mean Δ |
|---|---|---|---|---|---|---|
| **A8 enriched composition (no images)** | +0.098 | +0.135 | +0.071 | +0.210 | **4/4** | **+0.128** |
| E7b composition + Meal Type + food posteriors | +0.107 | −0.037 | +0.100 | +0.043 | 3/4 | +0.053 |
| C5/E3 CLIP food-text similarity | +0.048 | +0.153 | −0.025 | +0.013 | 3/4 | +0.047 |
| A7 source Meal Type one-hot | +0.047 | −0.033 | +0.025 | −0.059 | 2/4 | −0.005 |
| A1 image-availability indicator | +0.000 | +0.000 | −0.008 | −0.001 | 0/4 | −0.002 |
| E2 ImageNet food-class posteriors | −0.038 | −0.153 | −0.068 | −0.154 | 0/4 | −0.103 |
| A6 CONTROL macronutrients only | −5.701 | −4.133 | −5.640 | −3.889 | 0/4 | −4.841 |

**A8 is the only candidate with a consistent sign anywhere in this mission**, and
it contains no image information. Its best interval is covered/cold-start
+0.098 **[−0.061, +0.257]** — which includes zero. Full metrics there: MAE 22.924
vs 23.022, RMSE 31.217 vs 31.335, bias −0.247 vs −0.252, peak 25.079 vs 25.398,
ttp 29.547 vs 29.120 (worse), iAUC 1617.95 vs 1632.53, worst participant −0.93,
20 of 45 participants worse, 11.4 s fit.

**A6 is the interpretive yardstick and it is the most informative number in the
table.** Macronutrients alone, with no glucose history, lose **3.9 to 5.7 mg/dL**.
The glucose signal carries this problem; the food signal is a small correction on
top, and the logged macros already deliver essentially all of it.

---

## Deliverable 6 · Image shuffling and negative-control results

Two distinct shuffles were run, and they destroy different relationships:

* **A4 — permute the embedding→meal assignment among the fold's TRAINING meals,
  across participants.** Destroys *which photograph belongs to which meal*.
  Validation keeps its true photographs, so a candidate that still wins was not
  using genuine image-meal correspondence.
* **A5 — permute WITHIN each participant's own training meals.** Destroys *which
  of that person's meals* a photograph shows while preserving *whose* meal it is,
  separating meal-specific image content from participant-identity content.
* **A2 — identical architecture and preprocessing, untrained weights.** Destroys
  the pretrained representation while holding everything else fixed.

| combination | real (A3) | random weights (A2) | permuted among meals (A4) | permuted within participant (A5) |
|---|---|---|---|---|
| covered / cold-start | −0.163 | −0.264 | **−0.078** | −0.294 |
| covered / within-person | −0.395 | −0.178 | **+0.014** | −0.263 |
| full / cold-start | −0.236 | −0.382 | **−0.114** | −0.349 |
| full / within-person | **−0.219** | −0.275 | −0.400 | −0.330 |

**Scrambling the image-to-meal correspondence BEATS the real correspondence in
three of the four combinations** — including both covered-scope settings, where
every episode has a photograph. In covered/within-person the scrambled control is
the only one of the four that is positive at all (+0.014 against real embeddings'
−0.395, a 0.409 mg/dL gap in the wrong direction).

Real embeddings do beat the random-weight backbone in three of four, so the
pretrained representation is not worthless — the ImageNet food recognition is
genuine. But A5 (within-participant shuffle) is consistently the worst or near
worst, which says the part of the embedding that survives participant
normalisation is the part that hurts. The overall reading is that these embeddings
carry scene and participant-style variance (kitchen, plate, lighting) that costs
more than whatever food information they add.

---

## Deliverable 7 · Within-person and cold-start comparisons

Both regimes were run under both scopes; all four tables are in Deliverable 4.
The structural findings:

* **No candidate is favourable-and-significant in any regime or scope.** 0 of 72.
* Cold-start is more forgiving of image features than within-person in the covered
  scope (e.g. A3 −0.163 vs −0.395), the opposite of the pattern biological
  metadata showed, because image embeddings are *meal*-level rather than
  participant-level and so cannot act as an identity label — they add noise
  instead.
* The 21 significantly-worse results are concentrated in the embedding families
  and the macros-only control.
* A1, the image-availability indicator, is **exactly 0.000** in the covered scope,
  as it must be — in that scope the column is constant. An internal consistency
  check that passed.

---

## Deliverable 8 · Per-participant deterioration analysis

| candidate | worst-participant Δ (covered/cold) | participants worse |
|---|---|---|
| A7 source Meal Type | −0.60 | 19/45 |
| A8 enriched composition | −0.93 | 20/45 |
| E7b composition + Meal Type + posteriors | −1.42 | 20/45 |
| C5/E3 CLIP text similarity | −1.44 | 20/45 |
| E2 food posteriors | −1.48 | 26/45 |
| A3 ResNet-18 embeddings | −1.98 | — |
| C6/E5 image-similar retrieval | −2.97 | — |
| A6 macros only | −18.33 | — |

Within-person, the embedding families are considerably more damaging: A3 reaches
**−6.90** on its worst participant in the full scope and E6 reaches **−5.67**.
Even the best-behaved candidate, A8, makes 20 of 45 participants worse — i.e. its
+0.098 average is a near-even split, not a broad gain.

### Image-missing fallback

45 of 1,171 episodes (3.8%) have no eligible photograph, spread over **19 of 45
participants** (worst: `cgmacros_011` with 6). This population is **not random**:

| | covered | missing |
|---|---|---|
| n | 1,126 | 45 |
| median carbs | **65.0 g** | **31.0 g** |
| median pre-meal glucose | 117.0 | 122.0 |
| median 60-min response | **25.0 mg/dL** | **12.0 mg/dL** |

The unphotographed meals are smaller, lower-carbohydrate and lower-response —
plausibly snacks people did not bother photographing. **This is precisely the
"easier subset" trap the mission warns about, which is why both scopes were
evaluated and fs-1.3 was re-scored on exactly the 1,126 covered meals.** No
candidate is credited for a cohort change.

Note that **A8, the only consistently positive candidate, uses no image data at
all**, so it has no image-missing fallback path that could fail — it is defined on
every episode.

---

## Deliverable 9 · Model-selection and leakage audit

| vector | control |
|---|---|
| **Post-meal photographs** | The 1,548 follow-up images (median +16 min) are excluded. Only the 1,633 images whose capture time matches the meal timestamp within 60 s are eligible. |
| **`Amount Consumed`** | Never used. It is the "% of meal consumed", derived from the end-of-meal photograph. |
| **Filename / path / EXIF shortcuts** | Filenames are used only to corroborate timing in the audit, never as features. No EXIF, directory name or sequence number enters any candidate. |
| **Transform fitting** | Every PCA is fitted on the fold's training episodes only (`research/vision/features.py::Pca`), as is every median imputation. |
| **Encoder selection** | Backbones were fixed before scoring and none was chosen using held-out glucose. The ImageNet food-class groups and the CLIP prompts were both written down before any validation run. |
| **No manual food labelling** | No image in this dataset was hand-labelled. Food-category features are the pretrained classifier's own posteriors and CLIP's own zero-shot similarities. |
| **Retrieval causality** | C6/E5 admits an earlier meal only if its whole 120-minute response window elapsed strictly before the query meal — the same filter fs-1.3 uses — and never admits the query meal itself. |
| **Cohort change** | Both scopes evaluated; fs-1.3 re-scored on the exact covered meals. |
| **Reserved test set** | The **394-episode reference set was never loaded.** Every run prints "394 test episodes reserved and NOT loaded". |
| **Raw data** | Archive and `DataDictionary.pdf` both match the published SHA-256. Images read in place; the embedding cache lives in gitignored `data/local/vision/`. |

### Multiplicity

| | |
|---|---|
| Comparisons this mission | **72** (19 configurations × 4 scope-and-regime combinations) |
| Favourable CIs excluding zero | **0** |
| Significantly worse | **21** |

With zero favourable intervals there is no multiplicity argument to make and none
is attempted. A8's consistency across four combinations is suggestive, but four
correlated views of the same 1,171 episodes are not four independent experiments,
and its intervals include zero in all of them.

---

## Deliverable 10 · Best candidate recommendation

**NO PROMOTION. Retain fs-1.3.**

Against the nine promotion rules:

| rule | A8 (best candidate) |
|---|---|
| 1. Improves fs-1.3 on paired episodes | Yes, nominally: +0.071 to +0.210 |
| 2. **Practically meaningful** | **NO — 0.098 mg/dL cold-start is 0.4% of a 23 mg/dL error** |
| 3. Reasonable cold-start generalisation | Yes, positive in both regimes |
| 4. **Survives controls** | Partially: it uses no images, so image shuffles do not apply — but its interval includes zero everywhere |
| 5. No leakage | Yes, with the caveat that the logged macros describe the consumed meal |
| 6. Stable with missing images | Yes — needs no images |
| 7. **Complexity justified by gain** | **NO — nine extra columns for an effect indistinguishable from zero** |
| 8. API-contract compatible | Yes — derived entirely from fields the contract already carries |
| 9. No hidden future-image access | Yes |

It fails rules 2, 4 and 7. Every image-based candidate additionally fails rule 1.

**If a future mission revisits this**, A8 is the thing to revisit — it is cheap,
needs no images, no new API field and no external data, and it was positive in
all four views. It is simply not supported by this evidence today.

---

## Deliverable 11 · Updated cumulative configuration count

| | configurations |
|---|---|
| Prior missions (reported at `53a118e`) | ~202 |
| This mission (Phases One, Two, Three, Five) | **19** |
| **NEW CUMULATIVE TOTAL** | **~221** |

Each of the 19 was scored under 4 scope-and-regime combinations, adding 72 paired
comparisons. Every attempted candidate is recorded in
`research/vision/results/vision_leaderboard.csv` with its full configuration —
nothing was run and discarded.

---

## Deliverable 12 · Exact reproduction commands

```bash
# Phase Zero -- inventory, linkage and prediction-time audit
python -m research.vision.inventory
python -m research.vision.food_identifier_audit

# Frozen embeddings (cached by image content hash; needs certifi CA bundle
# because the sandboxed interpreter cannot verify TLS without it)
export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")
for b in resnet18 efficientnet_b0 clip_vit_b32 random_resnet18; do
  python -m research.vision.encoders --backbone $b
done

# Phases One / Two / Three / Five
python -m research.vision.runner --db-url sqlite:///data/local/cgmacros_v2.db

python -m pytest -q
```

New dependencies installed for this mission: `pillow`, `torchvision==0.17.2`,
`open_clip_torch`. Results land in `research/vision/results/`.

**Runtime and memory:** inventory ~90 s (3,454 images verified); embedding 1,126
images costs 50.8 ms/img (ResNet-18), 117.0 ms/img (EfficientNet-B0), 52.8 ms/img
(CLIP ViT-B/32) on CPU — ~4 minutes total, cached thereafter; the experiment
itself ~20 minutes. Peak RAM well under 8 GB. Downstream model complexity is
unchanged from fs-1.3 except for 8–32 appended columns.

---

## Deliverable 13 · Commit SHA and passing test count

Recorded in the commit message accompanying this report, which also carries the
full-suite pass count and the production-integrity verification.

---

## Failed hypotheses

| hypothesis | outcome |
|---|---|
| Meal photographs contain glucose-relevant food information beyond logged macros | **REJECTED.** Every embedding family negative in mean; 0 of 72 comparisons favourable-and-significant |
| A food-domain or larger backbone would do better | **REJECTED** across three independent backbones (ResNet-18, EfficientNet-B0, CLIP ViT-B/32) and three PCA widths |
| Pretrained food-class recognition gives usable categories | **REJECTED.** E2 negative in all four combinations, despite 73.3% top-1 food recognition |
| CLIP zero-shot food-form similarity (liquid/solid, refined/whole) helps | **NOT SUPPORTED.** Positive in 3 of 4 but no interval excludes zero |
| "This meal looks like that meal" retrieval helps | **REJECTED.** C6/E5 −0.167 covered/cold-start, worst participant −2.97 |
| Vision plus source categories or composition is complementary | **REJECTED.** E6 and E7 both worse than their non-vision halves |
| USDA FoodData Central enrichment | **UNSUPPORTED BY THE DATA** — no food identifier exists |
| Glycaemic index / load features | **UNSUPPORTED BY THE DATA** — no identified food |
| Source food categories beyond meal occasion | **UNAVAILABLE** — `Meal Type` is the only categorisation and it is a timing label |
| Multi-image aggregation per meal | **MOOT** — exactly one photograph per meal row; 0 multi-image meals |
| Enriched composition from source macros | **NOT PROMOTED.** The only candidate positive in all four combinations (+0.128 mean) but every interval includes zero |

---

## Final answer to the scientific question

*Do food identity and food composition contain meaningful predictive information
beyond the existing glucose and macronutrient signals?*

**On this dataset, no.** Food *identity* could not be tested as such, because
CGMacros records none — and the photographic proxy for it, across three pretrained
backbones and six feature constructions, was consistently harmful rather than
merely unhelpful. Food *composition* beyond the logged macros does not exist in the
source; the closest available thing, algebra on those macros, produced the only
consistently positive effect in the mission and it was still indistinguishable
from zero.

The macros-only control explains why: strip the glucose history and error rises
3.9–5.7 mg/dL. This problem is carried by the glucose signal, and the five logged
macronutrients already supply essentially all of the food-level correction that
45 participants and 1,171 episodes can support.
