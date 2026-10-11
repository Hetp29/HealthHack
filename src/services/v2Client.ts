import { API_BASE_URL, ApiError } from "./apiClient";
import type {
  AuthResponse, CgmReadings, DemoDevices, DemoDeviceProfile, ReplayAdvanceResponse, ReplayState,
  V2Dose, V2Meal, V2MealsResponse, V2Outcome, V2Prediction, V2Regimen, V2User, WearableObservations,
} from "./v2Types";
import type { MealMacros } from "@/types/contracts";

const V2_BASE = `${(import.meta.env["VITE_V2_API_BASE_URL"] as string | undefined) ?? `${API_BASE_URL}/api/v2`}`.replace(/\/$/, "");
const TOKEN_KEY = "glimpse.v2.token";

function token() {
  return typeof window === "undefined" ? null : sessionStorage.getItem(TOKEN_KEY);
}

export function clearV2Session() {
  if (typeof window !== "undefined") sessionStorage.removeItem(TOKEN_KEY);
}

export function hasV2Session() {
  return typeof window !== "undefined" && sessionStorage.getItem(TOKEN_KEY) !== null;
}

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 30000): Promise<T> {
  if (!V2_BASE) throw new ApiError("config", "VITE_API_BASE_URL is not configured.");
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    const session = token();
    if (session) headers.set("Authorization", `Bearer ${session}`);
    const response = await fetch(`${V2_BASE}${path}`, { ...init, headers, signal: init.signal ?? ctrl.signal });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        detail = typeof body.detail === "string" ? body.detail : body.code ?? detail;
      } catch { /* Preserve the HTTP status when the server does not return JSON. */ }
      throw new ApiError(response.status === 401 ? "http" : response.status === 404 ? "not_found" : response.status === 503 ? "model_unavailable" : response.status === 422 ? "invalid_input" : "http", detail, response.status);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (ctrl.signal.aborted) throw new ApiError("timeout", "The v2 backend did not respond in time.");
    throw new ApiError("network", "The v2 backend is unreachable.");
  } finally {
    clearTimeout(timer);
  }
}

export async function login(email: string, password: string) {
  const result = await request<AuthResponse>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
  sessionStorage.setItem(TOKEN_KEY, result.session.token);
  return result;
}

export async function register(email: string, password: string, displayName: string) {
  const result = await request<AuthResponse>("/auth/register", { method: "POST", body: JSON.stringify({ email, password, display_name: displayName, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }) });
  sessionStorage.setItem(TOKEN_KEY, result.session.token);
  return result;
}

export const getMe = () => request<V2User>("/me");
export const getOnboarding = () => request<{ completed: boolean; version?: number; answers?: Record<string, unknown> }>("/onboarding");
export const patchOnboarding = (answers: Record<string, unknown>, completed = true) => request("/onboarding", { method: "PATCH", body: JSON.stringify({ answers, completed }) });
export const putConsent = (type: "necessary_processing" | "personalization_history", state: "granted" | "withdrawn") => request(`/consents/${type}`, { method: "PUT", body: JSON.stringify({ state }) });
export const getReplay = () => request<ReplayState>("/demo/replay");
export const advanceReplay = (body: { expected_event_index: number; to?: string; minutes?: number }) => request<ReplayAdvanceResponse>("/demo/replay/advance", { method: "POST", body: JSON.stringify(body) }, 60000);
export const resetReplay = () => request<ReplayState & { reset: true; sign_in_again: true }>("/demo/replay/reset", { method: "POST" }, 60000);
export const getDemoDevices = () => request<DemoDevices>("/demo/devices");
export const activateDemoDevice = (profile: DemoDeviceProfile["profile"]) => request<DemoDevices & { profile: string; already_active?: boolean; observations_released?: number }>(`/demo/devices/${profile}/activate`, { method: "POST" });
export const getCgmReadings = (start?: string, end?: string) => request<CgmReadings>(`/cgm/readings?${new URLSearchParams({ ...(start ? { start } : {}), ...(end ? { end } : {}), limit: "5000" })}`);
export const getWearableObservations = (provider?: string) => request<WearableObservations>(`/wearables/observations?${new URLSearchParams({ ...(provider ? { provider } : {}), limit: "5000" })}`);
export const getMedications = () => request<{ regimens: V2Regimen[] }>("/medications");
export const getMedicationReminders = () => request<{ as_of: string; due: V2Dose[] }>("/medication-reminders/due");
export const getMedicationDoses = () => request<{ doses: V2Dose[] }>("/medication-doses");
export const recordDose = (event: { regimen_id: string; action: "taken" | "skipped" | "snoozed"; scheduled_at?: string | null; snoozed_until?: string | null }) => request<V2Dose>("/medication-doses", { method: "POST", body: JSON.stringify({ ...event, client_event_id: `glimpse-${crypto.randomUUID()}` }) });
export const getMeals = () => request<V2MealsResponse>("/meals?limit=200");
export const createMeal = (occurredAt: string, macros: MealMacros) => request<V2Meal>("/meals", { method: "POST", body: JSON.stringify({ occurred_at: occurredAt, macros }) });
export const predictMeal = (mealId: string) => request<V2Prediction>(`/meals/${mealId}/predict`, { method: "POST" }, 60000);
export const getPrediction = (id: string) => request<V2Prediction>(`/predictions/${id}`);
export const getOutcome = (mealId: string) => request<V2Outcome>(`/meals/${mealId}/outcome`);
export const getAlerts = () => request<{ alerts: Array<{ alert_id?: string; level?: string; headline?: string; detail?: string; status?: string }> }>("/alerts");
export const getStatus = () => request<{ demo_mode?: boolean }>("/status");
