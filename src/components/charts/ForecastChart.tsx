import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Disclosure } from "@/components/glimpse/primitives";
import type { ForecastPoint, GlucoseUnit } from "@/lib/types";
import { toUnit } from "@/lib/units";

interface Props {
  predicted: ForecastPoint[];
  /** What the sensor later recorded. Passed only once the server has released it. */
  observed?: ForecastPoint[] | null;
  unit: GlucoseUnit;
  /** The user's alert level, in mg/dL, drawn when the forecast is compared against one. */
  thresholdMgDl?: number | null;
  height?: number;
}

/**
 * The model's 25-point forecast, 0 to 120 minutes after the meal. The curve is drawn
 * exactly as returned: no smoothing beyond joining the points, and no band around it,
 * because the model's uncertainty is not calibrated.
 */
export function ForecastChart({ predicted, observed, unit, thresholdMgDl, height = 280 }: Props) {
  const data = useMemo(() => {
    const byMinute = new Map<number, { minute: number; predicted?: number; observed?: number }>();
    for (const point of predicted)
      byMinute.set(point.minute, {
        minute: point.minute,
        predicted: toUnit(point.glucose_mg_dl, unit),
      });
    for (const point of observed ?? []) {
      const row = byMinute.get(point.minute) ?? { minute: point.minute };
      row.observed = toUnit(point.glucose_mg_dl, unit);
      byMinute.set(point.minute, row);
    }
    return [...byMinute.values()].sort((a, b) => a.minute - b.minute);
  }, [predicted, observed, unit]);
  const hasObserved = (observed?.length ?? 0) > 0;
  const threshold = thresholdMgDl ? toUnit(thresholdMgDl, unit) : null;
  const number = (v: number) => (unit === "mg/dL" ? String(Math.round(v)) : v.toFixed(1));

  return (
    <div>
      <div
        role="img"
        aria-label={`Forecast glucose curve from the meal to 120 minutes after${hasObserved ? ", with the observed glucose overlaid" : ""}. A table of the values follows.`}
      >
        <ResponsiveContainer width="100%" height={height}>
          <LineChart data={data} margin={{ top: 12, right: 12, bottom: 4, left: -8 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis
              dataKey="minute"
              type="number"
              domain={[0, 120]}
              ticks={[0, 30, 60, 90, 120]}
              tickFormatter={(m: number) => (m === 0 ? "Meal" : `${m} min`)}
              tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
              tickLine={false}
              axisLine={{ stroke: "var(--border)" }}
            />
            <YAxis
              domain={[
                (min: number) => Math.floor(Math.min(min, threshold ?? min) * 0.94),
                (max: number) => Math.ceil(Math.max(max, threshold ?? max) * 1.06),
              ]}
              tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
              tickLine={false}
              axisLine={false}
              width={44}
              tickFormatter={number}
            />
            <Tooltip
              isAnimationActive={false}
              content={({ active, payload }) => {
                const row = payload?.[0]?.payload as
                  { minute: number; predicted?: number; observed?: number } | undefined;
                if (!active || !row) return null;
                return (
                  <div className="rounded-lg border border-border bg-card px-3 py-2 text-sm shadow-raised">
                    <div className="text-xs text-muted-foreground">
                      {row.minute === 0 ? "At the meal" : `${row.minute} min after the meal`}
                    </div>
                    {row.predicted !== undefined && (
                      <div className="tabular font-semibold text-predicted">
                        {number(row.predicted)} {unit}{" "}
                        <span className="font-normal text-muted-foreground">forecast</span>
                      </div>
                    )}
                    {row.observed !== undefined && (
                      <div className="tabular font-semibold text-observed">
                        {number(row.observed)} {unit}{" "}
                        <span className="font-normal text-muted-foreground">observed</span>
                      </div>
                    )}
                  </div>
                );
              }}
            />
            <Legend
              verticalAlign="top"
              align="right"
              height={28}
              iconType="plainline"
              formatter={(value: string) => (
                <span className="text-xs text-muted-foreground">{value}</span>
              )}
            />
            {threshold !== null && (
              <ReferenceLine
                y={threshold}
                stroke="var(--warning-border)"
                strokeDasharray="5 4"
                label={{
                  value: `Your alert level ${number(threshold)}`,
                  position: "insideBottomRight",
                  fontSize: 11,
                  fill: "var(--warning-foreground)",
                }}
              />
            )}
            <Line
              name="Forecast"
              type="monotone"
              dataKey="predicted"
              stroke="var(--predicted)"
              strokeWidth={2.5}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
            {hasObserved && (
              <Line
                name="Observed"
                type="monotone"
                dataKey="observed"
                stroke="var(--observed)"
                strokeWidth={2}
                strokeDasharray="1 0"
                dot={{ r: 2, fill: "var(--observed)" }}
                isAnimationActive={false}
                connectNulls
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <Disclosure title="View these values as a table" className="mt-3">
        <div className="max-h-72 overflow-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              Forecast{hasObserved ? " and observed" : ""} glucose by minute after the meal, in{" "}
              {unit}
            </caption>
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th scope="col" className="py-1.5 pr-4 font-medium">
                  Minutes after meal
                </th>
                <th scope="col" className="py-1.5 pr-4 font-medium">
                  Forecast ({unit})
                </th>
                {hasObserved && (
                  <th scope="col" className="py-1.5 font-medium">
                    Observed ({unit})
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="tabular text-foreground">
              {data.map((row) => (
                <tr key={row.minute} className="border-t border-border">
                  <th scope="row" className="py-1.5 pr-4 font-normal">
                    {row.minute}
                  </th>
                  <td className="py-1.5 pr-4">
                    {row.predicted !== undefined ? number(row.predicted) : "–"}
                  </td>
                  {hasObserved && (
                    <td className="py-1.5">
                      {row.observed !== undefined ? number(row.observed) : "–"}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Disclosure>
    </div>
  );
}
