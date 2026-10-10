import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { HistoryChart } from "@/components/glucose/HistoryChart";
import { Card, CardTitle, EmptyState, ErrorState, MetricCard, PageHeader, Skeleton } from "@/components/common/States";
import { HistoricalMeals } from "@/components/meals/HistoricalMeals";
import { ParticipantSelector } from "@/components/participants/ParticipantSelector";
import { useHistory } from "@/hooks/useHistory";
import { fmtGlucose, fmtNumber } from "@/lib/formatting";
import { useAppState } from "@/state/AppState";

// No head() here: the home route inherits title/description/og/twitter from
// __root.tsx, and ships no og:image so serve-time hosting can inject the
// project's social preview (explicit og:image or latest screenshot).
export const Route = createFileRoute("/")({
  component: Index,
});

function Index() {
  const { mode, selected, selectedId } = useAppState();
  const { status, retry } = useHistory(mode, selectedId);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const history = status.state === "success" ? status.data : null;
  const latestGlucose = history?.glucose.at(-1)?.glucose_mg_dl;
  const latestMeal = history?.meals.at(-1);

  return (
    <div suppressHydrationWarning>
      <PageHeader
        title="Patient overview"
        subtitle="Review glucose history, heart rate, and meal context for a selected participant."
        aside={<ParticipantSelector />}
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="Latest glucose"
          value={fmtGlucose(latestGlucose)}
          unit="mg/dL"
          hint={latestGlucose == null ? "Waiting for observations" : "Most recent recorded value"}
          loading={status.state === "loading"}
        />
        <MetricCard
          label="Glucose observations"
          value={history?.glucose.length ?? "—"}
          hint={history ? "Available in selected period" : "Select a participant"}
          loading={status.state === "loading"}
        />
        <MetricCard
          label="Recorded meals"
          value={history?.meals.length ?? "—"}
          hint={latestMeal ? "Most recent meal is shown below" : "No meals recorded"}
          loading={status.state === "loading"}
        />
        <MetricCard
          label="Latest heart rate"
          value={fmtNumber(history?.heart_rate.at(-1)?.heart_rate_bpm)}
          unit="bpm"
          hint={selected?.observation_end ? "Within observation period" : "Waiting for observations"}
          loading={status.state === "loading"}
        />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
        <Card>
          <CardTitle>Glucose and heart rate history</CardTitle>
          {status.state === "error" && <ErrorState error={status.error} onRetry={retry} />}
          {status.state === "success" && !history.glucose.length && (
            <EmptyState title="No glucose observations" body="There is no historical glucose data for this participant." />
          )}
          {status.state === "success" && history.glucose.length > 0 && (
            mounted ? <HistoryChart history={history} /> : <Skeleton className="h-[340px] w-full" />
          )}
          {status.state === "loading" && <Skeleton className="h-[340px] w-full" />}
          {status.state === "idle" && <EmptyState title="Select a participant" body="Choose a participant to load their history." />}
        </Card>
        <Card>
          <CardTitle>Historical meals</CardTitle>
          {status.state === "error" && <ErrorState error={status.error} onRetry={retry} />}
          {status.state === "success" && <HistoricalMeals meals={history.meals} />}
          {status.state === "loading" && <Skeleton className="h-48 w-full" />}
          {status.state === "idle" && <EmptyState title="No participant selected" />}
        </Card>
      </div>
    </div>
  );
}
