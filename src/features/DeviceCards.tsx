import { Link } from "@tanstack/react-router";
import { Activity, FileUp, HeartPulse, Layers, Smartphone, Watch } from "lucide-react";
import type { ComponentType } from "react";
import { toast } from "sonner";

import {
  Async,
  Notice,
  Pill,
  SkeletonBlock,
  SourceBadge,
  Spinner,
} from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import * as api from "@/lib/api";
import { useDemoDevices, useDevices, useRefreshAll } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { DemoDeviceProfile, DeviceProfileId, ProviderCapability } from "@/lib/types";
import { useGuardedMutation } from "@/lib/useGuardedMutation";

export const METRIC_LABELS: Record<string, string> = {
  heart_rate: "Heart rate",
  resting_heart_rate: "Resting heart rate",
  steps_daily: "Daily steps",
  sleep_duration: "Sleep",
  workout: "Workouts",
  recovery_score: "Recovery score",
  hrv_rmssd: "Heart rate variability (RMSSD)",
  hrv_sdnn: "Heart rate variability (SDNN)",
  glucose: "Glucose",
};

const ICONS: Record<string, ComponentType<{ className?: string }>> = {
  apple_health: Layers,
  apple_watch: Watch,
  fitbit: Activity,
  whoop: HeartPulse,
};

function Shell({
  icon: Icon,
  title,
  subtitle,
  badges,
  children,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  subtitle: string;
  badges: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <article className="card-surface flex min-w-0 flex-col p-5">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-muted text-foreground">
          <Icon className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-foreground">{title}</h3>
          <p className="text-[13px] text-muted-foreground">{subtitle}</p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">{badges}</div>
      <div className="mt-3 flex flex-1 flex-col text-sm text-muted-foreground">{children}</div>
    </article>
  );
}

function SimulatedCard({ profile }: { profile: DemoDeviceProfile }) {
  const { clock } = useSession();
  const refreshAll = useRefreshAll();
  const activate = useGuardedMutation({
    mutationFn: () => api.activateDemoDevice(profile.profile as DeviceProfileId),
    onSuccess: async (result) => {
      await refreshAll();
      toast.success(`${profile.label} sample is on`, {
        description: `${result.observations_released.toLocaleString()} simulated observations are now visible. No ${profile.label} account was contacted.`,
      });
    },
    onError: (error) => toast.error(api.describeError(error)),
  });
  const active = profile.status === "demo_simulated";
  const note = Object.values(profile.notes)[0];

  return (
    <Shell
      icon={ICONS[profile.profile] ?? Watch}
      title={profile.label}
      subtitle={
        profile.kind === "health_data_platform"
          ? "Health data platform that gathers data from other devices and apps"
          : "Wearable device"
      }
      badges={
        <>
          <SourceBadge source="simulated" />
          <Pill tone={active ? "teal" : "neutral"}>
            {active ? "Simulation on" : "Not simulated"}
          </Pill>
        </>
      }
    >
      <ul
        className="space-y-1.5"
        data-testid={`device-${profile.profile}`}
        data-status={profile.status}
      >
        {profile.metrics.map((metric) => (
          <li key={metric.metric} className="flex items-baseline justify-between gap-3">
            <span className="text-foreground">{METRIC_LABELS[metric.metric] ?? metric.metric}</span>
            <span className="tabular text-[13px]">
              {active
                ? `${metric.observations_visible.toLocaleString()} sample${metric.observations_visible === 1 ? "" : "s"}`
                : "—"}
            </span>
          </li>
        ))}
      </ul>
      {note && <p className="mt-3 text-[13px]">{note}</p>}
      <div className="mt-auto pt-4">
        {active ? (
          <p className="text-[13px]">
            Showing generated sample values up to{" "}
            {clock.dateTime(
              profile.metrics
                .map((m) => m.latest_observed_at)
                .filter(Boolean)
                .sort()
                .at(-1) ?? null,
            )}
            . The simulation stays on until the demonstration is reset.
          </p>
        ) : (
          <Button
            variant="outline"
            className="h-10 w-full"
            onClick={() => activate.mutate()}
            disabled={activate.isPending}
            data-testid={`simulate-${profile.profile}`}
          >
            {activate.isPending && <Spinner />} Simulate connection
          </Button>
        )}
      </div>
    </Shell>
  );
}

/** A demonstration account's sources: the replayed recording and the simulated wearables. */
function DemoDevicesGrid() {
  const devices = useDemoDevices();
  const { clock } = useSession();
  return (
    <Async
      query={devices}
      skeleton={
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <SkeletonBlock className="h-56" />
          <SkeletonBlock className="h-56" />
        </div>
      }
    >
      {(data) => (
        <div className="space-y-4">
          <Notice tone="warning" title="Simulated samples, not real connections">
            {data.disclaimer}
          </Notice>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            <Shell
              icon={Activity}
              title="Historical CGM"
              subtitle={data.glucose_source.label}
              badges={
                <>
                  <SourceBadge source={data.glucose_source.data_class} />
                  <Pill tone="neutral">Replaying</Pill>
                </>
              }
            >
              <p>{data.glucose_source.description}</p>
              <p className="mt-3 text-[13px]">
                <span className="tabular font-medium text-foreground">
                  {data.glucose_source.readings_visible.toLocaleString()}
                </span>{" "}
                readings released, latest at{" "}
                {clock.dateTime(data.glucose_source.latest_observed_at)}.
              </p>
              <p className="mt-auto pt-4 text-[13px] font-medium text-foreground">
                Used by the forecast.
              </p>
            </Shell>
            {data.profiles.map((profile) => (
              <SimulatedCard key={profile.profile} profile={profile} />
            ))}
          </div>
          <p className="text-[13px] text-muted-foreground">
            {data.used_by_forecast_model.explanation}
          </p>
        </div>
      )}
    </Async>
  );
}

const INTEGRATION_NOTE: Record<string, string> = {
  oauth_api: "Connects through the provider's sign-in page.",
  native_bridge:
    "Needs the GLIMPSE iPhone app to read Apple Health, and that app has not been built yet.",
  file_import: "Upload an export file from the provider's own app.",
  api_push: "Type readings in yourself.",
};

function ProviderCard({
  provider,
  status,
}: {
  provider: ProviderCapability;
  status: string | null;
}) {
  const refreshAll = useRefreshAll();
  const connect = useGuardedMutation({
    mutationFn: () => api.connectProvider(provider.provider),
    onSuccess: (result) => window.location.assign(result.authorization_url),
    onError: (error) =>
      toast.error(
        api.isCode(error, "provider_not_configured")
          ? `${provider.label} isn't set up on this server yet.`
          : api.describeError(error),
      ),
  });
  const disconnect = useGuardedMutation({
    mutationFn: () => api.disconnectProvider(provider.provider),
    onSuccess: async () => {
      await refreshAll();
      toast.success(`${provider.label} disconnected`);
    },
    onError: (error) => toast.error(api.describeError(error)),
  });
  const linked = status === "authorized" || status === "connected";
  const local = provider.integration === "file_import" || provider.integration === "api_push";
  const icon =
    provider.integration === "file_import"
      ? FileUp
      : provider.integration === "native_bridge"
        ? Smartphone
        : provider.kind === "cgm"
          ? Activity
          : Watch;

  return (
    <Shell
      icon={icon}
      title={provider.label}
      subtitle={provider.kind === "cgm" ? "Glucose data" : "Activity and sleep data"}
      badges={
        status === "connected" ? (
          <Pill tone="green">Receiving data</Pill>
        ) : status === "authorized" ? (
          <Pill tone="teal">Linked, no data yet</Pill>
        ) : status === "error" || status === "revoked" ? (
          <Pill tone="red">Needs reconnecting</Pill>
        ) : local ? (
          <Pill tone="teal">Available</Pill>
        ) : provider.configured ? (
          <Pill tone="neutral">Not connected</Pill>
        ) : (
          <Pill tone="neutral">Not available yet</Pill>
        )
      }
    >
      <p>{INTEGRATION_NOTE[provider.integration] ?? ""}</p>
      {!local && !provider.configured && provider.integration === "oauth_api" && (
        <p className="mt-2 text-[13px]">
          This server has no {provider.label} credentials, so a connection can't be started here.
        </p>
      )}
      <div className="mt-auto pt-4">
        {local ? (
          <Button asChild variant="outline" className="h-10 w-full">
            <Link to="/health" search={{ tab: "glucose" }}>
              {provider.integration === "file_import" ? "Import a file" : "Add a reading"}
            </Link>
          </Button>
        ) : linked ? (
          <Button
            variant="outline"
            className="h-10 w-full"
            onClick={() => disconnect.mutate()}
            disabled={disconnect.isPending}
          >
            {disconnect.isPending && <Spinner />} Disconnect
          </Button>
        ) : provider.configured && provider.integration === "oauth_api" ? (
          <Button
            className="h-10 w-full"
            onClick={() => connect.mutate()}
            disabled={connect.isPending}
          >
            {connect.isPending && <Spinner />} Connect {provider.label}
          </Button>
        ) : null}
      </div>
    </Shell>
  );
}

function RealDevicesGrid() {
  const devices = useDevices();
  return (
    <Async
      query={devices}
      skeleton={
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <SkeletonBlock className="h-48" />
          <SkeletonBlock className="h-48" />
        </div>
      }
    >
      {(data) => (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.providers.map((provider) => (
            <ProviderCard
              key={provider.provider}
              provider={provider}
              status={
                data.connections.find((c) => c.provider === provider.provider)?.status ?? null
              }
            />
          ))}
        </div>
      )}
    </Async>
  );
}

/** The sources this account can use, described as what they really are. */
export function DeviceCards() {
  const { isDemoAccount } = useSession();
  return isDemoAccount ? <DemoDevicesGrid /> : <RealDevicesGrid />;
}
