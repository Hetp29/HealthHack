import { useEffect, useRef, useState } from "react";
import type { DataMode, HistoryResponse } from "@/types/contracts";
import { getHistory } from "@/services/glimpseServices";
import { ApiError } from "@/services/apiClient";

export type HistoryState =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "success"; data: HistoryResponse }
  | { state: "error"; error: unknown };

/**
 * Loads participant history. Each participant change aborts the previous request
 * and a monotonically increasing request id guarantees stale responses are dropped.
 */
export function useHistory(mode: DataMode, participantId: string | null, fetcher = getHistory) {
  const [status, setStatus] = useState<HistoryState>({ state: "idle" });
  const [tick, setTick] = useState(0);
  const reqId = useRef(0);

  useEffect(() => {
    if (!participantId) return setStatus({ state: "idle" });
    const id = ++reqId.current;
    const ctrl = new AbortController();
    setStatus({ state: "loading" });
    fetcher(mode, participantId, ctrl.signal)
      .then((data) => {
        if (id === reqId.current && data.participant_id === participantId) setStatus({ state: "success", data });
      })
      .catch((error) => {
        if (id !== reqId.current || (error instanceof ApiError && error.kind === "aborted")) return;
        setStatus({ state: "error", error });
      });
    return () => ctrl.abort();
  }, [mode, participantId, tick, fetcher]);

  return { status, retry: () => setTick((t) => t + 1) };
}
