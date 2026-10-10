import type {
  DataMode,
  HealthResponse,
  HistoryResponse,
  ModelInfo,
  ModelMetrics,
  ParticipantsResponse,
  PredictRequest,
  PredictResponse,
} from "@/types/contracts";
import {
  healthResponseSchema,
  historyResponseSchema,
  modelInfoSchema,
  modelMetricsSchema,
  participantsResponseSchema,
  predictResponseSchema,
} from "@/lib/validation";
import { ApiError, apiRequest } from "./apiClient";

/** Lazy import keeps fixtures out of the live code path. */
const fixtures = () => import("@/fixtures/developmentFixtures");
const delay = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((res, rej) => {
    const t = setTimeout(res, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      rej(new ApiError("aborted", "Request cancelled"));
    });
  });

export async function getHealth(mode: DataMode, signal?: AbortSignal): Promise<HealthResponse> {
  if (mode === "fixture") return (await fixtures()).fixtureHealth;
  return apiRequest("/health", healthResponseSchema, { signal, timeoutMs: 5000 });
}

export async function getParticipants(mode: DataMode, signal?: AbortSignal): Promise<ParticipantsResponse> {
  if (mode === "fixture") return (await fixtures()).fixtureParticipants;
  return apiRequest("/api/participants", participantsResponseSchema, { signal });
}

export async function getHistory(mode: DataMode, id: string, signal?: AbortSignal): Promise<HistoryResponse> {
  if (mode === "fixture") {
    const f = await fixtures();
    await delay(250, signal);
    if (!f.fixtureParticipants.participants.some((p) => p.participant_id === id))
      throw new ApiError("not_found", "Unknown fixture participant", 404);
    return f.buildFixtureHistory(id);
  }
  return apiRequest(`/api/participants/${encodeURIComponent(id)}/history`, historyResponseSchema, { signal });
}

export async function predict(mode: DataMode, req: PredictRequest, signal?: AbortSignal): Promise<PredictResponse> {
  if (mode === "fixture") {
    const f = await fixtures();
    await delay(600, signal);
    return predictResponseSchema.parse(f.buildFixturePrediction(req));
  }
  return apiRequest("/api/predict", predictResponseSchema, { method: "POST", body: req, signal, timeoutMs: 30000 });
}

export async function getModelInfo(mode: DataMode, signal?: AbortSignal): Promise<ModelInfo> {
  if (mode === "fixture") return (await fixtures()).fixtureModelInfo;
  return apiRequest("/api/model/info", modelInfoSchema, { signal });
}

export async function getModelMetrics(mode: DataMode, signal?: AbortSignal): Promise<ModelMetrics> {
  if (mode === "fixture") throw new ApiError("not_found", "No validation metrics in fixture mode", 404);
  return apiRequest("/api/model/metrics", modelMetricsSchema, { signal });
}
