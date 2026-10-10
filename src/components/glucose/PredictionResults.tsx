import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Info, ShieldAlert } from "lucide-react";
import { HORIZONS, type PredictResponse } from "@/types/contracts";
import { fmtFeature, fmtGlucose, fmtNumber, fmtSource } from "@/lib/formatting";
import { Card, CardTitle, MetricCard } from "@/components/common/States";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<string, string> = {
  personalized: "Personalized Response",
  generalized_fallback: "Generalized Fallback",
  generalized: "Generalized Fallback",
  fixture: "Demo Forecast",
};

export function PredictionResults({ result, stale }: { result: PredictResponse; stale: boolean }) {
  const calibrated =
    result.uncertainty_status === "calibrated" &&
    result.points.every((p) => p.lower_mg_dl != null && p.upper_mg_dl != null);
  const data = result.points.map((p) => ({
    ...p,
    band: calibrated ? [p.lower_mg_dl, p.upper_mg_dl] : undefined,
  }));
  const vals = result.points.flatMap((p) => [p.glucose_mg_dl, p.lower_mg_dl ?? p.glucose_mg_dl, p.upper_mg_dl ?? p.glucose_mg_dl]);
  const lo = Math.floor((Math.min(...vals) - 10) / 10) * 10;
  const hi = Math.ceil((Math.max(...vals) + 10) / 10) * 10;
  const maxAbs = Math.max(1, ...result.drivers.map((d) => Math.abs(d.contribution_mg_dl)));
  const drivers = [...result.drivers].sort((a, b) => Math.abs(b.contribution_mg_dl) - Math.abs(a.contribution_mg_dl));

  return (
    <div className={cn("flex flex-col gap-5 transition-opacity duration-200", stale && "opacity-50")} aria-live="polite">
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <span className="rounded-full bg-accent px-3 py-1 font-semibold text-accent-foreground">
          {STATUS_LABEL[result.prediction_status] ?? result.prediction_status.replace(/_/g, " ")}
        </span>
        <span className="rounded-full border bg-card px-2.5 py-1 text-subtle">Model {result.model_version}</span>
        <span className="rounded-full border bg-card px-2.5 py-1 text-subtle">{fmtSource(result.data_source)}</span>
      </div>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {HORIZONS.map((h) => (
          <MetricCard key={h} label={`${h}-minute glucose`} value={fmtGlucose(result.glucose_at_minutes[h])} unit="mg/dL" />
        ))}
      </div>

      <Card>
        <CardTitle aside={<span className="text-xs text-muted-foreground">Minutes after meal</span>}>Predicted trajectory</CardTitle>
        <div className="h-[300px]">
          <ResponsiveContainer>
            <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
              <defs>
                <linearGradient id="trajFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.14} />
                  <stop offset="100%" stopColor="var(--primary)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey="minute" type="number" domain={[0, 120]} ticks={[0, 30, 60, 90, 120]} stroke="var(--muted-foreground)" fontSize={12} tickLine={false} />
              <YAxis domain={[lo, hi]} stroke="var(--muted-foreground)" fontSize={12} tickLine={false} axisLine={false} label={{ value: "mg/dL", angle: -90, position: "insideLeft", offset: 18, fontSize: 11, fill: "var(--muted-foreground)" }} />
              <Tooltip
                labelFormatter={(m) => `${m} min after meal`}
                formatter={(v: number | number[], name) =>
                  name === "band" && Array.isArray(v) ? [`${fmtGlucose(v[0])}–${fmtGlucose(v[1])} mg/dL`, "Interval"] : [`${fmtGlucose(v as number)} mg/dL`, "Predicted glucose"]
                }
                contentStyle={{ borderRadius: 10, border: "1px solid var(--border)", fontSize: 12 }}
              />
              {calibrated && <Area dataKey="band" stroke="none" fill="var(--primary)" fillOpacity={0.12} isAnimationActive={false} />}
              {!calibrated && <Area dataKey="glucose_mg_dl" stroke="none" fill="url(#trajFill)" baseValue={lo} isAnimationActive={false} tooltipType="none" />}
              <ReferenceLine x={result.metrics.time_to_peak_minutes} stroke="var(--muted-foreground)" strokeDasharray="3 3" />
              <Line dataKey="glucose_mg_dl" stroke="var(--primary)" strokeWidth={2.5} dot={{ r: 2.5, fill: "var(--primary)" }} activeDot={{ r: 5 }} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        {!calibrated && (
          <p className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <Info className="h-3.5 w-3.5" /> Prediction intervals have not been calibrated.
          </p>
        )}
      </Card>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <MetricCard label="Predicted peak" value={fmtGlucose(result.metrics.peak_mg_dl)} unit="mg/dL" />
        <MetricCard label="Peak rise above baseline" value={`+${fmtGlucose(result.metrics.peak_delta_mg_dl)}`} unit="mg/dL" />
        <MetricCard label="Time to peak" value={fmtNumber(result.metrics.time_to_peak_minutes)} unit="min" />
        <MetricCard label="2-hour incremental AUC" value={fmtNumber(result.metrics.incremental_auc_mg_dl_min)} unit="mg/dL·min" />
      </div>

      <Card>
        <CardTitle aside={<span className="text-xs text-muted-foreground">Model-based, not causal</span>}>Feature contributions</CardTitle>
        {drivers.length === 0 ? (
          <p className="text-[13px] text-subtle">Feature-level explanations are unavailable for this prediction.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {drivers.map((d) => {
              const pos = d.contribution_mg_dl >= 0;
              const w = `${(Math.abs(d.contribution_mg_dl) / maxAbs) * 50}%`;
              return (
                <li key={d.feature} className="grid grid-cols-[110px_1fr_70px] items-center gap-3 text-[13px] sm:grid-cols-[140px_1fr_80px]">
                  <span className="truncate text-foreground" title={d.explanation ?? undefined}>{fmtFeature(d.feature)}</span>
                  <div className="relative h-2.5 rounded-full bg-muted">
                    <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
                    <div
                      className={cn("absolute inset-y-0 rounded-full", pos ? "left-1/2 bg-primary" : "right-1/2 bg-negative")}
                      style={{ width: w }}
                    />
                  </div>
                  <span className={cn("tabular text-right font-medium", pos ? "text-accent-foreground" : "text-negative")}>
                    {pos ? "+" : "−"}{fmtNumber(Math.abs(d.contribution_mg_dl), 1)}
                  </span>
                </li>
              );
            })}
            <li className="text-xs text-muted-foreground">mg/dL contribution to the prediction. Contributions do not fully explain physiological response.</li>
          </ul>
        )}
      </Card>

      <div className="rounded-xl border border-warning-border/50 bg-warning p-4 text-[13px] text-warning-foreground">
        <div className="mb-1.5 flex items-center gap-1.5 font-semibold"><ShieldAlert className="h-4 w-4" /> Limitations</div>
        <ul className="list-disc space-y-0.5 pl-5">
          {result.limitations.map((l) => <li key={l}>{l}</li>)}
          <li>Research prototype using synthetic data; not for clinical decision-making.</li>
        </ul>
      </div>
    </div>
  );
}
