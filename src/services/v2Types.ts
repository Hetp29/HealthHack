import type { MealMacros, PredictResponse } from "@/types/contracts";

export interface V2Session {
  token: string;
  token_type: "Bearer";
  expires_at: string;
}

export interface V2Consent {
  consent_type: string;
  state: "granted" | "withdrawn" | "not_recorded";
  recorded_at: string | null;
  policy_version: string | null;
}

export interface V2User {
  user_id: string;
  email: string;
  display_name: string;
  timezone: string;
  participant_id: string;
  consents: V2Consent[];
  created_at: string;
}

export interface AuthResponse {
  user: V2User;
  session: V2Session;
}

export interface ReplayState {
  scenario_id: string;
  mode: "new_user" | "returning_user";
  mode_label: "NEW USER" | "RETURNING USER";
  display_from_meal: number;
  status: "ready" | "complete";
  position: string;
  event_index: number;
  event_count: number;
  wall_clock: string;
  window: { start: string; end: string; timezone: string };
  next_checkpoint: {
    id: string;
    kind: string;
    at: string;
    meal?: number;
    meal_record?: Record<string, unknown>;
  } | null;
  checkpoints: Array<Record<string, unknown>>;
  aliases: Record<string, string>;
  personalization_progress: {
    admissible_prior_meals: number;
    retrieval_active: boolean;
    retrieval_min_history: number;
    message: string;
  };
  data: { readings: number; meals: number; total_readings?: number; total_meals?: number };
  provenance: {
    labels?: Record<string, string>;
    backfilled_history?: { meals: number; description: string };
    [key: string]: unknown;
  };
}

export interface ReplayAdvanceResponse extends ReplayState {
  advanced: boolean;
  result?: {
    meal_id?: string;
    prediction_id?: string;
    forecast?: "created" | "refused";
    code?: string;
    alert_id?: string | null;
    alert_status?: string | null;
  };
}

export interface DemoDeviceProfile {
  profile: "apple_health" | "apple_watch" | "fitbit" | "whoop";
  provider: string;
  label: string;
  display_label: string;
  kind: string;
  description: string;
  status: "inactive" | "demo_simulated";
  simulated: true;
  data_class: "demo";
  authorization: string;
  metrics: Array<{ metric: string; unit: string; observations_visible: number; latest_observed_at: string | null }>;
}

export interface DemoDevices {
  position: string;
  simulation_version: string;
  disclaimer: string;
  profiles: DemoDeviceProfile[];
  glucose_source: {
    provider: string;
    label: string;
    kind: string;
    simulated: false;
    data_class: "research_dataset";
    status: string;
    description: string;
    readings_visible: number;
    latest_observed_at: string | null;
  };
}

export interface GlucoseReading {
  reading_id?: string;
  observed_at: string;
  glucose_mg_dl: number;
  data_class: "research_dataset" | "demo" | "user_device";
  provider: string;
}

export interface CgmReadings {
  start: string | null;
  end: string | null;
  readings: GlucoseReading[];
  data_class?: string;
  source?: Record<string, unknown>;
}

export interface WearableObservation {
  observation_id: string;
  provider: string;
  metric: string;
  value: number;
  unit: string;
  observed_at: string;
  observed_end_at?: string | null;
  received_at: string;
  data_class: "demo" | "user_device";
  detail: { simulated?: boolean; device_profile?: string; source_label?: string; [key: string]: unknown };
  used_by_forecast_model: false;
}

export interface WearableObservations {
  observations: WearableObservation[];
}

export interface V2Regimen {
  regimen_id: string;
  name: string;
  medication_class: string;
  form: string;
  dose: { amount: number; unit: string };
  frequency: string;
  schedule_times: string[];
  timezone: string;
  start_date: string | null;
  end_date: string | null;
  status: "current" | "discontinued";
  reminders_enabled: boolean;
  prescriber_note: string | null;
}

export interface V2Dose {
  dose_id: string;
  regimen_id: string;
  scheduled_at: string | null;
  action: "taken" | "skipped" | "snoozed" | "unknown" | "scheduled";
  actual_at: string | null;
  snoozed_until: string | null;
  actor: "user" | "system";
  regimen?: { name: string; dose: { amount: number; unit: string } };
}

export interface V2Meal {
  meal_id: string;
  occurred_at: string;
  macros: MealMacros | null;
  status?: string;
  prediction_id?: string | null;
}

export interface MealEstimate {
  estimate_id: string;
  status: string;
  is_estimate: boolean;
  needs_user_confirmation: boolean;
  totals: MealMacros;
  ranges?: Partial<Record<keyof MealMacros, { low: number; high: number }>>;
  items: Array<{
    name: string;
    portion_description: string;
    estimated_weight_g: number | null;
    visibility: string;
    confidence: string;
    notes: string | null;
  }>;
  overall_confidence?: string;
  assumptions?: string[];
  ambiguities?: string[];
  clarification_questions?: string[];
  warnings?: Array<{ code: string }>;
  provenance?: { provider: string; method: string; [key: string]: unknown };
}

export interface MealPhotoUpload {
  meal_id: string;
  occurred_at: string;
  entry_method: "photo";
  status: "draft" | string;
  estimate: MealEstimate | null;
  photo: { photo_id: string; content_type: string; url: string };
}

export interface V2MealsResponse {
  meals: V2Meal[];
}

export interface V2Prediction {
  prediction_id: string;
  meal_id: string;
  prediction_kind: "prospective" | string;
  personalization?: { admissible_prior_meals: number; retrieval_active: boolean; retrieval_min_history: number };
  forecast: PredictResponse;
  outcome_status?: "pending" | "scored" | string;
  outcome_not_before?: string | null;
  alert?: Record<string, unknown> | null;
}

export interface V2Outcome {
  status: "pending" | "scored";
  scoring_not_before?: string;
  forecast?: PredictResponse;
  observed?: { curve: Array<{ minute: number; glucose_mg_dl: number }> } | null;
  eligible_for_personalization?: boolean;
  ineligibility_reasons?: string[];
  errors?: { by_horizon?: unknown[]; mae_mg_dl?: number };
}
