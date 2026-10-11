import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, LineChart, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  Async,
  EmptyState,
  ErrorState,
  Notice,
  PageHeader,
  Panel,
  Pill,
  SkeletonBlock,
  SkeletonLines,
  Spinner,
} from "@/components/glimpse/primitives";
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
import { ForecastView, OutcomeView } from "@/features/ForecastView";
import { MACRO_FIELDS, isRecordedMeal, mealStatus } from "@/features/mealText";
import * as api from "@/lib/api";
import {
  useAlertSettings,
  useMeal,
  useOutcome,
  usePrediction,
  useRefreshMeals,
} from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { Meal } from "@/lib/types";
import { useGuardedMutation } from "@/lib/useGuardedMutation";

export const Route = createFileRoute("/meals/$mealId")({ component: MealPage });

const SOURCE_LABEL = {
  manual: "Entered by you",
  ai_accepted: "AI estimate, accepted by you",
  ai_edited: "AI estimate, edited by you",
  ai_unconfirmed: "AI estimate, not confirmed",
} as const;

function Photo({ meal }: { meal: Meal }) {
  const image = useQuery({
    queryKey: ["meals", meal.meal_id, "image"],
    queryFn: () => api.getMealImage(meal.meal_id),
    staleTime: Infinity,
    retry: false,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!image.data) return undefined;
    const created = URL.createObjectURL(image.data);
    setUrl(created);
    return () => URL.revokeObjectURL(created);
  }, [image.data]);
  if (image.isError)
    return <p className="text-sm text-muted-foreground">The photo couldn't be loaded.</p>;
  return url ? (
    <img
      src={url}
      alt="Photo of this meal"
      className="aspect-square w-full rounded-xl border border-border object-cover"
    />
  ) : (
    <SkeletonBlock className="aspect-square w-full" />
  );
}

function MealPage() {
  const { mealId } = Route.useParams();
  const { clock, isDemoAccount } = useSession();
  const navigate = useNavigate();
  const refreshMeals = useRefreshMeals();
  const meal = useMeal(mealId);
  const prediction = usePrediction(meal.data?.prediction_id);
  const settings = useAlertSettings();
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Whether an outcome exists is the server's decision: a meal typed into a replay comes
  // back `not_applicable`, because the recording belongs to what the participant ate.
  const outcome = useOutcome(mealId, !!meal.data?.prediction_id);

  const forecast = useGuardedMutation({
    retry: false,
    mutationFn: () => api.predictMeal(mealId),
    onSuccess: () => void refreshMeals(),
  });
  const remove = useGuardedMutation({
    mutationFn: () => api.deleteMeal(mealId),
    onSuccess: async () => {
      await refreshMeals();
      toast.success("Meal deleted");
      void navigate({ to: "/meals" });
    },
    onError: (error) => toast.error(api.describeError(error)),
  });

  const back = (
    <Link to="/meals" className="inline-flex items-center gap-1 hover:text-foreground">
      <ArrowLeft className="h-3.5 w-3.5" /> Meals
    </Link>
  );

  if (meal.isError) {
    return (
      <>
        <PageHeader title="Meal" eyebrow={back} />
        <Panel>
          {api.isCode(meal.error, "not_found") ? (
            <EmptyState
              title="This meal isn't here"
              action={
                <Button asChild>
                  <Link to="/meals">Back to meals</Link>
                </Button>
              }
            >
              It may have been deleted, or the demonstration may have been reset.
            </EmptyState>
          ) : (
            <ErrorState error={meal.error} onRetry={() => void meal.refetch()} />
          )}
        </Panel>
      </>
    );
  }
  if (!meal.data)
    return (
      <>
        <PageHeader title="Meal" eyebrow={back} />
        <Panel>
          <SkeletonLines lines={6} />
        </Panel>
      </>
    );

  const m = meal.data;
  if (m.status === "voided") {
    return (
      <>
        <PageHeader title="Meal" eyebrow={back} />
        <Panel>
          <EmptyState
            title="This meal isn't here"
            action={
              <Button asChild>
                <Link to="/meals">Back to meals</Link>
              </Button>
            }
          >
            It was deleted. Its forecast is kept on record but is no longer shown or scored.
          </EmptyState>
        </Panel>
      </>
    );
  }
  const status = mealStatus(m);
  const observed =
    outcome.data?.status === "scored" ? (outcome.data.observed?.curve ?? null) : null;
  const threshold = settings.data?.enabled ? settings.data.threshold.mg_dl : null;

  return (
    <>
      <PageHeader
        title={`Meal at ${clock.time(m.occurred_at)}`}
        eyebrow={back}
        description={
          <span className="flex flex-wrap items-center gap-2">
            {clock.day(m.occurred_at)}
            <Pill tone={status.tone}>{status.label}</Pill>
            {isDemoAccount &&
              (isRecordedMeal(m) ? (
                <Pill tone="indigo">From the recording</Pill>
              ) : (
                <Pill tone="neutral">Added by you</Pill>
              ))}
          </span>
        }
        actions={
          !(isDemoAccount && isRecordedMeal(m)) && (
            <Button variant="outline" onClick={() => setConfirmDelete(true)}>
              <Trash2 className="h-4 w-4" /> Delete
            </Button>
          )
        }
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <div className="min-w-0 space-y-5 lg:col-span-2">
          <Panel
            title="Forecast"
            description="Predicted glucose for the two hours after this meal."
          >
            {m.prediction_id ? (
              <Async query={prediction} skeleton={<SkeletonBlock className="h-80" />}>
                {(p) => (
                  <ForecastView prediction={p} observed={observed} thresholdMgDl={threshold} />
                )}
              </Async>
            ) : m.status !== "confirmed" ? (
              <EmptyState
                icon={LineChart}
                title="Confirm the nutrition first"
                compact
                action={
                  <Button asChild>
                    <Link to="/meals/log" search={{ meal: m.meal_id }}>
                      Continue this meal
                    </Link>
                  </Button>
                }
              >
                A forecast is only made from values you have confirmed.
              </EmptyState>
            ) : (
              <div>
                <EmptyState
                  icon={LineChart}
                  title="No forecast yet"
                  compact
                  action={
                    <Button
                      onClick={() => forecast.mutate()}
                      disabled={forecast.isPending}
                      data-testid="get-forecast"
                    >
                      {forecast.isPending ? <Spinner /> : <Sparkles className="h-4 w-4" />}{" "}
                      {forecast.isPending ? "Calculating…" : "Get the forecast"}
                    </Button>
                  }
                >
                  This meal is confirmed but hasn't been forecast.
                </EmptyState>
                {forecast.isError && (
                  <Notice
                    tone="warning"
                    title="The forecast couldn't be made"
                    action={
                      api.isCode(forecast.error, "insufficient_premeal_context") ? (
                        <Button asChild size="sm" variant="outline">
                          <Link to="/health" search={{ tab: "glucose" }}>
                            Add glucose data
                          </Link>
                        </Button>
                      ) : undefined
                    }
                  >
                    {api.describeError(forecast.error)}
                  </Notice>
                )}
              </div>
            )}
          </Panel>

          {m.prediction_id && (
            <Panel
              title="How it compared"
              description="The forecast against the glucose recorded afterwards."
            >
              <Async query={outcome} skeleton={<SkeletonLines lines={4} />} compact>
                {(o) => <OutcomeView outcome={o} formatTime={clock.dateTime} />}
              </Async>
            </Panel>
          )}
        </div>

        <div className="min-w-0 space-y-5">
          {m.photo && (
            <Panel title="Photo">
              <Photo meal={m} />
            </Panel>
          )}
          <Panel
            title="Nutrition"
            description={
              m.confirmation
                ? isDemoAccount && isRecordedMeal(m)
                  ? "From the recording's meal record"
                  : SOURCE_LABEL[m.confirmation.source]
                : undefined
            }
          >
            {m.confirmation ? (
              <dl className="divide-y divide-border text-sm">
                {MACRO_FIELDS.map(({ key, label, unit }) => (
                  <div key={key} className="flex items-baseline justify-between py-2">
                    <dt className="text-muted-foreground">{label}</dt>
                    <dd className="tabular font-medium">
                      {Math.round(m.confirmation![key] * 10) / 10} {unit}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-sm text-muted-foreground">Not confirmed yet.</p>
            )}
            {m.note && <p className="mt-3 text-[13px] text-muted-foreground">{m.note}</p>}
          </Panel>
        </div>
      </div>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this meal?</AlertDialogTitle>
            <AlertDialogDescription>
              The meal and its photo are removed and it no longer counts towards your personal
              history. Its forecast is kept as a record, but is no longer scored. This can't be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>Keep it</AlertDialogCancel>
            <AlertDialogAction
              disabled={remove.isPending}
              onClick={(e) => {
                e.preventDefault();
                remove.mutate();
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {remove.isPending && <Spinner />} Delete meal
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
