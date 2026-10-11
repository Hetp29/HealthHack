import type { Macros, Meal } from "@/lib/types";

/** The note the replay writes on meals it logs from the recording. */
const REPLAY_NOTE = "Research dataset meal record";

/** True for a meal the demonstration replay logged from the recording itself. */
export const isRecordedMeal = (meal: Pick<Meal, "note">) =>
  meal.note?.startsWith(REPLAY_NOTE) ?? false;

export function macroSummary(macros: Macros | null | undefined): string {
  if (!macros) return "Nutrition not confirmed yet";
  return `${Math.round(macros.carbs_g)} g carbs · ${Math.round(macros.protein_g)} g protein · ${Math.round(macros.fat_g)} g fat · ${Math.round(macros.calories_kcal)} kcal`;
}

export const MACRO_FIELDS = [
  { key: "carbs_g", label: "Carbohydrates", unit: "g", max: 1000 },
  { key: "protein_g", label: "Protein", unit: "g", max: 1000 },
  { key: "fat_g", label: "Fat", unit: "g", max: 1000 },
  { key: "fiber_g", label: "Fibre", unit: "g", max: 200 },
  { key: "calories_kcal", label: "Calories", unit: "kcal", max: 10000 },
] as const;

export function mealStatus(meal: Meal): { label: string; tone: "neutral" | "teal" | "amber" } {
  if (meal.prediction_id) return { label: "Forecast ready", tone: "teal" };
  if (meal.status === "confirmed") return { label: "Confirmed, no forecast", tone: "neutral" };
  if (meal.status === "analyzed") return { label: "Needs your review", tone: "amber" };
  return { label: "Draft", tone: "amber" };
}
