import { Link } from "@tanstack/react-router";
import { HeartPulse } from "lucide-react";
import { useMemo } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import {
  Async,
  EmptyState,
  Notice,
  Panel,
  SkeletonBlock,
  SourceBadge,
} from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import { METRIC_LABELS } from "@/features/DeviceCards";
import { useWearables } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { WearableObservation } from "@/lib/types";

const PROVIDER_LABELS: Record<string, string> = {
  healthkit_bridge: "Apple Health",
  apple_health: "Apple Health",
  apple_watch: "Apple Watch",
  fitbit: "Fitbit",
  whoop: "WHOOP",
};

function value(o: WearableObservation) {
  if (o.metric === "sleep_duration" || o.metric === "workout") {
    const hours = Math.floor(o.value / 60);
    return hours ? `${hours} h ${Math.round(o.value % 60)} min` : `${Math.round(o.value)} min`;
  }
  return `${Math.round(o.value).toLocaleString()} ${o.unit === "count" ? "" : o.unit}`.trim();
}

export function WearablesTab() {
  const { clock } = useSession();
  const wearables = useWearables({ hours: 96 });

  const groups = useMemo(() => {
    const map = new Map<string, WearableObservation[]>();
    for (const o of wearables.data?.observations ?? []) {
      const key = `${o.metric}|${o.detail.device_profile ?? o.provider}`;
      map.set(key, [...(map.get(key) ?? []), o]);
    }
    return [...map.entries()]
      .map(([key, list]) => ({
        key,
        list: list.sort((a, b) => a.observed_at.localeCompare(b.observed_at)),
      }))
      .sort((a, b) => a.key.localeCompare(b.key));
  }, [wearables.data]);

  return (
    <div className="space-y-5">
      <Notice title="Shown for context only">
        Heart rate, steps, sleep, recovery and workouts are stored and displayed. They are not
        inputs to the glucose forecast.
      </Notice>
      <Async
        query={wearables}
        skeleton={
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <SkeletonBlock className="h-40" />
            <SkeletonBlock className="h-40" />
          </div>
        }
      >
        {() =>
          groups.length === 0 ? (
            <Panel>
              <EmptyState
                icon={HeartPulse}
                title="No wearable data"
                action={
                  <Button asChild variant="outline">
                    <Link to="/health" search={{ tab: "devices" }}>
                      See data sources
                    </Link>
                  </Button>
                }
              >
                Nothing has been received from a wearable in the last four days.
              </EmptyState>
            </Panel>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {groups.map(({ key, list }) => {
                const latest = list[list.length - 1]!;
                const source =
                  latest.detail.source_label ??
                  PROVIDER_LABELS[latest.detail.device_profile ?? latest.provider] ??
                  latest.provider;
                const series = latest.metric === "heart_rate" && list.length > 3;
                return (
                  <Panel
                    key={key}
                    title={METRIC_LABELS[latest.metric] ?? latest.metric}
                    description={source}
                    action={
                      <SourceBadge
                        source={latest.detail.simulated ? "simulated" : latest.data_class}
                      />
                    }
                  >
                    <div className="tabular text-2xl font-semibold">{value(latest)}</div>
                    <div className="mt-0.5 text-[13px] text-muted-foreground">
                      {latest.detail.sport ? `${latest.detail.sport}, ` : ""}
                      {clock.dateTime(latest.observed_at)} · {list.length.toLocaleString()} record
                      {list.length === 1 ? "" : "s"}
                    </div>
                    {series && (
                      <div
                        className="mt-3"
                        role="img"
                        aria-label={`Heart rate over the last four days, ${list.length} samples`}
                      >
                        <ResponsiveContainer width="100%" height={90}>
                          <LineChart
                            data={list.map((o) => ({
                              t: new Date(o.observed_at).getTime(),
                              v: o.value,
                            }))}
                            margin={{ top: 4, right: 4, bottom: 0, left: 4 }}
                          >
                            <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} hide />
                            <YAxis domain={["dataMin - 5", "dataMax + 5"]} hide />
                            <Tooltip
                              isAnimationActive={false}
                              content={({ active, payload }) => {
                                const point = payload?.[0]?.payload as
                                  { t: number; v: number } | undefined;
                                return active && point ? (
                                  <div className="rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs shadow-raised">
                                    <span className="tabular font-semibold">
                                      {Math.round(point.v)} bpm
                                    </span>{" "}
                                    · {clock.dateTime(new Date(point.t).toISOString())}
                                  </div>
                                ) : null;
                              }}
                            />
                            <Line
                              type="monotone"
                              dataKey="v"
                              stroke="var(--muted-foreground)"
                              strokeWidth={1.5}
                              dot={false}
                              isAnimationActive={false}
                            />
                          </LineChart>
                        </ResponsiveContainer>
                      </div>
                    )}
                    {!series && list.length > 1 && (
                      <ul className="mt-3 space-y-1 text-[13px] text-muted-foreground">
                        {list
                          .slice(-4, -1)
                          .reverse()
                          .map((o) => (
                            <li key={o.observation_id} className="flex justify-between gap-3">
                              <span>{clock.dateTime(o.observed_at)}</span>
                              <span className="tabular">{value(o)}</span>
                            </li>
                          ))}
                      </ul>
                    )}
                  </Panel>
                );
              })}
            </div>
          )
        }
      </Async>
    </div>
  );
}
