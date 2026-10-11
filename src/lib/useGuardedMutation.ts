import {
  useMutation,
  type UseMutationOptions,
  type UseMutationResult,
} from "@tanstack/react-query";
import { useCallback, useRef } from "react";

/**
 * `useMutation`, except that `mutate` does nothing while a call from this same hook is
 * still in flight.
 *
 * A disabled button is not enough to stop a double click: the second click can arrive
 * before React has re-rendered the button as disabled. Every write in the app goes
 * through this, so one gesture is one request: one meal, one dose, one paid analysis.
 */
export function useGuardedMutation<
  TData = unknown,
  TError = Error,
  TVariables = void,
  TContext = unknown,
>(
  options: UseMutationOptions<TData, TError, TVariables, TContext>,
): UseMutationResult<TData, TError, TVariables, TContext> {
  const inFlight = useRef(false);
  const mutation = useMutation(options);
  const { mutateAsync } = mutation;
  const mutate = useCallback(
    (variables: TVariables) => {
      if (inFlight.current) return;
      inFlight.current = true;
      // Failures are reported through the mutation's own state and `onError`.
      void mutateAsync(variables)
        .catch(() => undefined)
        .finally(() => {
          inFlight.current = false;
        });
    },
    [mutateAsync],
  );
  return { ...mutation, mutate } as UseMutationResult<TData, TError, TVariables, TContext>;
}
