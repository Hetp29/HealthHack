# Four-Minute Demo Script

**Owner:** Ruhaan Shah · **Hard limit: 4:00** · **Contract:** v1.0 · **Model:** `v0.1.0`

Every number below was produced by the running system and is reproducible with the
commands in §Setup. If a number changes after a retrain, **re-run and update this file**
— do not narrate a stale figure.

> **Non-negotiable framing.** The data is synthetic. Say so out loud, once, early. Never
> claim external validation, clinical utility, or that the system can diagnose, dose, or
> replace care. The honesty is a scoring asset, not a liability — it is the difference
> between a prototype and a demo.

---

## Setup — run before recording

```bash
source .venv/bin/activate
python -m ml.train    --source synthetic_dev
python -m ml.evaluate --protocol all
uvicorn backend.app.main:app --port 8000      # terminal 1
cd frontend && npm run dev                    # terminal 2
```

Checklist:

- [ ] `curl localhost:8000/health` → `{"status":"ok","version":"1.0.0"}`
- [ ] `/api/model/info` shows `model_version: v0.1.0`, not `"none"`
- [ ] `/api/model/metrics` returns populated `by_model`
- [ ] Browser window sized for the recording; **terminal logs hidden**
- [ ] `python -m pytest -q` → 385 passed (screenshot for the deck)
- [ ] Backup recording saved **outside** any temporary AWS account

**The demo meal** — the same meal for both participants:
`85 g carbs · 24 g protein · 18 g fat · 7 g fiber · 600 kcal` at
`2026-10-03T12:30:00Z`.

---

## 0:00 – 0:25 · The problem

> "Two people eat the identical meal. One's glucose rises 72 mg/dL. The other's rises
> 21. Same carbs, same fat, same fiber — more than a three-fold difference in response.
>
> That is why population-average nutrition advice fails individuals. 'Eat less pasta'
> is right on average and wrong for the specific person in front of you. To give
> someone useful guidance you have to predict *their* response, not the average one."

**Screen:** two curves from the same meal, overlaid.

---

## 0:25 – 0:45 · What GlucoTwin is

> "GlucoTwin takes a person's continuous glucose history and a meal's macros, and
> forecasts the next two hours: the value at 30, 60, 90 and 120 minutes, the full
> five-minute curve, the peak and when it arrives, the two-hour excursion — and which
> inputs drove that prediction.
>
> Three models, each a step we can prove: glucose persistence as the floor, a
> cohort model that learns meal response, and a personal calibration layer on top. Built
> on synthetic development data, because the organizer dataset had not been released."

---

## 0:45 – 2:05 · Live prediction *(the core — 80 seconds)*

**Do not narrate a screenshot. Click through the real app.**

### Select `synthetic_p002` (0:45 – 1:00)

> "Seven days of five-minute CGM, meals, heart rate. Source label says
> `synthetic_dev` — it's on screen on every view, so you always know what you're
> looking at."

**Screen:** history dashboard, meal markers, provenance banner.

### Enter the meal and predict (1:00 – 1:30)

> "85 grams of carbs, 24 protein, 18 fat, 7 fiber. Predict."

**Real output, `synthetic_p002`:**

| | |
|---|---|
| Pre-meal baseline (measured) | **144.4 mg/dL** |
| 30 / 60 / 90 / 120 min | **190.6 / 214.7 / 216.7 / 205.6 mg/dL** |
| Peak | **216.7 mg/dL**, +**72.3** above baseline |
| Time to peak | **90 min** |
| 2-hour incremental AUC | **6581 mg/dL·min** |
| Status | `personalized` |

> "Minute zero isn't a prediction — it's their actual last CGM reading. The curve is
> anchored in measurement. And if there were no reading close enough before the meal,
> the API refuses the request rather than inventing a starting point."

### The explanation (1:30 – 1:50)

> "Why this curve? These aren't generated sentences — for this model the decomposition
> is exact: the intercept plus these contributions *equals* the prediction, to
> floating-point precision. We assert it in the test suite."

**Real drivers at the 90-minute peak horizon:**

| Feature | Contribution |
|---|---|
| `carbs_g` | **+11.21 mg/dL** |
| `participant_calibration` | **+8.78 mg/dL** |
| `cgm_std_60min_mg_dl` | +7.16 mg/dL |
| `minutes_since_prior_meal` | −6.03 mg/dL |

> "The second line is the point of the project. +8.78 mg/dL is *this person's own
> correction*, learned from their earlier meals. And note what the panel says: these are
> model attributions, not causal physiology."

### Same meal, different person (1:50 – 2:05)

> "Now the identical meal for `synthetic_p003`."

| | `synthetic_p002` | `synthetic_p003` |
|---|---|---|
| Baseline | 144.4 | 100.8 |
| Peak | **216.7** | **122.1** |
| Peak delta | **+72.3** | **+21.3** |
| Time to peak | 90 min | 60 min |
| iAUC | **6581** | **1679** |

> "Same meal. 72 versus 21 mg/dL. Nearly four times the excursion. That difference is
> the entire product."

---

## 2:05 – 2:50 · Validation *(45 seconds — do not rush this)*

**Screen:** Model Insights, served live from `/api/model/metrics`.

> "Held-out meals, split chronologically — we train on each person's earlier meals and
> test on their later ones. Splits are by whole meal, never by CGM row, because
> five-minute rows inside one response window aren't independent."

| Model | MAE (mg/dL) |
|---|---|
| Persistence (glucose doesn't move) | 24.91 |
| Cohort mean excursion | 17.88 |
| Generalized model | **9.07** |
| Personalized model | **8.75** |

> "Two baselines deliberately. Beating persistence only shows we know a meal raises
> glucose. Beating the cohort *mean* — which ignores the meal entirely — is what shows we
> know something about *this* meal. We roughly halve it. Peak error 8.4 mg/dL,
> two-hour AUC error 656."

**The honesty beat — say this, it is the strongest 15 seconds in the deck:**

> "Personalization is a *mixed* result and we report it that way. Three participants
> improved, two got worse. The best gain was 2.2 mg/dL; the worst regression was 1.5.
> And time-to-peak is our weak metric — about 20 minutes of error, where the dumb
> cohort-mean benchmark actually beats us. That's structural: we learn four knots, and a
> piecewise-linear curve can only peak *at* a knot. More knots would fix it. We haven't
> measured that, so we don't claim it."

> "Also: no uncertainty bands. We could draw one in five minutes. With 33 held-out
> meals we can't *measure* its coverage, so the API returns null bounds and the UI says
> 'uncertainty not calibrated.' Every number on this screen was computed by a command
> you can run from the repo."

---

## 2:50 – 3:20 · Architecture

**Screen:** `docs/architecture.md` diagram.

> "React front end, FastAPI service, scikit-learn models, SQLite behind an abstracted
> data layer. Three of us built in parallel against a dataset that hadn't shipped, which
> worked because there are exactly two seams: a frozen OpenAPI contract with generated
> JSON Schemas, and a five-method data-repository protocol. Both are version 1.0 and
> both are tested — the suite fails if the generated schemas drift from the contract.
>
> 385 tests. Twenty-two of them exist only to attack the leakage guarantee: they bury
> 400 mg/dL post-meal readings in the history and assert the feature vector doesn't
> move."

**On cloud — the exact words, and no more:**

> "It runs locally today, fully reproducible from a clean clone. The container
> definition is written. We haven't deployed to AWS: we chose to verify what the
> workshop account actually permits before building on it, rather than claim an
> architecture we hadn't run."

*(If a deployment is live and verified by recording time, replace that paragraph with
the real URL and services. Never otherwise.)*

---

## 3:20 – 3:50 · Limits and where it goes

> "What this is: a leakage-safe, reproducible pipeline that predicts meal response and
> explains itself, with a falsifiable personalization comparison.
>
> What it is not: validated. The data is synthetic — we wrote those response curves, so
> the model has shown it can learn a smooth relationship from pre-meal context, not that
> it has learned human metabolism. Six participants, 135 meals, one week. No prospective
> validation. It gives no dosing, no diagnosis, no treatment advice.
>
> What it's built for: the organizer dataset drops into the same adapter, the
> provenance label flips from synthetic to official, and every number on the metrics
> screen regenerates. The honest path to real value is a clinician using a *validated*
> version of this to explain to a patient why their response differs — and knowing when
> the model is guessing, because the model says so."

---

## 3:50 – 4:00 · Close

> "GlucoTwin. Personalized glucose response forecasting that shows its work, reports its
> failures, and tells you when it doesn't know. Code, contracts and the full validation
> report are in the repo."

---

## Delivery notes

| | |
|---|---|
| **Longest segment** | Live prediction (80 s). If overrunning, cut architecture to 20 s — never cut validation. |
| **Rehearse** | §2:05–2:50 twice. The mixed personalization result is the differentiator; delivered hesitantly it sounds like a weakness. |
| **Fallback** | If the live app fails, switch to the pre-recorded 45 s clip and keep talking. Have it open in a tab. |
| **Forbidden words** | "validated", "accurate", "clinically proven", "diagnose", "recommend", "insulin". |
| **Required words** | "synthetic", "held-out", "not medical advice". |

### If asked: how do you know there's no leakage?

> "Three ways. Features are built from a history object truncated at the meal timestamp
> — future readings are *absent*, not just unused, so a window bug can't reach forward.
> The chronological split is asserted at run time and the assertion is itself tested
> against a deliberately bad split. And twenty-two adversarial tests inject extreme
> post-meal values and require the feature vector to be byte-identical."

### If asked: why not deep learning?

> "135 meal episodes and 16 features. A neural network or a boosted ensemble would fit
> noise, and we'd lose the exact additive attribution that makes the explanation
> trustworthy. We chose the model the sample size supports. If the organizer dataset is
> large enough, the switch gets made on held-out performance — not on how it sounds."

### If asked: is the personalization gain real?

> "At this sample size, honestly: it's suggestive, not established. Aggregate MAE
> improves 9.07 to 8.75, three of five participants improved, two got worse. We report
> the per-participant table rather than the headline average precisely so you can see
> that. With six participants we can't claim significance, and we don't."
