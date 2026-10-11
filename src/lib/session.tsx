import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import * as api from "./api";
import { makeClock, type Clock } from "./time";
import type { Onboarding, ReplayState, ServiceStatus, User } from "./types";

export type SessionPhase =
  | "starting" // nothing known yet (server render, or the first request is in flight)
  | "signed_out"
  | "unreachable" // a token exists but the server cannot be reached
  | "ready";

interface Session {
  phase: SessionPhase;
  user: User | null;
  onboarding: Onboarding | null;
  status: ServiceStatus | null;
  /** The replay state, for a demonstration account. `null` for everyone else. */
  replay: ReplayState | null;
  isDemoAccount: boolean;
  processingAllowed: boolean;
  personalizationAllowed: boolean;
  clock: Clock;
  /** Why the last session ended, when it ended on its own. */
  endedReason: string | null;
  signedIn: (reason?: null) => void;
  signOut: () => Promise<void>;
  retry: () => void;
}

const SessionContext = createContext<Session | null>(null);

export const keys = {
  me: ["me"] as const,
  onboarding: ["onboarding"] as const,
  status: ["status"] as const,
  replay: ["demo", "replay"] as const,
};

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [mounted, setMounted] = useState(false);
  const [token, setTokenState] = useState<string | null>(null);
  const [endedReason, setEndedReason] = useState<string | null>(null);

  useEffect(() => {
    setTokenState(api.getToken());
    setMounted(true);
    const onEnded = (event: Event) => {
      setTokenState(null);
      setEndedReason((event as CustomEvent<string>).detail ?? "session_expired");
      queryClient.clear();
    };
    window.addEventListener(api.SESSION_EVENT, onEnded);
    return () => window.removeEventListener(api.SESSION_EVENT, onEnded);
  }, [queryClient]);

  const signedInNow = mounted && token !== null;
  const status = useQuery({
    queryKey: keys.status,
    queryFn: api.getStatus,
    enabled: mounted,
    staleTime: 60_000,
    retry: 1,
  });
  const me = useQuery({ queryKey: keys.me, queryFn: api.getMe, enabled: signedInNow, retry: 1 });
  const onboarding = useQuery({
    queryKey: keys.onboarding,
    queryFn: api.getOnboarding,
    enabled: signedInNow,
    retry: 1,
  });
  // The replay endpoint answers only for a demonstration account, which is how one is recognised.
  const replay = useQuery({
    queryKey: keys.replay,
    queryFn: async () => {
      try {
        return await api.getReplay();
      } catch (error) {
        if (error instanceof api.ApiError && [403, 404].includes(error.status)) return null;
        throw error;
      }
    },
    enabled: signedInNow && status.data?.demo_mode === true,
    retry: 1,
  });

  const signedIn = useCallback(() => {
    setEndedReason(null);
    setTokenState(api.getToken());
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      api.clearToken(); // the token is dropped locally whether or not the server heard
    }
    setTokenState(null);
    setEndedReason(null);
    queryClient.clear();
  }, [queryClient]);

  const retry = useCallback(() => {
    void queryClient.invalidateQueries();
  }, [queryClient]);

  const demoPending = status.data?.demo_mode === true && replay.isPending;
  const failed = me.isError || onboarding.isError || status.isError;
  let phase: SessionPhase;
  if (!mounted) phase = "starting";
  else if (!token) phase = "signed_out";
  else if (me.data && onboarding.data && status.data && !demoPending) phase = "ready";
  else if (failed) phase = "unreachable";
  else phase = "starting";

  const user = me.data ?? null;
  const replayState = replay.data ?? null;
  const timezone = user?.timezone ?? "UTC";
  const clock = useMemo(() => makeClock(replayState, timezone), [replayState, timezone]);
  const granted = (type: string) =>
    user?.consents.some((c) => c.consent_type === type && c.state === "granted") ?? false;

  const value: Session = {
    phase,
    user,
    onboarding: onboarding.data ?? null,
    status: status.data ?? null,
    replay: replayState,
    isDemoAccount: replayState !== null,
    processingAllowed: granted("necessary_processing"),
    personalizationAllowed: granted("personalization_history"),
    clock,
    endedReason,
    signedIn,
    signOut,
    retry,
  };
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession needs a SessionProvider");
  return context;
}
