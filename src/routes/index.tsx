import { Link, createFileRoute } from "@tanstack/react-router";
import {
  Activity,
  ArrowRight,
  Bell,
  Camera,
  Check,
  ChevronRight,
  HeartPulse,
  LineChart,
  Pill as PillIcon,
  Plus,
  ShieldAlert,
  Sparkles,
  Utensils,
} from "lucide-react";
import { useState } from "react";

import { GlucoseHistoryChart } from "@/components/charts/GlucoseHistoryChart";
import {
  Async,
  EmptyState,
  Notice,
  PageHeader,
  Panel,
  Pill,
  SkeletonBlock,
  SkeletonLines,
  SourceBadge,
  Stat,
} from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import { METRIC_LABELS } from "@/features/DeviceCards";
import { PersonalizationPill } from "@/features/ForecastView";
import { isRecordedMeal, latestForecast, macroSummary, mealStatus } from "@/features/mealText";
import {
  useAlerts,
  useCgmReadings,
  useCgmStatus,
  useDueReminders,
  useMeals,
  usePredictions,
  useRegimens,
  useWearables,
} from "@/lib/queries";
import { useSession } from "@/lib/session";
import { formatSpan } from "@/lib/time";
import type { WearableObservation } from "@/lib/types";
import { useUnits } from "@/lib/units";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({ component: HomePage });

const FRESHNESS = {
  fresh: { label: "Up to date", tone: "green" },
  delayed: { label: "Delayed", tone: "amber" },
  stale: { label: "Out of date", tone: "amber" },
  no_data: { label: "No readings", tone: "neutral" },
} as const;

function greeting(hour: number) {
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

function HomePage() {
  const session = useSession();
  const { clock, user, replay, isDemoAccount } = session;
  const firstName = (user?.display_name ?? "")
    .replace(/\(.*?\)/g, "")
    .trim()
    .split(/\s+/)[0];
  const hour = clock.isReplay ? clock.now().getUTCHours() : new Date().getHours();

  if (!session.processingAllowed) {
    return (
      <>
        <PageHeader title={`${greeting(hour)}${firstName ? `, ${firstName}` : ""}`} />
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
            Processing of your meals and glucose data is switched off, so there is nothing to show.
            You can switch it back on in Settings.
          </EmptyState>
        </Panel>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={`${greeting(hour)}${firstName ? `, ${firstName}` : ""}`}
        description={
          isDemoAccount && replay
            ? `You're viewing a replay of a 2020 glucose recording. It is now ${clock.dateTime(replay.position)} in the recording.`
            : "Here's where your glucose and meals stand."
        }
        actions={
          <Button asChild className="h-11 lg:hidden">
            <Link to="/meals/log">
              <Plus className="h-4 w-4" /> Log a meal
            </Link>
          </Button>
        }
      />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <div className="min-w-0 space-y-5 lg:col-span-2">
          <GlucosePanel />
          <LatestForecast />
          <RecentMeals />
        </div>
        <div className="min-w-0 space-y-5">
          <NextSteps />
          <AlertsPanel />
          <MedicationsPanel />
          <PersonalizationPanel />
          <WearablesPanel />
        </div>
      </div>
    </>
  );
}

function GlucosePanel() {
  const { clock } = useSession();
  const { unit, glucose } = useUnits();
  const [hours, setHours] = useState(24);
  const status = useCgmStatus();
  const readings = useCgmReadings(hours);
  const meals = useMeals();
  const markers = (meals.data?.meals ?? []).map((m) => ({ at: m.occurred_at, label: "Meal" }));

  return (
    <Panel
      title="Glucose"
      action={
        <div role="group" aria-label="Time range" className="flex rounded-lg bg-muted p-0.5">
          {[3, 6, 24].map((h) => (
            <button
              key={h}
              type="button"
              onClick={() => setHours(h)}
              aria-pressed={hours === h}
              className={cn(
                "h-8 rounded-md px-3 text-[13px] font-medium",
                hours === h ? "bg-card shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {h} h
            </button>
          ))}
        </div>
      }
    >
      <Async query={status} skeleton={<SkeletonLines lines={2} />} compact>
        {(s) =>
          s.latest ? (
            <div className="flex flex-wrap items-end justify-between gap-4">
              <Stat
                label="Latest reading"
                value={<span data-testid="latest-glucose">{glucose(s.latest.glucose_mg_dl)}</span>}
                unit={unit}
                tone="observed"
                hint={`${clock.dateTime(s.latest.observed_at)} · ${s.latest_age_minutes !== null ? `${formatSpan(s.latest_age_minutes)} old` : ""}`}
              />
              <div className="flex flex-wrap items-center gap-1.5">
                <SourceBadge source={s.data_class} />
                <Pill tone={FRESHNESS[s.freshness].tone}>{FRESHNESS[s.freshness].label}</Pill>
                <Pill tone={s.forecast_ready ? "teal" : "neutral"}>
                  {s.forecast_ready ? "Ready to forecast" : "Too old to forecast"}
                </Pill>
              </div>
            </div>
          ) : (
            <EmptyState
              icon={Activity}
              title="No glucose readings yet"
              compact
              action={
                <Button asChild variant="outline">
                  <Link to="/health" search={{ tab: "glucose" }}>
                    Add glucose data
                  </Link>
                </Button>
              }
            >
              Import an export from your glucose monitor's app, or type a reading in. A forecast
              needs a reading from the 15 minutes before a meal.
            </EmptyState>
          )
        }
      </Async>
      {status.data?.latest && (
        <div className="mt-4">
          <Async query={readings} skeleton={<SkeletonBlock className="h-64" />} compact>
            {(r) =>
              r.readings.length > 1 ? (
                <GlucoseHistoryChart
                  readings={r.readings}
                  unit={unit}
                  clock={clock}
                  meals={markers}
                />
              ) : (
                <p className="py-10 text-center text-sm text-muted-foreground">
                  No readings in the last {hours} hours.
                </p>
              )
            }
          </Async>
          {!status.data.forecast_ready && (
            <p className="mt-2 text-[13px] text-muted-foreground">
              {status.data.forecast_requirement}
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}

function LatestForecast() {
  const { clock } = useSession();
  const { unit, glucose } = useUnits();
  const predictions = usePredictions();
  const meals = useMeals();
  return (
    <Panel title="Latest forecast">
      <Async query={predictions} compact>
        {({ predictions: list }) => {
          const found = meals.data ? latestForecast(list, meals.data.meals) : null;
          if (!meals.data && list.length > 0) return <SkeletonLines lines={3} />;
          if (!found) {
            return (
              <EmptyState
                icon={LineChart}
                title="No forecasts yet"
                compact
                action={
                  <Button asChild>
                    <Link to="/meals/log">
                      <Camera className="h-4 w-4" /> Log a meal
                    </Link>
                  </Button>
                }
              >
                Log a meal and confirm its nutrition to see how your glucose may respond over the
                next two hours.
              </EmptyState>
            );
          }
          const { prediction: latest, meal } = found;
          const { metrics, glucose_at_minutes: at } = latest.forecast;
          return (
            <div
              data-testid="home-latest-forecast"
              data-meal-id={latest.meal_id}
              data-prediction-id={latest.prediction_id}
            >
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-sm font-medium text-foreground">
                  Meal at {clock.dateTime(meal.occurred_at)}
                </span>
                <PersonalizationPill prediction={latest} />
              </div>
              <p
                className="mt-1 text-[13px] text-muted-foreground"
                data-testid="home-latest-forecast-meal"
              >
                {macroSummary(meal.confirmation)}
              </p>
              <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
                <Stat
                  label="Forecast peak"
                  value={
                    <span data-testid="home-latest-forecast-peak">
                      {glucose(metrics.peak_mg_dl)}
                    </span>
                  }
                  unit={unit}
                  tone="predicted"
                />
                <Stat label="Time to peak" value={metrics.time_to_peak_minutes} unit="min" />
                <Stat label="At 60 min" value={glucose(at["60"])} unit={unit} />
                <Stat label="At 120 min" value={glucose(at["120"])} unit={unit} />
              </div>
              <Button asChild variant="outline" className="mt-5">
                <Link to="/meals/$mealId" params={{ mealId: latest.meal_id }}>
                  View the full forecast <ArrowRight className="h-4 w-4" />
                </Link>
              </Button>
            </div>
          );
        }}
      </Async>
    </Panel>
  );
}

function RecentMeals() {
  const { clock } = useSession();
  const { unit, glucose } = useUnits();
  const meals = useMeals();
  const predictions = usePredictions();
  const peaks = new Map(
    (predictions.data?.predictions ?? []).map((p) => [
      p.prediction_id,
      p.forecast.metrics.peak_mg_dl,
    ]),
  );
  return (
    <Panel
      title="Recent meals"
      action={
        <Button asChild variant="ghost" size="sm">
          <Link to="/meals">All meals</Link>
        </Button>
      }
    >
      <Async query={meals} compact>
        {({ meals: list }) => {
          // Newest first; at one replay instant, a meal the person added follows the recorded one.
          const recent = [...list]
            .sort(
              (a, b) =>
                b.occurred_at.localeCompare(a.occurred_at) ||
                Number(isRecordedMeal(a)) - Number(isRecordedMeal(b)) ||
                a.meal_id.localeCompare(b.meal_id),
            )
            .slice(0, 4);
          if (!recent.length) {
            return (
              <EmptyState icon={Utensils} title="No meals logged" compact>
                Your logged meals will appear here.
              </EmptyState>
            );
          }
          return (
            <ul className="-mx-2 divide-y divide-border">
              {recent.map((meal) => {
                const status = mealStatus(meal);
                const peak = meal.prediction_id ? peaks.get(meal.prediction_id) : undefined;
                return (
                  <li key={meal.meal_id} data-testid="home-meal-row" data-meal-id={meal.meal_id}>
                    <Link
                      to="/meals/$mealId"
                      params={{ mealId: meal.meal_id }}
                      className="flex items-center gap-3 rounded-lg px-2 py-3 hover:bg-muted/50"
                    >
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                        {meal.entry_method === "photo" ? (
                          <Camera className="h-4 w-4" />
                        ) : (
                          <Utensils className="h-4 w-4" />
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium">
                          {clock.dateTime(meal.occurred_at)}
                        </span>
                        <span className="block truncate text-[13px] text-muted-foreground">
                          {macroSummary(meal.confirmation)}
                        </span>
                        {peak !== undefined && (
                          <span
                            className="block text-[13px] text-predicted"
                            data-testid="home-meal-peak"
                          >
                            Forecast peak {glucose(peak)} {unit}
                          </span>
                        )}
                      </span>
                      <Pill tone={status.tone} className="hidden sm:inline-flex">
                        {status.label}
                      </Pill>
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                    </Link>
                  </li>
                );
              })}
            </ul>
          );
        }}
      </Async>
    </Panel>
  );
}

/** What to do next, worked out from what the account really has so far. */
function NextSteps() {
  const { isDemoAccount, replay, onboarding, clock } = useSession();
  const status = useCgmStatus();
  const meals = useMeals();
  const predictions = usePredictions();
  const regimens = useRegimens();

  if (isDemoAccount && replay) {
    const next = replay.next_checkpoint;
    return (
      <Panel title="Next in the replay">
        {next ? (
          <>
            <p className="text-sm text-muted-foreground">
              <span className="font-medium text-foreground">{clock.dateTime(next.at)}.</span>{" "}
              {next.kind === "meal_forecast"
                ? "The next recorded meal is logged and a forecast is made for it."
                : "The two hours after a meal are released and its forecast is scored."}
            </p>
            <Button
              className="mt-4 w-full"
              onClick={() =>
                document.querySelector<HTMLButtonElement>("[data-testid=provenance]")?.click()
              }
            >
              Open demo controls
            </Button>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            The recording has been replayed to its end. Open the demo controls to reset it.
          </p>
        )}
      </Panel>
    );
  }

  const wantsMeds = onboarding?.answers["takes_glucose_medication"] === "yes";
  const steps = [
    {
      done: status.data ? status.data.freshness !== "no_data" : null,
      label: "Add glucose readings",
      to: "/health",
      search: { tab: "glucose" as const },
    },
    {
      done: meals.data ? meals.data.meals.length > 0 : null,
      label: "Log your first meal",
      to: "/meals/log",
      search: undefined,
    },
    {
      done: predictions.data ? predictions.data.predictions.length > 0 : null,
      label: "Get your first forecast",
      to: "/meals/log",
      search: undefined,
    },
    ...(wantsMeds
      ? [
          {
            done: regimens.data ? regimens.data.regimens.length > 0 : null,
            label: "Add your medication schedule",
            to: "/health",
            search: { tab: "medications" as const },
          },
        ]
      : []),
  ] as const;
  if (steps.every((s) => s.done === true)) return null;

  return (
    <Panel title="Getting started">
      <ul className="space-y-1">
        {steps.map((step) => (
          <li key={step.label}>
            <Link
              to={step.to}
              {...(step.search ? { search: step.search } : {})}
              className="flex items-center gap-3 rounded-lg px-2 py-2.5 text-sm hover:bg-muted/50"
            >
              <span
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border",
                  step.done
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border-strong",
                )}
              >
                {step.done && <Check className="h-3 w-3" />}
              </span>
              <span
                className={cn(
                  "flex-1",
                  step.done ? "text-muted-foreground line-through" : "font-medium",
                )}
              >
                {step.label}
              </span>
              {!step.done && <ChevronRight className="h-4 w-4 text-muted-foreground" />}
            </Link>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function AlertsPanel() {
  const { clock } = useSession();
  const alerts = useAlerts();
  const active = (alerts.data?.alerts ?? []).filter(
    (a) => a.status === "active" && !a.acknowledged_at,
  );
  if (!alerts.isError && active.length === 0) return null;
  return (
    <Panel
      title="Forecast alerts"
      action={
        <Button asChild variant="ghost" size="sm">
          <Link to="/health" search={{ tab: "alerts" }}>
            Manage
          </Link>
        </Button>
      }
    >
      <Async query={alerts} compact>
        {() => (
          <ul className="space-y-2">
            {active.slice(0, 3).map((alert) => (
              <li key={alert.alert_id}>
                <Link
                  to="/meals/$mealId"
                  params={{ mealId: alert.payload.meal_id }}
                  className="flex gap-3 rounded-lg bg-warning p-3 text-sm text-warning-foreground hover:brightness-95"
                >
                  <Bell className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>
                    <span className="block font-medium">{alert.payload.label}</span>
                    <span className="text-[13px] opacity-90">
                      {clock.dateTime(alert.created_at)}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Async>
    </Panel>
  );
}

function MedicationsPanel() {
  const { clock } = useSession();
  const due = useDueReminders();
  const regimens = useRegimens();
  const current = (regimens.data?.regimens ?? []).filter((r) => r.status === "current");
  if (regimens.data && current.length === 0) return null;
  return (
    <Panel
      title="Medications"
      action={
        <Button asChild variant="ghost" size="sm">
          <Link to="/health" search={{ tab: "medications" }}>
            Open
          </Link>
        </Button>
      }
    >
      <Async query={due} compact>
        {({ due: list }) =>
          list.length ? (
            <ul className="space-y-2">
              {list.slice(0, 3).map((dose) => (
                <li key={dose.dose_id} className="flex items-center gap-3 text-sm">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground">
                    <PillIcon className="h-4 w-4" />
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate font-medium">
                      {dose.regimen?.name ?? "Scheduled dose"}
                    </span>
                    <span className="text-[13px] text-muted-foreground">
                      Due {clock.dateTime(dose.scheduled_at)}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">
              No doses are due right now. {current.length} medication
              {current.length === 1 ? "" : "s"} on your list.
            </p>
          )
        }
      </Async>
    </Panel>
  );
}

function PersonalizationPanel() {
  const { replay, personalizationAllowed } = useSession();
  const predictions = usePredictions();
  const meals = useMeals();
  const latest = latestForecast(predictions.data?.predictions ?? [], meals.data?.meals ?? [])
    ?.prediction.personalization;
  const progress = replay?.personalization_progress;
  const count = latest?.admissible_prior_meals ?? progress?.admissible_prior_meals ?? null;
  const needed = latest?.retrieval_min_history ?? progress?.retrieval_min_history ?? 5;
  const active = latest?.retrieval_active ?? progress?.retrieval_active ?? false;

  return (
    <Panel
      title="Personal history"
      action={
        <Button asChild variant="ghost" size="sm">
          <Link to="/insights">Insights</Link>
        </Button>
      }
    >
      {!personalizationAllowed ? (
        <p className="text-sm text-muted-foreground">
          Learning from your past meals is switched off, so every forecast is a general one. You can
          change this in Settings.
        </p>
      ) : count === null ? (
        <p className="text-sm text-muted-foreground">
          Once you have {needed} scored meals, forecasts start to draw on how your own glucose
          responded before.
        </p>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <Sparkles
              className={cn("h-4 w-4", active ? "text-primary" : "text-muted-foreground")}
            />
            <span className="text-sm font-medium" data-testid="personalization-status">
              {active ? "Forecasts use your history" : `${count} of ${needed} eligible meals`}
            </span>
          </div>
          <div
            className="mt-3 h-2 overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={needed}
            aria-valuenow={Math.min(count, needed)}
            aria-label="Eligible meals towards personal history"
          >
            <div
              className="h-full rounded-full bg-primary"
              style={{ width: `${Math.min(100, (count / needed) * 100)}%` }}
            />
          </div>
          <p className="mt-3 text-[13px] text-muted-foreground">
            {active
              ? `${count} earlier scored meals were available to your latest forecast.`
              : `${Math.max(0, needed - count)} more scored meal${needed - count === 1 ? "" : "s"} until forecasts can draw on your own responses.`}
          </p>
        </>
      )}
    </Panel>
  );
}

function WearablesPanel() {
  const { clock } = useSession();
  const wearables = useWearables({ hours: 48 });
  return (
    <Panel
      title="Wearables"
      action={
        <Button asChild variant="ghost" size="sm">
          <Link to="/health" search={{ tab: "wearables" }}>
            Open
          </Link>
        </Button>
      }
    >
      <Async query={wearables} compact>
        {({ observations }) => {
          const latest = new Map<string, WearableObservation>();
          for (const o of observations) {
            const seen = latest.get(o.metric);
            if (!seen || o.observed_at > seen.observed_at) latest.set(o.metric, o);
          }
          if (!latest.size) {
            return (
              <EmptyState
                icon={HeartPulse}
                title="No wearable data"
                compact
                action={
                  <Button asChild variant="outline" size="sm">
                    <Link to="/health" search={{ tab: "devices" }}>
                      See data sources
                    </Link>
                  </Button>
                }
              >
                Wearable data is shown for context only. It is never used by the forecast.
              </EmptyState>
            );
          }
          const simulated = [...latest.values()].some((o) => o.detail.simulated);
          return (
            <>
              {simulated && <SourceBadge source="simulated" className="mb-3" />}
              <dl className="space-y-2.5 text-sm">
                {[...latest.values()].slice(0, 5).map((o) => (
                  <div key={o.metric} className="flex items-baseline justify-between gap-3">
                    <dt className="text-muted-foreground">{METRIC_LABELS[o.metric] ?? o.metric}</dt>
                    <dd className="tabular text-right font-medium">
                      {Math.round(o.value).toLocaleString()} {o.unit === "count" ? "steps" : o.unit}
                      <span className="block text-xs font-normal text-muted-foreground">
                        {clock.dateTime(o.observed_at)}
                      </span>
                    </dd>
                  </div>
                ))}
              </dl>
              <p className="mt-3 text-[13px] text-muted-foreground">
                Shown for context. Not used by the forecast.
              </p>
            </>
          );
        }}
      </Async>
    </Panel>
  );
}
