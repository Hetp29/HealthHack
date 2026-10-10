/**
 * DEVELOPMENT FIXTURES — demonstration data only.
 * Every object is tagged data_source: "development_fixture" and conforms to the
 * same schemas as live responses. None of these values are model outputs,
 * validation results, or real participant observations.
 */
import type {
  HealthResponse,
  HistoryResponse,
  ModelInfo,
  ParticipantsResponse,
  PredictResponse,
  PredictRequest,
} from "@/types/contracts";

export const FIXTURE_SOURCE = "development_fixture";
const START = Date.UTC(2026, 9, 1, 0, 0, 0); // Oct 1 2026

export const fixtureHealth: HealthResponse = { status: "ok", model_loaded: false, data_source: FIXTURE_SOURCE };

export const fixtureParticipants: ParticipantsResponse = {
  data_source: FIXTURE_SOURCE,
  participants: ["fixture_p001", "fixture_p002", "fixture_p003"].map((id, i) => ({
    participant_id: id,
    label: `Fixture participant ${i + 1}`,
    observation_start: new Date(START).toISOString(),
    observation_end: new Date(START + 7 * 864e5 - 3e5).toISOString(),
    prediction_timestamp: new Date(START + 7 * 864e5 + 12.5 * 36e5).toISOString(),
  })),
};

/** Deterministic PRNG so fixtures are stable between renders and tests. */
function rng(seed: number) {
  let s = seed;
  return () => ((s = (s * 16807) % 2147483647) - 1) / 2147483646;
}

export function buildFixtureHistory(participantId: string): HistoryResponse {
  const seed = [...participantId].reduce((a, c) => a + c.charCodeAt(0), 0);
  const r = rng(seed * 7919);
  const baseline = 92 + (seed % 12);
  const mealHours = [7.5, 12.5, 19];
  const meals = Array.from({ length: 7 }).flatMap((_, d) =>
    mealHours.map((h, mi) => ({
      timestamp: new Date(START + d * 864e5 + (h + (r() - 0.5)) * 36e5).toISOString(),
      carbs_g: Math.round(([45, 60, 70][mi] ?? 50) + (r() - 0.5) * 30),
      protein_g: Math.round(18 + r() * 15),
      fat_g: Math.round(10 + r() * 15),
      fiber_g: mi === 0 && r() > 0.6 ? null : Math.round(3 + r() * 6),
      calories_kcal: mi === 2 && r() > 0.7 ? null : Math.round(380 + r() * 300),
    })),
  );
  const mealTimes = meals.map((m) => ({ t: Date.parse(m.timestamp), c: m.carbs_g ?? 50 }));
  const glucose = [];
  const heart_rate = [];
  for (let i = 0; i < 7 * 288; i++) {
    const t = START + i * 3e5;
    // simulated sensor gap on day 4 to exercise honest gap rendering
    if (i >= 3 * 288 + 30 && i < 3 * 288 + 54) continue;
    let v = baseline + 6 * Math.sin((2 * Math.PI * (t - START)) / 864e5 - 1.2) + (r() - 0.5) * 4;
    for (const m of mealTimes) {
      const dt = (t - m.t) / 6e4;
      if (dt > 0 && dt < 200) v += m.c * 0.9 * (dt / 50) * Math.exp(1 - dt / 50);
    }
    glucose.push({ timestamp: new Date(t).toISOString(), glucose_mg_dl: Math.round(v * 10) / 10 });
    if (i % 3 === 0) {
      const hour = ((t - START) / 36e5) % 24;
      const sleeping = hour < 6 || hour > 23;
      heart_rate.push({
        timestamp: new Date(t).toISOString(),
        heart_rate_bpm: Math.round((sleeping ? 56 : 70) + r() * 14),
      });
    }
  }
  return {
    participant_id: participantId,
    data_source: FIXTURE_SOURCE,
    observation_start: new Date(START).toISOString(),
    observation_end: new Date(START + 7 * 864e5 - 3e5).toISOString(),
    glucose,
    heart_rate,
    meals,
  };
}

/** Static illustrative trajectory. Does NOT respond to meal inputs. */
const FIXTURE_CURVE = [
  105, 108, 113, 119, 124, 130, 136, 141, 144, 146, 146, 145, 143, 141, 139, 137, 135, 132, 130, 127, 125, 123,
  121, 119, 118,
];

export function buildFixturePrediction(req: PredictRequest): PredictResponse {
  return {
    participant_id: req.participant_id,
    meal_timestamp: req.meal_timestamp,
    model_version: "fixture",
    data_source: FIXTURE_SOURCE,
    prediction_status: "fixture",
    points: FIXTURE_CURVE.map((g, i) => ({ minute: i * 5, glucose_mg_dl: g, lower_mg_dl: null, upper_mg_dl: null })),
    glucose_at_minutes: { "30": 124, "60": 146, "90": 139, "120": 118 },
    metrics: { peak_mg_dl: 146, peak_delta_mg_dl: 41, time_to_peak_minutes: 50, incremental_auc_mg_dl_min: 3200 },
    drivers: [
      { feature: "carbs_g", contribution_mg_dl: 12.5, explanation: "Illustrative fixture value" },
      { feature: "fiber_g", contribution_mg_dl: -4.2, explanation: "Illustrative fixture value" },
      { feature: "fat_g", contribution_mg_dl: -2.1, explanation: "Illustrative fixture value" },
      { feature: "protein_g", contribution_mg_dl: 1.3, explanation: "Illustrative fixture value" },
    ],
    uncertainty_status: "not_calibrated",
    limitations: [
      "Development fixture: this static curve does not respond to the meal you entered.",
      "Synthetic research demonstration; not medical advice.",
    ],
  };
}

export const fixtureModelInfo: ModelInfo = {
  model_version: "fixture",
  data_source: FIXTURE_SOURCE,
  feature_set_version: null,
  evaluation_protocol: null,
  evaluated_at: null,
  limitations: ["Fixture mode — no trained model is attached, so no validation results exist."],
};
// Model metrics intentionally have NO fixture: fabricated validation numbers are prohibited.
