# Frontend/backend integration note

Glimpse currently runs in fixture mode by default. The data-mode control in `AppShell` switches to live mode, which routes requests through `src/services/apiClient.ts` and `src/services/glimpseServices.ts`.

## Authentication and consent

The `/auth` screen is intentionally a frontend gateway. Replace `authenticate()` in `src/state/AppState.tsx` with the session callback from the identity provider. The callback must set the authenticated user only after credentials, Terms, and Privacy Policy consent have been accepted. Persist the provider session securely; do not put access tokens in local storage.

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
