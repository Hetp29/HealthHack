import { Link, createFileRoute } from "@tanstack/react-router";
import { LineChart as LineIcon, ShieldAlert, Sparkles } from "lucide-react";
import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  Async,
  Disclosure,
  EmptyState,
  KeyValue,
  Notice,
  PageHeader,
  Panel,
  Pill,
  SkeletonLines,
  Stat,
} from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import { describeReason } from "@/features/ForecastView";
import { isRecordedMeal } from "@/features/mealText";
import { useMeals, useModelInfo, useOutcomes, usePredictions } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { toUnit, useUnits } from "@/lib/units";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/insights")({ component: InsightsPage });

function InsightsPage() {
  const session = useSession();
  const { clock, replay, isDemoAccount, status } = session;
  const { unit, glucose } = useUnits();
  const meals = useMeals();
  const predictions = usePredictions();
  const modelInfo = useModelInfo();

  // Meals whose forecast can honestly be compared with what was recorded afterwards.
  const forecastMeals = useMemo(
    () =>
      (meals.data?.meals ?? [])
        .filter((m) => m.prediction_id && (!isDemoAccount || isRecordedMeal(m)))
        .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at)),
    [meals.data, isDemoAccount],
  );
  const outcomes = useOutcomes(forecastMeals);
  const rows = forecastMeals.map((meal, index) => ({ meal, outcome: outcomes[index]?.data }));
  const scored = rows.filter(
    (r) => r.outcome?.status === "scored" && r.outcome.observed && r.outcome.errors,
  );
  const loadingOutcomes = outcomes.some((o) => o.isPending);
  const misses = scored
    .map((r) => r.outcome!.errors!.mae_mg_dl)
    .filter((v): v is number => typeof v === "number");
  const meanMiss = misses.length ? misses.reduce((a, b) => a + b, 0) / misses.length : null;
  const eligible = scored.filter((r) => r.outcome!.eligible_for_personalization).length;

  const latest = predictions.data?.predictions[0]?.personalization;
  const progress = replay?.personalization_progress;
  const count = latest?.admissible_prior_meals ?? progress?.admissible_prior_meals ?? null;
  const needed = latest?.retrieval_min_history ?? progress?.retrieval_min_history ?? 5;
  const active = latest?.retrieval_active ?? progress?.retrieval_active ?? false;

  const chart = scored.map((r, i) => ({
    label: `${i + 1}`,
    when: clock.dateTime(r.meal.occurred_at),
    predicted: toUnit(r.outcome!.predicted.metrics.peak_mg_dl, unit),
    observed:
      r.outcome!.observed!.peak_mg_dl !== undefined
        ? toUnit(r.outcome!.observed!.peak_mg_dl, unit)
        : undefined,
  }));
  const number = (v: number) => (unit === "mg/dL" ? String(Math.round(v)) : v.toFixed(1));

  if (!session.processingAllowed) {
    return (
      <>
        <PageHeader title="Insights" />
        <Panel>
          <EmptyState
            icon={ShieldAlert}
            title="GLIMPSE needs your permission"
            action={
              <Button asChild>
                <Link to="/settings" hash="privacy">
                  Review permissions
                </Link>
              </Button>
            }
          >
            Processing of your meals and glucose data is switched off.
          </EmptyState>
        </Panel>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Insights"
        description="How your forecasts have compared with what happened, and how much of your own history they can draw on."
      />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Panel title="Personal history" className="lg:col-span-1">
          {!session.personalizationAllowed ? (
            <Notice title="Switched off">
              You haven't allowed GLIMPSE to learn from your past meals, so every forecast is a
              general one.{" "}
              <Link to="/settings" hash="privacy" className="font-medium underline">
                Change this in Settings
              </Link>
              .
            </Notice>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <Sparkles
                  className={cn("h-5 w-5", active ? "text-primary" : "text-muted-foreground")}
                />
                <span className="font-semibold" data-testid="insights-personalization">
                  {active ? "In use" : "Not in use yet"}
                </span>
              </div>
              <div className="mt-4 flex items-baseline gap-1.5">
                <span className="tabular text-3xl font-semibold">{count ?? 0}</span>
                <span className="text-sm text-muted-foreground">
                  eligible earlier meal{count === 1 ? "" : "s"}
                  {!active && ` of the ${needed} needed`}
                </span>
              </div>
              <div
                className="mt-3 h-2 overflow-hidden rounded-full bg-muted"
                role="progressbar"
                aria-label="Eligible meals towards personal history"
                aria-valuemin={0}
                aria-valuemax={needed}
                aria-valuenow={Math.min(count ?? 0, needed)}
              >
                <div
                  className="h-full rounded-full bg-primary"
                  style={{ width: `${Math.min(100, ((count ?? 0) / needed) * 100)}%` }}
                />
              </div>
              <p className="mt-4 text-sm text-muted-foreground">
                {count === null
                  ? "This count appears with your first forecast."
                  : active
                    ? "Your latest forecast could compare the meal with your own earlier meals and how your glucose responded to them."
                    : `After ${needed} eligible scored meals, a forecast can start comparing a new meal with your own earlier ones. Until then every forecast is a general one.`}
              </p>
              {progress?.description && (
                <p className="mt-2 text-[13px] text-muted-foreground">{progress.description}</p>
              )}
            </>
          )}
          <Disclosure title="What makes a meal eligible" className="mt-4">
            A meal counts once its forecast has been scored: its nutrition was confirmed before the
            forecast, the full two hours afterwards have enough glucose readings, no other meal fell
            inside those two hours, and you have allowed learning from past meals. The model itself
            is fixed and is not retrained on your data.
          </Disclosure>
        </Panel>

        <Panel
          title="Forecast against outcome"
          description="Forecast peak and observed peak for each scored meal."
          className="lg:col-span-2"
        >
          <Async query={meals} skeleton={<SkeletonLines lines={5} />} compact>
            {() =>
              forecastMeals.length === 0 ? (
                <EmptyState icon={LineIcon} title="No scored forecasts yet" compact>
                  Once a meal has a forecast and two hours of glucose afterwards, the comparison
                  appears here.
                </EmptyState>
              ) : loadingOutcomes && scored.length === 0 ? (
                <SkeletonLines lines={5} />
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
                    <Stat label="Forecast meals" value={forecastMeals.length} />
                    <Stat
                      label="Scored"
                      value={scored.length}
                      hint={`${forecastMeals.length - scored.length} waiting or not scorable`}
                    />
                    <Stat
                      label="Average miss"
                      value={meanMiss !== null ? glucose(meanMiss) : "–"}
                      unit={meanMiss !== null ? unit : ""}
                      hint="mean of each meal's average miss"
                    />
                    <Stat
                      label="Count towards history"
                      value={eligible}
                      hint={`of ${scored.length} scored`}
                    />
                  </div>
                  {chart.length > 1 && (
                    <div
                      className="mt-5"
                      role="img"
                      aria-label={`Forecast peak and observed peak for ${chart.length} scored meals. The table below lists the values.`}
                    >
                      <ResponsiveContainer width="100%" height={240}>
                        <LineChart data={chart} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
                          <CartesianGrid stroke="var(--border)" vertical={false} />
                          <XAxis
                            dataKey="label"
                            tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                            tickLine={false}
                            axisLine={{ stroke: "var(--border)" }}
                            label={{
                              value: "Scored meal, in order",
                              position: "insideBottom",
                              offset: 2,
                              fontSize: 11,
                              fill: "var(--muted-foreground)",
                            }}
                            height={44}
                          />
                          <YAxis
                            tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                            tickLine={false}
                            axisLine={false}
                            width={44}
                            domain={["auto", "auto"]}
                            tickFormatter={number}
                          />
                          <Tooltip
                            isAnimationActive={false}
                            content={({ active: on, payload }) => {
                              const row = payload?.[0]?.payload as
                                (typeof chart)[number] | undefined;
                              return on && row ? (
                                <div className="rounded-lg border border-border bg-card px-3 py-2 text-sm shadow-raised">
                                  <div className="text-xs text-muted-foreground">{row.when}</div>
                                  <div className="tabular font-semibold text-predicted">
                                    {number(row.predicted)} {unit} forecast peak
                                  </div>
                                  {row.observed !== undefined && (
                                    <div className="tabular font-semibold text-observed">
                                      {number(row.observed)} {unit} observed peak
                                    </div>
                                  )}
                                </div>
                              ) : null;
                            }}
                          />
                          <Legend
                            verticalAlign="top"
                            align="right"
                            height={28}
                            iconType="plainline"
                            formatter={(v: string) => (
                              <span className="text-xs text-muted-foreground">{v}</span>
                            )}
                          />
                          <Line
                            name="Forecast peak"
                            dataKey="predicted"
                            stroke="var(--predicted)"
                            strokeWidth={2}
                            dot={{ r: 2.5 }}
                            isAnimationActive={false}
                          />
                          <Line
                            name="Observed peak"
                            dataKey="observed"
                            stroke="var(--observed)"
                            strokeWidth={2}
                            dot={{ r: 2.5 }}
                            isAnimationActive={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </>
              )
            }
          </Async>
        </Panel>

        {forecastMeals.length > 0 && (
          <Panel title="Meal by meal" className="lg:col-span-3" padded={false}>
            <div className="overflow-x-auto border-t border-border">
              <table className="w-full min-w-[40rem] text-left text-sm">
                <caption className="sr-only">
                  Each forecast meal with its forecast peak, observed peak and average miss, in{" "}
                  {unit}
                </caption>
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    {[
                      "Meal",
                      "Forecast peak",
                      "Observed peak",
                      "Average miss",
                      "Personal history",
                    ].map((h) => (
                      <th key={h} scope="col" className="px-5 py-2.5 font-medium sm:px-6">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="tabular">
                  {[...rows].reverse().map(({ meal, outcome }) => (
                    <tr key={meal.meal_id} className="border-t border-border">
                      <th scope="row" className="px-5 py-3 font-medium sm:px-6">
                        <Link
                          to="/meals/$mealId"
                          params={{ mealId: meal.meal_id }}
                          className="text-primary underline-offset-2 hover:underline"
                        >
                          {clock.dateTime(meal.occurred_at)}
                        </Link>
                      </th>
                      <td className="px-5 py-3 text-predicted sm:px-6">
                        {outcome ? glucose(outcome.predicted.metrics.peak_mg_dl) : "…"}
                      </td>
                      <td className="px-5 py-3 text-observed sm:px-6">
                        {outcome?.status === "scored" ? glucose(outcome.observed?.peak_mg_dl) : "–"}
                      </td>
                      <td className="px-5 py-3 sm:px-6">
                        {outcome?.status === "scored" ? glucose(outcome.errors?.mae_mg_dl) : "–"}
                      </td>
                      <td className="px-5 py-3 sm:px-6">
                        {!outcome ? (
                          "…"
                        ) : outcome.status === "pending" ? (
                          <Pill tone="neutral">Waiting for two hours of readings</Pill>
                        ) : outcome.status !== "scored" ? (
                          <Pill tone="amber">Couldn't be scored</Pill>
                        ) : outcome.eligible_for_personalization ? (
                          <Pill tone="green">Counts</Pill>
                        ) : (
                          <span
                            title={outcome.ineligibility_reasons.map(describeReason).join("; ")}
                          >
                            <Pill tone="neutral">Doesn't count</Pill>
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}

        <div className="lg:col-span-3">
          <Disclosure title="About the forecasting model">
            <p>
              Forecasts come from one fixed research model. It was evaluated on a research dataset,
              not in a clinical trial, and its uncertainty is not calibrated, which is why GLIMPSE
              never shows a range or a probability.
            </p>
            {status && (
              <KeyValue
                className="mt-3"
                items={[
                  ["Model", status.model.model_id ?? "Not loaded"],
                  [
                    "Integrity check",
                    status.model.integrity_verified
                      ? "Passed"
                      : status.model.integrity_verified === false
                        ? "Failed"
                        : "Not enforced",
                  ],
                  ["Personal history policy", status.personalization_policy.replace(/_/g, " ")],
                  ...(modelInfo.data
                    ? [
                        ["Version", modelInfo.data.model_version] as [string, string],
                        ["Feature set", modelInfo.data.feature_set_version] as [string, string],
                      ]
                    : []),
                ]}
              />
            )}
            {modelInfo.data && modelInfo.data.limitations.length > 0 && (
              <ul className="mt-3 list-disc space-y-1.5 pl-5">
                {modelInfo.data.limitations.map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            )}
          </Disclosure>
        </div>
      </div>
    </>
  );
}
