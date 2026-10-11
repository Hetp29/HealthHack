# Frontend/backend integration note

The v2 Glimpse client routes authenticated requests through `src/services/v2Client.ts` to `/api/v2`. It stores only the backend-issued opaque bearer session in `sessionStorage`; credentials are never embedded in the frontend bundle.

## Authentication and consent

The The `/auth` screen calls `POST /api/v2/auth/login` for the new-user and returning-user demo accounts, or `POST /api/v2/auth/register` for a new account. It records necessary consent through `PUT /api/v2/consents/necessary_processing`. The frontend must continue to treat the backend session as authoritative and must not put access tokens in local storage.

## v2 endpoints currently used

- `POST /api/v2/auth/login`
- `POST /api/v2/auth/register`
- `PUT /api/v2/consents/{consent_type}`
- `GET /api/v2/onboarding`, `PATCH /api/v2/onboarding`
- `GET /api/v2/status`
- `GET /api/v2/demo/replay`, `POST /api/v2/demo/replay/advance`
- `GET /api/v2/demo/devices`, `POST /api/v2/demo/devices/{profile}/activate`
- `GET /api/v2/cgm/readings`
- `GET /api/v2/wearables/observations`
- `GET /api/v2/meals`, `POST /api/v2/meals`, `POST /api/v2/meals/{meal_id}/predict`
- `GET /api/v2/predictions/{prediction_id}`, `GET /api/v2/meals/{meal_id}/outcome`
- `GET /api/v2/medications`, `GET /api/v2/medication-reminders/due`, `GET /api/v2/medication-doses`, `POST /api/v2/medication-doses`
- `GET /api/v2/alerts`

## Device connections

`/connect-devices` currently uses explicit mock authorization actions. Replace each `connect()` call with a provider-specific OAuth/native bridge flow. Return one of `Connected`, `Demo / Fixture Data`, or `Permissions Denied`, and keep denied permissions non-blocking. Provider tokens should be exchanged and stored server-side.

## Onboarding

`QuestionnaireForm` emits the typed `QuestionnaireAnswers` object after required processing consent. Send this payload to a user-scoped profile endpoint. Questionnaire values configure presentation, alerts, and preferences; they must not be appended to or modify the frozen 39-feature prediction model input.

## Medication events

The medication page currently demonstrates explicit, user-triggered dose logging. Replace the local `onLogDose` handler with an authenticated event endpoint. The server should append immutable events with actor, scheduled time, actual time, and status. Never infer `taken` from page visibility or a reminder timer.

## Predictions and safety

Keep prediction requests compatible with the existing typed contracts and preserve the model feature ordering and weights. Responses should identify their data source and model availability. Forecasts, alerts, medication logs, and nutritional estimates are informational only and must retain the displayed clinical disclaimer; no endpoint should return insulin dose instructions or a diagnosis.

## Error and audit behavior

Map authentication failures, provider permission denials, validation errors, and model unavailability to explicit UI states. Avoid success-shaped fallbacks for failed live requests. Add request IDs and consent/audit records on the backend without logging health data or credentials.
