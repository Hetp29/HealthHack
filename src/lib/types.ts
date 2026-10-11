/**
 * Types for the GLIMPSE v2 API, written from the published contract
 * (`contracts/v2/openapi.json`) and the captured examples in `contracts/v2/examples/`.
 * Field names are the backend's; nothing here is invented.
 */

export type DataClass = "research_dataset" | "demo" | "user_device";
export type GlucoseUnit = "mg/dL" | "mmol/L";

export interface Macros {
  carbs_g: number;
  protein_g: number;
  fat_g: number;
  fiber_g: number;
  calories_kcal: number;
}
export const MACRO_KEYS = ["carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal"] as const;
export type MacroKey = (typeof MACRO_KEYS)[number];

export interface Consent {
  consent_type: "necessary_processing" | "personalization_history";
  state: "granted" | "withdrawn" | "not_recorded";
  recorded_at: string | null;
  policy_version: string | null;
}

export interface User {
  user_id: string;
  email: string;
  display_name: string | null;
  timezone: string;
  participant_id: string;
  consents: Consent[];
  created_at: string;
}

export interface AuthResponse {
  user: User;
  session: { token: string; token_type: "Bearer"; expires_at: string };
}

export interface Onboarding {
  answers: Record<string, unknown>;
  completed: boolean;
  version: number;
  updated_at: string | null;
  sections: Record<string, string[]>;
}

export interface ServiceStatus {
  model: {
    model_id: string | null;
    artifact_sha256: string | null;
    integrity_verified: boolean | null;
    integrity_enforced: boolean | null;
    loaded: boolean;
  };
  personalization_policy: string;
  demo_mode: boolean;
  meal_analysis: { provider: string; configured: boolean; model: string | null };
  notification_channels: Record<string, { available: boolean; reason?: string }>;
}

// ---- glucose ---------------------------------------------------------------

export interface GlucoseReading {
  observation_id: string;
  observed_at: string;
  received_at: string;
  glucose_mg_dl: number;
  provider: string;
  device_id: string;
  record_kind: "cgm" | "scan" | "fingerstick";
  data_class: DataClass;
  corrected: boolean;
}

export interface CgmReadings {
  start: string;
  end: string;
  unit: GlucoseUnit;
  truncated: boolean;
  readings: GlucoseReading[];
  gaps: Array<{ start: string; end: string; minutes: number }>;
}

export interface CgmStatus {
  as_of: string;
  freshness: "fresh" | "delayed" | "stale" | "no_data";
  latest: GlucoseReading | null;
  latest_age_minutes: number | null;
  forecast_ready: boolean;
  forecast_requirement: string;
  data_class: DataClass | null;
  last_24h: { readings: number; median_interval_minutes: number | null; coverage: number | null };
}

export interface IngestSummary {
  batch_id: string;
  provider: string;
  data_class: DataClass;
  counts: {
    received: number;
    inserted: number;
    duplicates: number;
    corrected: number;
    rejected: number;
    rejected_by_reason: Record<string, number>;
  };
}

// ---- meals -----------------------------------------------------------------

export interface EstimateItem {
  name: string;
  portion_description: string;
  estimated_weight_g: number | null;
  visibility: "visible" | "partially_visible" | "assumed_hidden";
  confidence: "low" | "medium" | "high";
  notes: string | null;
}

export interface MealEstimate {
  estimate_id: string;
  status: "ok" | "not_food";
  is_estimate: true;
  needs_user_confirmation: true;
  totals: Macros | null;
  ranges: Partial<Record<MacroKey, { low: number; high: number }>> | null;
  items: EstimateItem[];
  overall_confidence: "low" | "medium" | "high" | null;
  assumptions: string[];
  ambiguities: string[];
  clarification_questions: string[];
  not_food_reason: string | null;
  warnings: Array<{ code: string }>;
  provenance: {
    provider: string;
    ai_model_requested: string;
    ai_model_reported: string | null;
    prompt_version: string;
    method: string;
  };
  created_at: string;
}

export interface MealConfirmation extends Macros {
  confirmation_id: string;
  revision: number;
  source: "manual" | "ai_accepted" | "ai_edited" | "ai_unconfirmed";
  confirmed_at: string;
  based_on_estimate_id: string | null;
}

export interface Meal {
  meal_id: string;
  occurred_at: string;
  entry_method: "photo" | "manual";
  status: "draft" | "analyzed" | "confirmed" | "voided";
  note: string | null;
  consumption_status: "as_logged" | "partial" | "not_eaten";
  photo: {
    photo_id: string;
    content_type: string;
    width: number;
    height: number;
    url: string;
  } | null;
  estimate: MealEstimate | null;
  confirmation: MealConfirmation | null;
  prediction_id: string | null;
  created_at: string;
}

// ---- forecasts -------------------------------------------------------------

export interface ForecastPoint {
  minute: number;
  glucose_mg_dl: number;
}

/** The frozen contract v1.0 `ForecastResponse`, embedded unchanged by v2. */
export interface Forecast {
  participant_id: string;
  meal_timestamp: string;
  model_version: string;
  data_source: string;
  prediction_status: "personalized" | "generalized_fallback";
  points: ForecastPoint[];
  glucose_at_minutes: Record<"30" | "60" | "90" | "120", number>;
  metrics: {
    peak_mg_dl: number;
    peak_delta_mg_dl: number;
    time_to_peak_minutes: number;
    incremental_auc_mg_dl_min: number;
  };
  drivers: Array<{ feature: string; contribution_mg_dl: number; explanation: string }>;
  uncertainty_status: "not_calibrated" | "empirical_holdout";
  limitations: string[];
}

export interface AlertPayload {
  label: string;
  threshold: { value: number; unit: GlucoseUnit; mg_dl: number };
  predicted_crossing_minute: number;
  predicted_peak: { mg_dl: number; minute: number; in_user_unit: number };
  meal_id: string;
  forecast_generated_at: string;
  latest_cgm_age_minutes: number | null;
  uncertainty_status: string;
  informational_only: boolean;
  disclaimer: string;
}

export interface AlertEvent {
  alert_id: string;
  prediction_id: string;
  kind: string;
  status: "active" | "suppressed_quiet_hours" | "suppressed_cooldown" | "suppressed_retrospective";
  payload: AlertPayload;
  created_at: string;
  acknowledged_at: string | null;
}

export interface Prediction {
  prediction_id: string;
  meal_id: string;
  created_at: string;
  replayed: boolean;
  prediction_kind: "prospective" | "retrospective";
  inputs_confirmed: boolean;
  model: {
    model_id: string;
    model_version: string;
    feature_set_version: string;
    artifact_sha256: string;
  };
  personalization: {
    policy: string;
    consent: boolean;
    admissible_prior_meals: number;
    retrieval_active: boolean;
    retrieval_min_history: number;
    calibration_applied: boolean;
  };
  context_quality: {
    latest_cgm_age_minutes: number | null;
    cgm_readings_last_60_min: number | null;
    cgm_device: { provider: string; device_id: string; data_class: DataClass } | null;
    multi_device_overlap: boolean | null;
    missing_features: string[];
    history_truncated_at_offset_change: boolean;
  };
  /** `not_applicable`: a hypothetical demonstration meal, which never has an outcome. */
  outcome_status: "pending" | "scored" | "insufficient_data" | "voided" | "not_applicable" | null;
  outcome_not_before: string | null;
  alert: (Pick<AlertEvent, "alert_id" | "status" | "kind"> & { payload: AlertPayload }) | null;
  forecast: Forecast;
}

export interface Outcome {
  episode_id: string | null;
  prediction_id: string;
  meal_id: string;
  status: "pending" | "scored" | "insufficient_data" | "voided" | "not_applicable";
  /** Why there is no outcome, when `status` is `not_applicable`. */
  reason?: string;
  message?: string;
  window_end_at: string | null;
  scoring_not_before: string | null;
  scored_at: string | null;
  eligible_for_personalization: boolean;
  ineligibility_reasons: string[];
  coverage: {
    readings_in_window: number;
    required_readings: number;
    horizons_missing: number[];
  } | null;
  observed: {
    baseline_mg_dl: number;
    curve: ForecastPoint[];
    peak_mg_dl?: number;
    peak_delta_mg_dl?: number;
    time_to_peak_minutes?: number;
  } | null;
  errors: {
    by_horizon: Record<
      string,
      { predicted_mg_dl: number; observed_mg_dl: number; error_mg_dl: number }
    >;
    mae_mg_dl?: number;
    bias_mg_dl?: number;
    peak_error_mg_dl?: number;
  } | null;
  predicted: { glucose_at_minutes: Forecast["glucose_at_minutes"]; metrics: Forecast["metrics"] };
}

// ---- wearables / devices ---------------------------------------------------

export interface WearableObservation {
  observation_id: string;
  provider: string;
  metric: string;
  value: number;
  unit: string;
  observed_at: string;
  observed_end_at: string | null;
  received_at: string;
  data_class: DataClass;
  detail: {
    simulated?: boolean;
    device_profile?: string;
    source_label?: string;
    sport?: string;
    local_date?: string;
  };
  used_by_forecast_model: false;
}

export type DeviceProfileId = "apple_health" | "apple_watch" | "fitbit" | "whoop";

export interface DemoDeviceProfile {
  profile: DeviceProfileId;
  provider: string;
  label: string;
  display_label: string;
  kind: "health_data_platform" | "wearable_device";
  description: string;
  status: "inactive" | "demo_simulated";
  simulated: true;
  data_class: "demo";
  authorization: string;
  metrics: Array<{
    metric: string;
    unit: string;
    observations_visible: number;
    latest_observed_at: string | null;
  }>;
  notes: Record<string, string>;
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
    simulated: boolean;
    data_class: DataClass;
    status: string;
    description: string;
    readings_visible: number;
    latest_observed_at: string | null;
  };
  used_by_forecast_model: {
    glucose_source: boolean;
    simulated_profiles: boolean;
    explanation: string;
  };
}

export interface DeviceConnection {
  connection_id: string;
  provider: string;
  kind: "cgm" | "wearable";
  status:
    | "pending"
    | "authorized"
    | "connected"
    | "error"
    | "revoked"
    | "disconnected"
    | "demo_simulated";
  last_success_at: string | null;
  authentic_data_received: boolean;
}

export interface ProviderCapability {
  provider: string;
  label: string;
  kind: string;
  integration: string;
  configured: boolean;
  metrics: string[];
  not_available: Record<string, string>;
}

export interface TimelineItem {
  at: string;
  type: "wearable" | "meal" | "alert" | "medication_dose";
  data_class: string;
  simulated: boolean;
  source: string | null;
  metric?: string;
  value?: number;
  unit?: string;
  meal_id?: string;
  status?: string;
  action?: string;
  detail?: { sport?: string; local_date?: string };
}

// ---- medications -----------------------------------------------------------

export interface Regimen {
  regimen_id: string;
  name: string;
  medication_class: string | null;
  form: string | null;
  dose: { amount: number | null; unit: string | null };
  frequency: string | null;
  schedule_times: string[];
  timezone: string;
  start_date: string | null;
  end_date: string | null;
  status: "current" | "discontinued";
  reminders_enabled: boolean;
  prescriber_note: string | null;
}

export interface Dose {
  dose_id: string;
  regimen_id: string;
  scheduled_at: string | null;
  action: "scheduled" | "taken" | "skipped" | "snoozed" | "unknown";
  actual_at: string | null;
  snoozed_until: string | null;
  actor: "user" | "system";
  recorded_at: string;
  regimen?: { name: string; dose: { amount: number | null; unit: string | null } };
}

export interface Education {
  content_version: string;
  review_status: string;
  limitation: string;
  entries: Array<{
    medication_class: string;
    title: string;
    summary: string;
    hypoglycemia_note: string;
    sources: Array<{ publisher: string; title: string; url: string }>;
  }>;
}

export interface AlertSettings {
  enabled: boolean;
  threshold: { value: number; unit: GlucoseUnit; mg_dl: number };
  channels: Array<{ channel: "in_app" | "push" | "email"; available: boolean }>;
  available_channels: string[];
  quiet_hours: { start: string | null; end: string | null };
  cooldown_minutes: number;
  disclaimer: string;
}

// ---- demonstration replay --------------------------------------------------

export interface CheckpointResult {
  meal_id?: string;
  prediction_id?: string;
  forecast?: "created" | "refused";
  code?: string;
  admissible_prior_meals?: number;
  retrieval_active?: boolean;
  alert_id?: string | null;
  alert_status?: string | null;
  outcome?: string;
  eligible_for_personalization?: boolean;
  ineligibility_reasons?: string[];
}

export interface Checkpoint {
  id: string;
  kind: "meal_forecast" | "outcome_reveal";
  at: string;
  meal: number | null;
  phase: "backfill" | "display";
  done: boolean;
  result: CheckpointResult | null;
}

export interface ReplayState {
  scenario_id: string;
  mode: "new_user" | "returning_user";
  mode_label: string;
  display_from_meal: number;
  status: "ready" | "complete";
  position: string;
  event_index: number;
  event_count: number;
  wall_clock: string;
  window: { start: string; end: string; timezone: string };
  next_checkpoint:
    | (Pick<Checkpoint, "id" | "kind" | "at" | "meal"> & {
        meal_record?: Macros & { occurred_at: string; source: string };
      })
    | null;
  aliases: Record<string, string>;
  checkpoints: Checkpoint[];
  personalization_progress: {
    admissible_prior_meals: number | null;
    retrieval_active: boolean | null;
    retrieval_min_history: number;
    description: string;
  };
  data: {
    readings_released: number;
    readings_total: number;
    meals_logged: number;
    meals_total: number;
  };
  provenance: {
    glucose_and_meals: { data_class: string; source: string | null; description: string };
    profile_and_medications: { data_class: string; description: string };
    forecasts: { data_class: string; description: string };
    labels: string[];
    backfilled_history?: { meals: number; description: string };
  };
  advanced?: boolean;
}
