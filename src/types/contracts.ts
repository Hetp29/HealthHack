/**
 * Glimpse Contract v1.0 — frontend types.
 *
 * FROZEN (from directive §5): PredictRequest / PredictResponse.
 * ASSUMED ADAPTERS: Participant, History, ModelInfo, ModelMetrics, Health.
 * These adapter shapes are documented assumptions until the backend team
 * supplies authoritative schemas — they are NOT frozen contracts.
 */

export type DataMode = "live" | "fixture";

export interface MealMacros {
  carbs_g: number;
  protein_g: number;
  fat_g: number;
  fiber_g: number;
  calories_kcal: number;
}

export interface PredictRequest {
  participant_id: string;
  meal_timestamp: string; // RFC3339
  meal: MealMacros;
  context_override: Record<string, unknown> | null;
}

export interface PredictionPoint {
  minute: number;
  glucose_mg_dl: number;
  lower_mg_dl: number | null;
  upper_mg_dl: number | null;
}

export const HORIZONS = ["30", "60", "90", "120"] as const;
export type Horizon = (typeof HORIZONS)[number];

export interface PredictionDriver {
  feature: string;
  contribution_mg_dl: number;
  explanation: string | null;
}

export interface PredictResponse {
  participant_id: string;
  meal_timestamp: string;
  model_version: string;
  data_source: string;
  prediction_status: string;
  points: PredictionPoint[];
  glucose_at_minutes: Record<Horizon, number>;
  metrics: {
    peak_mg_dl: number;
    peak_delta_mg_dl: number;
    time_to_peak_minutes: number;
    incremental_auc_mg_dl_min: number;
  };
  drivers: PredictionDriver[];
  uncertainty_status: string;
  limitations: string[];
}

export interface Participant {
  participant_id: string;
  label?: string | null | undefined;
  observation_start?: string | null | undefined;
  observation_end?: string | null | undefined;
  /** Backend-suggested valid Day-8 prediction timestamp, if exposed. */
  prediction_timestamp?: string | null | undefined;
}

export interface ParticipantsResponse {
  data_source: string;
  participants: Participant[];
}

export interface GlucoseObservation {
  timestamp: string;
  glucose_mg_dl: number;
}

export interface HeartRateObservation {
  timestamp: string;
  heart_rate_bpm: number;
}

export interface HistoricalMeal {
  timestamp: string;
  carbs_g: number | null;
  protein_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  calories_kcal: number | null;
}

export interface HistoryResponse {
  participant_id: string;
  data_source: string;
  observation_start: string | null;
  observation_end: string | null;
  glucose: GlucoseObservation[];
  heart_rate: HeartRateObservation[];
  meals: HistoricalMeal[];
}

export interface HealthResponse {
  status: string;
  model_loaded?: boolean | null | undefined;
  data_source?: string | null | undefined;
}

export interface ModelInfo {
  model_version: string;
  data_source: string;
  feature_set_version?: string | null | undefined;
  evaluation_protocol?: string | null | undefined;
  evaluated_at?: string | null | undefined;
  limitations?: string[] | null | undefined;
}

export interface ErrorPair {
  mae_mg_dl: number;
  rmse_mg_dl: number;
}

export interface HorizonError extends ErrorPair {
  horizon_minutes: number;
}

export interface ModelMetrics {
  model_version: string;
  train_samples?: number | null | undefined;
  eval_samples?: number | null | undefined;
  participant_count?: number | null | undefined;
  overall?: ErrorPair | null | undefined;
  by_horizon?: HorizonError[] | null | undefined;
  personalization?: {
    generalized: ErrorPair;
    personalized: ErrorPair;
    win_rate?: number | null | undefined;
    win_rate_definition?: string | null | undefined;
    compared_samples?: number | null | undefined;
  } | null | undefined;
}

/* ---------- NEIL PROPOSED CONTRACTS (v2) ---------- */

export interface QuestionnaireAnswers {
  // Section 1: Basics
  dob?: string;
  sex_assigned_at_birth?: 'male' | 'female' | 'intersex' | 'prefer_not_to_say';
  height_cm?: number;
  weight_kg?: number;
  primary_goal?: 'meal_responses' | 'spike_awareness' | 'diabetes_mgmt' | 'metabolic_exploration' | 'other';
  
  // Section 2: Metabolic
  diagnosis_status?: 'type_1' | 'type_2' | 'prediabetes' | 'gestational' | 'unknown' | 'none';
  diagnosis_year?: number;
  hba1c_value?: number;
  hba1c_unit?: '%' | 'mmol/mol';
  fasting_glucose_value?: number;
  fasting_glucose_unit?: 'mg/dL' | 'mmol/L';
  clinician_plan_summary?: string;

  // Section 3: Monitoring
  monitoring_method?: 'cgm' | 'finger_prick' | 'both' | 'none';
  cgm_brand?: string;
  connect_preference?: 'now' | 'later' | 'no';
  cgm_usage_duration?: string;

  // Section 4: Meds
  takes_glucose_meds?: boolean;
  prescribed_medications?: Array<{
    name: string;
    dose: string;
    unit: string;
    schedule: string;
    times_per_day?: number;
    since?: string;
  }>;
  uses_insulin?: boolean;
  wants_medication_reminders?: boolean;

  // Section 5: Meals
  meals_per_day?: number;
  meals?: Array<{
    name: string;
    details?: string;
  }>;
  dietary_pattern?: string;
  dietary_restrictions?: string[];
  meal_logging_method?: 'photo' | 'manual' | 'both';

  // Section 6: Wearables
  uses_wearable?: boolean;
  wearable_brand?: string;
  wearable_version?: string;
  wearable_other?: string;
  wearable_connect_preference?: 'now' | 'later' | 'no';

  // Section 7: Alerts
  enable_spike_alerts?: boolean;
  spike_threshold_value?: number;
  spike_threshold_unit?: 'mg/dL' | 'mmol/L';
  alert_channels?: ('push' | 'email' | 'in_app')[];
  quiet_hours_start?: string; // HH:mm
  quiet_hours_end?: string;   // HH:mm

  // Section 8: Consent
  necessary_processing_consent: boolean; // MUST BE TRUE
  optional_personal_history_consent: boolean;
}

export type DoseStatus = 'taken' | 'skipped' | 'snoozed' | 'unknown';

export interface MedicationRegimen {
  regimen_id: string;
  user_id: string;
  drug_name: string;
  dose: string;
  unit: string;
  schedule: string;
  status: 'active' | 'discontinued';
  start_date?: string;
  end_date?: string;
}

export interface DoseEvent {
  dose_id: string;
  regimen_id: string;
  scheduled_at: string; // ISO 8601 UTC
  actual_at?: string | null;
  status: DoseStatus;
  actor: 'user' | 'system';
}

export interface MedicationEducation {
  drug_class: string;
  potential_glucose_effect: string;
  hypo_risk: boolean;
  source_citation: string;
  verification_date: string;
}

export interface AlertRule {
  rule_id: string;
  user_id: string;
  enabled: boolean;
  threshold_mg_dl: number;
  channels: ('push' | 'email' | 'in_app')[];
  quiet_hours_start?: string;
  quiet_hours_end?: string;
}