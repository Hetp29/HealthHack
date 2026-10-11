import { BookOpen, Check, Clock, Pill as PillIcon, Plus, X } from "lucide-react";
import { useState } from "react";
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
import { useDoses, useDueReminders, useEducation, useRefreshAll, useRegimens } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { toInstant } from "@/lib/time";
import type { Dose, Regimen } from "@/lib/types";
import { useGuardedMutation } from "@/lib/useGuardedMutation";

const CLASSES = [
  ["", "Not sure"],
  ["metformin", "Metformin"],
  ["insulin", "Insulin"],
  ["glp1_receptor_agonist", "GLP-1 receptor agonist"],
  ["sulfonylurea", "Sulfonylurea"],
  ["sglt2_inhibitor", "SGLT2 inhibitor"],
  ["other", "Other"],
] as const;
const CLASS_LABEL: Record<string, string> = Object.fromEntries(CLASSES);
const ACTION = {
  taken: "Taken",
  skipped: "Skipped",
  snoozed: "Snoozed",
  scheduled: "Scheduled",
  unknown: "Not recorded",
} as const;
const eventId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `evt-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
const doseText = (dose: Regimen["dose"]) =>
  dose.amount !== null ? `${dose.amount} ${dose.unit ?? ""}`.trim() : null;

/** Records what the user says they did. A dose is never marked taken on its own. */
function useRecordDose() {
  const refreshAll = useRefreshAll();
  const { clock } = useSession();
  return useGuardedMutation({
    mutationFn: (input: {
      regimen_id: string;
      action: "taken" | "skipped" | "snoozed";
      scheduled_at?: string | null;
    }) =>
      api.recordDose({
        ...input,
        client_event_id: eventId(),
        ...(input.action === "snoozed"
          ? { snoozed_until: toInstant(new Date(clock.now().getTime() + 15 * 60000)) }
          : {}),
      }),
    onSuccess: async (_dose, input) => {
      await refreshAll();
      toast.success(
        input.action === "taken"
          ? "Recorded as taken"
          : input.action === "skipped"
            ? "Recorded as skipped"
            : "Snoozed for 15 minutes",
      );
    },
    onError: (error) => toast.error(`Not recorded. ${api.describeError(error)}`),
  });
}

function DueList({ due }: { due: Dose[] }) {
  const { clock } = useSession();
  const record = useRecordDose();
  if (!due.length)
    return <p className="text-sm text-muted-foreground">Nothing is due right now.</p>;
  return (
    <ul className="space-y-3">
      {due.map((dose) => {
        const pending =
          record.isPending &&
          record.variables?.scheduled_at === dose.scheduled_at &&
          record.variables?.regimen_id === dose.regimen_id;
        return (
          <li
            key={dose.dose_id}
            className="rounded-xl border border-border-strong p-4"
            data-testid="due-dose"
          >
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground">
                <PillIcon className="h-4 w-4" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-medium">
                  {dose.regimen?.name ?? "Scheduled dose"}
                  {dose.regimen && doseText(dose.regimen.dose)
                    ? `, ${doseText(dose.regimen.dose)}`
                    : ""}
                </div>
                <div className="text-[13px] text-muted-foreground">
                  Scheduled for {clock.dateTime(dose.scheduled_at)}
                </div>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                size="sm"
                className="h-9"
                disabled={record.isPending}
                onClick={() =>
                  record.mutate({
                    regimen_id: dose.regimen_id,
                    action: "taken",
                    scheduled_at: dose.scheduled_at,
                  })
                }
                data-testid="dose-taken"
              >
                {pending && record.variables?.action === "taken" ? (
                  <Spinner />
                ) : (
                  <Check className="h-4 w-4" />
                )}{" "}
                Taken
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-9"
                disabled={record.isPending}
                onClick={() =>
                  record.mutate({
                    regimen_id: dose.regimen_id,
                    action: "snoozed",
                    scheduled_at: dose.scheduled_at,
                  })
                }
              >
                <Clock className="h-4 w-4" /> Snooze 15 min
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-9"
                disabled={record.isPending}
                onClick={() =>
                  record.mutate({
                    regimen_id: dose.regimen_id,
                    action: "skipped",
                    scheduled_at: dose.scheduled_at,
                  })
                }
              >
                <X className="h-4 w-4" /> Skip
              </Button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function RegimenRow({ regimen }: { regimen: Regimen }) {
  const refreshAll = useRefreshAll();
  const record = useRecordDose();
  const patch = useGuardedMutation({
    mutationFn: (changes: Record<string, unknown>) => api.patchRegimen(regimen.regimen_id, changes),
    onSuccess: () => void refreshAll(),
    onError: (error) => toast.error(api.describeError(error)),
  });
  const current = regimen.status === "current";
  return (
    <li className="py-4" data-testid="regimen">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium">{regimen.name}</div>
          <div className="text-[13px] text-muted-foreground">
            {[
              doseText(regimen.dose),
              regimen.form,
              regimen.frequency,
              regimen.medication_class ? CLASS_LABEL[regimen.medication_class] : null,
            ]
              .filter(Boolean)
              .join(" · ") || "No dose details"}
          </div>
          {regimen.schedule_times.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {regimen.schedule_times.map((time) => (
                <Pill key={time} tone="neutral" icon={Clock}>
                  {time}
                </Pill>
              ))}
            </div>
          )}
          {regimen.prescriber_note && (
            <p className="mt-1.5 text-[13px] text-muted-foreground">{regimen.prescriber_note}</p>
          )}
        </div>
        {current && (
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            Reminders
            <Switch
              checked={regimen.reminders_enabled}
              disabled={patch.isPending || regimen.schedule_times.length === 0}
              onCheckedChange={(checked) => patch.mutate({ reminders_enabled: checked })}
              aria-label={`Reminders for ${regimen.name}`}
            />
          </label>
        )}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {current ? (
          <>
            <Button
              size="sm"
              variant="outline"
              disabled={record.isPending}
              onClick={() => record.mutate({ regimen_id: regimen.regimen_id, action: "taken" })}
            >
              <Check className="h-4 w-4" /> Log a dose taken now
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={patch.isPending}
              onClick={() => patch.mutate({ status: "discontinued" })}
            >
              Move to previous
            </Button>
          </>
        ) : (
          <Button
            size="sm"
            variant="outline"
            disabled={patch.isPending}
            onClick={() => patch.mutate({ status: "current" })}
          >
            Move back to current
          </Button>
        )}
      </div>
    </li>
  );
}

function AddRegimen({ onDone }: { onDone: () => void }) {
  const refreshAll = useRefreshAll();
  const [name, setName] = useState("");
  const [medClass, setMedClass] = useState("");
  const [amount, setAmount] = useState("");
  const [unit, setUnit] = useState("mg");
  const [frequency, setFrequency] = useState("");
  const [times, setTimes] = useState<string[]>([]);
  const [newTime, setNewTime] = useState("08:00");
  const [reminders, setReminders] = useState(true);
  const [touched, setTouched] = useState(false);

  const nameError = name.trim() ? null : "Enter the medication's name.";
  const amountError =
    amount.trim() && !(Number(amount) > 0) ? "Enter the dose as a number above 0." : null;
  const create = useGuardedMutation({
    mutationFn: () =>
      api.createRegimen({
        name: name.trim(),
        ...(medClass ? { medication_class: medClass } : {}),
        ...(amount.trim() ? { dose_amount: Number(amount), dose_unit: unit.trim() || null } : {}),
        ...(frequency.trim() ? { frequency: frequency.trim() } : {}),
        schedule_times: times,
        reminders_enabled: reminders && times.length > 0,
      }),
    onSuccess: async () => {
      await refreshAll();
      toast.success("Medication added");
      onDone();
    },
  });

  return (
    <form
      noValidate
      className="space-y-4 rounded-xl border border-border-strong p-4"
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (!nameError && !amountError) create.mutate();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id="med-name" label="Medication name" error={touched ? nameError : null}>
          <input
            id="med-name"
            className={inputClass}
            value={name}
            maxLength={160}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={touched && !!nameError}
          />
        </Field>
        <Field
          id="med-class"
          label="Type"
          optional
          hint="Used only to show general information about this type."
        >
          <select
            id="med-class"
            className={inputClass}
            value={medClass}
            onChange={(e) => setMedClass(e.target.value)}
          >
            {CLASSES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id="med-amount"
          label="Prescribed dose"
          optional
          error={touched ? amountError : null}
        >
          <div className="flex gap-2">
            <input
              id="med-amount"
              inputMode="decimal"
              className={inputClass}
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              aria-invalid={touched && !!amountError}
            />
            <input
              aria-label="Dose unit"
              className={`${inputClass} w-24 shrink-0`}
              value={unit}
              maxLength={24}
              onChange={(e) => setUnit(e.target.value)}
            />
          </div>
        </Field>
        <Field id="med-frequency" label="How often" optional>
          <input
            id="med-frequency"
            className={inputClass}
            value={frequency}
            maxLength={80}
            placeholder="For example: twice daily"
            onChange={(e) => setFrequency(e.target.value)}
          />
        </Field>
      </div>
      <div>
        <div className="text-sm font-medium">Times of day</div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {times.map((time) => (
            <span
              key={time}
              className="inline-flex items-center gap-1 rounded-full bg-muted py-1 pl-3 pr-1 text-sm"
            >
              {time}
              <button
                type="button"
                aria-label={`Remove ${time}`}
                onClick={() => setTimes(times.filter((t) => t !== time))}
                className="flex h-6 w-6 items-center justify-center rounded-full hover:bg-border"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </span>
          ))}
          <input
            type="time"
            aria-label="Time to add"
            className={`${inputClass} w-32`}
            value={newTime}
            onChange={(e) => setNewTime(e.target.value)}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-10"
            disabled={
              !/^\d{2}:\d{2}$/.test(newTime) || times.includes(newTime) || times.length >= 12
            }
            onClick={() => setTimes([...times, newTime].sort())}
          >
            <Plus className="h-4 w-4" /> Add time
          </Button>
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm">
          <Switch
            checked={reminders && times.length > 0}
            disabled={times.length === 0}
            onCheckedChange={setReminders}
          />{" "}
          Remind me at these times
        </label>
        {times.length === 0 && (
          <p className="mt-1 text-[13px] text-muted-foreground">
            Add at least one time to be reminded.
          </p>
        )}
      </div>
      {create.isError && (
        <Notice tone="danger" title="The medication wasn't added">
          {api.describeError(create.error)}
        </Notice>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={create.isPending}>
          {create.isPending && <Spinner />} Save medication
        </Button>
        <Button type="button" variant="ghost" onClick={onDone} disabled={create.isPending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export function MedicationsTab() {
  const { clock } = useSession();
  const regimens = useRegimens();
  const due = useDueReminders();
  const doses = useDoses();
  const education = useEducation();
  const [adding, setAdding] = useState(false);

  return (
    <div className="space-y-5">
      <Notice title="A record, not advice">
        GLIMPSE keeps track of what you tell it and reminds you at the times you set. It never
        suggests starting, stopping or changing a dose, and medication records are not used by the
        glucose forecast.
      </Notice>

      <Panel title="Due now">
        <Async query={due} compact>
          {(d) => <DueList due={d.due} />}
        </Async>
      </Panel>

      <Panel
        title="Your medications"
        action={
          !adding && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setAdding(true)}
              data-testid="add-medication"
            >
              <Plus className="h-4 w-4" /> Add medication
            </Button>
          )
        }
      >
        {adding && (
          <div className="mb-4">
            <AddRegimen onDone={() => setAdding(false)} />
          </div>
        )}
        <Async query={regimens} compact>
          {({ regimens: list }) => {
            const current = list.filter((r) => r.status === "current");
            const previous = list.filter((r) => r.status === "discontinued");
            if (!list.length && !adding) {
              return (
                <EmptyState icon={PillIcon} title="No medications yet" compact>
                  Add a medication and its times to get reminders and keep a dose history.
                </EmptyState>
              );
            }
            return (
              <>
                {current.length > 0 && (
                  <ul className="divide-y divide-border">
                    {current.map((r) => (
                      <RegimenRow key={r.regimen_id} regimen={r} />
                    ))}
                  </ul>
                )}
                {previous.length > 0 && (
                  <Disclosure title={`Previous medications (${previous.length})`} className="mt-3">
                    <ul className="divide-y divide-border text-foreground">
                      {previous.map((r) => (
                        <RegimenRow key={r.regimen_id} regimen={r} />
                      ))}
                    </ul>
                  </Disclosure>
                )}
              </>
            );
          }}
        </Async>
      </Panel>

      <Panel title="Dose history">
        <Async query={doses} skeleton={<SkeletonLines lines={4} />} compact>
          {({ doses: list }) => {
            const recorded = list.filter((d) => d.action !== "scheduled");
            if (!recorded.length)
              return (
                <p className="text-sm text-muted-foreground">No doses have been recorded yet.</p>
              );
            const names = new Map(
              (regimens.data?.regimens ?? []).map((r) => [r.regimen_id, r.name]),
            );
            return (
              <ul className="divide-y divide-border text-sm">
                {recorded.slice(0, 30).map((dose) => (
                  <li
                    key={dose.dose_id}
                    className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2.5"
                  >
                    <span className="min-w-0">
                      <span className="font-medium">
                        {dose.regimen?.name ?? names.get(dose.regimen_id) ?? "Medication"}
                      </span>
                      <span className="block text-[13px] text-muted-foreground">
                        {dose.scheduled_at
                          ? `Scheduled ${clock.dateTime(dose.scheduled_at)}`
                          : "Unscheduled dose"}
                        {dose.actual_at && ` · taken ${clock.dateTime(dose.actual_at)}`}
                        {dose.snoozed_until && ` · until ${clock.time(dose.snoozed_until)}`}
                      </span>
                    </span>
                    <Pill
                      tone={
                        dose.action === "taken"
                          ? "green"
                          : dose.action === "skipped"
                            ? "amber"
                            : "neutral"
                      }
                    >
                      {ACTION[dose.action]}
                    </Pill>
                  </li>
                ))}
              </ul>
            );
          }}
        </Async>
      </Panel>

      <Panel
        title="About these medication types"
        description="General, sourced information. It is not about your own prescription."
      >
        <Async query={education} compact>
          {(data) => (
            <div className="space-y-3">
              <p className="text-[13px] text-muted-foreground">{data.limitation}</p>
              {data.entries.map((entry) => (
                <Disclosure
                  key={entry.medication_class}
                  title={
                    <span className="flex items-center gap-2">
                      <BookOpen className="h-4 w-4 text-muted-foreground" />
                      {entry.title}
                    </span>
                  }
                >
                  <p>{entry.summary}</p>
                  <p className="mt-2">{entry.hypoglycemia_note}</p>
                  <ul className="mt-2 space-y-1 text-[13px]">
                    {entry.sources.map((source) => (
                      <li key={source.url}>
                        Source:{" "}
                        <a
                          href={source.url}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium text-primary underline-offset-2 hover:underline"
                        >
                          {source.publisher}, {source.title}
                        </a>
                      </li>
                    ))}
                  </ul>
                </Disclosure>
              ))}
            </div>
          )}
        </Async>
      </Panel>
    </div>
  );
}
