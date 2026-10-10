import type { PredictResponse } from "@/types/contracts";
import { hasDiabetes, type UserProfile } from "@/lib/profile";

export type SpikeLevel = "high" | "rising" | "steady";

export interface SpikeAssessment {
  level: SpikeLevel;
  peak_mg_dl: number;
  peak_minute: number;
  rise_mg_dl: number;
  threshold_mg_dl: number;
  threshold_reason: string;
  headline: string;
  detail: string;
}

/** A rise this large above the pre-meal level is flagged even when the peak stays in range. */
export const SHARP_RISE_MG_DL = 50;

/**
 * Peak threshold used for the alert. 180 mg/dL is the commonly used upper bound of the
 * target range for people with diabetes; 140 mg/dL is a commonly used post-meal ceiling
 * for people without diabetes. Informational only, not a clinical target.
 */
export function peakThreshold(profile: UserProfile | null): { value: number; reason: string } {
  return hasDiabetes(profile)
    ? { value: 180, reason: "180 mg/dL: common upper target for people with diabetes" }
    : { value: 140, reason: "140 mg/dL: common post-meal ceiling without diabetes" };
}

/** Classify a forecast into a spike alert. Pure; uses only the frozen PredictResponse fields. */
export function assessSpike(result: PredictResponse, profile: UserProfile | null): SpikeAssessment {
  const peakPoint = result.points.reduce(
    (best, p) => (p.glucose_mg_dl > best.glucose_mg_dl ? p : best),
    result.points[0] ?? {
      minute: result.metrics.time_to_peak_minutes,
      glucose_mg_dl: result.metrics.peak_mg_dl,
    },
  );
  const peak = Math.max(peakPoint.glucose_mg_dl, result.metrics.peak_mg_dl);
  const minute =
    peak === peakPoint.glucose_mg_dl ? peakPoint.minute : result.metrics.time_to_peak_minutes;
  const rise = result.metrics.peak_delta_mg_dl;
  const t = peakThreshold(profile);
  const r = Math.round;

  if (peak >= t.value) {
    return {
      level: "high",
      peak_mg_dl: peak,
      peak_minute: minute,
      rise_mg_dl: rise,
      threshold_mg_dl: t.value,
      threshold_reason: t.reason,
      headline: `Spike alert: glucose may reach ~${r(peak)} mg/dL`,
      detail: `Predicted to peak about ${r(minute)} min after this meal, above ${t.value} mg/dL.`,
    };
  }
  if (rise >= SHARP_RISE_MG_DL) {
    return {
      level: "rising",
      peak_mg_dl: peak,
      peak_minute: minute,
      rise_mg_dl: rise,
      threshold_mg_dl: t.value,
      threshold_reason: t.reason,
      headline: `Sharp rise: about +${r(rise)} mg/dL`,
      detail: `Peak ~${r(peak)} mg/dL about ${r(minute)} min after the meal: in range, but a fast climb.`,
    };
  }
  return {
    level: "steady",
    peak_mg_dl: peak,
    peak_minute: minute,
    rise_mg_dl: rise,
    threshold_mg_dl: t.value,
    threshold_reason: t.reason,
    headline: "No spike expected",
    detail: `Peak ~${r(peak)} mg/dL (+${r(Math.max(0, rise))}) stays below ${t.value} mg/dL.`,
  };
}
