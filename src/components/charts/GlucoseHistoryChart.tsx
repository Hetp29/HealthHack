import { useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Clock } from "@/lib/time";
import type { GlucoseReading, GlucoseUnit } from "@/lib/types";
import { toUnit } from "@/lib/units";

const GAP_MS = 20 * 60_000;

interface Props {
  readings: GlucoseReading[];
  unit: GlucoseUnit;
  clock: Clock;
  /** Meal times to mark on the axis. */
  meals?: Array<{ at: string; label: string }>;
  height?: number;
}

/** Observed glucose over time. Observed values are always indigo; gaps are left as gaps. */
export function GlucoseHistoryChart({ readings, unit, clock, meals = [], height = 260 }: Props) {
  const data = useMemo(() => {
    const sorted = [...readings].sort((a, b) => a.observed_at.localeCompare(b.observed_at));
    const points: Array<{ t: number; glucose: number | null }> = [];
    let previous = 0;
    for (const reading of sorted) {
      const t = new Date(reading.observed_at).getTime();
      // A break in the line is more honest than a straight segment across missing data.
      if (previous && t - previous > GAP_MS) points.push({ t: previous + 1, glucose: null });
      points.push({ t, glucose: toUnit(reading.glucose_mg_dl, unit) });
      previous = t;
    }
    return points;
  }, [readings, unit]);

  const first = data[0]?.t ?? 0;
  const last = data[data.length - 1]?.t ?? 0;
  const ticks = useMemo(() => {
    const span = last - first;
    if (span <= 0) return [first];
    const step =
      [1, 2, 3, 4, 6, 12, 24].map((h) => h * 3_600_000).find((s) => span / s <= 7) ?? 86_400_000;
    const out: number[] = [];
    for (let t = Math.ceil(first / step) * step; t <= last; t += step) out.push(t);
    return out;
  }, [first, last]);
  const visibleMeals = meals.filter((m) => {
    const t = new Date(m.at).getTime();
    return t >= first && t <= last;
  });
  const multiDay = last - first > 26 * 3_600_000;

  return (
    <div
      role="img"
      aria-label={`Observed glucose, ${readings.length} readings from ${clock.dateTime(new Date(first).toISOString())} to ${clock.dateTime(new Date(last).toISOString())}`}
    >
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 12, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={[first, last]}
            ticks={ticks}
            tickFormatter={(t: number) => {
              const iso = new Date(t).toISOString();
              return multiDay ? `${clock.day(iso)} ${clock.time(iso)}` : clock.time(iso);
            }}
            tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
            tickLine={false}
            axisLine={{ stroke: "var(--border)" }}
            minTickGap={24}
          />
          <YAxis
            domain={[
              (min: number) => Math.floor(Math.min(min, toUnit(70, unit)) * 0.95),
              (max: number) => Math.ceil(Math.max(max, toUnit(160, unit)) * 1.05),
            ]}
            tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
            tickLine={false}
            axisLine={false}
            width={44}
            tickFormatter={(v: number) => (unit === "mg/dL" ? String(Math.round(v)) : v.toFixed(1))}
          />
          <Tooltip
            isAnimationActive={false}
            content={({ active, payload }) => {
              const point = payload?.[0]?.payload as
                { t: number; glucose: number | null } | undefined;
              if (!active || !point || point.glucose === null) return null;
              return (
                <div className="rounded-lg border border-border bg-card px-3 py-2 text-sm shadow-raised">
                  <div className="text-xs text-muted-foreground">
                    {clock.dateTime(new Date(point.t).toISOString())}
                  </div>
                  <div className="tabular font-semibold text-observed">
                    {unit === "mg/dL" ? Math.round(point.glucose) : point.glucose.toFixed(1)} {unit}
                    <span className="ml-1.5 font-normal text-muted-foreground">observed</span>
                  </div>
                </div>
              );
            }}
          />
          {visibleMeals.map((meal) => (
            <ReferenceLine
              key={meal.at}
              x={new Date(meal.at).getTime()}
              stroke="var(--muted-foreground)"
              strokeDasharray="3 4"
              label={{
                value: meal.label,
                position: "insideTopLeft",
                fontSize: 11,
                fill: "var(--muted-foreground)",
              }}
            />
          ))}
          <Line
            type="monotone"
            dataKey="glucose"
            stroke="var(--observed)"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
