import { Activity, Plus } from "lucide-react";
import { useState } from "react";

import { GlucoseHistoryChart } from "@/components/charts/GlucoseHistoryChart";
import {
  Async,
  EmptyState,
  Field,
  Notice,
  Panel,
  Pill,
  SkeletonBlock,
  SkeletonLines,
  SourceBadge,
  Spinner,
  Stat,
  inputClass,
} from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import { CgmImport } from "@/features/CgmImport";
import * as api from "@/lib/api";
import { useCgmReadings, useCgmStatus, useMeals, useRefreshAll } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { formatSpan, toInstant } from "@/lib/time";
import type { GlucoseUnit } from "@/lib/types";
import { toMgDl, useUnits } from "@/lib/units";
import { cn } from "@/lib/utils";
import { useGuardedMutation } from "@/lib/useGuardedMutation";

const RANGES = [
  { hours: 24, label: "24 h" },
  { hours: 72, label: "3 days" },
  { hours: 168, label: "7 days" },
];

const localNow = () => {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

function ManualReading() {
  const { unit: preferred } = useUnits();
  const refreshAll = useRefreshAll();
  const [value, setValue] = useState("");
  const [unit, setUnit] = useState<GlucoseUnit>(preferred);
  const [at, setAt] = useState(localNow);
  const [touched, setTouched] = useState(false);

  const mgDl = toMgDl(Number(value), unit);
  const valueError =
    value.trim() === "" || !Number.isFinite(Number(value))
      ? "Enter the reading as a number."
      : mgDl < 20 || mgDl > 600
        ? "Enter a reading between 20 and 600 mg/dL (1.1 and 33.3 mmol/L)."
        : null;
  const timeError = Number.isNaN(new Date(at).getTime())
    ? "Enter when the reading was taken."
    : new Date(at).getTime() > Date.now() + 60_000
      ? "Choose a time that isn't in the future."
      : null;

  const save = useGuardedMutation({
    mutationFn: () =>
      api.pushReading({ observed_at: toInstant(new Date(at)), value: Number(value), unit }),
    onSuccess: () => {
      setValue("");
      setTouched(false);
      void refreshAll();
    },
  });
  const counts = save.data?.counts;

  return (
    <form
      className="space-y-4"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (!valueError && !timeError) save.mutate();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id="reading-value" label="Glucose reading" error={touched ? valueError : null}>
          <div className="flex gap-2">
            <input
              id="reading-value"
              inputMode="decimal"
              className={inputClass}
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
                save.reset();
              }}
              aria-invalid={touched && !!valueError}
            />
            <select
              aria-label="Unit"
              className={cn(inputClass, "w-28 shrink-0")}
              value={unit}
              onChange={(e) => setUnit(e.target.value as GlucoseUnit)}
            >
              <option value="mg/dL">mg/dL</option>
              <option value="mmol/L">mmol/L</option>
            </select>
          </div>
        </Field>
        <Field id="reading-at" label="Taken at" error={touched ? timeError : null}>
          <input
            id="reading-at"
            type="datetime-local"
            className={inputClass}
            value={at}
            max={localNow()}
            onChange={(e) => setAt(e.target.value)}
          />
        </Field>
      </div>
      <Button type="submit" variant="outline" className="h-10" disabled={save.isPending}>
        {save.isPending ? <Spinner /> : <Plus className="h-4 w-4" />} Add reading
      </Button>
      {save.isError && (
        <Notice tone="danger" title="The reading wasn't saved">
          {api.describeError(save.error)}
        </Notice>
      )}
      {counts &&
        (counts.inserted > 0 ? (
          <Notice tone="success">Reading saved.</Notice>
        ) : counts.duplicates > 0 ? (
          <Notice>That reading was already stored.</Notice>
        ) : (
          <Notice tone="warning" title="The reading was rejected">
            {Object.keys(counts.rejected_by_reason)
              .map((r) => r.replace(/_/g, " "))
              .join(", ") || "It did not pass validation."}
          </Notice>
        ))}
    </form>
  );
}

export function GlucoseTab() {
  const { clock, isDemoAccount } = useSession();
  const { unit, glucose } = useUnits();
  const [hours, setHours] = useState(24);
  const status = useCgmStatus();
  const readings = useCgmReadings(hours);
  const meals = useMeals();
  const markers = (meals.data?.meals ?? []).map((m) => ({ at: m.occurred_at, label: "Meal" }));

  return (
    <div className="space-y-5">
      <Panel
        title="Glucose readings"
        action={
          <div role="group" aria-label="Time range" className="flex rounded-lg bg-muted p-0.5">
            {RANGES.map((r) => (
              <button
                key={r.hours}
                type="button"
                onClick={() => setHours(r.hours)}
                aria-pressed={hours === r.hours}
                className={cn(
                  "h-8 rounded-md px-3 text-[13px] font-medium",
                  hours === r.hours
                    ? "bg-card shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {r.label}
              </button>
            ))}
          </div>
        }
      >
        <Async query={status} skeleton={<SkeletonLines lines={2} />} compact>
          {(s) =>
            s.latest ? (
              <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
                <Stat
                  label="Latest"
                  value={glucose(s.latest.glucose_mg_dl)}
                  unit={unit}
                  tone="observed"
                  hint={clock.dateTime(s.latest.observed_at)}
                />
                <Stat
                  label="Reading age"
                  value={s.latest_age_minutes !== null ? formatSpan(s.latest_age_minutes) : "–"}
                  hint={s.forecast_ready ? "Ready to forecast" : "Too old to forecast"}
                />
                <Stat
                  label="Last 24 hours"
                  value={s.last_24h.readings.toLocaleString()}
                  unit="readings"
                  hint={
                    s.last_24h.median_interval_minutes !== null
                      ? `about every ${Math.round(s.last_24h.median_interval_minutes)} min`
                      : undefined
                  }
                />
                <div>
                  <div className="text-[13px] font-medium text-muted-foreground">Source</div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    <SourceBadge source={s.data_class} />
                    <Pill tone="neutral">{s.latest.device_id}</Pill>
                  </div>
                </div>
              </div>
            ) : (
              <EmptyState icon={Activity} title="No glucose readings yet" compact>
                {isDemoAccount
                  ? "The recording has not released any readings yet."
                  : "Import an export file or add a reading below."}
              </EmptyState>
            )
          }
        </Async>
        {status.data?.latest && (
          <div className="mt-5">
            <Async query={readings} skeleton={<SkeletonBlock className="h-72" />} compact>
              {(r) => (
                <>
                  {r.readings.length > 1 ? (
                    <GlucoseHistoryChart
                      readings={r.readings}
                      unit={unit}
                      clock={clock}
                      meals={hours <= 72 ? markers : []}
                      height={300}
                    />
                  ) : (
                    <p className="py-10 text-center text-sm text-muted-foreground">
                      No readings in this period.
                    </p>
                  )}
                  <p className="mt-2 text-[13px] text-muted-foreground">
                    {r.readings.length.toLocaleString()} readings shown
                    {r.truncated && " (the period holds more than can be shown at once)"}
                    {r.gaps.length > 0 &&
                      `, with ${r.gaps.length} gap${r.gaps.length === 1 ? "" : "s"} where no reading was recorded`}
                    .
                  </p>
                </>
              )}
            </Async>
          </div>
        )}
      </Panel>

      {isDemoAccount ? (
        <Notice title="This account's glucose comes from a recording">
          Readings are released from the 2020 recording as the replay advances, so adding or
          importing readings is switched off here.
        </Notice>
      ) : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <Panel
            title="Import from your glucose monitor"
            description="Upload an export file from Dexcom Clarity or LibreView."
          >
            <CgmImport />
          </Panel>
          <Panel title="Add a reading" description="Type in a reading from your meter or monitor.">
            <ManualReading />
          </Panel>
        </div>
      )}
    </div>
  );
}
