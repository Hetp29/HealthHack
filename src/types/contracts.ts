/**
 * GlucoTwin Contract v1.0 — frontend types.
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

/* ---------- ASSUMED adapter schemas ---------- */

export interface Participant {
  participant_id: string;
  label?: string | null;
  observation_start?: string | null;
  observation_end?: string | null;
  /** Backend-suggested valid Day-8 prediction timestamp, if exposed. */
  prediction_timestamp?: string | null;
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
  model_loaded?: boolean | null;
  data_source?: string | null;
}

export interface ModelInfo {
  model_version: string;
  data_source: string;
  feature_set_version?: string | null;
  evaluation_protocol?: string | null;
  evaluated_at?: string | null;
  limitations?: string[] | null;
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
  train_samples?: number | null;
  eval_samples?: number | null;
  participant_count?: number | null;
  overall?: ErrorPair | null;
  by_horizon?: HorizonError[] | null;
  personalization?: {
    generalized: ErrorPair;
    personalized: ErrorPair;
    win_rate?: number | null;
    win_rate_definition?: string | null;
    compared_samples?: number | null;
  } | null;
}
