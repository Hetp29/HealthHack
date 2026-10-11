import { useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  ArrowRight,
  Bell,
  Camera,
  Check,
  HelpCircle,
  LineChart,
  Lock,
  Pill as PillIcon,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  ChoiceGroup,
  Field,
  Logo,
  Notice,
  Pill,
  Spinner,
  inputClass,
} from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { CgmImport } from "@/features/CgmImport";
import { DeviceCards } from "@/features/DeviceCards";
import {
  LABELS,
  answersFor,
  draftFrom,
  hasDiagnosis,
  options,
  usesCgm,
  validate,
  type Draft,
  type StepId,
} from "@/features/onboardingModel";
import * as api from "@/lib/api";
import { keys, useSession } from "@/lib/session";
import type { GlucoseUnit } from "@/lib/types";
import { useUnits } from "@/lib/units";
import { cn } from "@/lib/utils";
import { useGuardedMutation } from "@/lib/useGuardedMutation";

export const Route = createFileRoute("/onboarding")({ component: OnboardingPage });

const STEPS: Array<{ id: StepId; title: string; optional: boolean }> = [
  { id: "welcome", title: "Welcome", optional: false },
  { id: "consent", title: "Your permission", optional: false },
  { id: "about", title: "About you", optional: false },
  { id: "health", title: "Glucose and health", optional: true },
  { id: "devices", title: "Your data sources", optional: true },
  { id: "routine", title: "Medications and meals", optional: true },
  { id: "alerts", title: "Alerts and units", optional: true },
  { id: "review", title: "Review", optional: false },
];

function Why({ children }: { children: ReactNode }) {
  return (
    <p className="mt-3 flex gap-2 rounded-lg bg-muted/70 px-3 py-2.5 text-[13px] leading-relaxed text-muted-foreground">
      <HelpCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>
        <span className="font-medium text-foreground">Why we ask. </span>
        {children}
      </span>
    </p>
  );
}

function Question({
  title,
  children,
  optional = true,
}: {
  title: string;
  children: ReactNode;
  optional?: boolean;
}) {
  return (
    <fieldset className="min-w-0">
      <legend className="mb-2 flex w-full items-baseline justify-between gap-2 text-sm font-medium text-foreground">
        <span>{title}</span>
        {optional && <span className="text-xs font-normal text-muted-foreground">Optional</span>}
      </legend>
      {children}
    </fieldset>
  );
}

function UnitInput<U extends string>({
  id,
  value,
  unit,
  units,
  onValue,
  onUnit,
  error,
  disabled,
}: {
  id: string;
  value: string;
  unit: U;
  units: readonly U[];
  onValue: (v: string) => void;
  onUnit: (u: U) => void;
  error?: string | undefined;
  disabled?: boolean;
}) {
  return (
    <div className="flex gap-2">
      <input
        id={id}
        inputMode="decimal"
        className={inputClass}
        value={value}
        onChange={(e) => onValue(e.target.value)}
        aria-invalid={!!error}
        aria-describedby={error ? `${id}-error` : undefined}
        disabled={disabled}
      />
      <select
        aria-label="Unit"
        className={cn(inputClass, "w-28 shrink-0")}
        value={unit}
        onChange={(e) => onUnit(e.target.value as U)}
        disabled={disabled}
      >
        {units.map((u) => (
          <option key={u} value={u}>
            {u}
          </option>
        ))}
      </select>
    </div>
  );
}

function ConsentCard({
  icon: Icon,
  title,
  required,
  checked,
  onChange,
  children,
  testId,
}: {
  icon: typeof Lock;
  title: string;
  required: boolean;
  checked: boolean;
  onChange: (v: boolean) => void;
  children: ReactNode;
  testId: string;
}) {
  const id = `consent-${testId}`;
  return (
    <div
      className={cn(
        "rounded-xl border p-4 transition-colors",
        checked ? "border-primary bg-accent/50" : "border-border-strong bg-card",
      )}
    >
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-card text-primary shadow-sm">
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor={id} className="font-semibold text-foreground">
              {title}
            </label>
            <Pill tone={required ? "teal" : "neutral"}>{required ? "Required" : "Optional"}</Pill>
          </div>
          <div className="mt-1 text-sm leading-relaxed text-muted-foreground">{children}</div>
        </div>
        <Switch id={id} checked={checked} onCheckedChange={onChange} data-testid={id} />
      </div>
    </div>
  );
}

function OnboardingPage() {
  const session = useSession();
  const units = useUnits();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const user = session.user!;
  const onboarding = session.onboarding!;
  // Someone who has already finished is revisiting their answers, not being onboarded again.
  const editing = useRef(onboarding.completed).current;
  const storageKey = `glimpse.onboarding.step.${user.user_id}`;

  const [index, setIndex] = useState(() => {
    if (editing) return 1;
    const saved =
      typeof window !== "undefined" ? Number(window.sessionStorage.getItem(storageKey)) : 0;
    return Number.isInteger(saved) && saved > 0 && saved < STEPS.length ? saved : 0;
  });
  const [draft, setDraft] = useState<Draft>(() =>
    draftFrom(onboarding.answers, user.display_name ?? "", units.unit),
  );
  const [necessary, setNecessary] = useState(session.processingAllowed);
  const [personalization, setPersonalization] = useState(session.personalizationAllowed);
  const [showErrors, setShowErrors] = useState(false);
  const [finished, setFinished] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);

  const step = STEPS[index]!;
  const errors = useMemo(() => validate(step.id, draft), [step.id, draft]);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  useEffect(() => {
    window.sessionStorage.setItem(storageKey, String(index));
    window.scrollTo({ top: 0 });
    heading.current?.focus();
  }, [index, storageKey]);

  const go = (next: number) => {
    setShowErrors(false);
    save.reset();
    setIndex(Math.max(0, Math.min(STEPS.length - 1, next)));
  };

  /** Saves what this step owns. Nothing is reported as saved unless the server accepted it. */
  const save = useGuardedMutation({
    mutationFn: async (target: StepId) => {
      if (target === "consent") {
        if (necessary !== session.processingAllowed)
          await api.putConsent("necessary_processing", necessary ? "granted" : "withdrawn");
        if (personalization !== session.personalizationAllowed) {
          await api.putConsent(
            "personalization_history",
            personalization ? "granted" : "withdrawn",
          );
        }
        await queryClient.invalidateQueries({ queryKey: keys.me });
        return;
      }
      if (target === "about" && draft.display_name.trim() !== (user.display_name ?? "")) {
        queryClient.setQueryData(
          keys.me,
          await api.patchMe({ display_name: draft.display_name.trim() }),
        );
      }
      const answers = answersFor(target, draft);
      const completing = target === "review";
      if (Object.keys(answers).length || completing) {
        const saved = await api.patchOnboarding({
          answers,
          ...(completing ? { completed: true } : {}),
        });
        queryClient.setQueryData(keys.onboarding, saved);
      }
      if (target === "alerts") {
        const current = await api.getAlertSettings();
        const threshold = Number(draft.alert_threshold);
        await api.putAlertSettings({
          enabled: draft.wants_spike_alerts === true,
          // With alerts off the level is irrelevant, so the stored one is left as it was.
          threshold:
            draft.wants_spike_alerts && Number.isFinite(threshold) && threshold > 0
              ? { value: threshold, unit: draft.alert_threshold_unit }
              : { value: current.threshold.value, unit: current.threshold.unit },
          channels: ["in_app"],
          quiet_hours_start:
            draft.wants_spike_alerts && draft.quiet_hours_enabled ? draft.quiet_start : null,
          quiet_hours_end:
            draft.wants_spike_alerts && draft.quiet_hours_enabled ? draft.quiet_end : null,
          cooldown_minutes: current.cooldown_minutes,
        });
        await queryClient.invalidateQueries({ queryKey: ["alerts"] });
      }
    },
    onSuccess: (_data, target) => {
      if (target === "review") {
        window.sessionStorage.removeItem(storageKey);
        setFinished(true);
        window.scrollTo({ top: 0 });
      } else go(index + 1);
    },
  });

  const onContinue = () => {
    if (step.id === "welcome") return go(1);
    if (step.id === "consent" && !necessary) return setShowErrors(true);
    if (Object.keys(errors).length) {
      setShowErrors(true);
      const first = Object.keys(errors)[0];
      if (first) document.getElementById(first)?.focus();
      return;
    }
    save.mutate(step.id);
  };
  const shown = (key: keyof Draft) => (showErrors ? errors[key] : undefined);
  const firstName =
    (draft.display_name || user.display_name || "")
      .replace(/\(.*?\)/g, "")
      .trim()
      .split(/\s+/)[0] ?? "";

  if (finished) {
    return (
      <Frame editing={editing} progress={1} label="All done">
        <div className="text-center">
          <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-success-soft text-success">
            <Check className="h-7 w-7" />
          </span>
          <h1 className="mt-5 text-2xl font-semibold" tabIndex={-1} data-testid="onboarding-done">
            {editing
              ? "Your answers are saved"
              : `You're all set${firstName ? `, ${firstName}` : ""}`}
          </h1>
          <p className="mx-auto mt-2 max-w-md text-muted-foreground">
            {editing
              ? "You can change any of this again from Settings."
              : "Here is what to do next to get your first forecast."}
          </p>
        </div>
        {!editing && (
          <ol className="mt-8 space-y-3">
            {[
              {
                icon: Upload,
                title: "Add glucose readings",
                body: "A forecast needs a reading from the 15 minutes before a meal. Import an export file or type a reading in.",
                to: "/health" as const,
                search: { tab: "glucose" as const },
              },
              {
                icon: Camera,
                title: "Log your first meal",
                body: "Photograph it or enter the nutrition yourself, then confirm the numbers.",
                to: "/meals/log" as const,
                search: undefined,
              },
              ...(draft.takes_glucose_medication === "yes"
                ? [
                    {
                      icon: PillIcon,
                      title: "Add your medication schedule",
                      body: "Naming a medication here did not set a reminder. Add its times to be reminded.",
                      to: "/health" as const,
                      search: { tab: "medications" as const },
                    },
                  ]
                : []),
            ].map(({ icon: Icon, title, body, to, search }, i) => (
              <li key={title}>
                <Link
                  to={to}
                  {...(search ? { search } : {})}
                  className="flex items-center gap-4 rounded-xl border border-border-strong bg-card p-4 hover:border-primary/60"
                >
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent text-accent-foreground">
                    <Icon className="h-5 w-5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block font-semibold">
                      {i + 1}. {title}
                    </span>
                    <span className="block text-sm text-muted-foreground">{body}</span>
                  </span>
                  <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                </Link>
              </li>
            ))}
          </ol>
        )}
        <Button
          className="mt-8 h-11 w-full text-[15px]"
          onClick={() => void navigate({ to: editing ? "/settings" : "/" })}
          data-testid="onboarding-finish"
        >
          {editing ? "Back to Settings" : "Go to my dashboard"}
        </Button>
      </Frame>
    );
  }

  return (
    <Frame
      editing={editing}
      progress={index / (STEPS.length - 1)}
      label={`Step ${index + 1} of ${STEPS.length}`}
      onSignOut={() => void session.signOut()}
    >
      <nav aria-label="Setup progress" className="mb-6 hidden gap-1.5 sm:flex">
        {STEPS.map((s, i) => (
          <span
            key={s.id}
            aria-current={i === index ? "step" : undefined}
            className={cn(
              "flex-1 border-t-2 pt-2 text-xs font-medium",
              i < index
                ? "border-primary text-primary"
                : i === index
                  ? "border-primary text-foreground"
                  : "border-border text-muted-foreground",
            )}
          >
            {s.title}
          </span>
        ))}
      </nav>

      <h1
        ref={heading}
        tabIndex={-1}
        className="text-2xl font-semibold outline-none"
        data-testid={`step-${step.id}`}
      >
        {step.id === "welcome" ? `Welcome${firstName ? `, ${firstName}` : ""}` : step.title}
      </h1>

      {step.id === "welcome" && (
        <div className="mt-3 space-y-6">
          <p className="text-muted-foreground">
            GLIMPSE forecasts how your glucose may respond to a meal over the next two hours, then
            shows you how that compared with what your sensor recorded. Setting up takes about four
            minutes.
          </p>
          <ul className="space-y-4">
            {[
              {
                icon: ShieldCheck,
                title: "First, your permission",
                body: "You choose what GLIMPSE may process. Nothing is analysed before you agree.",
              },
              {
                icon: LineChart,
                title: "Then a few questions",
                body: "About you, your glucose monitoring and your routine. Most are optional and you can skip them.",
              },
              {
                icon: Bell,
                title: "Finally, your preferences",
                body: "Alerts, units and where your glucose data will come from. Everything can be changed later.",
              },
            ].map(({ icon: Icon, title, body }) => (
              <li key={title} className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent text-accent-foreground">
                  <Icon className="h-5 w-5" />
                </span>
                <span>
                  <span className="block font-semibold">{title}</span>
                  <span className="block text-sm text-muted-foreground">{body}</span>
                </span>
              </li>
            ))}
          </ul>
          <Notice>
            GLIMPSE is a research prototype. Its forecasts are informational, their uncertainty is
            not calibrated, and they are not medical advice or a replacement for your glucose
            monitor's own alerts.
          </Notice>
        </div>
      )}

      {step.id === "consent" && (
        <div className="mt-3 space-y-4">
          <p className="text-muted-foreground">
            Two separate choices. You can change either one at any time in Settings.
          </p>
          <ConsentCard
            icon={Lock}
            title="Process my meals and glucose data"
            required
            checked={necessary}
            onChange={setNecessary}
            testId="necessary_processing"
          >
            Lets GLIMPSE store the meals, photos, glucose readings, medications and device data you
            add, and use them to produce your forecasts. Without this, GLIMPSE can't do anything
            with your data.
          </ConsentCard>
          <ConsentCard
            icon={LineChart}
            title="Learn from my past meals"
            required={false}
            checked={personalization}
            onChange={setPersonalization}
            testId="personalization_history"
          >
            Lets future forecasts draw on how your glucose responded to your own earlier meals. If
            you leave this off, forecasts still work but never use your personal history.
          </ConsentCard>
          {showErrors && !necessary && (
            <Notice tone="danger">
              GLIMPSE needs the first permission to work. Turn it on to continue, or sign out if
              you'd rather not.
            </Notice>
          )}
        </div>
      )}

      {step.id === "about" && (
        <div className="mt-1">
          <Why>
            Your name personalises the app. The rest helps describe who GLIMPSE is used by. None of
            it changes your forecast.
          </Why>
          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <Field
              id="display_name"
              label="Name"
              error={shown("display_name")}
              className="sm:col-span-2"
            >
              <input
                id="display_name"
                className={inputClass}
                value={draft.display_name}
                maxLength={80}
                autoComplete="name"
                onChange={(e) => set("display_name", e.target.value)}
                aria-invalid={!!shown("display_name")}
              />
            </Field>
            <Field id="date_of_birth" label="Date of birth" optional error={shown("date_of_birth")}>
              <input
                id="date_of_birth"
                type="date"
                className={inputClass}
                value={draft.date_of_birth}
                max={new Date().toISOString().slice(0, 10)}
                onChange={(e) => set("date_of_birth", e.target.value)}
                aria-invalid={!!shown("date_of_birth")}
              />
            </Field>
            <Field id="sex_assigned_at_birth" label="Sex assigned at birth" optional>
              <select
                id="sex_assigned_at_birth"
                className={inputClass}
                value={draft.sex_assigned_at_birth ?? ""}
                onChange={(e) => set("sex_assigned_at_birth", e.target.value || null)}
              >
                <option value="">Choose…</option>
                {options("sex_assigned_at_birth").map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field id="height" label="Height" optional error={shown("height")}>
              <UnitInput
                id="height"
                value={draft.height}
                unit={draft.height_unit}
                units={["cm", "in"] as const}
                onValue={(v) => set("height", v)}
                onUnit={(u) => set("height_unit", u)}
                error={shown("height")}
              />
            </Field>
            <Field id="weight" label="Weight" optional error={shown("weight")}>
              <UnitInput
                id="weight"
                value={draft.weight}
                unit={draft.weight_unit}
                units={["kg", "lb"] as const}
                onValue={(v) => set("weight", v)}
                onUnit={(u) => set("weight_unit", u)}
                error={shown("weight")}
              />
            </Field>
          </div>
          <div className="mt-6">
            <Question title="What would you most like from GLIMPSE?">
              <ChoiceGroup
                name="primary_goal"
                label="Primary goal"
                value={draft.primary_goal}
                onChange={(v) => set("primary_goal", v)}
                options={options("primary_goal")}
              />
            </Question>
          </div>
        </div>
      )}

      {step.id === "health" && (
        <div className="mt-1 space-y-6">
          <Why>
            This gives context to what you see. GLIMPSE does not diagnose anything, and these
            answers are not used to compute your forecast.
          </Why>
          <Question title="Have you been told you have diabetes or prediabetes?">
            <ChoiceGroup
              name="diabetes_status"
              label="Diabetes status"
              value={draft.diabetes_status}
              onChange={(v) => set("diabetes_status", v)}
              options={options("diabetes_status")}
            />
          </Question>
          {hasDiagnosis(draft) && (
            <Field
              id="diagnosis_year"
              label="Year of diagnosis"
              optional
              error={shown("diagnosis_year")}
              className="max-w-xs"
            >
              <input
                id="diagnosis_year"
                inputMode="numeric"
                className={inputClass}
                value={draft.diagnosis_year}
                maxLength={4}
                onChange={(e) => set("diagnosis_year", e.target.value)}
                aria-invalid={!!shown("diagnosis_year")}
              />
            </Field>
          )}
          <div className="grid gap-5 sm:grid-cols-2">
            <Field id="hba1c" label="Most recent HbA1c" optional error={shown("hba1c")}>
              <UnitInput
                id="hba1c"
                value={draft.hba1c}
                unit={draft.hba1c_unit}
                units={["%", "mmol/mol"] as const}
                disabled={draft.hba1c_unknown}
                onValue={(v) => set("hba1c", v)}
                onUnit={(u) => set("hba1c_unit", u)}
                error={shown("hba1c")}
              />
              <label className="mt-2 flex items-center gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-[var(--primary)]"
                  checked={draft.hba1c_unknown}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      hba1c_unknown: e.target.checked,
                      hba1c: e.target.checked ? "" : d.hba1c,
                    }))
                  }
                />{" "}
                I don't know
              </label>
            </Field>
            <Field
              id="fasting_glucose"
              label="Usual fasting glucose"
              optional
              error={shown("fasting_glucose")}
            >
              <UnitInput
                id="fasting_glucose"
                value={draft.fasting_glucose}
                unit={draft.fasting_glucose_unit}
                units={["mg/dL", "mmol/L"] as const}
                disabled={draft.fasting_glucose_unknown}
                onValue={(v) => set("fasting_glucose", v)}
                onUnit={(u) => set("fasting_glucose_unit", u)}
                error={shown("fasting_glucose")}
              />
              <label className="mt-2 flex items-center gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-[var(--primary)]"
                  checked={draft.fasting_glucose_unknown}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      fasting_glucose_unknown: e.target.checked,
                      fasting_glucose: e.target.checked ? "" : d.fasting_glucose,
                    }))
                  }
                />{" "}
                I don't know
              </label>
            </Field>
          </div>
          <Question title="How do you check your glucose?">
            <ChoiceGroup
              name="monitoring_method"
              label="Monitoring method"
              value={draft.monitoring_method}
              onChange={(v) => set("monitoring_method", v)}
              options={options("monitoring_method")}
            />
          </Question>
          {usesCgm(draft) && (
            <>
              <Question title="Which continuous glucose monitor?">
                <ChoiceGroup
                  name="cgm_brand"
                  label="CGM brand"
                  columns={3}
                  value={draft.cgm_brand}
                  onChange={(v) => set("cgm_brand", v)}
                  options={options("cgm_brand")}
                />
              </Question>
              <Question title="How long have you used one?">
                <ChoiceGroup
                  name="cgm_usage_duration"
                  label="CGM usage duration"
                  value={draft.cgm_usage_duration}
                  onChange={(v) => set("cgm_usage_duration", v)}
                  options={options("cgm_usage_duration")}
                />
              </Question>
            </>
          )}
          {draft.monitoring_method === "none" && (
            <Notice>
              A forecast needs a recent glucose reading, so you'll need a monitor or meter before
              GLIMPSE can forecast a meal. You can still explore the app.
            </Notice>
          )}
        </div>
      )}

      {step.id === "devices" && (
        <div className="mt-1 space-y-6">
          <Why>
            Forecasts are built from your glucose readings. Wearable data is shown alongside for
            context only and is never used by the forecast.
          </Why>
          {session.isDemoAccount ? (
            <>
              <p className="text-sm text-muted-foreground">
                This demonstration account already replays a recorded glucose series. You can also
                switch on simulated wearable samples. Nothing is switched on unless you choose it.
              </p>
              <DeviceCards />
            </>
          ) : (
            <>
              <Question title="Your glucose data">
                <ChoiceGroup
                  name="cgm_connect_preference"
                  label="Glucose data preference"
                  columns={3}
                  value={draft.cgm_connect_preference}
                  onChange={(v) => set("cgm_connect_preference", v)}
                  options={options("cgm_connect_preference")}
                />
              </Question>
              {draft.cgm_connect_preference === "now" && (
                <div className="rounded-xl border border-border-strong p-4">
                  <h3 className="font-semibold">Import an export file</h3>
                  <p className="mb-4 mt-1 text-sm text-muted-foreground">
                    Export your readings from Dexcom Clarity or LibreView as a .csv file and upload
                    it here. You can also do this later under Health.
                  </p>
                  <CgmImport />
                </div>
              )}
              <Notice>
                Direct connections to Dexcom, Apple Health, Fitbit and WHOOP aren't available on
                this server yet, so nothing is linked automatically. Health shows the current status
                of each.
              </Notice>
              <Question title="Do you wear a smartwatch or fitness tracker?">
                <ChoiceGroup
                  name="uses_wearable"
                  label="Uses a wearable"
                  value={draft.uses_wearable}
                  onChange={(v) => set("uses_wearable", v)}
                  options={options("uses_wearable")}
                />
              </Question>
              {draft.uses_wearable === "yes" && (
                <>
                  <Question title="Which one?">
                    <ChoiceGroup
                      name="wearable_platform"
                      label="Wearable"
                      value={draft.wearable_platform}
                      onChange={(v) => set("wearable_platform", v)}
                      options={options("wearable_platform")}
                    />
                  </Question>
                  <Question title="Would you want to connect it once that's possible?">
                    <ChoiceGroup
                      name="wearable_connect_preference"
                      label="Wearable connection preference"
                      columns={3}
                      value={draft.wearable_connect_preference}
                      onChange={(v) => set("wearable_connect_preference", v)}
                      options={options("wearable_connect_preference")}
                    />
                  </Question>
                </>
              )}
            </>
          )}
        </div>
      )}

      {step.id === "routine" && (
        <div className="mt-1 space-y-6">
          <Why>
            So GLIMPSE can offer reminders and fit how you log meals. Medications are never used to
            compute a forecast, and GLIMPSE never suggests a dose.
          </Why>
          <Question title="Do you take medication that affects your glucose?">
            <ChoiceGroup
              name="takes_glucose_medication"
              label="Takes glucose medication"
              columns={3}
              value={draft.takes_glucose_medication}
              onChange={(v) => set("takes_glucose_medication", v)}
              options={options("takes_glucose_medication")}
            />
          </Question>
          {draft.takes_glucose_medication === "yes" && (
            <>
              <Field
                id="medication_names"
                label="Which ones?"
                optional
                hint="Separate names with commas. This is a note for your profile; it does not set up reminders."
              >
                <input
                  id="medication_names"
                  className={inputClass}
                  value={draft.medication_names}
                  maxLength={300}
                  onChange={(e) => set("medication_names", e.target.value)}
                  placeholder="For example: Metformin"
                />
              </Field>
              <Question title="Would you like medication reminders?">
                <ChoiceGroup
                  name="wants_medication_reminders"
                  label="Medication reminders"
                  value={
                    draft.wants_medication_reminders === null
                      ? null
                      : draft.wants_medication_reminders
                        ? "yes"
                        : "no"
                  }
                  onChange={(v) => set("wants_medication_reminders", v === "yes")}
                  options={[
                    { value: "yes", label: "Yes" },
                    { value: "no", label: "No" },
                  ]}
                />
              </Question>
              {draft.wants_medication_reminders && (
                <Notice>
                  Reminders start once you add each medication's times under Health → Medications.
                  We'll point you there when you finish.
                </Notice>
              )}
            </>
          )}
          <Question title="Do you use insulin?">
            <ChoiceGroup
              name="uses_insulin"
              label="Uses insulin"
              columns={3}
              value={draft.uses_insulin}
              onChange={(v) => set("uses_insulin", v)}
              options={options("uses_insulin")}
            />
          </Question>
          <div className="grid gap-5 sm:grid-cols-2">
            <Field
              id="meals_per_day"
              label="Meals on a typical day"
              optional
              error={shown("meals_per_day")}
            >
              <input
                id="meals_per_day"
                inputMode="numeric"
                className={inputClass}
                value={draft.meals_per_day}
                maxLength={2}
                onChange={(e) => set("meals_per_day", e.target.value)}
                aria-invalid={!!shown("meals_per_day")}
              />
            </Field>
            <Field id="dietary_pattern" label="Eating pattern" optional>
              <select
                id="dietary_pattern"
                className={inputClass}
                value={draft.dietary_pattern ?? ""}
                onChange={(e) => set("dietary_pattern", e.target.value || null)}
              >
                <option value="">Choose…</option>
                {options("dietary_pattern").map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field id="dietary_restrictions" label="Allergies or foods you avoid" optional>
            <input
              id="dietary_restrictions"
              className={inputClass}
              value={draft.dietary_restrictions}
              maxLength={300}
              onChange={(e) => set("dietary_restrictions", e.target.value)}
            />
          </Field>
          <Question title="How would you like to log meals?">
            <ChoiceGroup
              name="meal_logging_method"
              label="Meal logging method"
              columns={3}
              value={draft.meal_logging_method}
              onChange={(v) => set("meal_logging_method", v)}
              options={options("meal_logging_method")}
            />
          </Question>
        </div>
      )}

      {step.id === "alerts" && (
        <div className="mt-1 space-y-6">
          <Why>
            An alert tells you when a meal's forecast rises above a level you choose. It is
            informational and does not replace your glucose monitor's own alarms.
          </Why>
          <Question title="Tell me when a forecast rises above my chosen level" optional={false}>
            <ChoiceGroup
              name="wants_spike_alerts"
              label="Forecast alerts"
              value={
                draft.wants_spike_alerts === null ? null : draft.wants_spike_alerts ? "yes" : "no"
              }
              onChange={(v) =>
                setDraft((d) => ({
                  ...d,
                  wants_spike_alerts: v === "yes",
                  alert_threshold:
                    v === "yes" && !d.alert_threshold
                      ? d.alert_threshold_unit === "mg/dL"
                        ? "180"
                        : "10.0"
                      : d.alert_threshold,
                }))
              }
              options={[
                { value: "yes", label: "Yes, alert me" },
                { value: "no", label: "No alerts" },
              ]}
            />
          </Question>
          {draft.wants_spike_alerts && (
            <>
              <Field
                id="alert_threshold"
                label="Alert level"
                error={shown("alert_threshold")}
                className="max-w-sm"
                hint="Between 100 and 400 mg/dL (5.6 and 22.2 mmol/L)."
              >
                <UnitInput
                  id="alert_threshold"
                  value={draft.alert_threshold}
                  unit={draft.alert_threshold_unit}
                  units={["mg/dL", "mmol/L"] as const}
                  onValue={(v) => set("alert_threshold", v)}
                  onUnit={(u) => set("alert_threshold_unit", u)}
                  error={shown("alert_threshold")}
                />
              </Field>
              <div className="rounded-xl border border-border-strong p-4">
                <div className="flex items-center justify-between gap-3">
                  <label htmlFor="quiet-hours" className="text-sm font-medium">
                    Quiet hours
                  </label>
                  <Switch
                    id="quiet-hours"
                    checked={draft.quiet_hours_enabled}
                    onCheckedChange={(v) => set("quiet_hours_enabled", v)}
                  />
                </div>
                <p className="mt-1 text-[13px] text-muted-foreground">
                  Alerts raised during quiet hours are recorded but not shown as active.
                </p>
                {draft.quiet_hours_enabled && (
                  <div className="mt-3 grid grid-cols-2 gap-3">
                    <Field id="quiet_start" label="From" error={shown("quiet_start")}>
                      <input
                        id="quiet_start"
                        type="time"
                        className={inputClass}
                        value={draft.quiet_start}
                        onChange={(e) => set("quiet_start", e.target.value)}
                      />
                    </Field>
                    <Field id="quiet_end" label="Until">
                      <input
                        id="quiet_end"
                        type="time"
                        className={inputClass}
                        value={draft.quiet_end}
                        onChange={(e) => set("quiet_end", e.target.value)}
                      />
                    </Field>
                  </div>
                )}
              </div>
              <p className="text-[13px] text-muted-foreground">
                Alerts appear inside GLIMPSE. Push and email alerts aren't available yet.
              </p>
            </>
          )}
          <Question title="Show glucose in" optional={false}>
            <ChoiceGroup<GlucoseUnit>
              name="display_unit"
              label="Glucose unit"
              value={units.unit}
              onChange={units.setUnit}
              options={[
                { value: "mg/dL", label: "mg/dL" },
                { value: "mmol/L", label: "mmol/L" },
              ]}
            />
            <p className="mt-2 text-[13px] text-muted-foreground">
              This display choice is remembered on this device.
            </p>
          </Question>
        </div>
      )}

      {step.id === "review" && (
        <Review
          draft={draft}
          necessary={session.processingAllowed}
          personalization={session.personalizationAllowed}
          unit={units.unit}
          onEdit={(id) => go(STEPS.findIndex((s) => s.id === id))}
          demo={session.isDemoAccount}
        />
      )}

      {save.isError && (
        <Notice tone="danger" className="mt-6" title="That wasn't saved">
          {api.describeError(save.error)} Your answers are still here; try again when you're ready.
        </Notice>
      )}

      <div className="sticky bottom-0 -mx-5 mt-8 flex items-center gap-2 border-t border-border bg-card/95 px-5 py-4 backdrop-blur sm:-mx-8 sm:px-8">
        {index > 0 && (
          <Button
            variant="ghost"
            className="h-11"
            onClick={() => go(index - 1)}
            disabled={save.isPending}
            data-testid="onboarding-back"
          >
            <ArrowLeft className="h-4 w-4" /> Back
          </Button>
        )}
        <div className="flex-1" />
        {step.optional && (
          <Button
            variant="ghost"
            className="h-11 text-muted-foreground"
            onClick={() => go(index + 1)}
            disabled={save.isPending}
            data-testid="onboarding-skip"
          >
            Skip for now
          </Button>
        )}
        <Button
          className="h-11 min-w-32 text-[15px]"
          onClick={onContinue}
          disabled={save.isPending}
          data-testid="onboarding-continue"
        >
          {save.isPending && <Spinner />}
          {step.id === "welcome"
            ? "Get started"
            : step.id === "review"
              ? editing
                ? "Save changes"
                : "Finish setup"
              : save.isPending
                ? "Saving…"
                : "Save and continue"}
        </Button>
      </div>
    </Frame>
  );
}

function Frame({
  children,
  progress,
  label,
  editing,
  onSignOut,
}: {
  children: ReactNode;
  progress: number;
  label: string;
  editing: boolean;
  onSignOut?: () => void;
}) {
  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-10 border-b border-border bg-card/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-3xl items-center justify-between gap-4 px-4 sm:px-6">
          <Logo />
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground" data-testid="onboarding-progress">
              {label}
            </span>
            {editing ? (
              <Button asChild variant="ghost" size="sm">
                <Link to="/settings">Close</Link>
              </Button>
            ) : (
              onSignOut && (
                <Button variant="ghost" size="sm" onClick={onSignOut}>
                  Sign out
                </Button>
              )
            )}
          </div>
        </div>
        <div
          className="h-1 bg-muted"
          role="progressbar"
          aria-label="Setup progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress * 100)}
        >
          <div
            className="h-full bg-primary transition-[width] duration-300"
            style={{ width: `${Math.round(progress * 100)}%` }}
          />
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-10">
        <div className="card-surface px-5 pt-6 sm:px-8 sm:pt-8 [&:not(:has(.sticky))]:pb-8">
          {children}
        </div>
        {!editing && (
          <p className="mt-4 text-center text-[13px] text-muted-foreground">
            Your answers are saved as you go. You can leave and pick up where you stopped.
          </p>
        )}
      </main>
    </div>
  );
}

function Review({
  draft,
  necessary,
  personalization,
  unit,
  onEdit,
  demo,
}: {
  draft: Draft;
  necessary: boolean;
  personalization: boolean;
  unit: GlucoseUnit;
  onEdit: (step: StepId) => void;
  demo: boolean;
}) {
  const label = (question: string, value: string | null) =>
    value ? (LABELS[question]?.[value] ?? value) : null;
  const amount = (value: string, u: string, unknown = false) =>
    value.trim() ? `${value.trim()} ${u}` : unknown ? "I don't know" : null;
  const yesNo = (value: boolean | null) => (value === null ? null : value ? "Yes" : "No");
  const sections: Array<{ id: StepId; title: string; rows: Array<[string, string | null]> }> = [
    {
      id: "consent",
      title: "Your permission",
      rows: [
        ["Process my meals and glucose data", necessary ? "Allowed" : "Not allowed"],
        ["Learn from my past meals", personalization ? "Allowed" : "Not allowed"],
      ],
    },
    {
      id: "about",
      title: "About you",
      rows: [
        ["Name", draft.display_name.trim() || null],
        ["Date of birth", draft.date_of_birth || null],
        ["Sex assigned at birth", label("sex_assigned_at_birth", draft.sex_assigned_at_birth)],
        ["Height", amount(draft.height, draft.height_unit)],
        ["Weight", amount(draft.weight, draft.weight_unit)],
        ["Goal", label("primary_goal", draft.primary_goal)],
      ],
    },
    {
      id: "health",
      title: "Glucose and health",
      rows: [
        ["Diabetes status", label("diabetes_status", draft.diabetes_status)],
        ...(hasDiagnosis(draft)
          ? [["Year of diagnosis", draft.diagnosis_year || null] as [string, string | null]]
          : []),
        ["HbA1c", amount(draft.hba1c, draft.hba1c_unit, draft.hba1c_unknown)],
        [
          "Fasting glucose",
          amount(draft.fasting_glucose, draft.fasting_glucose_unit, draft.fasting_glucose_unknown),
        ],
        ["Monitoring", label("monitoring_method", draft.monitoring_method)],
        ...(usesCgm(draft)
          ? ([
              ["Monitor", label("cgm_brand", draft.cgm_brand)],
              ["Used for", label("cgm_usage_duration", draft.cgm_usage_duration)],
            ] as Array<[string, string | null]>)
          : []),
      ],
    },
    ...(demo
      ? []
      : [
          {
            id: "devices" as StepId,
            title: "Your data sources",
            rows: [
              ["Glucose data", label("cgm_connect_preference", draft.cgm_connect_preference)],
              [
                "Wearable",
                draft.uses_wearable === "yes"
                  ? (label("wearable_platform", draft.wearable_platform) ?? "Yes")
                  : label("uses_wearable", draft.uses_wearable),
              ],
            ] as Array<[string, string | null]>,
          },
        ]),
    {
      id: "routine",
      title: "Medications and meals",
      rows: [
        ["Glucose medication", label("takes_glucose_medication", draft.takes_glucose_medication)],
        ...(draft.takes_glucose_medication === "yes"
          ? ([
              ["Medications", draft.medication_names.trim() || null],
              ["Reminders", yesNo(draft.wants_medication_reminders)],
            ] as Array<[string, string | null]>)
          : []),
        ["Insulin", label("uses_insulin", draft.uses_insulin)],
        ["Meals per day", draft.meals_per_day || null],
        ["Eating pattern", label("dietary_pattern", draft.dietary_pattern)],
        ["Foods avoided", draft.dietary_restrictions.trim() || null],
        ["Meal logging", label("meal_logging_method", draft.meal_logging_method)],
      ],
    },
    {
      id: "alerts",
      title: "Alerts and units",
      rows: [
        [
          "Forecast alerts",
          draft.wants_spike_alerts === null ? null : draft.wants_spike_alerts ? "On" : "Off",
        ],
        ...(draft.wants_spike_alerts
          ? ([
              ["Alert level", amount(draft.alert_threshold, draft.alert_threshold_unit)],
              [
                "Quiet hours",
                draft.quiet_hours_enabled ? `${draft.quiet_start} to ${draft.quiet_end}` : "Off",
              ],
            ] as Array<[string, string | null]>)
          : []),
        ["Glucose shown in", unit],
      ],
    },
  ];
  return (
    <div className="mt-3 space-y-5">
      <p className="text-muted-foreground">
        Check your answers. Anything left blank simply stays unanswered.
      </p>
      {sections.map((section) => (
        <section key={section.id} className="rounded-xl border border-border">
          <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
            <h2 className="text-sm font-semibold">{section.title}</h2>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onEdit(section.id)}
              aria-label={`Edit ${section.title}`}
            >
              Edit
            </Button>
          </div>
          <dl className="divide-y divide-border px-4 text-sm">
            {section.rows.map(([key, value]) => (
              <div key={key} className="flex items-baseline justify-between gap-4 py-2">
                <dt className="text-muted-foreground">{key}</dt>
                <dd
                  className={cn(
                    "text-right",
                    value ? "font-medium text-foreground" : "text-muted-foreground",
                  )}
                >
                  {value ?? "Not answered"}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}
