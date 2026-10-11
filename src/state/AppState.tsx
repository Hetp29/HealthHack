import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { DataMode, Participant } from "@/types/contracts";
import { getOnboarding, getStatus, hasV2Session } from "@/services/v2Client";
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
  isAuthenticated: boolean;
  isOnboarded: boolean;
  authenticate: () => void;
  completeOnboarding: () => void;
}

const Ctx = createContext<AppState | null>(null);

const envMode = (import.meta.env["VITE_DATA_MODE"] as string | undefined)?.toLowerCase();
export const INITIAL_MODE: DataMode = envMode === "fixture" ? "fixture" : "live";

export function AppStateProvider({ children, initialMode = INITIAL_MODE }: { children: ReactNode; initialMode?: DataMode }) {
  const [mode, setModeRaw] = useState<DataMode>(initialMode);
  const [connectivity, setConnectivity] = useState<Connectivity>("checking");
  const [dataSource, setDataSource] = useState<string | null>(null);
  const [participants, setParticipants] = useState<Status<Participant[]>>({ state: "loading" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [pTick, setPTick] = useState(0);
  const [isAuthenticated, setIsAuthenticated] = useState(() => hasV2Session());
  const [isOnboarded, setIsOnboarded] = useState(false);

  const authenticate = useCallback(() => {
    setIsAuthenticated(true);
  }, []);

  const completeOnboarding = useCallback(() => {
    setIsOnboarded(true);
  }, []);

  useEffect(() => {
    if (!isAuthenticated) return;
    getOnboarding()
      .then((onboarding) => setIsOnboarded(onboarding.completed))
      .catch(() => {
        // The route guard remains conservative when the backend cannot confirm completion.
        setIsOnboarded(false);
      });
  }, [isAuthenticated]);

  const setMode = useCallback((m: DataMode) => {
    setModeRaw(m);
    setSelectedId(null);
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    setConnectivity("checking");
    if (!isAuthenticated) {
      setConnectivity("unavailable");
      return () => ctrl.abort();
    }
    if (mode === "fixture") {
      setConnectivity("unavailable");
      return () => ctrl.abort();
    }
    getStatus()
      .then((status) => {
        setConnectivity("connected");
        setDataSource(status.demo_mode ? "authenticated v2 demo" : "authenticated v2");
      })
      .catch((e) => {
        if (e instanceof ApiError && e.kind === "aborted") return;
        setConnectivity(e instanceof ApiError && e.kind === "model_unavailable" ? "model_unavailable" : "unavailable");
      });
    return () => ctrl.abort();
  }, [mode, tick, isAuthenticated]);

  useEffect(() => {
    setParticipants({ state: "success", data: [] });
    setSelectedId(null);
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
      isAuthenticated,
      isOnboarded,
      authenticate,
      completeOnboarding,
    }),
    [mode, setMode, connectivity, dataSource, participants, selectedId, selected, isAuthenticated, isOnboarded, authenticate, completeOnboarding],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAppState must be used within AppStateProvider");
  return v;
}
