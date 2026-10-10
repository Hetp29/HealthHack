import { describe, expect, it } from "vitest";
import { buildFixturePrediction } from "@/fixtures/developmentFixtures";
import { atwaterKcal, MEAL_PRESETS } from "@/lib/mealPresets";
import { DEFAULT_PROFILE, type UserProfile } from "@/lib/profile";
import { assessSpike, peakThreshold } from "@/lib/spikeAlert";
import type { PredictResponse } from "@/types/contracts";

const base = buildFixturePrediction({
  participant_id: "p",
  meal_timestamp: "2026-10-08T12:30:00Z",
  meal: { carbs_g: 50, protein_g: 20, fat_g: 10, fiber_g: 4, calories_kcal: 370 },
  context_override: null,
});

/** A forecast whose curve rises linearly from `start` to `peak` at minute 60, then falls back. */
function forecast(start: number, peak: number): PredictResponse {
  const points = Array.from({ length: 25 }, (_, i) => {
    const minute = i * 5;
    const g =
      minute <= 60
        ? start + ((peak - start) * minute) / 60
        : peak - ((peak - start) * (minute - 60)) / 60;
    return { minute, glucose_mg_dl: g, lower_mg_dl: null, upper_mg_dl: null };
  });
  return {
    ...base,
    points,
    metrics: {
      ...base.metrics,
      peak_mg_dl: peak,
      peak_delta_mg_dl: peak - start,
      time_to_peak_minutes: 60,
    },
  };
}

const withDiabetes: UserProfile = { ...DEFAULT_PROFILE, diabetes: "type2" };

describe("spike alert", () => {
  it("uses 180 mg/dL with diabetes and 140 otherwise (including no profile)", () => {
    expect(peakThreshold(withDiabetes).value).toBe(180);
    expect(peakThreshold({ ...DEFAULT_PROFILE, diabetes: "none" }).value).toBe(140);
    expect(peakThreshold(null).value).toBe(140);
  });

  it("flags a peak at or above the threshold as high, with peak time", () => {
    const a = assessSpike(forecast(110, 190), withDiabetes);
    expect(a.level).toBe("high");
    expect(a.peak_minute).toBe(60);
    expect(a.headline).toContain("190");
  });

  it("the same forecast is high without diabetes but only rising with diabetes", () => {
    const f = forecast(100, 160);
    expect(assessSpike(f, null).level).toBe("high");
    expect(assessSpike(f, withDiabetes).level).toBe("rising");
  });

  it("flags a fast climb that stays in range as rising", () => {
    expect(assessSpike(forecast(80, 135), null).level).toBe("rising");
  });

  it("reports steady when the peak is low and the rise small", () => {
    const a = assessSpike(forecast(100, 125), null);
    expect(a.level).toBe("steady");
    expect(a.headline).toBe("No spike expected");
  });

  it("classifies the demo fixture forecast without throwing", () => {
    expect(["high", "rising", "steady"]).toContain(assessSpike(base, null).level);
  });
});

describe("meal presets", () => {
  it("have non-negative macros and calories consistent with them", () => {
    for (const p of MEAL_PRESETS) {
      for (const v of Object.values(p.macros)) expect(v).toBeGreaterThanOrEqual(0);
      expect(p.macros.calories_kcal).toBe(atwaterKcal(p.macros));
    }
  });
});
