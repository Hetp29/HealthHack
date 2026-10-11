/**
 * The GLIMPSE v2 client. Every call here is a real backend endpoint; there is no
 * fixture fallback anywhere in the application.
 */
import type {
  AlertEvent,
  AlertSettings,
  AuthResponse,
  CgmReadings,
  CgmStatus,
  Consent,
  DemoDevices,
  DeviceConnection,
  DeviceProfileId,
  Dose,
  Education,
  GlucoseUnit,
  IngestSummary,
  Macros,
  Meal,
  MealEstimate,
  Onboarding,
  Outcome,
  Prediction,
  ProviderCapability,
  Regimen,
  ReplayState,
  ServiceStatus,
  TimelineItem,
  User,
  WearableObservation,
} from "./types";

const env = import.meta.env as Record<string, string | boolean | undefined>;
const ROOT = (
  (env["VITE_API_BASE_URL"] as string | undefined) ?? (env["DEV"] ? "http://localhost:8000" : "")
).replace(/\/$/, "");
export const API_ROOT = ROOT;
const V2 = ((env["VITE_V2_API_BASE_URL"] as string | undefined) ?? `${ROOT}/api/v2`).replace(
  /\/$/,
  "",
);

const TOKEN_KEY = "glimpse.v2.token";
export const SESSION_EVENT = "glimpse:session-ended";

const hasWindow = () => typeof window !== "undefined";
export const getToken = () => (hasWindow() ? window.sessionStorage.getItem(TOKEN_KEY) : null);
export const setToken = (token: string) => window.sessionStorage.setItem(TOKEN_KEY, token);
export function clearToken() {
  if (hasWindow()) window.sessionStorage.removeItem(TOKEN_KEY);
}

export type ApiErrorKind = "network" | "timeout" | "http";

/** A failed request. `code` is the backend's stable error code when it sent one. */
export class ApiError extends Error {
  constructor(
    public kind: ApiErrorKind,
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
    public requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface Options {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  json?: unknown;
  body?: Blob;
  contentType?: string;
  query?: Record<string, string | number | boolean | undefined | null>;
  timeoutMs?: number;
  /** A 401 here is an answer (wrong password), not an expired session. */
  authEndpoint?: boolean;
  raw?: boolean;
  base?: string;
}

async function call<T>(path: string, options: Options = {}): Promise<T> {
  const { method = "GET", json, body, contentType, query, timeoutMs = 30000 } = options;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const url = `${options.base ?? V2}${path}${params.size ? `?${params}` : ""}`;
  const headers = new Headers({ Accept: "application/json" });
  const token = getToken();
  if (token && !options.base) headers.set("Authorization", `Bearer ${token}`);
  if (json !== undefined) headers.set("Content-Type", "application/json");
  if (body && contentType) headers.set("Content-Type", contentType);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: json !== undefined ? JSON.stringify(json) : (body ?? null),
      signal: controller.signal,
    });
  } catch {
    if (controller.signal.aborted) {
      throw new ApiError("timeout", 0, "timeout", "The server took too long to respond.");
    }
    throw new ApiError("network", 0, "network", "GLIMPSE could not reach its server.");
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    let payload: {
      code?: string;
      message?: string;
      details?: Record<string, unknown>;
      request_id?: string;
    } = {};
    try {
      payload = (await response.json()) as typeof payload;
    } catch {
      /* a non-JSON error body: keep the status */
    }
    const error = new ApiError(
      "http",
      response.status,
      payload.code ?? `http_${response.status}`,
      payload.message ?? `Request failed (${response.status}).`,
      payload.details ?? {},
      payload.request_id ?? response.headers.get("x-request-id"),
    );
    if (response.status === 401 && !options.authEndpoint && hasWindow()) {
      clearToken();
      window.dispatchEvent(new CustomEvent(SESSION_EVENT, { detail: error.code }));
    }
    throw error;
  }
  if (options.raw) return (await response.blob()) as T;
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ---- account ---------------------------------------------------------------

export async function login(email: string, password: string) {
  const result = await call<AuthResponse>("/auth/login", {
    method: "POST",
    json: { email, password },
    authEndpoint: true,
  });
  setToken(result.session.token);
  return result;
}

export async function register(input: { email: string; password: string; display_name: string }) {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const result = await call<AuthResponse>("/auth/register", {
    method: "POST",
    json: { ...input, timezone },
    authEndpoint: true,
  });
  setToken(result.session.token);
  return result;
}

export async function logout() {
  if (!getToken()) return; // nothing to end, for example after a demonstration reset
  try {
    await call<void>("/auth/logout", { method: "POST" });
  } finally {
    clearToken();
  }
}

export const getMe = () => call<User>("/me");
export const patchMe = (changes: { display_name?: string; timezone?: string }) =>
  call<User>("/me", { method: "PATCH", json: changes });
export const getStatus = () => call<ServiceStatus>("/status");

export const getOnboarding = () => call<Onboarding>("/onboarding");
export const patchOnboarding = (input: {
  answers: Record<string, unknown>;
  completed?: boolean;
  expected_version?: number;
}) => call<Onboarding>("/onboarding", { method: "PATCH", json: input });

export const getConsents = () => call<{ consents: Consent[] }>("/consents");
export const putConsent = (type: Consent["consent_type"], state: "granted" | "withdrawn") =>
  call<{ consents: Consent[]; processing_allowed: boolean }>(`/consents/${type}`, {
    method: "PUT",
    json: { state },
  });

// ---- glucose ---------------------------------------------------------------

export const getCgmStatus = () => call<CgmStatus>("/cgm/status");
export const getCgmReadings = (range: { start?: string; end?: string } = {}) =>
  call<CgmReadings>("/cgm/readings", { query: { ...range, limit: 5000 } });
export const pushReading = (input: { observed_at: string; value: number; unit: GlucoseUnit }) =>
  call<IngestSummary>("/cgm/readings", {
    method: "POST",
    json: {
      source: "manual_entry",
      device_id: "manual",
      readings: [{ ...input, record_kind: "cgm" }],
    },
  });
export const importCgmFile = (
  file: File,
  format: "dexcom_clarity_csv" | "libreview_csv",
  dateOrder?: "MDY" | "DMY",
) =>
  call<IngestSummary>("/cgm/import", {
    method: "POST",
    body: file,
    contentType: "text/csv",
    query: { format, date_order: dateOrder },
    timeoutMs: 120000,
  });

// ---- meals and forecasts ---------------------------------------------------

export const getMeals = () => call<{ meals: Meal[] }>("/meals", { query: { limit: 200 } });
export const getMeal = (mealId: string) => call<Meal>(`/meals/${mealId}`);
export const createMeal = (input: { occurred_at: string; note?: string; macros?: Macros }) =>
  call<Meal>("/meals", { method: "POST", json: input });
export const uploadMealPhoto = (file: Blob, occurredAt: string, contentType: string) =>
  call<Meal>("/meals/images", {
    method: "POST",
    body: file,
    contentType,
    query: { occurred_at: occurredAt },
    timeoutMs: 90000,
  });
/** One paid analysis per photo: the backend returns the stored estimate on any repeat. */
export const analyzeMeal = (mealId: string) =>
  call<{ meal_id: string; estimate: MealEstimate }>(`/meals/${mealId}/analyze`, {
    method: "POST",
    timeoutMs: 90000,
  });
export const confirmMeal = (mealId: string, macros: Macros) =>
  call<Meal>(`/meals/${mealId}/confirm`, { method: "PATCH", json: macros });
export const deleteMeal = (mealId: string) => call<void>(`/meals/${mealId}`, { method: "DELETE" });
export const getMealImage = (mealId: string) => call<Blob>(`/meals/${mealId}/image`, { raw: true });
export const predictMeal = (mealId: string) =>
  call<Prediction>(`/meals/${mealId}/predict`, { method: "POST", timeoutMs: 60000 });
export const getPredictions = () =>
  call<{ predictions: Prediction[] }>("/predictions", { query: { limit: 100 } });
export const getPrediction = (id: string) => call<Prediction>(`/predictions/${id}`);
export const getOutcome = (mealId: string) => call<Outcome>(`/meals/${mealId}/outcome`);

// ---- devices and wearables -------------------------------------------------

export const getDevices = () =>
  call<{ connections: DeviceConnection[]; providers: ProviderCapability[] }>("/devices");
export const getWearables = (
  query: { provider?: string; metric?: string; start?: string; end?: string } = {},
) =>
  call<{ observations: WearableObservation[]; units: Record<string, string>; note: string }>(
    "/wearables/observations",
    { query: { ...query, limit: 5000 } },
  );

// ---- medications -----------------------------------------------------------

export const getRegimens = () => call<{ regimens: Regimen[] }>("/medications");
export const createRegimen = (input: Record<string, unknown>) =>
  call<Regimen>("/medications", { method: "POST", json: input });
export const patchRegimen = (id: string, changes: Record<string, unknown>) =>
  call<Regimen>(`/medications/${id}`, { method: "PATCH", json: changes });
export const getDueReminders = () =>
  call<{ as_of: string; due: Dose[] }>("/medication-reminders/due");
export const getDoses = () =>
  call<{ doses: Dose[] }>("/medication-doses", { query: { limit: 200 } });
export const recordDose = (input: {
  regimen_id: string;
  action: "taken" | "skipped" | "snoozed";
  scheduled_at?: string | null;
  snoozed_until?: string;
  client_event_id: string;
}) => call<Dose & { replayed: boolean }>("/medication-doses", { method: "POST", json: input });
export const getEducation = () => call<Education>("/medication-education");

// ---- alerts ----------------------------------------------------------------

export const getAlertSettings = () => call<AlertSettings>("/alert-settings");
export const putAlertSettings = (input: {
  enabled: boolean;
  threshold: { value: number; unit: GlucoseUnit };
  channels: string[];
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  cooldown_minutes: number;
}) => call<AlertSettings>("/alert-settings", { method: "PUT", json: input });
export const getAlerts = () => call<{ alerts: AlertEvent[] }>("/alerts", { query: { limit: 100 } });
export const acknowledgeAlert = (id: string) =>
  call<AlertEvent>(`/alerts/${id}/acknowledge`, { method: "POST" });

// ---- demonstration (routes exist only when the server runs in demo mode) ----

export const getReplay = () => call<ReplayState>("/demo/replay");
export const advanceReplay = (input: {
  expected_event_index: number;
  to?: string;
  minutes?: number;
}) => call<ReplayState>("/demo/replay/advance", { method: "POST", json: input, timeoutMs: 120000 });
export const resetReplay = () =>
  call<ReplayState & { reset: true; sign_in_again: true }>("/demo/replay/reset", {
    method: "POST",
    timeoutMs: 120000,
  });
export const getDemoDevices = () => call<DemoDevices>("/demo/devices");
export const activateDemoDevice = (profile: DeviceProfileId) =>
  call<DemoDevices & { profile: string; already_active: boolean; observations_released: number }>(
    `/demo/devices/${profile}/activate`,
    { method: "POST", timeoutMs: 60000 },
  );
export const getHealthTimeline = (hours = 48) =>
  call<{ start: string; end: string; position: string; disclaimer: string; items: TimelineItem[] }>(
    "/demo/health-timeline",
    { query: { hours, limit: 300 } },
  );

// ---- frozen v1 model information (public, read-only) -------------------------

export const getModelInfo = () =>
  call<{
    model_version: string;
    trained_at: string | null;
    data_source: string;
    feature_set_version: string;
    limitations: string[];
  }>("/api/model/info", { base: ROOT });

// ---- errors, in plain language ----------------------------------------------

const MESSAGES: Record<string, string> = {
  network: "GLIMPSE can't reach its server. Check your connection and try again.",
  timeout: "That took too long. Please try again.",
  invalid_credentials: "That email and password don't match.",
  email_taken: "An account with this email already exists. Try signing in instead.",
  session_expired: "Your session has ended. Please sign in again.",
  unauthenticated: "Please sign in to continue.",
  consent_required:
    "GLIMPSE needs your permission to process meal and glucose data before it can do this.",
  insufficient_premeal_context:
    "A forecast needs a glucose reading from the 15 minutes before the meal, and there isn't one yet.",
  meal_not_confirmed: "Confirm the meal's nutrition values before asking for a forecast.",
  meal_has_no_photo: "This meal has no photo to analyze. You can enter the values yourself.",
  vision_not_configured:
    "Photo analysis isn't switched on for this server. You can enter the values yourself.",
  vision_timeout: "Photo analysis timed out. Try again, or enter the values yourself.",
  vision_rate_limited:
    "Photo analysis is busy right now. Try again shortly, or enter the values yourself.",
  vision_unavailable: "Photo analysis is unavailable right now. You can enter the values yourself.",
  vision_unreachable:
    "The photo analysis service couldn't be reached. You can enter the values yourself.",
  vision_provider_error:
    "The photo analysis service returned an error. You can enter the values yourself.",
  vision_unparseable:
    "The photo couldn't be turned into an estimate. You can enter the values yourself.",
  vision_invalid_estimate:
    "The photo analysis returned an unusable estimate. Please enter the values yourself.",
  analysis_limit_reached:
    "You've reached today's limit for photo analysis. You can still enter values yourself.",
  image_too_large: "That image is too large. Please choose one under 10 MB.",
  unsupported_image_type: "That file type isn't supported. Please use a JPEG, PNG or WebP photo.",
  content_type_mismatch: "That file doesn't look like the image type it claims to be.",
  invalid_image: "That image couldn't be opened. Please try a different photo.",
  model_unavailable: "The forecasting model isn't available right now.",
  model_integrity_failure:
    "Forecasting is switched off because the server's model could not be verified.",
  not_demo_account: "This is only available in a demonstration account.",
  demo_data_unavailable: "The demonstration data isn't installed on this server.",
  demo_requires_frozen_model:
    "The demonstration needs the approved forecasting model, which this server isn't running.",
  not_found: "We couldn't find that.",
};

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const known = MESSAGES[error.code];
    if (known) return known;
    if (error.code === "invalid_request") {
      const first = (
        error.details["errors"] as Array<{ field: string; message: string }> | undefined
      )?.[0];
      return first
        ? `Please check ${first.field.replace(/_/g, " ")}: ${first.message}.`
        : error.message;
    }
    if (error.status >= 500) return "Something went wrong on the server. Please try again.";
    return error.message;
  }
  return "Something unexpected went wrong.";
}

export const isCode = (error: unknown, ...codes: string[]) =>
  error instanceof ApiError && codes.includes(error.code);

export const deleteAccount = (password: string) =>
  call<void>("/account", { method: "DELETE", json: { password }, authEndpoint: true });
export const getNotifications = () =>
  call<{
    notifications: Array<{
      delivery_id: string;
      source_type: string;
      source_id: string;
      channel: string;
      status: string;
      detail: string | null;
      created_at: string;
    }>;
  }>("/notifications", { query: { limit: 50 } });
export const connectProvider = (provider: string) =>
  call<{ provider: string; authorization_url: string }>(`/devices/${provider}/connect`, {
    method: "POST",
  });
export const disconnectProvider = (provider: string) =>
  call<{ provider: string; status: string; revoked_upstream: boolean }>(`/devices/${provider}`, {
    method: "DELETE",
  });
