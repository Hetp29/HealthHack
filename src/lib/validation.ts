import { z } from "zod";
import type { MealMacros } from "@/types/contracts";

const finite = z.number().finite();
const nullableFinite = finite.nullable().optional().transform((v) => v ?? null);

export const predictResponseSchema = z
  .object({
    participant_id: z.string(),
    meal_timestamp: z.string(),
    model_version: z.string(),
    data_source: z.string(),
    prediction_status: z.string(),
    points: z
      .array(
        z.object({
          minute: finite,
          glucose_mg_dl: finite,
          lower_mg_dl: nullableFinite,
          upper_mg_dl: nullableFinite,
        }),
      )
      .length(25, "points must contain exactly 25 entries")
      .refine((pts) => pts.every((p, i) => p.minute === i * 5), {
        message: "points must be sorted at minutes 0,5,…,120",
      }),
    glucose_at_minutes: z.object({ "30": finite, "60": finite, "90": finite, "120": finite }),
    metrics: z.object({
      peak_mg_dl: finite,
      peak_delta_mg_dl: finite,
      time_to_peak_minutes: finite,
      incremental_auc_mg_dl_min: finite,
    }),
    drivers: z.array(
      z.object({
        feature: z.string(),
        contribution_mg_dl: finite,
        explanation: z.string().nullable().optional().transform((v) => v ?? null),
      }),
    ),
    uncertainty_status: z.string(),
    limitations: z.array(z.string()),
  });

const participantSchema = z.object({
  participant_id: z.string(),
  label: z.string().nullable().optional(),
  observation_start: z.string().nullable().optional(),
  observation_end: z.string().nullable().optional(),
  prediction_timestamp: z.string().nullable().optional(),
});

export const participantsResponseSchema = z.object({
  data_source: z.string(),
  participants: z.array(participantSchema),
});

const nullableNum = finite.nullable();
export const historyResponseSchema = z.object({
  participant_id: z.string(),
  data_source: z.string(),
  observation_start: z.string().nullable(),
  observation_end: z.string().nullable(),
  glucose: z.array(z.object({ timestamp: z.string(), glucose_mg_dl: finite })),
  heart_rate: z.array(z.object({ timestamp: z.string(), heart_rate_bpm: finite })),
  meals: z.array(
    z.object({
      timestamp: z.string(),
      carbs_g: nullableNum,
      protein_g: nullableNum,
      fat_g: nullableNum,
      fiber_g: nullableNum,
      calories_kcal: nullableNum,
    }),
  ),
});

export const healthResponseSchema = z.object({
  status: z.string(),
  model_loaded: z.boolean().nullable().optional(),
  data_source: z.string().nullable().optional(),
});

export const modelInfoSchema = z.object({
  model_version: z.string(),
  data_source: z.string(),
  feature_set_version: z.string().nullable().optional(),
  evaluation_protocol: z.string().nullable().optional(),
  evaluated_at: z.string().nullable().optional(),
  limitations: z.array(z.string()).nullable().optional(),
});

const errorPair = z.object({ mae_mg_dl: finite, rmse_mg_dl: finite });
export const modelMetricsSchema = z.object({
  model_version: z.string(),
  train_samples: finite.nullable().optional(),
  eval_samples: finite.nullable().optional(),
  participant_count: finite.nullable().optional(),
  overall: errorPair.nullable().optional(),
  by_horizon: z.array(errorPair.extend({ horizon_minutes: finite })).nullable().optional(),
  personalization: z
    .object({
      generalized: errorPair,
      personalized: errorPair,
      win_rate: finite.nullable().optional(),
      win_rate_definition: z.string().nullable().optional(),
      compared_samples: finite.nullable().optional(),
    })
    .nullable()
    .optional(),
});

/* ---------------- Meal form validation ---------------- */

export type MealFormValues = Record<keyof MealMacros, string>;
export type MealFormErrors = Partial<Record<keyof MealMacros | "meal_timestamp", string>>;

/** Documented plausibility bounds for a single meal. Values above are rejected, not altered. */
export const MEAL_BOUNDS: Record<keyof MealMacros, number> = {
  carbs_g: 400,
  protein_g: 250,
  fat_g: 250,
  fiber_g: 100,
  calories_kcal: 5000,
};

export function validateMeal(
  values: MealFormValues,
  timestamp: string,
): { errors: MealFormErrors; meal: MealMacros | null } {
  const errors: MealFormErrors = {};
  const out: Partial<MealMacros> = {};
  (Object.keys(MEAL_BOUNDS) as (keyof MealMacros)[]).forEach((k) => {
    const raw = values[k].trim();
    if (raw === "") return void (errors[k] = "Required");
    const n = Number(raw);
    if (!Number.isFinite(n)) return void (errors[k] = "Must be a number");
    if (n < 0) return void (errors[k] = "Cannot be negative");
    if (n > MEAL_BOUNDS[k]) return void (errors[k] = `Exceeds plausible bound (${MEAL_BOUNDS[k]})`);
    out[k] = n;
  });
  if (out.fiber_g !== undefined && out.carbs_g !== undefined && out.fiber_g > out.carbs_g) {
    errors.fiber_g = "Fiber cannot exceed total carbohydrates";
  }
  if (!timestamp) errors.meal_timestamp = "Select a meal time";
  else if (Number.isNaN(Date.parse(timestamp))) errors.meal_timestamp = "Invalid timestamp";
  const ok = Object.keys(errors).length === 0;
  return { errors, meal: ok ? (out as MealMacros) : null };
}
