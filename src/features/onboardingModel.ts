/**
 * The questionnaire, as the form holds it and as the server stores it.
 *
 * Only question ids the server recognises are ever sent (it rejects any other with 422),
 * and measurements always travel with their unit. Questions GLIMPSE does not ask yet
 * (`clinician_plan`, `medication_schedule`, `discontinued_medications`,
 * `meal_time_ranges`, `wearable_permissions`) are simply never sent.
 */
import type { GlucoseUnit } from "@/lib/types";

export interface Draft {
  display_name: string;
  date_of_birth: string;
  sex_assigned_at_birth: string | null;
  height: string;
  height_unit: "cm" | "in";
  weight: string;
  weight_unit: "kg" | "lb";
  primary_goal: string | null;

  diabetes_status: string | null;
  diagnosis_year: string;
  hba1c: string;
  hba1c_unit: "%" | "mmol/mol";
  hba1c_unknown: boolean;
  fasting_glucose: string;
  fasting_glucose_unit: GlucoseUnit;
  fasting_glucose_unknown: boolean;
  monitoring_method: string | null;
  cgm_brand: string | null;
  cgm_usage_duration: string | null;

  cgm_connect_preference: string | null;
  uses_wearable: string | null;
  wearable_platform: string | null;
  wearable_connect_preference: string | null;

  takes_glucose_medication: string | null;
  medication_names: string;
  uses_insulin: string | null;
  wants_medication_reminders: boolean | null;
  meals_per_day: string;
  dietary_pattern: string | null;
  dietary_restrictions: string;
  meal_logging_method: string | null;

  wants_spike_alerts: boolean | null;
  alert_threshold: string;
  alert_threshold_unit: GlucoseUnit;
  quiet_hours_enabled: boolean;
  quiet_start: string;
  quiet_end: string;
}

export type StepId =
  "welcome" | "consent" | "about" | "health" | "devices" | "routine" | "alerts" | "review";

const text = (value: unknown) => (typeof value === "string" ? value : "");
const choice = (value: unknown) => (typeof value === "string" && value ? value : null);
const flag = (value: unknown) => (typeof value === "boolean" ? value : null);

function measured<U extends string>(value: unknown, fallback: U, units: readonly U[]) {
  if (value && typeof value === "object" && "value" in value && "unit" in value) {
    const { value: amount, unit } = value as { value: unknown; unit: unknown };
    if (typeof amount === "number" && units.includes(unit as U))
      return { value: String(amount), unit: unit as U, unknown: false };
  }
  return { value: "", unit: fallback, unknown: value === "unknown" };
}

export function draftFrom(
  answers: Record<string, unknown>,
  displayName: string,
  preferredUnit: GlucoseUnit,
): Draft {
  const height = measured(answers["height"], "cm", ["cm", "in"] as const);
  const weight = measured(answers["weight"], "kg", ["kg", "lb"] as const);
  const hba1c = measured(answers["hba1c"], "%", ["%", "mmol/mol"] as const);
  const fasting = measured(answers["fasting_glucose"], preferredUnit, ["mg/dL", "mmol/L"] as const);
  const threshold = measured(answers["alert_threshold"], preferredUnit, [
    "mg/dL",
    "mmol/L",
  ] as const);
  const quiet = answers["quiet_hours"] as { start?: unknown; end?: unknown } | undefined;
  const names = answers["medication_names"];
  return {
    display_name: displayName,
    date_of_birth: text(answers["date_of_birth"]),
    sex_assigned_at_birth: choice(answers["sex_assigned_at_birth"]),
    height: height.value,
    height_unit: height.unit,
    weight: weight.value,
    weight_unit: weight.unit,
    primary_goal: choice(answers["primary_goal"]),
    diabetes_status: choice(answers["diabetes_status"]),
    diagnosis_year: answers["diagnosis_year"] ? String(answers["diagnosis_year"]) : "",
    hba1c: hba1c.value,
    hba1c_unit: hba1c.unit,
    hba1c_unknown: hba1c.unknown,
    fasting_glucose: fasting.value,
    fasting_glucose_unit: fasting.unit,
    fasting_glucose_unknown: fasting.unknown,
    monitoring_method: choice(answers["monitoring_method"]),
    cgm_brand: choice(answers["cgm_brand"]),
    cgm_usage_duration: choice(answers["cgm_usage_duration"]),
    cgm_connect_preference: choice(answers["cgm_connect_preference"]),
    uses_wearable: choice(answers["uses_wearable"]),
    wearable_platform: choice(answers["wearable_platform"]),
    wearable_connect_preference: choice(answers["wearable_connect_preference"]),
    takes_glucose_medication: choice(answers["takes_glucose_medication"]),
    medication_names: Array.isArray(names) ? names.join(", ") : text(names),
    uses_insulin: choice(answers["uses_insulin"]),
    wants_medication_reminders: flag(answers["wants_medication_reminders"]),
    meals_per_day: answers["meals_per_day"] ? String(answers["meals_per_day"]) : "",
    dietary_pattern: choice(answers["dietary_pattern"]),
    dietary_restrictions: text(answers["dietary_restrictions"]),
    meal_logging_method: choice(answers["meal_logging_method"]),
    wants_spike_alerts: flag(answers["wants_spike_alerts"]),
    alert_threshold: threshold.value,
    alert_threshold_unit: threshold.unit,
    quiet_hours_enabled: !!(quiet && quiet.start && quiet.end),
    quiet_start: text(quiet?.start) || "22:00",
    quiet_end: text(quiet?.end) || "07:00",
  };
}

const number = (value: string) => {
  const parsed = Number(value.trim());
  return value.trim() !== "" && Number.isFinite(parsed) ? parsed : null;
};
const orNull = <T>(value: T | null | "") => (value === "" || value === null ? null : value);
const measure = (value: string, unit: string, unknown = false) => {
  const amount = number(value);
  if (amount !== null) return { value: amount, unit };
  return unknown ? "unknown" : null;
};

export const hasDiagnosis = (draft: Draft) =>
  ["prediabetes", "type_1", "type_2", "gestational"].includes(draft.diabetes_status ?? "");
export const usesCgm = (draft: Draft) => ["cgm", "both"].includes(draft.monitoring_method ?? "");

/** The answers one step owns. `null` clears an answer, including one hidden by an earlier choice. */
export function answersFor(step: StepId, draft: Draft): Record<string, unknown> {
  switch (step) {
    case "about":
      return {
        date_of_birth: orNull(draft.date_of_birth),
        sex_assigned_at_birth: draft.sex_assigned_at_birth,
        height: measure(draft.height, draft.height_unit),
        weight: measure(draft.weight, draft.weight_unit),
        primary_goal: draft.primary_goal,
      };
    case "health":
      return {
        diabetes_status: draft.diabetes_status,
        diagnosis_year: hasDiagnosis(draft) ? number(draft.diagnosis_year) : null,
        hba1c: measure(draft.hba1c, draft.hba1c_unit, draft.hba1c_unknown),
        fasting_glucose: measure(
          draft.fasting_glucose,
          draft.fasting_glucose_unit,
          draft.fasting_glucose_unknown,
        ),
        monitoring_method: draft.monitoring_method,
        cgm_brand: usesCgm(draft) ? draft.cgm_brand : null,
        cgm_usage_duration: usesCgm(draft) ? draft.cgm_usage_duration : null,
      };
    case "devices":
      return {
        cgm_connect_preference: draft.cgm_connect_preference,
        uses_wearable: draft.uses_wearable,
        wearable_platform: draft.uses_wearable === "yes" ? draft.wearable_platform : null,
        wearable_connect_preference:
          draft.uses_wearable === "yes" ? draft.wearable_connect_preference : null,
      };
    case "routine": {
      const takes = draft.takes_glucose_medication === "yes";
      const names = draft.medication_names
        .split(/[,\n]/)
        .map((n) => n.trim())
        .filter(Boolean);
      return {
        takes_glucose_medication: draft.takes_glucose_medication,
        medication_names: takes && names.length ? names : null,
        uses_insulin: draft.uses_insulin,
        wants_medication_reminders: takes ? draft.wants_medication_reminders : null,
        meals_per_day: number(draft.meals_per_day),
        dietary_pattern: draft.dietary_pattern,
        dietary_restrictions: orNull(draft.dietary_restrictions.trim()),
        meal_logging_method: draft.meal_logging_method,
      };
    }
    case "alerts":
      return {
        wants_spike_alerts: draft.wants_spike_alerts,
        alert_threshold: draft.wants_spike_alerts
          ? measure(draft.alert_threshold, draft.alert_threshold_unit)
          : null,
        alert_channels: draft.wants_spike_alerts ? ["in_app"] : null,
        quiet_hours:
          draft.wants_spike_alerts && draft.quiet_hours_enabled
            ? { start: draft.quiet_start, end: draft.quiet_end }
            : null,
      };
    default:
      return {};
  }
}

type Errors = Partial<Record<keyof Draft, string>>;

function range(value: string, low: number, high: number, label: string): string | undefined {
  if (value.trim() === "") return undefined;
  const parsed = number(value);
  if (parsed === null) return `Enter ${label} as a number.`;
  if (parsed < low || parsed > high)
    return `That doesn't look right. Enter a value between ${low} and ${high}.`;
  return undefined;
}

/** Problems that would stop a step being saved. Empty optional answers are never errors. */
export function validate(step: StepId, draft: Draft): Errors {
  const errors: Errors = {};
  const put = (key: keyof Draft, message: string | undefined) => {
    if (message) errors[key] = message;
  };
  if (step === "about") {
    if (!draft.display_name.trim()) errors.display_name = "Enter the name you'd like us to use.";
    if (draft.date_of_birth) {
      const born = new Date(`${draft.date_of_birth}T00:00:00Z`);
      const age = (Date.now() - born.getTime()) / (365.25 * 86_400_000);
      if (Number.isNaN(born.getTime()) || age < 0)
        errors.date_of_birth = "Enter a date in the past.";
      else if (age > 120) errors.date_of_birth = "Check the year of this date.";
    }
    put(
      "height",
      draft.height_unit === "cm"
        ? range(draft.height, 50, 260, "your height")
        : range(draft.height, 20, 100, "your height"),
    );
    put(
      "weight",
      draft.weight_unit === "kg"
        ? range(draft.weight, 20, 400, "your weight")
        : range(draft.weight, 45, 880, "your weight"),
    );
  }
  if (step === "health") {
    if (hasDiagnosis(draft))
      put(
        "diagnosis_year",
        range(draft.diagnosis_year, 1920, new Date().getFullYear(), "the year"),
      );
    if (!draft.hba1c_unknown)
      put(
        "hba1c",
        draft.hba1c_unit === "%"
          ? range(draft.hba1c, 3, 20, "your HbA1c")
          : range(draft.hba1c, 9, 195, "your HbA1c"),
      );
    if (!draft.fasting_glucose_unknown) {
      put(
        "fasting_glucose",
        draft.fasting_glucose_unit === "mg/dL"
          ? range(draft.fasting_glucose, 40, 400, "your fasting glucose")
          : range(draft.fasting_glucose, 2.2, 22.2, "your fasting glucose"),
      );
    }
  }
  if (step === "routine") put("meals_per_day", range(draft.meals_per_day, 1, 12, "meals per day"));
  if (step === "alerts" && draft.wants_spike_alerts) {
    // The server accepts an alert level between 100 and 400 mg/dL.
    if (!draft.alert_threshold.trim())
      errors.alert_threshold = "Choose the glucose level you'd like to be told about.";
    else
      put(
        "alert_threshold",
        draft.alert_threshold_unit === "mg/dL"
          ? range(draft.alert_threshold, 100, 400, "the level")
          : range(draft.alert_threshold, 5.6, 22.2, "the level"),
      );
    if (
      draft.quiet_hours_enabled &&
      (!/^\d{2}:\d{2}$/.test(draft.quiet_start) || !/^\d{2}:\d{2}$/.test(draft.quiet_end))
    ) {
      errors.quiet_start = "Enter both a start and an end time.";
    }
  }
  return errors;
}

export const LABELS: Record<string, Record<string, string>> = {
  sex_assigned_at_birth: {
    female: "Female",
    male: "Male",
    intersex: "Intersex",
    prefer_not_to_say: "Prefer not to say",
  },
  primary_goal: {
    meal_responses: "Understand my meal responses",
    spike_awareness: "Notice glucose spikes sooner",
    diabetes_management: "Support my diabetes management",
    metabolic_exploration: "Explore my metabolic health",
    other: "Something else",
  },
  diabetes_status: {
    none: "No diabetes",
    prediabetes: "Prediabetes",
    type_1: "Type 1 diabetes",
    type_2: "Type 2 diabetes",
    gestational: "Gestational diabetes",
    unknown: "I'm not sure",
    prefer_not_to_say: "Prefer not to say",
  },
  monitoring_method: {
    cgm: "Continuous glucose monitor",
    fingerstick: "Finger-prick meter",
    both: "Both",
    none: "I don't monitor yet",
  },
  cgm_brand: { dexcom: "Dexcom", freestyle_libre: "FreeStyle Libre", other: "Another brand" },
  cgm_usage_duration: {
    under_1_month: "Less than a month",
    "1_to_6_months": "1 to 6 months",
    "6_to_12_months": "6 to 12 months",
    over_1_year: "More than a year",
  },
  cgm_connect_preference: {
    now: "I'll add my data now",
    later: "I'll add it later",
    no: "I don't plan to",
  },
  uses_wearable: { yes: "Yes", no: "No" },
  wearable_platform: {
    apple_watch: "Apple Watch",
    fitbit: "Fitbit",
    whoop: "WHOOP",
    other: "Another device",
  },
  wearable_connect_preference: {
    now: "When it's available",
    later: "Maybe later",
    no: "No thanks",
  },
  takes_glucose_medication: { yes: "Yes", no: "No", unsure: "I'm not sure" },
  uses_insulin: { yes: "Yes", no: "No", prefer_not_to_say: "Prefer not to say" },
  dietary_pattern: {
    no_specific: "No particular pattern",
    vegetarian: "Vegetarian",
    vegan: "Vegan",
    low_carb: "Lower carbohydrate",
    mediterranean: "Mediterranean",
    other: "Something else",
  },
  meal_logging_method: {
    photo: "Photograph my meals",
    manual: "Enter nutrition myself",
    both: "A mix of both",
  },
};

export const options = (question: string) =>
  Object.entries(LABELS[question] ?? {}).map(([value, label]) => ({ value, label }));
