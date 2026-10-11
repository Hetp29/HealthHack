import { useMutation } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Bell, Check } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  Async,
  Disclosure,
  EmptyState,
  Field,
  Notice,
  Panel,
  Pill,
  SkeletonLines,
  Spinner,
  inputClass,
} from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import * as api from "@/lib/api";
import { useAlertSettings, useAlerts, useNotifications, useRefreshAll } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { AlertEvent, AlertSettings, GlucoseUnit } from "@/lib/types";
import { toMgDl, useUnits } from "@/lib/units";
import { cn } from "@/lib/utils";

const STATUS: Record<AlertEvent["status"], string> = {
  active: "Raised",
  suppressed_quiet_hours: "Held back: quiet hours",
  suppressed_cooldown: "Held back: too soon after another alert",
  suppressed_retrospective: "Held back: forecast made after the meal",
};

function SettingsForm({ settings }: { settings: AlertSettings }) {
  const refreshAll = useRefreshAll();
  const [enabled, setEnabled] = useState(settings.enabled);
  const [value, setValue] = useState(String(settings.threshold.value));
  const [unit, setUnit] = useState<GlucoseUnit>(settings.threshold.unit);
  const [quiet, setQuiet] = useState(!!settings.quiet_hours.start);
  const [start, setStart] = useState(settings.quiet_hours.start ?? "22:00");
  const [end, setEnd] = useState(settings.quiet_hours.end ?? "07:00");
  const [cooldown, setCooldown] = useState(String(settings.cooldown_minutes));
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    setEnabled(settings.enabled);
    setValue(String(settings.threshold.value));
    setUnit(settings.threshold.unit);
    setQuiet(!!settings.quiet_hours.start);
    setCooldown(String(settings.cooldown_minutes));
  }, [settings]);

  const mgDl = toMgDl(Number(value), unit);
  const valueError =
    !Number.isFinite(Number(value)) || value.trim() === ""
      ? "Enter a level as a number."
      : mgDl < 100 || mgDl > 400
        ? "Choose a level between 100 and 400 mg/dL (5.6 and 22.2 mmol/L)."
        : null;
  const cooldownError =
    !/^\d+$/.test(cooldown.trim()) || Number(cooldown) > 1440
      ? "Enter minutes between 0 and 1440."
      : null;

  const save = useMutation({
    mutationFn: () =>
      api.putAlertSettings({
        enabled,
        threshold: { value: Number(value), unit },
        channels: ["in_app"],
        quiet_hours_start: quiet ? start : null,
        quiet_hours_end: quiet ? end : null,
        cooldown_minutes: Number(cooldown),
      }),
    onSuccess: async () => {
      await refreshAll();
      toast.success("Alert settings saved");
    },
  });

  return (
    <form
      noValidate
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (!valueError && !cooldownError) save.mutate();
      }}
    >
      <label className="flex items-center justify-between gap-4 rounded-xl border border-border-strong p-4">
        <span>
          <span className="block font-medium">Forecast alerts</span>
          <span className="block text-sm text-muted-foreground">
            Tell me when a meal's forecast rises above my level.
          </span>
        </span>
        <Switch checked={enabled} onCheckedChange={setEnabled} data-testid="alerts-enabled" />
      </label>
      <div className={cn("grid gap-5 sm:grid-cols-2", !enabled && "opacity-60")}>
        <Field id="alert-level" label="Alert level" error={touched ? valueError : null}>
          <div className="flex gap-2">
            <input
              id="alert-level"
              inputMode="decimal"
              className={inputClass}
              value={value}
              onChange={(e) => setValue(e.target.value)}
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
        <Field
          id="alert-cooldown"
          label="Minimum minutes between alerts"
          error={touched ? cooldownError : null}
        >
          <input
            id="alert-cooldown"
            inputMode="numeric"
            className={inputClass}
            value={cooldown}
            onChange={(e) => setCooldown(e.target.value)}
            aria-invalid={touched && !!cooldownError}
          />
        </Field>
      </div>
      <div className={cn("rounded-xl border border-border-strong p-4", !enabled && "opacity-60")}>
        <label className="flex items-center justify-between gap-3 text-sm font-medium">
          Quiet hours <Switch checked={quiet} onCheckedChange={setQuiet} />
        </label>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Alerts during quiet hours are recorded in your history but not raised.
        </p>
        {quiet && (
          <div className="mt-3 grid max-w-sm grid-cols-2 gap-3">
            <Field id="quiet-from" label="From">
              <input
                id="quiet-from"
                type="time"
                className={inputClass}
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </Field>
            <Field id="quiet-to" label="Until">
              <input
                id="quiet-to"
                type="time"
                className={inputClass}
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </Field>
          </div>
        )}
      </div>
      <p className="text-[13px] text-muted-foreground">
        Alerts are shown inside GLIMPSE.{" "}
        {settings.channels.filter((c) => !c.available).length > 0 ||
        settings.available_channels.length <= 1
          ? "Push and email delivery aren't available on this server."
          : ""}{" "}
        {settings.disclaimer}
      </p>
      {save.isError && (
        <Notice tone="danger" title="Your settings weren't saved">
          {api.describeError(save.error)}
        </Notice>
      )}
      <Button type="submit" disabled={save.isPending} data-testid="save-alert-settings">
        {save.isPending && <Spinner />} Save alert settings
      </Button>
    </form>
  );
}

export function AlertsTab() {
  const { clock } = useSession();
  const { unit, glucose } = useUnits();
  const refreshAll = useRefreshAll();
  const settings = useAlertSettings();
  const alerts = useAlerts();
  const deliveries = useNotifications();
  const acknowledge = useMutation({
    mutationFn: api.acknowledgeAlert,
    onSuccess: () => void refreshAll(),
    onError: (error) => toast.error(api.describeError(error)),
  });

  return (
    <div className="space-y-5">
      <Panel title="Alert settings">
        <Async query={settings} skeleton={<SkeletonLines lines={5} />} compact>
          {(s) => <SettingsForm settings={s} />}
        </Async>
      </Panel>
      <Panel title="Alert history" description="Every time a forecast crossed your level.">
        <Async query={alerts} skeleton={<SkeletonLines lines={4} />} compact>
          {({ alerts: list }) =>
            list.length === 0 ? (
              <EmptyState icon={Bell} title="No alerts yet" compact>
                An alert appears here when a meal's forecast rises above your level.
              </EmptyState>
            ) : (
              <ul className="divide-y divide-border">
                {list.map((alert) => {
                  const unread = alert.status === "active" && !alert.acknowledged_at;
                  return (
                    <li
                      key={alert.alert_id}
                      className="flex flex-wrap items-start justify-between gap-3 py-3.5"
                      data-testid="alert-row"
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={cn("text-sm", unread ? "font-semibold" : "font-medium")}>
                            {alert.payload.label}
                          </span>
                          <Pill
                            tone={
                              alert.status === "active" ? (unread ? "amber" : "neutral") : "neutral"
                            }
                          >
                            {alert.status === "active" && alert.acknowledged_at
                              ? "Acknowledged"
                              : STATUS[alert.status]}
                          </Pill>
                        </div>
                        <div className="mt-0.5 text-[13px] text-muted-foreground">
                          {clock.dateTime(alert.created_at)} · forecast peak{" "}
                          {glucose(alert.payload.predicted_peak.mg_dl)} {unit}, reaching{" "}
                          {glucose(alert.payload.threshold.mg_dl)} {unit} about{" "}
                          {alert.payload.predicted_crossing_minute} min after the meal
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <Button asChild size="sm" variant="ghost">
                          <Link to="/meals/$mealId" params={{ mealId: alert.payload.meal_id }}>
                            View forecast
                          </Link>
                        </Button>
                        {unread && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={acknowledge.isPending}
                            onClick={() => acknowledge.mutate(alert.alert_id)}
                            data-testid="acknowledge"
                          >
                            <Check className="h-4 w-4" /> Acknowledge
                          </Button>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )
          }
        </Async>
        {(deliveries.data?.notifications.length ?? 0) > 0 && (
          <Disclosure title="Delivery log" className="mt-4">
            <ul className="space-y-1.5 text-[13px]">
              {deliveries.data!.notifications.map((n) => (
                <li key={n.delivery_id} className="flex flex-wrap justify-between gap-2">
                  <span>
                    {clock.dateTime(n.created_at)} · {n.source_type.replace(/_/g, " ")} ·{" "}
                    {n.channel.replace(/_/g, " ")}
                  </span>
                  <span className="font-medium text-foreground">{n.status.replace(/_/g, " ")}</span>
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
      </Panel>
    </div>
  );
}
