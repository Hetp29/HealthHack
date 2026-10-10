import type { HistoryResponse } from "@/types/contracts";

export interface HistorySummary {
  readingCount: number;
  meanGlucose: number | null;
  /** 10th percentile of recorded heart-rate samples; null when no HR data. */
  restingHeartRate: number | null;
  mealCount: number;
}

/**
 * Descriptive-only summaries over observed data. Rules:
 * - mean glucose: arithmetic mean of all CGM readings
 * - resting HR proxy: 10th percentile of available heart-rate readings
 * Missing data yields null (rendered "Not available"), never zero.
 */
export function summarizeHistory(h: HistoryResponse): HistorySummary {
  const g = h.glucose.map((o) => o.glucose_mg_dl);
  const hr = h.heart_rate.map((o) => o.heart_rate_bpm).sort((a, b) => a - b);
  return {
    readingCount: g.length,
    meanGlucose: g.length ? g.reduce((a, b) => a + b, 0) / g.length : null,
    restingHeartRate: hr.length ? hr[Math.floor((hr.length - 1) * 0.1)] : null,
    mealCount: h.meals.length,
  };
}
