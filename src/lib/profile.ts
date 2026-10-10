/**
 * The user's self-reported setup answers. Stored only in this browser (localStorage);
 * never sent to the backend, and not a model input under Contract v1.0. Today it only
 * personalizes how a forecast is described (e.g. which spike threshold applies).
 */

export type DiabetesStatus = "none" | "prediabetes" | "type1" | "type2" | "unsure";
export type Medication = "insulin" | "metformin" | "glp1" | "other";

export interface UserProfile {
  diabetes: DiabetesStatus;
  medications: Medication[];
  a1c_percent: number | null;
  age_years: number | null;
  height_cm: number | null;
  weight_kg: number | null;
}

export const DIABETES_LABELS: Record<DiabetesStatus, string> = {
  none: "No diabetes",
  prediabetes: "Prediabetes",
  type1: "Type 1 diabetes",
  type2: "Type 2 diabetes",
  unsure: "Not sure",
};

export const MEDICATION_LABELS: Record<Medication, string> = {
  insulin: "Insulin",
  metformin: "Metformin",
  glp1: "GLP-1 medication",
  other: "Other glucose-lowering medication",
};

export const DEFAULT_PROFILE: UserProfile = {
  diabetes: "unsure",
  medications: [],
  a1c_percent: null,
  age_years: null,
  height_cm: null,
  weight_kg: null,
};

const KEY = "glimpse.profile.v1";

export function loadProfile(): UserProfile | null {
  try {
    const raw = globalThis.localStorage?.getItem(KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Partial<UserProfile>;
    if (!p || typeof p !== "object" || !(p.diabetes && p.diabetes in DIABETES_LABELS)) return null;
    return {
      ...DEFAULT_PROFILE,
      ...p,
      medications: Array.isArray(p.medications) ? p.medications : [],
    };
  } catch {
    return null;
  }
}

export function saveProfile(p: UserProfile): boolean {
  try {
    globalThis.localStorage?.setItem(KEY, JSON.stringify(p));
    return true;
  } catch {
    return false;
  }
}

export function clearProfile(): void {
  try {
    globalThis.localStorage?.removeItem(KEY);
  } catch {
    /* storage unavailable: nothing to clear */
  }
}

export function bmi(p: Pick<UserProfile, "height_cm" | "weight_kg">): number | null {
  if (!p.height_cm || !p.weight_kg || p.height_cm <= 0) return null;
  const m = p.height_cm / 100;
  return p.weight_kg / (m * m);
}

export const hasDiabetes = (p: UserProfile | null) =>
  p?.diabetes === "type1" || p?.diabetes === "type2";
