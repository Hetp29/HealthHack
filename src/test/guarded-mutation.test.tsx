import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { useGuardedMutation } from "@/lib/useGuardedMutation";

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

describe("useGuardedMutation", () => {
  it("sends one request for a double click, however fast the second click is", async () => {
    let finish: (value: string) => void = () => undefined;
    const send = vi.fn(() => new Promise<string>((resolve) => (finish = resolve)));
    const { result } = renderHook(() => useGuardedMutation({ mutationFn: send }), { wrapper });

    act(() => {
      result.current.mutate(); // both calls happen before React can re-render a disabled button
      result.current.mutate();
    });
    await waitFor(() => expect(send).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 30)); // room for a second request to appear, if there were one
    expect(send).toHaveBeenCalledTimes(1);

    await act(async () => finish("done"));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    act(() => result.current.mutate()); // a later, deliberate click works again
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
  });

  it("releases after a failure, reports it, and never reports success", async () => {
    const send = vi.fn(async () => {
      throw new Error("refused");
    });
    const onSuccess = vi.fn();
    const { result } = renderHook(() => useGuardedMutation({ mutationFn: send, onSuccess }), {
      wrapper,
    });
    act(() => result.current.mutate());
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(onSuccess).not.toHaveBeenCalled();
    act(() => result.current.mutate());
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
  });
});
