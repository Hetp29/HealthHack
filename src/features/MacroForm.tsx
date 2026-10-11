import { Field, inputClass } from "@/components/glimpse/primitives";
import type { MacroKey, Macros, MealEstimate } from "@/lib/types";

import { MACRO_FIELDS } from "./mealText";

export type MacroDraft = Record<MacroKey, string>;

export const emptyMacros = (): MacroDraft => ({
  carbs_g: "",
  protein_g: "",
  fat_g: "",
  fiber_g: "",
  calories_kcal: "",
});
export const macrosToDraft = (macros: Macros): MacroDraft => ({
  carbs_g: String(macros.carbs_g),
  protein_g: String(macros.protein_g),
  fat_g: String(macros.fat_g),
  fiber_g: String(macros.fiber_g),
  calories_kcal: String(macros.calories_kcal),
});

/** Each value's problem, if any. The limits are the server's own. */
export function macroErrors(draft: MacroDraft): Partial<Record<MacroKey, string>> {
  const errors: Partial<Record<MacroKey, string>> = {};
  for (const { key, label, max } of MACRO_FIELDS) {
    const raw = draft[key].trim();
    const value = Number(raw);
    if (raw === "") errors[key] = `Enter ${label.toLowerCase()}. Use 0 if there is none.`;
    else if (!Number.isFinite(value)) errors[key] = "Enter a number.";
    else if (value < 0) errors[key] = "This can't be negative.";
    else if (value > max) errors[key] = `Enter a value up to ${max.toLocaleString()}.`;
  }
  return errors;
}

export const draftToMacros = (draft: MacroDraft): Macros => ({
  carbs_g: Number(draft.carbs_g),
  protein_g: Number(draft.protein_g),
  fat_g: Number(draft.fat_g),
  fiber_g: Number(draft.fiber_g),
  calories_kcal: Number(draft.calories_kcal),
});

export function MacroForm({
  draft,
  onChange,
  errors,
  ranges,
  disabled,
}: {
  draft: MacroDraft;
  onChange: (draft: MacroDraft) => void;
  errors: Partial<Record<MacroKey, string>>;
  ranges?: MealEstimate["ranges"];
  disabled?: boolean;
}) {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
      {MACRO_FIELDS.map(({ key, label, unit }) => {
        const range = ranges?.[key];
        return (
          <Field
            key={key}
            id={`macro-${key}`}
            label={`${label} (${unit})`}
            error={errors[key]}
            hint={
              range ? `Estimated ${Math.round(range.low)} to ${Math.round(range.high)}` : undefined
            }
          >
            <input
              id={`macro-${key}`}
              inputMode="decimal"
              className={inputClass}
              value={draft[key]}
              disabled={disabled}
              onChange={(e) => onChange({ ...draft, [key]: e.target.value })}
              aria-invalid={!!errors[key]}
              aria-describedby={
                errors[key] ? `macro-${key}-error` : range ? `macro-${key}-hint` : undefined
              }
            />
          </Field>
        );
      })}
    </div>
  );
}
