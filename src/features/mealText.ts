import type { Macros, Meal, Prediction } from "@/lib/types";

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

/**
 * The forecast to present as "latest", with the meal it belongs to.
 *
 * Only a meal's current forecast counts: one made from values the user has since
 * changed, or for a meal that was deleted, is history, not the latest word.
 *
 * In a demonstration every record made at one replay position carries the same
 * timestamp, and the server keeps no insertion order to separate them. A meal the
 * person added at that position was necessarily added after the replay logged its
 * own, so it is preferred; any remaining tie is broken the same way every time.
 */
export function latestForecast(
  predictions: Prediction[],
  meals: Meal[],
): { prediction: Prediction; meal: Meal } | null {
  const byId = new Map(meals.map((meal) => [meal.meal_id, meal]));
  const current = predictions.flatMap((prediction) => {
    const meal = byId.get(prediction.meal_id);
    return meal && meal.prediction_id === prediction.prediction_id ? [{ prediction, meal }] : [];
  });
  current.sort(
    (a, b) =>
      b.prediction.created_at.localeCompare(a.prediction.created_at) ||
      Number(isRecordedMeal(a.meal)) - Number(isRecordedMeal(b.meal)) ||
      b.meal.occurred_at.localeCompare(a.meal.occurred_at) ||
      a.prediction.prediction_id.localeCompare(b.prediction.prediction_id),
  );
  return current[0] ?? null;
}
