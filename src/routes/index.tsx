import { createFileRoute } from "@tanstack/react-router";
import { RefreshCw, SkipForward } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { HistoryChart } from "@/components/glucose/HistoryChart";
import { Card, CardTitle, EmptyState, ErrorState, MetricCard, PageHeader, Skeleton } from "@/components/common/States";
import { getAlerts, getCgmReadings, getDemoDevices, getMeals, getOutcome, getPrediction, getReplay, getWearableObservations, advanceReplay } from "@/services/v2Client";
import type { DemoDevices, ReplayState, V2Outcome, V2Prediction, WearableObservation } from "@/services/v2Types";
import type { HistoryResponse } from "@/types/contracts";

export const Route = createFileRoute("/")({ component: Dashboard });

type Checkpoint = { kind?: string; done?: boolean; meal_id?: string; result?: { prediction_id?: string; meal_id?: string } | null };

/** The most recent completed replay forecast: the backend reports each one in `checkpoints[].result`. */
function latestForecast(state: ReplayState | null): { predictionId: string; mealId: string | null } | null {
  const done = ((state?.checkpoints ?? []) as Checkpoint[]).filter(
    (c) => c.kind === "meal_forecast" && c.done && c.result?.prediction_id,
  );
  const last = done.at(-1);
  return last?.result?.prediction_id
    ? { predictionId: last.result.prediction_id, mealId: last.meal_id ?? last.result.meal_id ?? null }
    : null;
}

function Dashboard() {
  const [replay, setReplay] = useState<ReplayState | null>(null);
  const [devices, setDevices] = useState<DemoDevices | null>(null);
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [wearables, setWearables] = useState<WearableObservation[]>([]);
  const [alertCount, setAlertCount] = useState(0);
  const [latestAlert, setLatestAlert] = useState<{ headline?: string; detail?: string; level?: string } | null>(null);
  const [prediction, setPrediction] = useState<V2Prediction | null>(null);
  const [outcome, setOutcome] = useState<V2Outcome | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [nextReplay, cgm, meals, nextDevices, wearableResult, alerts] = await Promise.all([
        getReplay().catch(() => null),
        getCgmReadings(),
        getMeals(),
        getDemoDevices().catch(() => null),
        getWearableObservations().catch(() => ({ observations: [] })),
        getAlerts().catch(() => ({ alerts: [] })),
      ]);
      setReplay(nextReplay);
      const forecast = latestForecast(nextReplay);
      if (forecast) {
        const [nextPrediction, nextOutcome] = await Promise.all([
          getPrediction(forecast.predictionId),
          forecast.mealId ? getOutcome(forecast.mealId).catch(() => null) : Promise.resolve(null),
        ]);
        setPrediction(nextPrediction);
        setOutcome(nextOutcome);
      } else {
        setPrediction(null);
        setOutcome(null);
      }
      setDevices(nextDevices);
      setWearables(wearableResult.observations);
      setAlertCount(alerts.alerts.length);
      setLatestAlert(alerts.alerts.at(-1) ?? null);
      setHistory({
        participant_id: "authenticated-user",
        data_source: "v2",
        observation_start: cgm.start ?? null,
        observation_end: cgm.end ?? null,
        glucose: cgm.readings.map((reading) => ({ timestamp: reading.observed_at, glucose_mg_dl: reading.glucose_mg_dl })),
        heart_rate: wearableResult.observations.filter((observation) => observation.metric === "heart_rate").map((observation) => ({ timestamp: observation.observed_at, heart_rate_bpm: observation.value })),
        meals: meals.meals.map((meal) => ({ timestamp: meal.occurred_at, carbs_g: meal.macros?.carbs_g ?? null, protein_g: meal.macros?.protein_g ?? null, fat_g: meal.macros?.fat_g ?? null, fiber_g: meal.macros?.fiber_g ?? null, calories_kcal: meal.macros?.calories_kcal ?? null })),
      });
      setError(null);
    } catch (reason) {
      setError(reason);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const advance = async () => {
    if (!replay || replay.status === "complete") return;
    setBusy(true);
    try {
      await advanceReplay({ expected_event_index: replay.event_index });
      // load() re-reads the replay state and shows its latest forecast and outcome.
      await load();
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  };

  const latestGlucose = history?.glucose.at(-1)?.glucose_mg_dl;
  const activeWearables = devices?.profiles.filter((device) => device.status === "demo_simulated") ?? [];

  return <div>
    <PageHeader title="Patient overview" subtitle="Your replayed glucose history and simulated health context." />
    {replay && <div className="mb-6 rounded-xl border border-amber-400/60 bg-amber-50 p-4 text-sm text-amber-900"><div className="flex flex-wrap items-center justify-between gap-3"><div><strong>DEMO · HISTORICAL REPLAY · NOT LIVE</strong><span className="ml-2 rounded-full border border-amber-700/30 px-2 py-0.5 text-xs">{replay.mode_label}</span><p className="mt-1">Replay time: {new Date(replay.position).toLocaleString()}</p></div><div className="flex items-center gap-2"><span>{replay.personalization_progress.admissible_prior_meals} eligible previous meals — {replay.personalization_progress.retrieval_active ? "personalization active" : `personalization begins at ${replay.personalization_progress.retrieval_min_history}`}</span><button className="rounded-md border border-amber-700/30 p-2 disabled:opacity-50" onClick={() => void load()} aria-label="Refresh replay"><RefreshCw className="h-4 w-4" /></button><button className="inline-flex items-center gap-2 rounded-md bg-amber-900 px-3 py-2 text-white disabled:opacity-50" disabled={busy || replay.status === "complete"} onClick={() => void advance()}>{busy ? "Advancing…" : replay.status === "complete" ? "Replay complete" : <><SkipForward className="h-4 w-4" />Advance</>}</button></div></div><p className="mt-2 text-xs">{replay.personalization_progress.message}</p></div>}
    {devices?.disclaimer && <p className="mb-4 rounded-lg border p-3 text-xs text-muted-foreground">{devices.disclaimer}</p>}
    {devices?.glucose_source && <p className="mb-4 text-xs text-muted-foreground"><strong>{devices.glucose_source.label}</strong> · {devices.glucose_source.description}</p>}
    {error != null && <ErrorState error={error} onRetry={() => void load()} />}
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><MetricCard label="Latest glucose" value={latestGlucose == null ? "—" : Math.round(latestGlucose)} unit="mg/dL" hint="Research recording at replay time" /><MetricCard label="Glucose observations" value={history?.glucose.length ?? "—"} hint="Visible through replay time" /><MetricCard label="Recorded meals" value={history?.meals.length ?? "—"} hint="Account history" /><MetricCard label="Sample alerts" value={alertCount} hint="Informational only" /></div>
    {prediction && <Card className="mt-6"><CardTitle>Latest replay forecast</CardTitle><p className="mb-3 text-sm text-muted-foreground">Prospective forecast from the authenticated v2 backend. Personal history: {prediction.personalization?.admissible_prior_meals ?? 0} admissible prior meals; retrieval {prediction.personalization?.retrieval_active ? "active" : "off"}.</p><div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{[30, 60, 90, 120].map((minute) => <MetricCard key={minute} label={`${minute}-minute`} value={prediction.forecast.glucose_at_minutes[String(minute) as keyof typeof prediction.forecast.glucose_at_minutes]} unit="mg/dL" />)}</div>{outcome && <p className="mt-4 rounded-lg border p-3 text-sm">{outcome.status === "scored" ? "Outcome revealed: observed glucose is available for comparison." : `Outcome pending; available at replay time ${outcome.scoring_not_before ?? "later"}.`}</p>}</Card>}
    {latestAlert && <Card className="mt-6 border-warning-border/50 bg-warning"><CardTitle>Informational spike notification</CardTitle><p className="font-medium">{latestAlert.headline ?? latestAlert.level ?? "Sample alert"}</p><p className="text-sm">{latestAlert.detail ?? "A backend-generated sample alert is available."}</p><p className="mt-2 text-xs">Alerts are informational and do not constitute clinical diagnosis, medical advice, or insulin dose instructions.</p></Card>}
    <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]"><Card><CardTitle>Research glucose history</CardTitle>{history ? <HistoryChart history={history} /> : <Skeleton className="h-48 w-full" />}</Card><Card><CardTitle>Simulated health context</CardTitle>{activeWearables.length === 0 ? <EmptyState title="No simulated sources active" body="Use Connect Devices to add generated sample wearable context." /> : <div className="space-y-3">{activeWearables.map((device) => <div key={device.profile} className="rounded-lg border p-3"><p className="font-medium">{device.display_label}</p><p className="text-xs text-muted-foreground">{device.metrics.map((metric) => `${metric.metric}: ${metric.observations_visible}`).join(" · ")}</p></div>)}</div>}<p className="mt-4 text-xs text-muted-foreground">Wearable observations are displayed for context and are not inputs to the glucose forecast.</p></Card></div>
    <Card className="mt-6"><CardTitle>Historical meals</CardTitle>{history ? <p className="text-sm text-muted-foreground">{history.meals.length} meals are visible. Advance the replay to release the next recorded meal, forecast, or outcome.</p> : <Skeleton className="h-10 w-full" />}</Card>
    {wearables.length > 0 && <p className="mt-3 text-xs text-muted-foreground">{wearables.length} simulated wearable observations available.</p>}
  </div>;
}
