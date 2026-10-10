import type { MealMacros } from "@/types/contracts";

export interface MealPreset {
  id: string;
  label: string;
  macros: MealMacros;
}

/** kcal from macros (Atwater 4/4/9), so a preset's calories always match its macros. */
export const atwaterKcal = (m: Pick<MealMacros, "carbs_g" | "protein_g" | "fat_g">) =>
  Math.round(4 * m.carbs_g + 4 * m.protein_g + 9 * m.fat_g);

const preset = (
  id: string,
  label: string,
  carbs_g: number,
  protein_g: number,
  fat_g: number,
  fiber_g: number,
): MealPreset => ({
  id,
  label,
  macros: {
    carbs_g,
    protein_g,
    fat_g,
    fiber_g,
    calories_kcal: atwaterKcal({ carbs_g, protein_g, fat_g }),
  },
});

/**
 * Typical meals: the median carbs/protein/fat/fiber of each logged meal type in the
 * CGMacros study (1,706 meals, 45 participants). Calories are recomputed from the
 * macros so the numbers stay internally consistent.
 */
export const MEAL_PRESETS: MealPreset[] = [
  preset("breakfast", "Typical breakfast", 66, 22, 10, 0),
  preset("lunch", "Typical lunch", 76, 32, 19, 5),
  preset("dinner", "Typical dinner", 42, 21, 13, 4),
  preset("snack", "Typical snack", 23, 3, 3, 2),
];
