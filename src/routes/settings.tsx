import { useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { ClipboardList, History, LogOut } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  ChoiceGroup,
  Field,
  KeyValue,
  Notice,
  PageHeader,
  Panel,
  Pill,
  Spinner,
  inputClass,
} from "@/components/glimpse/primitives";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import * as api from "@/lib/api";
import { useRefreshAll } from "@/lib/queries";
import { keys, useSession } from "@/lib/session";
import type { Consent, GlucoseUnit } from "@/lib/types";
import { useUnits } from "@/lib/units";
import { useGuardedMutation } from "@/lib/useGuardedMutation";

export const Route = createFileRoute("/settings")({ component: SettingsPage });

function ProfilePanel() {
  const { user, isDemoAccount } = useSession();
  const queryClient = useQueryClient();
  const [name, setName] = useState(user?.display_name ?? "");
  const [timezone, setTimezone] = useState(user?.timezone ?? "UTC");
  const [touched, setTouched] = useState(false);
  let timezoneError: string | null = null;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: timezone.trim() });
  } catch {
    timezoneError = "Enter a timezone such as America/New_York or Europe/London.";
  }
  const nameError = name.trim() ? null : "Enter a name.";
  const changed = name.trim() !== (user?.display_name ?? "") || timezone.trim() !== user?.timezone;

  const save = useGuardedMutation({
    mutationFn: () =>
      api.patchMe({
        display_name: name.trim(),
        ...(isDemoAccount ? {} : { timezone: timezone.trim() }),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(keys.me, updated);
      toast.success("Profile saved");
    },
  });

  return (
    <Panel title="Profile">
      <form
        noValidate
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          setTouched(true);
          if (!nameError && !timezoneError) save.mutate();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="settings-name" label="Name" error={touched ? nameError : null}>
            <input
              id="settings-name"
              className={inputClass}
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field
            id="settings-email"
            label="Email"
            hint="Your email is how you sign in and can't be changed here."
          >
            <input
              id="settings-email"
              className={inputClass}
              value={user?.email ?? ""}
              readOnly
              disabled
            />
          </Field>
          <Field
            id="settings-timezone"
            label="Timezone"
            error={touched ? timezoneError : null}
            hint={
              isDemoAccount
                ? "Fixed for a demonstration account, which uses the recording's own clock."
                : "Used to show times and to schedule reminders."
            }
          >
            <input
              id="settings-timezone"
              className={inputClass}
              value={timezone}
              disabled={isDemoAccount}
              onChange={(e) => setTimezone(e.target.value)}
            />
          </Field>
        </div>
        {save.isError && (
          <Notice tone="danger" title="Your profile wasn't saved">
            {api.describeError(save.error)}
          </Notice>
        )}
        <Button type="submit" disabled={!changed || save.isPending}>
          {save.isPending && <Spinner />} Save profile
        </Button>
      </form>
    </Panel>
  );
}

function PrivacyPanel() {
  const { processingAllowed, personalizationAllowed } = useSession();
  const refreshAll = useRefreshAll();
  const [confirm, setConfirm] = useState(false);
  const change = useGuardedMutation({
    mutationFn: (input: { type: Consent["consent_type"]; state: "granted" | "withdrawn" }) =>
      api.putConsent(input.type, input.state),
    onSuccess: async (_result, input) => {
      setConfirm(false);
      await refreshAll();
      toast.success(
        input.state === "granted" ? "Permission switched on" : "Permission switched off",
      );
    },
    onError: (error) => toast.error(`Not changed. ${api.describeError(error)}`),
  });

  return (
    <Panel
      id="privacy"
      title="Privacy and permissions"
      description="Two separate choices. Each takes effect as soon as you change it."
    >
      <div className="space-y-3">
        <div className="flex items-start justify-between gap-4 rounded-xl border border-border-strong p-4">
          <div>
            <div className="flex flex-wrap items-center gap-2 font-medium">
              Process my meals and glucose data <Pill tone="teal">Required to use GLIMPSE</Pill>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              Store what you add and use it to produce your forecasts. Switching this off stops all
              processing; your data stays stored until you delete your account.
            </p>
          </div>
          <Switch
            checked={processingAllowed}
            disabled={change.isPending}
            data-testid="consent-necessary"
            onCheckedChange={(checked) =>
              checked
                ? change.mutate({ type: "necessary_processing", state: "granted" })
                : setConfirm(true)
            }
            aria-label="Process my meals and glucose data"
          />
        </div>
        <div className="flex items-start justify-between gap-4 rounded-xl border border-border-strong p-4">
          <div>
            <div className="flex flex-wrap items-center gap-2 font-medium">
              Learn from my past meals <Pill tone="neutral">Optional</Pill>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              Let future forecasts draw on how your glucose responded to your own earlier meals.
              With this off, forecasts are always general ones.
            </p>
          </div>
          <Switch
            checked={personalizationAllowed}
            disabled={change.isPending}
            data-testid="consent-personalization"
            onCheckedChange={(checked) =>
              change.mutate({
                type: "personalization_history",
                state: checked ? "granted" : "withdrawn",
              })
            }
            aria-label="Learn from my past meals"
          />
        </div>
      </div>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Stop processing your data?</AlertDialogTitle>
            <AlertDialogDescription>
              GLIMPSE will stop showing your meals, glucose, medications and forecasts, and can't
              log or forecast anything until you switch this back on. Nothing is deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={change.isPending}>Keep it on</AlertDialogCancel>
            <AlertDialogAction
              disabled={change.isPending}
              onClick={(e) => {
                e.preventDefault();
                change.mutate({ type: "necessary_processing", state: "withdrawn" });
              }}
            >
              {change.isPending && <Spinner />} Switch off
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Panel>
  );
}

function DeleteAccount() {
  const { signOut } = useSession();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const remove = useGuardedMutation({
    mutationFn: () => api.deleteAccount(password),
    onSuccess: async () => {
      api.clearToken();
      await signOut();
      toast.success("Your account and its data were deleted");
    },
  });
  return (
    <>
      <Button
        variant="outline"
        className="text-destructive hover:text-destructive"
        onClick={() => {
          setOpen(true);
          setPassword("");
          remove.reset();
        }}
      >
        Delete my account
      </Button>
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete your account?</AlertDialogTitle>
            <AlertDialogDescription>
              Your account and everything in it, including meals, photos, glucose readings,
              forecasts and medications, is permanently deleted. This can't be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <Field
            id="delete-password"
            label="Enter your password to confirm"
            error={remove.isError ? api.describeError(remove.error) : null}
          >
            <input
              id="delete-password"
              type="password"
              autoComplete="current-password"
              className={inputClass}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>Keep my account</AlertDialogCancel>
            <AlertDialogAction
              disabled={remove.isPending || !password}
              onClick={(e) => {
                e.preventDefault();
                remove.mutate();
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {remove.isPending && <Spinner />} Delete everything
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function SettingsPage() {
  const { replay, isDemoAccount, status, signOut, clock, onboarding } = useSession();
  const units = useUnits();
  const answered = Object.keys(onboarding?.answers ?? {}).length;

  return (
    <>
      <PageHeader title="Settings" description="Your profile, permissions and preferences." />
      <div className="mx-auto max-w-3xl space-y-5 lg:mx-0">
        <ProfilePanel />

        <Panel
          title="Health profile"
          description={`${answered} question${answered === 1 ? "" : "s"} answered. These describe you and your routine; they are not used to compute forecasts.`}
          action={
            <Button asChild variant="outline">
              <Link to="/onboarding" data-testid="edit-questionnaire">
                <ClipboardList className="h-4 w-4" /> Review my answers
              </Link>
            </Button>
          }
        />

        <PrivacyPanel />

        <Panel
          title="Units"
          description="How glucose is shown. Remembered on this device; your stored readings are unchanged."
        >
          <ChoiceGroup<GlucoseUnit>
            name="unit"
            label="Glucose unit"
            value={units.unit}
            onChange={units.setUnit}
            options={[
              { value: "mg/dL", label: "mg/dL" },
              { value: "mmol/L", label: "mmol/L" },
            ]}
          />
          <p className="mt-3 text-sm text-muted-foreground">
            Alert level and notification preferences are under{" "}
            <Link
              to="/health"
              search={{ tab: "alerts" }}
              className="font-medium text-primary underline-offset-2 hover:underline"
            >
              Health → Alerts
            </Link>
            .
          </p>
        </Panel>

        {isDemoAccount && replay && (
          <Panel
            title="Demonstration"
            action={
              <Pill tone="indigo" icon={History}>
                Historical replay
              </Pill>
            }
          >
            <KeyValue
              items={[
                ["Scenario", replay.mode === "new_user" ? "New user" : "Returning user"],
                ["Recording time now", clock.dateTime(replay.position)],
                ["Events completed", `${replay.event_index} of ${replay.event_count}`],
              ]}
            />
            <dl className="mt-3 space-y-3 text-sm text-muted-foreground">
              <div>
                <dt className="font-medium text-foreground">Glucose and meals</dt>
                <dd>{replay.provenance.glucose_and_meals.description}</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Profile and medications</dt>
                <dd>{replay.provenance.profile_and_medications.description}</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Forecasts</dt>
                <dd>{replay.provenance.forecasts.description}</dd>
              </div>
              {replay.provenance.backfilled_history && (
                <div>
                  <dt className="font-medium text-foreground">Earlier history</dt>
                  <dd>{replay.provenance.backfilled_history.description}</dd>
                </div>
              )}
            </dl>
            <p className="mt-3 text-[13px] text-muted-foreground">
              Use Demo controls in the top bar to move through the recording or reset it.
            </p>
          </Panel>
        )}

        <Panel title="About GLIMPSE">
          <p className="text-sm text-muted-foreground">
            GLIMPSE is a research prototype. Its forecasts are informational, their uncertainty is
            not calibrated, and they are not medical advice, a diagnosis, or a replacement for your
            glucose monitor's own alerts.
          </p>
          {status && (
            <KeyValue
              className="mt-3"
              items={[
                ["Forecasting model", status.model.loaded ? "Available" : "Unavailable"],
                [
                  "Photo analysis",
                  status.meal_analysis.configured ? "Available" : "Not switched on for this server",
                ],
              ]}
            />
          )}
        </Panel>

        <Panel title="Account">
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() => void signOut()}
              data-testid="settings-sign-out"
            >
              <LogOut className="h-4 w-4" /> Sign out
            </Button>
            {!isDemoAccount && <DeleteAccount />}
          </div>
          {isDemoAccount && (
            <p className="mt-3 text-[13px] text-muted-foreground">
              A demonstration account can't be deleted here. Reset it from Demo controls instead.
            </p>
          )}
        </Panel>
      </div>
    </>
  );
}
