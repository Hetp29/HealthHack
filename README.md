# GLIMPSE
Glucose Learning through Integrated Multimodal Prediction and Sensor Evidence
See beyond the reading. Understand the response.
Rutgers HealthHack 2026 · Challenge 3 — Predict My Glucose Response
GLIMPSE is an AWS-deployed metabolic-health research prototype that brings two-hour glucose forecasting, adaptive personalization from meal-response history, AI-assisted food logging, and integrated health-data visualization into one responsive application.
A user can photograph a meal, review and correct estimated macronutrients, request a genuine model-generated glucose forecast, and explore what the forecast means. Two built-in demonstration journeys make the complete experience explorable without owning a CGM or granting access to a personal health account.
Research and demonstration use only. GLIMPSE is not a medical device and is not validated for diagnosis, treatment, insulin dosing, or clinical decision-making. Historical research CGM recordings and simulated wearable/profile data are identified as such. Forecast uncertainty is not clinically calibrated.

Links: Source repository · [Release runbook](docs/release/RUNBOOK.md) · [Technical documentation](docs/project/) · [Model validation](docs/validation.md)
Live application: AWS deployment is reported complete. Add the final verified public HTTPS URL here before submission. Do not substitute a local development address.

## The challenge
Glucose is personal. Two individuals may eat similar meals yet experience different responses; one individual's response also changes with meal composition, timing, and baseline conditions. CGMs reveal what has happened, but a stream of readings alone does not answer the practical question: “How might my glucose respond to this meal?”
GLIMPSE addresses four related gaps:
1. From observation to prediction: Forecast the full post-meal trajectory, rather than display only retrospective glucose readings.
2. From generic to personal: Use eligible prior meal-response history to adapt forecasts to the individual's observed patterns.
3. From friction to understanding: Convert a meal photograph into an editable nutritional estimate, explicitly confirmed before inference.
4. From fragmented dashboards to one experience: Present glucose, food, activity, heart-rate, sleep, medication, and alert information in a coherent application, with transparent data provenance.
## What the prototype does
Capability	Implemented experience
Glucose forecasting	Frozen, real ML inference yielding a 25-point trajectory over 0–120 minutes; 30/60/90/120-minute estimates and forecast summaries
Adaptive personalization	Retrieval of the user's eligible, completed historical meal responses; enabled once five qualifying earlier meals exist
AI meal analysis	Image upload → real OpenAI food/nutrition estimate → editable macros → explicit confirmation → forecast
Home and meal history	Glucose charts, meal records, saved forecasts, statuses, and relevant dashboard updates sourced from backend records
Outcome comparison	Recorded post-meal CGM response revealed only after the historical replay reaches the eligible outcome window
Health context	Historical research CGM plus clearly simulated Apple Health, Apple Watch, Fitbit, and WHOOP observations, including heart-rate information
Medications and alerts	Fictional demo regimens, persisted Taken/Skipped events, and informational model-related alerts
Accounts and privacy	Authentication, consent, onboarding, per-account records, and permission-aware access
Full demo mode	New-user and returning-user journeys with controlled chronological replay and deterministic reset


## How our forecasting model works
### Model development and selection
We iterated through forecasting approaches and evaluation protocols before freezing a model suitable for reproducible, low-latency inference. The final architecture balances a regularized linear model against nonlinear function fitting:
- Ridge regression: A stable, regularized component that learns broad feature–response relationships.
- Histogram Gradient Boosting (HGB): A nonlinear component that captures interactions missed by linear regression.
- Ensemble: A 50/50 blend of the two components.
The resulting fs-1.3 model uses 39 engineered features to produce a 25-point glucose trajectory at five-minute intervals, from minute 0 through minute 120. Minute 0 is grounded in the eligible pre-meal CGM baseline rather than an invented reading.
The production model identity remains glucotwin-fs13-blend-personalized (v0.1.0) for reproducibility, even though the product is branded GLIMPSE.
Frozen model artifact SHA-256:
12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe
### Adaptive learning from personal history
GLIMPSE's history-based adaptive personalization makes new forecasts sensitive to a person's eligible previous meal responses. Once five completed qualifying earlier meals are available, the application can retrieve pertinent historical patterns and incorporate them into subsequent forecasts. It enforces chronological eligibility to prevent future measurements from leaking into predictions.
This is adaptive retrieval, not online weight retraining. Model parameters remain frozen. Personalization can change because the admissible history available at prediction time changes.
### Evaluation and limitations
The model was assessed on historical CGM and recorded meals with chronology-aware evaluation and integrity checks. Development evaluation results include approximately 23.09 mg/dL MAE on one reported held-out protocol and 23.55 mg/dL on a reported within-person evaluation; these are development/research evaluations, not prospective clinical accuracy claims. Dataset splits, exclusions, and detailed definitions must be checked against the authoritative model-evaluation records before quoting these numbers externally.
The selected demonstration episodes were reviewed during development and must not be represented as an independent external validation cohort. Uncertainty bounds are not calibrated; GLIMPSE does not invent probability estimates or clinical confidence intervals.
See [validation documentation](docs/validation.md), [governance records](docs/governance/), and the frozen model manifest for precise methodologies and verified values.
## End-to-end user experience
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
Every prediction is tied to a confirmed meal and relevant glucose context. Dashboard meal and forecast summaries are backed by application records; uploading a photograph does not fabricate CGM observations, change wearable readings, or imply that a user ate the pictured food in the historical recording.
## Try the two built-in demos
Demo	What it shows
New user	Historical CGM replay, genuine meal forecasts, outcome reveals, and progression from no eligible personal history to retrieval activation at five meals
Returning user	An established eligible history and history-based personalization already active at the start


Both demos are accessible from the normal sign-in experience using privately configured demo credentials. They share the real application, persistent backend, and frozen model rather than using frontend-only screenshots or prediction fixtures.
Provenance: Historical CGM and recorded meals come from CGMacros research data; Apple Health, Apple Watch, Fitbit, and WHOOP observations are simulated. Demo profiles and medications are fictional. A new manually entered demo meal receives a conditional forecast, not a fabricated recorded outcome; it cannot contaminate the authentic research-history retrieval set.
## Architecture and technology
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
Layer	Tools and responsibilities
Frontend	React, TypeScript, TanStack Start/Router, responsive UI components, accessible glucose visualizations
API	Python, FastAPI, typed request/response contracts, authentication, authorization, upload and inference orchestration
Forecasting	scikit-learn, Ridge, Histogram Gradient Boosting, 39-feature fs-1.3 model, history retrieval, artifact verification
Food recognition	OpenAI vision-capable API; estimates editable before confirmation; keys remain server-side
Persistence	PostgreSQL with Alembic migrations
Packaging	Docker; health probes, startup gating, reproducible environment configuration
Cloud	AWS-hosted application. Confirm the exact provisioned AWS service inventory in the final infrastructure handoff before describing individual managed services as deployed.
Quality	Python/TypeScript tests, real-browser E2E, Docker + PostgreSQL smoke tests, frozen-model and demo replay verification


The cloud deployment is reported complete. The public URL, region, and final service topology should be copied from the verified AWS deployment handoff before submission—not inferred from the original architecture plan.
## API compatibility
- Frozen API v1: Retained for research reproducibility and immutable contract compatibility.
- Application API v2: Authenticated user accounts, consent, onboarding, meals, photos, confirmation, predictions, outcomes, alerts, medications, and demo orchestration.
The v2 application is layered around the frozen forecasting engine rather than replacing it. See [`contracts/README.md`](contracts/README.md), [`contracts/v2/`](contracts/v2/), and [`docs/project/API_INTERFACE_REGISTRY.md`](docs/project/API_INTERFACE_REGISTRY.md).
## Installation and deployment
Start with the authoritative [release runbook](docs/release/RUNBOOK.md). The commands below are a high-level local development guide. Exact dependencies, environment settings, credentials, and artifact paths depend on the verified release configuration.

### Requirements
- Git, Python 3.12, Node.js and npm
- PostgreSQL, or the repository's supported Docker-based local stack
- Privately provisioned frozen model artifact and both demonstration bundles
- Backend environment configuration (database, session/authentication secrets, demo settings)
- Optional server-side OpenAI API key for actual image analysis
1. Clone the repository
git clone https://github.com/Hetp29/HealthHack.git
cd HealthHack
Check out the team's verified final release commit, rather than an unreviewed development branch.
2. Set up Python dependencies
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
3. Configure services and research artifacts
1. Set backend environment variables following the supplied example configuration and release runbook.
2. Start PostgreSQL and configure the application database connection.
3. Provision the genuine frozen model and the authorized historical demo bundles outside the public repository.
4. Verify model checksums. Do not retrain or use the old synthetic-development quickstart as a replacement for the frozen release artifact.
5. Set OPENAI_API_KEY on the backend only if real image analysis is required.
4. Apply migrations and start the backend
alembic upgrade head
uvicorn backend.app.v2.asgi:app --host 127.0.0.1 --port 8000
If configuration is held in .env, use the startup mechanism documented in the runbook so the server actually receives those values (for example, Uvicorn's --env-file option where supported).
API: http://localhost:8000 · Interactive API docs: http://localhost:8000/docs
5. Start the frontend
Run the following commands from the actual frontend package directory defined in the repository:
npm ci
npm run dev
Local application: http://localhost:5173
For the production frontend bundle:
npm run build
6. Validate the working prototype
Use the documented smoke and browser test commands:
python scripts/smoke_v2.py http://127.0.0.1:8000
npm run test:e2e
Run the browser command from its configured package directory, with the environment and demo credentials required by the release runbook. The end-to-end suite may reset both demo accounts; do not run it casually against a public demonstration environment.
Additional deployment checks:
bash infra/local/verify_postgres.sh
bash infra/local/container_smoke.sh
These checks require the appropriate PostgreSQL/Docker prerequisites and privately provisioned artifacts.
7. Deploy on AWS
Follow [`docs/release/RUNBOOK.md`](docs/release/RUNBOOK.md) and the infrastructure handoff. Deploy the exact reviewed commit, provision database and private model/data assets, configure backend secrets and HTTPS/CORS, and verify the public application through normal browser use.
For deterministic demo replay, preserve the verified single backend worker configuration. Apply and verify sign-in/upload rate limiting before exposing the service publicly. After the final end-to-end test, reset both demo scenarios to their starting states.
## Verification and release status
The corrected local release baseline c7e485e was reported to pass:
Verification	Reported result
Backend regression suite	1,071 passed; 1 skipped
Frontend suite	35 passed, TypeScript clean, production build successful
Frozen model and original v1 contract	Original integrity checks restored and passed
Both demo scenarios	Smoke, replay, and real-browser journeys passed
Meal photo → confirmed macros → forecast → Home	End-to-end browser flow passed locally
PostgreSQL 16	Migrations verified; 332 tests passed, 2 failed due to reported shared-database harness isolation in specific tests
Docker acceptance	Full container check passed, including checksum refusal gate and single-worker replay


The two PostgreSQL test failures remain a documented test-harness limitation, not two passing checks. The last reviewed baseline was locally verified and subsequently prepared for deployment. Update this table from the final merged/deployed commit's verification report before making a final deployed-test claim.
## Frozen contract protection
The product name is GLIMPSE, but some legacy glucotwin identifiers remain intentionally unchanged for reproducibility. This includes the frozen model ID and historical contract text. Internal configuration compatibility is maintained outside frozen source files.
Original frozen Contract v1.0 SHA-256:
be836dddaac5ed4cd78af7adac02bf9b7a1b77133143b84df34621f1be9200f4
See [`docs/project/DECISIONS.md`](docs/project/DECISIONS.md) and [`docs/governance/`](docs/governance/) for the freeze and brand-migration decisions.
## Privacy, data provenance, and responsible use
- Real research data: Selected CGMacros historical glucose and meal records, governed by their permitted use and provenance.
- Simulated wearable data: Apple Health, Apple Watch, Fitbit, WHOOP, heart rate, steps, sleep, and recovery observations in Demo Mode are illustrative sample data, not live vendor connections.
- User-entered meals: Confirmed nutrition is used for conditional forecasting; a hypothetical replay meal cannot acquire an unrelated historical glucose outcome.
- AI limitations: Photograph-derived nutrient estimates require user review and may be inaccurate. Images are transmitted to OpenAI when the real analysis feature is invoked; use only authorized images.
- Clinical limitations: GLIMPSE does not provide diagnoses, dosing instructions, emergency monitoring, or treatment advice. Accuracy has not been established through prospective clinical trials.
- Secrets and artifacts: Credentials, private research bundles, and model artifacts are not intended to be included in the public source repository.
## Repository guide
Location	Description
[`frontend/`](frontend/)	GLIMPSE responsive application, charts, onboarding, meal and demo UX
[`backend/`](backend/)	FastAPI app, v1/v2 routes, authentication, persistence and services
[`ml/`](ml/)	Frozen forecasting pipeline, features, inference, training/evaluation code and provenance
[`research/`](research/)	Research evaluation and model governance support
[`contracts/`](contracts/)	Frozen v1 contract and v2 API schemas/examples
[`data/`](data/)	Data ingestion and canonical data-layer code; research assets are distributed separately
[`infra/`](infra/)	Container, PostgreSQL and infrastructure/deployment checks
[`scripts/`](scripts/)	API smoke and demo verification tooling
[`tests/`](tests/)	Backend, research, integrity and integration tests
[`docs/release/`](docs/release/)	Release runbook, final audits, test matrix and deployment handoff
[`docs/project/`](docs/project/)	Architecture decisions, interface registry and team handoffs
[`docs/governance/`](docs/governance/)	Original frozen model/contract verification records
[`presentation/`](presentation/)	Presentation and demo materials


Directory names reflect the established repository organization; consult the verified release tree for the actual frontend package location and exact commands.
## Team
Contributor	Primary responsibilities
Ruhaan Shah	Forecasting research, model evaluation and freeze, FastAPI/backend, integrations, product and release coordination
Het Patel	Data layer, PostgreSQL, Docker, AWS infrastructure and deployment verification
Neil Barot	React/TypeScript user experience, onboarding, dashboard, charts and application integration


## What's next
GLIMPSE demonstrates the feasibility of pairing a reproducible glucose-forecasting engine with everyday nutritional input and history-based personalization. Next research steps include evaluating on larger independent cohorts, calibrating predictive uncertainty, rigorously assessing additional wearable predictors such as heart rate and sleep, and testing safe per-user calibration or retraining under prospective validation.
GLIMPSE does not aim to replace medical judgment. It aims to make the emerging possibilities of personalized metabolic prediction understandable, testable, and accessible.
Built for Rutgers HealthHack 2026. Research prototype; not for clinical use.
