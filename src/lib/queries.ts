/** Every server read the screens make, as cached queries with one key each. */
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import * as api from "./api";
import { useSession } from "./session";
import type { Meal } from "./types";

const noRetryOn4xx = (count: number, error: unknown) =>
  !(error instanceof api.ApiError && error.status >= 400 && error.status < 500) && count < 1;

function useReady() {
  const session = useSession();
  return session.phase === "ready" && session.processingAllowed;
}

export const useCgmStatus = () =>
  useQuery({
    queryKey: ["cgm", "status"],
    queryFn: api.getCgmStatus,
    enabled: useReady(),
    retry: noRetryOn4xx,
  });

export const useCgmReadings = (hours: number) => {
  const { clock } = useSession();
  // The server's default window is 24 h back from the account's clock; longer spans ask explicitly.
  const end = clock.now();
  const start = new Date(end.getTime() - hours * 3_600_000);
  const range = hours === 24 ? {} : { start: start.toISOString(), end: end.toISOString() };
  return useQuery({
    queryKey: ["cgm", "readings", hours, clock.isReplay ? end.toISOString() : "live"],
    queryFn: () => api.getCgmReadings(range),
    enabled: useReady(),
    retry: noRetryOn4xx,
  });
};

export const useMeals = () =>
  useQuery({
    queryKey: ["meals"],
    queryFn: api.getMeals,
    enabled: useReady(),
    retry: noRetryOn4xx,
  });
export const useMeal = (mealId: string | undefined) =>
  useQuery({
    queryKey: ["meals", mealId],
    queryFn: () => api.getMeal(mealId!),
    enabled: useReady() && !!mealId,
    retry: noRetryOn4xx,
  });
export const usePredictions = () =>
  useQuery({
    queryKey: ["predictions"],
    queryFn: api.getPredictions,
    enabled: useReady(),
    retry: noRetryOn4xx,
  });
export const usePrediction = (id: string | null | undefined) =>
  useQuery({
    queryKey: ["predictions", id],
    queryFn: () => api.getPrediction(id!),
    enabled: useReady() && !!id,
    retry: noRetryOn4xx,
  });
export const useOutcome = (mealId: string | undefined, enabled = true) =>
  useQuery({
    queryKey: ["outcomes", mealId],
    queryFn: () => api.getOutcome(mealId!),
    enabled: useReady() && !!mealId && enabled,
    retry: noRetryOn4xx,
  });

/** Outcomes for every forecast meal, fetched side by side. */
export function useOutcomes(meals: Meal[]) {
  const ready = useReady();
  return useQueries({
    queries: meals.map((meal) => ({
      queryKey: ["outcomes", meal.meal_id],
      queryFn: () => api.getOutcome(meal.meal_id),
      enabled: ready,
      retry: noRetryOn4xx,
    })),
  });
}

export const useDevices = () =>
  useQuery({
    queryKey: ["devices"],
    queryFn: api.getDevices,
    enabled: useReady(),
    retry: noRetryOn4xx,
  });
export const useDemoDevices = () => {
  const { isDemoAccount } = useSession();
  return useQuery({
    queryKey: ["demo", "devices"],
    queryFn: api.getDemoDevices,
    enabled: useReady() && isDemoAccount,
    retry: noRetryOn4xx,
  });
};
export const useWearables = (
  query: { metric?: string; provider?: string; hours?: number } = {},
) => {
  const { clock } = useSession();
  const end = clock.now();
  const { hours, ...rest } = query;
  const range = hours
    ? { start: new Date(end.getTime() - hours * 3_600_000).toISOString(), end: end.toISOString() }
    : {};
  return useQuery({
    queryKey: ["wearables", query, clock.isReplay ? end.toISOString() : "live"],
    queryFn: () => api.getWearables({ ...rest, ...range }),
    enabled: useReady(),
    retry: noRetryOn4xx,
  });
};
export const useHealthTimeline = () => {
  const { isDemoAccount } = useSession();
  return useQuery({
    queryKey: ["demo", "timeline"],
    queryFn: () => api.getHealthTimeline(48),
    enabled: useReady() && isDemoAccount,
    retry: noRetryOn4xx,
  });
};

export const useRegimens = () =>
  useQuery({
    queryKey: ["medications", "regimens"],
    queryFn: api.getRegimens,
    enabled: useReady(),
    retry: noRetryOn4xx,
  });
export const useDueReminders = () =>
  useQuery({
    queryKey: ["medications", "due"],
    queryFn: api.getDueReminders,
    enabled: useReady(),
    retry: noRetryOn4xx,
  });
export const useDoses = () =>
  useQuery({
    queryKey: ["medications", "doses"],
    queryFn: api.getDoses,
    enabled: useReady(),
    retry: noRetryOn4xx,
  });
export const useEducation = () =>
  useQuery({
    queryKey: ["medications", "education"],
    queryFn: api.getEducation,
    enabled: useReady(),
    staleTime: Infinity,
    retry: noRetryOn4xx,
  });

export const useAlertSettings = () =>
  useQuery({
    queryKey: ["alerts", "settings"],
    queryFn: api.getAlertSettings,
    enabled: useReady(),
    retry: noRetryOn4xx,
  });
export const useAlerts = () =>
  useQuery({
    queryKey: ["alerts", "events"],
    queryFn: api.getAlerts,
    enabled: useReady(),
    retry: noRetryOn4xx,
  });
export const useNotifications = () =>
  useQuery({
    queryKey: ["alerts", "deliveries"],
    queryFn: api.getNotifications,
    enabled: useReady(),
    retry: noRetryOn4xx,
  });
export const useModelInfo = () =>
  useQuery({
    queryKey: ["model-info"],
    queryFn: api.getModelInfo,
    staleTime: Infinity,
    retry: noRetryOn4xx,
  });

/**
 * After anything that moves the whole account forward (the replay, a consent change).
 * Screens that are not open are refetched too, so the next one visited never opens on
 * data from before the change.
 */
export function useRefreshAll() {
  const queryClient = useQueryClient();
  return useCallback(() => queryClient.invalidateQueries({ refetchType: "all" }), [queryClient]);
}

/** What a meal, its nutrition or its forecast can change: and nothing else. */
const MEAL_DEPENDENT = ["meals", "predictions", "outcomes", "alerts"] as const;

/**
 * After a meal is uploaded, analysed, confirmed, forecast or deleted. Refetches the meal
 * and forecast lists Home and Meals read (open or not) and leaves glucose, wearables
 * and medications alone: a meal does not change a measurement.
 */
export function useRefreshMeals() {
  const queryClient = useQueryClient();
  return useCallback(
    () =>
      Promise.all(
        MEAL_DEPENDENT.map((key) =>
          queryClient.invalidateQueries({ queryKey: [key], refetchType: "all" }),
        ),
      ),
    [queryClient],
  );
}
