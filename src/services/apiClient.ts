import type { ZodType, ZodTypeDef } from "zod";

export const API_BASE_URL: string =
  (import.meta.env["VITE_API_BASE_URL"] as string | undefined) ??
  (import.meta.env.DEV ? "http://localhost:8000" : "");

export type ApiErrorKind =
  | "network"
  | "timeout"
  | "aborted"
  | "not_found"
  | "invalid_input"
  | "model_unavailable"
  | "http"
  | "malformed"
  | "config";

export class ApiError extends Error {
  constructor(
    public kind: ApiErrorKind,
    message: string,
    public status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const describeError = (e: unknown): string => {
  if (!(e instanceof ApiError)) return "Unexpected error.";
  switch (e.kind) {
    case "network":
      return "Backend unreachable (network or CORS failure).";
    case "timeout":
      return "The backend did not respond in time.";
    case "not_found":
      return "Participant or resource not found (404).";
    case "invalid_input":
      return `The backend rejected the input (422). ${e.message}`;
    case "model_unavailable":
      return "Model is unavailable (503).";
    case "malformed":
      return "The backend returned a response that does not match the contract.";
    case "config":
      return e.message;
    default:
      return e.message;
  }
};

interface RequestOpts {
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal | undefined;
  timeoutMs?: number;
}

export async function apiRequest<T>(
  path: string,
  schema: ZodType<T, ZodTypeDef, unknown>,
  { method = "GET", body, signal, timeoutMs = 15000 }: RequestOpts = {},
): Promise<T> {
  if (!API_BASE_URL) throw new ApiError("config", "VITE_API_BASE_URL is not configured.");
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ctrl.abort();
  }, timeoutMs);
  const onAbort = () => ctrl.abort();
  signal?.addEventListener("abort", onAbort);

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL.replace(/\/$/, "")}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : null,
      signal: ctrl.signal,
    });
  } catch {
    if (timedOut) throw new ApiError("timeout", "Request timed out");
    if (signal?.aborted) throw new ApiError("aborted", "Request cancelled");
    throw new ApiError("network", "Network error");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }

  if (!res.ok) {
    let detail = "";
    try {
      const j = await res.json();
      detail = typeof j?.detail === "string" ? j.detail : JSON.stringify(j?.detail ?? "");
    } catch {
      /* ignore */
    }
    const kind: ApiErrorKind =
      res.status === 404 ? "not_found" : res.status === 422 ? "invalid_input" : res.status === 503 ? "model_unavailable" : "http";
    throw new ApiError(kind, detail || `HTTP ${res.status}`, res.status);
  }

  let json: unknown;
  try {
    json = await res.json();
  } catch {
    throw new ApiError("malformed", "Response was not JSON");
  }
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    console.error(`[Glimpse] Contract violation on ${method} ${path}`, parsed.error.issues);
    throw new ApiError("malformed", parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; "));
  }
  return parsed.data;
}
