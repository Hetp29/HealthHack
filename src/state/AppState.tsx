import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { DataMode, Participant } from "@/types/contracts";
import { getHealth, getParticipants } from "@/services/glimpseServices";
import { ApiError } from "@/services/apiClient";

export type Connectivity = "checking" | "connected" | "model_unavailable" | "unavailable" | "fixture";
type Status<T> = { state: "loading" } | { state: "success"; data: T } | { state: "error"; error: unknown };

interface AppState {
  mode: DataMode;
  setMode: (m: DataMode) => void;
  connectivity: Connectivity;
  dataSource: string | null;
  recheck: () => void;
  participants: Status<Participant[]>;
  reloadParticipants: () => void;
  selectedId: string | null;
  setSelectedId: (id: string) => void;
  selected: Participant | null;
}

const Ctx = createContext<AppState | null>(null);

const envMode = (import.meta.env.VITE_DATA_MODE as string | undefined)?.toLowerCase();
export const INITIAL_MODE: DataMode = envMode === "live" ? "live" : "fixture";

export function AppStateProvider({ children, initialMode = INITIAL_MODE }: { children: ReactNode; initialMode?: DataMode }) {
  const [mode, setModeRaw] = useState<DataMode>(initialMode);
  const [connectivity, setConnectivity] = useState<Connectivity>("checking");
  const [dataSource, setDataSource] = useState<string | null>(null);
  const [participants, setParticipants] = useState<Status<Participant[]>>({ state: "loading" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [pTick, setPTick] = useState(0);

  const setMode = useCallback((m: DataMode) => {
    setModeRaw(m);
    setSelectedId(null);
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    setConnectivity("checking");
    getHealth(mode, ctrl.signal)
      .then((h) => {
        if (mode === "fixture") setConnectivity("fixture");
        else setConnectivity(h.model_loaded === false ? "model_unavailable" : "connected");
        if (h.data_source) setDataSource(h.data_source);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.kind === "aborted") return;
        setConnectivity(e instanceof ApiError && e.kind === "model_unavailable" ? "model_unavailable" : "unavailable");
      });
    return () => ctrl.abort();
  }, [mode, tick]);

  useEffect(() => {
    const ctrl = new AbortController();
    setParticipants({ state: "loading" });
    getParticipants(mode, ctrl.signal)
      .then((r) => {
        setParticipants({ state: "success", data: r.participants });
        setDataSource(r.data_source);
        setSelectedId((cur) =>
          cur && r.participants.some((p) => p.participant_id === cur) ? cur : r.participants[0]?.participant_id ?? null,
        );
      })
      .catch((e) => {
        if (e instanceof ApiError && e.kind === "aborted") return;
        setParticipants({ state: "error", error: e });
      });
    return () => ctrl.abort();
  }, [mode, pTick]);

  const selected =
    participants.state === "success" ? participants.data.find((p) => p.participant_id === selectedId) ?? null : null;

  const value = useMemo<AppState>(
    () => ({
      mode,
      setMode,
      connectivity,
      dataSource,
      recheck: () => setTick((t) => t + 1),
      participants,
      reloadParticipants: () => setPTick((t) => t + 1),
      selectedId,
      setSelectedId,
      selected,
    }),
    [mode, setMode, connectivity, dataSource, participants, selectedId, selected],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAppState must be used within AppStateProvider");
  return v;
}
