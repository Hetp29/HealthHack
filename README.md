# GLIMPSE

### Glucose Learning through Integrated Multimodal Prediction and Sensor Evidence

**See beyond the reading. Understand the response.**

**Rutgers HealthHack 2026 · Challenge 3 — Predict My Glucose Response**

GLIMPSE is an AWS-deployed metabolic-health research prototype that brings **two-hour glucose forecasting**, **adaptive personalization from meal-response history**, **AI-assisted food logging**, and **integrated health-data visualization** into one responsive application.

A user can photograph a meal, review and correct estimated macronutrients, request a genuine model-generated glucose forecast, and explore what the forecast means. Two built-in demonstration journeys make the complete experience explorable without owning a CGM or granting access to a personal health account.

> **Research and demonstration use only.** GLIMPSE is not a medical device and is not validated for diagnosis, treatment, insulin dosing, or clinical decision-making. Historical research CGM recordings and simulated wearable/profile data are identified as such. Forecast uncertainty is not clinically calibrated.

**Links:** [Source repository](https://github.com/Hetp29/HealthHack) · [Release runbook](docs/release/RUNBOOK.md) · [Technical documentation](docs/project/) · [Model validation](docs/validation.md)

> **Live application:** AWS deployment is reported complete. Add the final *verified public HTTPS URL* here before submission. Do not substitute a local development address.

---

## Judge demo access

**No CGM device or personal health records are required.** Open the GLIMPSE application using the verified AWS HTTPS URL (add it in the **Live application** field above before submission), choose a demo scenario on the sign-in page, and use the credentials below.

| Demo scenario | Sign-in email | Password |
|---|---|---|
| **New-user demo** — watch personalization become active as historical meals accumulate | `demo@glucotwin.example` | `fz0FdExPBqwyLAVySavf` |
| **Returning-user demo** — explore an account with history-based personalization already active | `demo-returning@glucotwin.example` | `fz0FdExPBqwyLAVySavf` |

**Suggested judge walkthrough:** Sign in → inspect Home → open Meals → view a genuine forecast → advance Demo Mode to reveal an observed historical outcome → explore Insights and simulated device connections. For a photo demonstration, use a photo you own, inspect/edit the nutrition estimates, confirm, and generate a forecast.

These are intentionally public **demo-only credentials**. The scenarios use historical research glucose and simulated wearable/profile data, not personal patient accounts. **Never reuse this password for administrator access or private user accounts.** See the [full demo instructions](#demo-account-access) below.

---

## The challenge

Glucose is personal. Two individuals may eat similar meals yet experience different responses; one individual's response also changes with meal composition, timing, and baseline conditions. CGMs reveal what has happened, but a stream of readings alone does not answer the practical question: **“How might my glucose respond to this meal?”**

GLIMPSE addresses four related gaps:

1. **From observation to prediction:** Forecast the full post-meal trajectory, rather than display only retrospective glucose readings.
2. **From generic to personal:** Use eligible prior meal-response history to adapt forecasts to the individual's observed patterns.
3. **From friction to understanding:** Convert a meal photograph into an editable nutritional estimate, explicitly confirmed before inference.
4. **From fragmented dashboards to one experience:** Present glucose, food, activity, heart-rate, sleep, medication, and alert information in a coherent application, with transparent data provenance.

## What the prototype does

| Capability | Implemented experience |
|---|---|
| **Glucose forecasting** | Frozen, real ML inference yielding a 25-point trajectory over 0–120 minutes; 30/60/90/120-minute estimates and forecast summaries |
| **Adaptive personalization** | Retrieval of the user's eligible, completed historical meal responses; enabled once five qualifying earlier meals exist |
| **AI meal analysis** | Image upload → real OpenAI food/nutrition estimate → editable macros → explicit confirmation → forecast |
| **Home and meal history** | Glucose charts, meal records, saved forecasts, statuses, and relevant dashboard updates sourced from backend records |
| **Outcome comparison** | Recorded post-meal CGM response revealed only after the historical replay reaches the eligible outcome window |
| **Health context** | Historical research CGM plus clearly simulated Apple Health, Apple Watch, Fitbit, and WHOOP observations, including heart-rate information |
| **Medications and alerts** | Fictional demo regimens, persisted Taken/Skipped events, and informational model-related alerts |
| **Accounts and privacy** | Authentication, consent, onboarding, per-account records, and permission-aware access |
| **Full demo mode** | New-user and returning-user journeys with controlled chronological replay and deterministic reset |

### One clear distinction: health-data integration versus forecasting inputs

**The currently verified fs-1.3 forecast uses CGM history, confirmed meal nutrition, meal timing, and eligible previous meal-response history.** GLIMPSE also integrates and displays **heart rate**, activity, sleep, and recovery-related wearable observations as additional context. The frozen prediction model **does not currently ingest heart-rate or other wearable measurements as predictive features**, and its weights are **not automatically retrained** from new user data. Incorporating heart-rate-aware inference and validated per-user retraining are future research directions. This separation is essential to interpreting the demo correctly.

---

## How our forecasting model works

### Model development and selection

We iterated through forecasting approaches and evaluation protocols before freezing a model suitable for reproducible, low-latency inference. The final architecture balances a regularized linear model against nonlinear function fitting:

- **Ridge regression:** A stable, regularized component that learns broad feature–response relationships.
- **Histogram Gradient Boosting (HGB):** A nonlinear component that captures interactions missed by linear regression.
- **Ensemble:** A **50/50 blend** of the two components.

The resulting **fs-1.3** model uses **39 engineered features** to produce a **25-point glucose trajectory at five-minute intervals, from minute 0 through minute 120**. Minute 0 is grounded in the eligible pre-meal CGM baseline rather than an invented reading.

The production model identity remains `glucotwin-fs13-blend-personalized` (`v0.1.0`) for reproducibility, even though the product is branded **GLIMPSE**.

**Frozen model artifact SHA-256:**

```text
12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe
```

### Adaptive learning from personal history

GLIMPSE's **history-based adaptive personalization** makes new forecasts sensitive to a person's *eligible previous meal responses*. Once **five completed qualifying earlier meals** are available, the application can retrieve pertinent historical patterns and incorporate them into subsequent forecasts. It enforces chronological eligibility to prevent future measurements from leaking into predictions.

**This is adaptive retrieval, not online weight retraining.** Model parameters remain frozen. Personalization can change because the admissible history available at prediction time changes.

### Evaluation and limitations

The model was assessed on historical CGM and recorded meals with chronology-aware evaluation and integrity checks. Development evaluation results include approximately **23.09 mg/dL MAE** on one reported held-out protocol and **23.55 mg/dL** on a reported within-person evaluation; these are **development/research evaluations, not prospective clinical accuracy claims**. Dataset splits, exclusions, and detailed definitions must be checked against the authoritative model-evaluation records before quoting these numbers externally.

The selected demonstration episodes were reviewed during development and **must not be represented as an independent external validation cohort**. Uncertainty bounds are not calibrated; GLIMPSE does not invent probability estimates or clinical confidence intervals.

See [validation documentation](docs/validation.md), [governance records](docs/governance/), and the frozen model manifest for precise methodologies and verified values.

---

## End-to-end user experience

```text
Sign in / Create account
         ↓
Consent + guided onboarding
         ↓
Home dashboard / historical CGM / simulated health sources
         ↓
Upload meal photo or enter nutrition manually
         ↓
OpenAI-assisted food and nutrition estimate
         ↓
Review and edit macronutrients → Confirm meal
         ↓
Genuine fs-1.3 two-hour glucose forecast
         ↓
Inspect forecast, threshold information, and meal history
         ↓
For authentic recorded meals: reveal observed CGM outcomes
         ↓
Accumulated eligible history → adaptive personalization
```

Every prediction is tied to a confirmed meal and relevant glucose context. Dashboard meal and forecast summaries are backed by application records; uploading a photograph does **not** fabricate CGM observations, change wearable readings, or imply that a user ate the pictured food in the historical recording.

### Try the two built-in demos

| Demo | What it shows |
|---|---|
| **New user** | Historical CGM replay, genuine meal forecasts, outcome reveals, and progression from no eligible personal history to retrieval activation at five meals |
| **Returning user** | An established eligible history and history-based personalization already active at the start |

Both demos are accessible from the normal sign-in experience using privately configured demo credentials. They share the real application, persistent backend, and frozen model rather than using frontend-only screenshots or prediction fixtures.

**Provenance:** Historical CGM and recorded meals come from CGMacros research data; Apple Health, Apple Watch, Fitbit, and WHOOP observations are **simulated**. Demo profiles and medications are **fictional**. A new manually entered demo meal receives a conditional forecast, **not** a fabricated recorded outcome; it cannot contaminate the authentic research-history retrieval set.

---

## Architecture and technology

```text
                  ┌────────────────────────────┐
                  │ GLIMPSE Web Application    │
                  │ React · TypeScript         │
                  │ TanStack Start / Router    │
                  └──────────────┬─────────────┘
                                 │ authenticated HTTPS / REST
                  ┌──────────────▼─────────────┐
                  │ FastAPI Application (v2)   │
                  │ Auth · Consent · Meals     │
                  │ Forecasts · Alerts · Demo  │
                  └──────┬────────┬────────────┘
                         │        │
             ┌───────────▼─┐  ┌──▼──────────────────┐
             │ PostgreSQL  │  │ Frozen fs-1.3 model │
             │ Persistent  │  │ Ridge + HGB ensemble│
             │ records     │  │ Historical retrieval│
             └─────────────┘  └─────────────────────┘
                         │
                 ┌───────▼──────────┐
                 │ OpenAI API       │
                 │ Server-side food │
                 │ image analysis   │
                 └──────────────────┘

             Private model artifacts + demo bundles
             Integrity checked outside public Git
```

| Layer | Tools and responsibilities |
|---|---|
| **Frontend** | React, TypeScript, TanStack Start/Router, responsive UI components, accessible glucose visualizations |
| **API** | Python, FastAPI, typed request/response contracts, authentication, authorization, upload and inference orchestration |
| **Forecasting** | scikit-learn, Ridge, Histogram Gradient Boosting, 39-feature fs-1.3 model, history retrieval, artifact verification |
| **Food recognition** | OpenAI vision-capable API; estimates editable before confirmation; keys remain server-side |
| **Persistence** | PostgreSQL with Alembic migrations |
| **Packaging** | Docker; health probes, startup gating, reproducible environment configuration |
| **Cloud** | AWS-hosted application. Confirm the exact provisioned AWS service inventory in the final infrastructure handoff before describing individual managed services as deployed. |
| **Quality** | Python/TypeScript tests, real-browser E2E, Docker + PostgreSQL smoke tests, frozen-model and demo replay verification |

The cloud deployment is reported complete. The **public URL, region, and final service topology** should be copied from the verified AWS deployment handoff before submission—not inferred from the original architecture plan.

### API compatibility

- **Frozen API v1:** Retained for research reproducibility and immutable contract compatibility.
- **Application API v2:** Authenticated user accounts, consent, onboarding, meals, photos, confirmation, predictions, outcomes, alerts, medications, and demo orchestration.

The v2 application is layered around the frozen forecasting engine rather than replacing it. See [`contracts/README.md`](contracts/README.md), [`contracts/v2/`](contracts/v2/), and [`docs/project/API_INTERFACE_REGISTRY.md`](docs/project/API_INTERFACE_REGISTRY.md).

---

## Installation: run GLIMPSE locally

GLIMPSE is a **full-stack application**, not a frontend-only demonstration. To run the complete experience, you need the React frontend, FastAPI v2 backend, database, frozen model artifact, and the two private demo bundles. **Cloning the public repository alone is not sufficient for a working glucose-forecast demo.**

> **Fastest route:** If you only want to evaluate the finished prototype, use the team's verified HTTPS deployment link (add it near the top of this README). The installation below is intended for developers and reviewers reproducing it locally.
>
> **Source of truth:** [`docs/release/RUNBOOK.md`](docs/release/RUNBOOK.md) is authoritative for exact release-specific environment variables, artifact paths, and production startup. Do not use the original synthetic training quickstart to provision the final model.

### A. Prerequisites

| Requirement | Purpose |
|---|---|
| Git | Clone the repository |
| Python **3.12** and `pip` | Install and run FastAPI/backend code |
| Node.js and npm | Install and run the frontend |
| PostgreSQL (or the supported local Docker stack) | Persist accounts, meals, predictions, consent, and demo state |
| Docker with Compose support (recommended for integration verification) | Reproducible container-based runtime and checks |
| Frozen fs-1.3 model artifact and **both** demo bundles | Real inference and new/returning Demo Mode; distributed privately |
| Backend environment configuration | Database URL, signing secrets, demo configuration, optional OpenAI settings |
| OpenAI API key (optional) | Enables real photo analysis; without it, other features can still be tested |

**Do not commit** `.env`, authentication keys, OpenAI secrets, model binaries, private CGMacros bundles, or user-uploaded meal images.

### B. Clone the verified code

```bash
git clone https://github.com/Hetp29/HealthHack.git
cd HealthHack

# Use the team's final published release revision.
git status --short
git log -1 --oneline
```

For local reproduction, select the final release SHA documented by the maintainers; avoid mixing a frontend from one commit with a backend from another. The predeployment corrected baseline was `c7e485e`, but the **deployed release may have a later merge SHA**.

### C. Create the Python environment

**macOS / Linux**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

**Windows PowerShell**

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Use the repository's pinned dependency files rather than upgrading individual packages opportunistically. The development requirements include testing tools; consult the runbook for any slimmer production installation.

### D. Configure backend settings and private data

1. Locate the repository's example environment configuration (`.env.example` or the specific example cited in the runbook). Create your local `.env` from it; **do not copy production credentials**.
2. Configure the **PostgreSQL connection**, authentication/session secret, application/demo mode, and permitted frontend origin using the variable names in the runbook. Some legacy `GLUCOTWIN_*` identifiers are intentionally retained for compatibility; do not rename or guess them.
3. Obtain the verified **frozen model artifact** and **both demo bundles** from an authorized team member. Put them in the documented runtime locations. They are intentionally not stored in public Git.
4. If testing food photography, set `OPENAI_API_KEY` **only on the backend**. The configured vision model for the verified local workflow was `gpt-4.1-mini`; follow the runbook for its variable name and supported override.
5. Check the original frozen manifest and model hashes *before* attempting to serve predictions.

The frozen model SHA-256 must be:

```text
12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe
```

The original frozen API v1 contract SHA-256 must be:

```text
be836dddaac5ed4cd78af7adac02bf9b7a1b77133143b84df34621f1be9200f4
```

**If the private artifacts are missing, stop and request them.** Do not substitute a synthetic model, bypass checksum validation, or invent demo observations.

### E. Start PostgreSQL and apply migrations

Start a local PostgreSQL instance or use the database provisioning process in the repository's local infrastructure runbook. Create the expected database and a least-privilege application user, then set the matching connection string in your backend environment.

From the repository root, with the Python environment active and configuration loaded:

```bash
alembic upgrade head
```

If this fails, check that PostgreSQL is running, the configured database exists, the application user can connect, and Alembic is reading the intended environment. **Do not continue with an empty or unmigrated production database.**

### F. Start the real backend

Open **Terminal 1**, in the repository root:

```bash
source .venv/bin/activate
uvicorn backend.app.v2.asgi:app --env-file .env --host 127.0.0.1 --port 8000
```

On Windows, activate the environment using the PowerShell command above. If the release runbook defines a different environment-file location or startup wrapper, use its command instead.

Expected endpoints:

- Backend base URL: <http://127.0.0.1:8000>
- Interactive OpenAPI docs: <http://127.0.0.1:8000/docs>
- Health probe: <http://127.0.0.1:8000/health>

In a separate terminal, verify that the health endpoint responds:

```bash
curl -i http://127.0.0.1:8000/health
```

A health response confirms the server is serving; it **does not by itself prove** that the frozen model and demo bundles are loaded. Run the smoke and integrity checks in Step H.

### G. Start the frontend

Open **Terminal 2**. Change into the directory that contains the **GLIMPSE frontend's `package.json`**. The release documentation identifies the correct path; confirm it rather than assuming the monorepo root is the Node package.

```bash
# Run these from the frontend package directory (the one with package.json).
npm ci
npm run dev
```

Open **<http://localhost:5173>** in a browser. Keep both terminals running while you use GLIMPSE.

Make sure the frontend's API base URL points to **http://127.0.0.1:8000** (or the value configured by the release runbook), and that the backend permits that exact frontend origin. A working frontend with a mismatched API URL will not provide a functioning prototype.

**Production frontend check** (same frontend package directory):

```bash
npx tsc --noEmit
npm test
npm run build
```

### H. Verify the complete application

From the repository root, with the backend and database available:

```bash
# Authenticated v2/application HTTP smoke tests
python scripts/smoke_v2.py http://127.0.0.1:8000

# Database integration and real-container acceptance
bash infra/local/verify_postgres.sh
bash infra/local/container_smoke.sh
```

The last two scripts require a correctly configured PostgreSQL/Docker environment and access to the authentic model and demo artifacts. Consult their script usage and the release runbook before executing them on a shared database.

From the **frontend package directory**, with the backend reachable:

```bash
npm run test:e2e
```

**Caution:** Browser E2E tests may reset or mutate both demonstration accounts. Run them in an isolated local test environment, **not against the public judge-facing demo**.

### Demo account access

The deployed prototype provides two prepared demonstration scenarios. Select the matching option on the GLIMPSE sign-in page:

| Scenario | Email | Password |
|---|---|---|
| **New-user demo** | `demo@glucotwin.example` | `fz0FdExPBqwyLAVySavf` |
| **Returning-user demo** | `demo-returning@glucotwin.example` | `fz0FdExPBqwyLAVySavf` |

The legacy email domain is an internal account identifier; the product is branded **GLIMPSE**. These are **dedicated demonstration credentials**, not personal user accounts. Because the password is published in this README, do not reuse it for any privileged, production-admin, or real-user account. Keep the demo environment isolated and reset scenarios before judging.

Manually inspect the critical journey:

1. Register or sign in and complete onboarding/consent.
2. Select New-user Demo or Returning-user Demo using the dedicated credentials in the table above.
3. Activate simulated Apple Health, Apple Watch, Fitbit, or WHOOP data from the UI.
4. View historical CGM readings and generate an eligible recorded-meal forecast.
5. Advance replay to reveal the authentic observed glucose outcome.
6. Upload a **team-owned** food photograph (with OpenAI configured), review/edit nutrients, confirm the meal, and generate a real forecast.
7. Confirm the meal and forecast appear on Home/Meals and survive a reload.
8. Verify medication Taken/Skipped actions persist.

**Expected behavior:** A newly entered hypothetical demo meal receives a conditional forecast, **not** a fabricated CGMacros outcome; it cannot contaminate the recorded-history personalization set.

### I. Common setup problems

| Symptom | What to check |
|---|---|
| Frontend loads but sign-in fails | Backend is running; frontend API URL and CORS origin match; correct demo email/password; required consent state |
| Backend cannot start | Python dependencies, database URL, secrets, correct `.env` loading, model artifact and checksum |
| `alembic upgrade head` fails | Database is running, credentials work, expected schema/configuration are present |
| Forecast fails | Confirmed meal, eligible pre-meal CGM baseline, complete private artifact, model integrity, correct replay state |
| Photo analysis unavailable | `OPENAI_API_KEY` on backend, supported image type/size, backend OpenAI configuration |
| Demo user has no sample data | Both private bundles provisioned, account seeded, scenario selected/activated, replay position |
| Home fails to refresh | Backend network requests, user session, mutation success, query invalidation; check browser console |
| Browser tests change demo state | Tests may reset demo accounts; use an isolated local instance and reinitialize before presentation |

### J. Deploying the verified version to AWS

The AWS deployment is handled by the team infrastructure configuration, not by ad hoc commands copied from this README. Follow [`docs/release/RUNBOOK.md`](docs/release/RUNBOOK.md) and [`docs/release/HET_DEPLOYMENT_HANDOFF.md`](docs/release/HET_DEPLOYMENT_HANDOFF.md):

1. Deploy the **reviewed, merged release commit**; identify the deployed SHA.
2. Provision PostgreSQL, private artifact/demo-data storage, and server-side application secrets.
3. Build and deploy the backend image with checksum validation **enabled**.
4. Run database migrations and health/readiness probes.
5. Configure the production frontend API URL, HTTPS, and exact-origin CORS.
6. Preserve **one replay API worker**, as required by the current demo implementation.
7. Verify sign-in/upload rate limiting and account isolation at the deployed edge.
8. Run the **public URL** smoke/browser journeys using an authorized test account and team-owned image.
9. Reset both demonstration scenarios before judges use the application.
10. Record the public URL, deployed commit, test status, and any limitations in the submission documentation.

Never put AWS credentials, signing secrets, OpenAI keys, private research bundles, or model binaries in this README or public Git.

---

## Verification and release status

The corrected local release baseline `c7e485e` was reported to pass:

| Verification | Reported result |
|---|---|
| Backend regression suite | **1,071 passed; 1 skipped** |
| Frontend suite | **35 passed**, TypeScript clean, production build successful |
| Frozen model and original v1 contract | Original integrity checks restored and passed |
| Both demo scenarios | Smoke, replay, and real-browser journeys passed |
| Meal photo → confirmed macros → forecast → Home | End-to-end browser flow passed locally |
| PostgreSQL 16 | Migrations verified; **332 tests passed, 2 failed** due to reported shared-database harness isolation in specific tests |
| Docker acceptance | Full container check passed, including checksum refusal gate and single-worker replay |

The two PostgreSQL test failures remain a **documented test-harness limitation**, not two passing checks. The last reviewed baseline was locally verified and subsequently prepared for deployment. **Update this table from the final merged/deployed commit's verification report before making a final deployed-test claim.**

### Frozen contract protection

The product name is **GLIMPSE**, but some legacy `glucotwin` identifiers remain intentionally unchanged for reproducibility. This includes the frozen model ID and historical contract text. Internal configuration compatibility is maintained outside frozen source files.

**Original frozen Contract v1.0 SHA-256:**

```text
be836dddaac5ed4cd78af7adac02bf9b7a1b77133143b84df34621f1be9200f4
```

See [`docs/project/DECISIONS.md`](docs/project/DECISIONS.md) and [`docs/governance/`](docs/governance/) for the freeze and brand-migration decisions.

---

## Privacy, data provenance, and responsible use

- **Real research data:** Selected CGMacros historical glucose and meal records, governed by their permitted use and provenance.
- **Simulated wearable data:** Apple Health, Apple Watch, Fitbit, WHOOP, heart rate, steps, sleep, and recovery observations in Demo Mode are illustrative sample data, not live vendor connections.
- **User-entered meals:** Confirmed nutrition is used for conditional forecasting; a hypothetical replay meal cannot acquire an unrelated historical glucose outcome.
- **AI limitations:** Photograph-derived nutrient estimates require user review and may be inaccurate. Images are transmitted to OpenAI when the real analysis feature is invoked; use only authorized images.
- **Clinical limitations:** GLIMPSE does not provide diagnoses, dosing instructions, emergency monitoring, or treatment advice. Accuracy has not been established through prospective clinical trials.
- **Secrets and artifacts:** Credentials, private research bundles, and model artifacts are not intended to be included in the public source repository.

---

## Repository guide

| Location | Description |
|---|---|
| [`frontend/`](frontend/) | GLIMPSE responsive application, charts, onboarding, meal and demo UX |
| [`backend/`](backend/) | FastAPI app, v1/v2 routes, authentication, persistence and services |
| [`ml/`](ml/) | Frozen forecasting pipeline, features, inference, training/evaluation code and provenance |
| [`research/`](research/) | Research evaluation and model governance support |
| [`contracts/`](contracts/) | Frozen v1 contract and v2 API schemas/examples |
| [`data/`](data/) | Data ingestion and canonical data-layer code; research assets are distributed separately |
| [`infra/`](infra/) | Container, PostgreSQL and infrastructure/deployment checks |
| [`scripts/`](scripts/) | API smoke and demo verification tooling |
| [`tests/`](tests/) | Backend, research, integrity and integration tests |
| [`docs/release/`](docs/release/) | Release runbook, final audits, test matrix and deployment handoff |
| [`docs/project/`](docs/project/) | Architecture decisions, interface registry and team handoffs |
| [`docs/governance/`](docs/governance/) | Original frozen model/contract verification records |
| [`presentation/`](presentation/) | Presentation and demo materials |

Directory names reflect the established repository organization; consult the verified release tree for the actual frontend package location and exact commands.

## Team

| Contributor | Primary responsibilities |
|---|---|
| **Ruhaan Shah** | Forecasting research, model evaluation and freeze, FastAPI/backend, integrations, product and release coordination |
| **Het Patel** | Data layer, PostgreSQL, Docker, AWS infrastructure and deployment verification |
| **Neil Barot** | React/TypeScript user experience, onboarding, dashboard, charts and application integration |

---

## What's next

GLIMPSE demonstrates the feasibility of pairing a reproducible glucose-forecasting engine with everyday nutritional input and history-based personalization. Next research steps include evaluating on larger independent cohorts, calibrating predictive uncertainty, rigorously assessing additional wearable predictors such as heart rate and sleep, and testing safe per-user calibration or retraining under prospective validation.

**GLIMPSE does not aim to replace medical judgment. It aims to make the emerging possibilities of personalized metabolic prediction understandable, testable, and accessible.**

---

*Built for Rutgers HealthHack 2026. Research prototype; not for clinical use.*
