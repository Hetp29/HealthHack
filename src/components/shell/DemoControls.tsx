import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Check, FastForward, History, RotateCcw, SkipForward } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Notice, Pill, Spinner } from "@/components/glimpse/primitives";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import * as api from "@/lib/api";
import { useRefreshAll } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { Checkpoint, ReplayState } from "@/lib/types";
import { cn } from "@/lib/utils";

export function describeCheckpoint(
  checkpoint: Pick<Checkpoint, "kind" | "meal">,
  displayFrom: number,
): string {
  const number = checkpoint.meal === null ? "" : ` ${checkpoint.meal - displayFrom + 1}`;
  return checkpoint.kind === "meal_forecast"
    ? `Meal${number} is logged from the recording and a forecast is requested`
    : `The two hours after meal${number} are released and its forecast is scored`;
}

/**
 * Moves a demonstration account through its recording. Everything here calls the real
 * replay endpoints; the app itself never invents an event.
 */
export function DemoControls({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { replay, clock, signOut } = useSession();
  const refreshAll = useRefreshAll();
  const navigate = useNavigate();
  const [confirmReset, setConfirmReset] = useState(false);

  const advance = useMutation({
    mutationFn: ({ minutes }: { minutes?: number; checkpointId?: string }) =>
      api.advanceReplay({
        expected_event_index: replay!.event_index,
        ...(minutes ? { minutes } : {}),
      }),
    onSuccess: async (state: ReplayState, input) => {
      await refreshAll();
      if (input.minutes) {
        toast.success(`Moved ${input.minutes} minutes forward`, {
          description: "Newly recorded glucose readings are now visible.",
        });
        return;
      }
      // The checkpoint that was next when the button was pressed is the one just performed.
      const done = state.checkpoints.find((checkpoint) => checkpoint.id === input.checkpointId);
      if (!state.advanced || !done) {
        toast.info("Nothing left to advance to.");
        return;
      }
      const mealId = done.result?.meal_id;
      toast.success(
        done.kind === "meal_forecast"
          ? done.result?.forecast === "refused"
            ? "Meal logged; the forecast was refused"
            : "Meal logged and forecast created"
          : done.result?.outcome === "scored"
            ? "Outcome released and scored"
            : "Outcome released",
        mealId
          ? {
              action: {
                label: "View",
                onClick: () => void navigate({ to: "/meals/$mealId", params: { mealId } }),
              },
            }
          : {},
      );
    },
    onError: async (error) => {
      // 409 means the replay already moved (another tab, or a repeated click): show the truth.
      await refreshAll();
      toast.error(
        api.isCode(error, "replay_position_changed")
          ? "The replay had already moved on. Showing its current position."
          : api.describeError(error),
      );
    },
  });

  const reset = useMutation({
    mutationFn: api.resetReplay,
    onSuccess: async () => {
      setConfirmReset(false);
      onOpenChange(false);
      api.clearToken(); // the reset already ended this session on the server
      await signOut();
      toast.success("Demonstration reset", {
        description:
          "The demonstration account was rebuilt. Sign in again to start from the beginning.",
      });
    },
    onError: (error) => toast.error(api.describeError(error)),
  });

  if (!replay) return null;
  const next = replay.next_checkpoint;
  const shown = replay.checkpoints.filter((c) => c.phase === "display");
  const doneCount = shown.filter((c) => c.done).length;
  const busy = advance.isPending || reset.isPending;

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-md">
          <SheetHeader className="border-b border-border p-5 text-left">
            <SheetTitle>Demo controls</SheetTitle>
            <SheetDescription>
              This account replays a glucose recording made in 2020. Moving forward releases more of
              that recording; nothing here is live.
            </SheetDescription>
          </SheetHeader>

          <div className="space-y-5 p-5">
            <div className="flex flex-wrap gap-1.5">
              <Pill tone="indigo" icon={History}>
                Historical replay
              </Pill>
              <Pill tone="neutral">
                {replay.mode === "new_user" ? "New user scenario" : "Returning user scenario"}
              </Pill>
              <Pill tone="amber">Not live</Pill>
            </div>

            <dl className="grid grid-cols-2 gap-4 rounded-xl bg-muted/60 p-4 text-sm">
              <div>
                <dt className="text-muted-foreground">Recording time</dt>
                <dd className="mt-0.5 font-semibold" data-testid="replay-position">
                  {clock.dateTime(replay.position)}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Progress</dt>
                <dd className="mt-0.5 font-semibold tabular">
                  {doneCount} of {shown.length} events
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Meals logged</dt>
                <dd className="mt-0.5 font-semibold tabular">
                  {replay.data.meals_logged} of {replay.data.meals_total}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Readings released</dt>
                <dd className="mt-0.5 font-semibold tabular">
                  {replay.data.readings_released.toLocaleString()} of{" "}
                  {replay.data.readings_total.toLocaleString()}
                </dd>
              </div>
            </dl>

            <div>
              <h3 className="text-sm font-semibold">Next event</h3>
              {next ? (
                <p className="mt-1 text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">{clock.dateTime(next.at)}.</span>{" "}
                  {describeCheckpoint(next, replay.display_from_meal)}.
                </p>
              ) : (
                <p className="mt-1 text-sm text-muted-foreground">
                  The recording has been replayed to its end. Reset to start again.
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  onClick={() => advance.mutate(next ? { checkpointId: next.id } : {})}
                  disabled={busy || !next}
                  data-testid="demo-advance"
                >
                  {advance.isPending && !advance.variables?.minutes ? (
                    <Spinner />
                  ) : (
                    <SkipForward className="h-4 w-4" />
                  )}
                  Advance to next event
                </Button>
                <Button
                  variant="outline"
                  onClick={() => advance.mutate({ minutes: 30 })}
                  disabled={busy || replay.status === "complete"}
                >
                  {advance.isPending && advance.variables?.minutes ? (
                    <Spinner />
                  ) : (
                    <FastForward className="h-4 w-4" />
                  )}
                  +30 minutes
                </Button>
              </div>
              {advance.isPending && (
                <p className="mt-2 text-[13px] text-muted-foreground" role="status">
                  Releasing recorded readings and running the forecast…
                </p>
              )}
            </div>

            <div>
              <h3 className="text-sm font-semibold">Timeline</h3>
              <ol className="mt-2 space-y-1">
                {shown.map((checkpoint) => {
                  const isNext = next?.id === checkpoint.id;
                  return (
                    <li
                      key={checkpoint.id}
                      className={cn(
                        "flex items-start gap-2.5 rounded-lg px-2.5 py-2 text-[13px]",
                        isNext && "bg-accent",
                      )}
                    >
                      <span
                        className={cn(
                          "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                          checkpoint.done
                            ? "border-primary bg-primary text-primary-foreground"
                            : isNext
                              ? "border-primary"
                              : "border-border-strong",
                        )}
                      >
                        {checkpoint.done && <Check className="h-3 w-3" />}
                      </span>
                      <span className="min-w-0">
                        <span className="font-medium text-foreground">
                          {clock.dateTime(checkpoint.at)}
                        </span>
                        <span className="text-muted-foreground">
                          {" "}
                          ·{" "}
                          {checkpoint.kind === "meal_forecast"
                            ? "Meal and forecast"
                            : "Outcome released"}
                        </span>
                        {checkpoint.meal !== null && (
                          <span className="text-muted-foreground">
                            {" "}
                            (meal {checkpoint.meal - replay.display_from_meal + 1})
                          </span>
                        )}
                      </span>
                    </li>
                  );
                })}
              </ol>
            </div>

            {replay.provenance.backfilled_history && (
              <Notice title="Earlier history">
                {replay.provenance.backfilled_history.description}
              </Notice>
            )}

            <div className="border-t border-border pt-5">
              <h3 className="text-sm font-semibold">Start over</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Resetting rebuilds this demonstration account from the beginning. You will be signed
                out and need to sign in again.
              </p>
              <Button
                variant="outline"
                className="mt-3"
                onClick={() => setConfirmReset(true)}
                disabled={busy}
              >
                <RotateCcw className="h-4 w-4" /> Reset demonstration
              </Button>
            </div>
          </div>
        </SheetContent>
      </Sheet>

      <AlertDialog open={confirmReset} onOpenChange={setConfirmReset}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset this demonstration?</AlertDialogTitle>
            <AlertDialogDescription>
              Every meal, forecast, dose record and simulated device in this demonstration account
              is deleted and the recording returns to its starting point. Meals you logged yourself
              are deleted too. You will be signed out; sign in again with the same demonstration
              details. No other account is affected.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={reset.isPending}>Keep going</AlertDialogCancel>
            <AlertDialogAction
              disabled={reset.isPending}
              onClick={(event) => {
                event.preventDefault();
                reset.mutate();
              }}
            >
              {reset.isPending && <Spinner />} Reset and sign out
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
