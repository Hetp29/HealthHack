import { useMemo, useState } from "react";
import {
  Brush,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { HistoryResponse } from "@/types/contracts";

const GAP_MS = 12 * 60 * 1000;

type Row = { t: number; glucose: number | null; hr: number | null };

/** Merge series on timestamp and insert null rows across gaps so lines break honestly. */
function buildRows(h: HistoryResponse): Row[] {
  const map = new Map<number, Row>();
  for (const g of h.glucose) {
    const t = Date.parse(g.timestamp);
    map.set(t, { t, glucose: g.glucose_mg_dl, hr: null });
  }
  for (const r of h.heart_rate) {
    const t = Date.parse(r.timestamp);
    const row = map.get(t) ?? { t, glucose: null, hr: null };
    row.hr = r.heart_rate_bpm;
    map.set(t, row);
  }
  const sorted = [...map.values()].sort((a, b) => a.t - b.t);
  const out: Row[] = [];
  let lastG: number | null = null;
  for (const row of sorted) {
    if (row.glucose != null) {
      if (lastG != null && row.t - lastG > GAP_MS) out.push({ t: lastG + 1, glucose: null, hr: null });
      lastG = row.t;
    }
    out.push(row);
  }
  return out;
}

const tickFmt = (t: number) =>
  new Date(t).toLocaleDateString("en-US", { weekday: "short", day: "numeric", timeZone: "UTC" });
const tipFmt = (t: number) =>
  new Date(t).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "UTC" }) + " UTC";

export function HistoryChart({ history }: { history: HistoryResponse }) {
  const [showHr, setShowHr] = useState(false);
  const rows = useMemo(() => buildRows(history), [history]);
  const hasHr = history.heart_rate.length > 0;
  const [min, max] = useMemo(() => {
    const v = history.glucose.map((g) => g.glucose_mg_dl);
    const lo = Math.min(...v), hi = Math.max(...v);
    const pad = Math.max(10, (hi - lo) * 0.1);
    return [Math.floor((lo - pad) / 10) * 10, Math.ceil((hi + pad) / 10) * 10];
  }, [history]);
  const ticks = useMemo(() => {
    if (!rows.length) return [];
    const start = new Date(rows[0].t);
    start.setUTCHours(0, 0, 0, 0);
    const out = [];
    for (let t = start.getTime(); t <= rows[rows.length - 1].t; t += 864e5) out.push(t);
    return out;
  }, [rows]);
  const meals = useMemo(() => history.meals.map((m) => Date.parse(m.timestamp)), [history]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-4 text-[12px] text-subtle">
        <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-4 rounded bg-primary" /> Glucose (mg/dL)</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full border-2 border-foreground/50 bg-card" /> Recorded meal</span>
        {hasHr && (
          <label className="ml-auto inline-flex cursor-pointer items-center gap-2">
            <input type="checkbox" checked={showHr} onChange={(e) => setShowHr(e.target.checked)} className="accent-primary" />
            Overlay heart rate (bpm, right axis)
          </label>
        )}
      </div>
      <div className="h-[340px] w-full">
        <ResponsiveContainer>
          <LineChart data={rows} margin={{ top: 8, right: showHr ? 8 : 16, bottom: 0, left: -8 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} ticks={ticks} tickFormatter={tickFmt} stroke="var(--muted-foreground)" fontSize={12} tickLine={false} />
            <YAxis yAxisId="g" domain={[min, max]} stroke="var(--muted-foreground)" fontSize={12} tickLine={false} axisLine={false} label={{ value: "mg/dL", angle: -90, position: "insideLeft", offset: 18, fontSize: 11, fill: "var(--muted-foreground)" }} />
            {showHr && (
              <YAxis yAxisId="hr" orientation="right" domain={["dataMin - 5", "dataMax + 5"]} stroke="var(--heart)" fontSize={12} tickLine={false} axisLine={false} label={{ value: "bpm", angle: 90, position: "insideRight", fontSize: 11, fill: "var(--heart)" }} />
            )}
            <Tooltip
              labelFormatter={(t) => tipFmt(Number(t))}
              formatter={(v: number, name) => (name === "hr" ? [`${v} bpm`, "Heart rate"] : [`${v} mg/dL`, "Glucose"])}
              contentStyle={{ borderRadius: 10, border: "1px solid var(--border)", fontSize: 12 }}
            />
            <Line yAxisId="g" dataKey="glucose" stroke="var(--primary)" strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls={false} />
            {showHr && <Line yAxisId="hr" dataKey="hr" stroke="var(--heart)" strokeOpacity={0.6} strokeWidth={1} dot={false} isAnimationActive={false} connectNulls />}
            {meals.map((t) => (
              <ReferenceDot key={t} yAxisId="g" x={t} y={min + 4} r={3.5} fill="var(--card)" stroke="var(--foreground)" strokeOpacity={0.5} strokeWidth={1.5} ifOverflow="hidden" />
            ))}
            <Brush dataKey="t" height={24} stroke="var(--primary)" tickFormatter={tickFmt} travellerWidth={8} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">Times shown in UTC. Breaks in the line indicate sensor gaps longer than 12 minutes. Drag the lower handles to zoom.</p>
    </div>
  );
}
