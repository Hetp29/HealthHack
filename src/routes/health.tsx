import { Link, createFileRoute } from "@tanstack/react-router";
import { ShieldAlert } from "lucide-react";

import { EmptyState, PageHeader, Panel } from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import { DeviceCards } from "@/features/DeviceCards";
import { AlertsTab } from "@/features/health/AlertsTab";
import { GlucoseTab } from "@/features/health/GlucoseTab";
import { MedicationsTab } from "@/features/health/MedicationsTab";
import { WearablesTab } from "@/features/health/WearablesTab";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

const TABS = [
  { id: "devices", label: "Data sources" },
  { id: "glucose", label: "Glucose" },
  { id: "wearables", label: "Wearables" },
  { id: "medications", label: "Medications" },
  { id: "alerts", label: "Alerts" },
] as const;
export type HealthTab = (typeof TABS)[number]["id"];

export const Route = createFileRoute("/health")({
  component: HealthPage,
  validateSearch: (search: Record<string, unknown>): { tab?: HealthTab } => {
    const tab = TABS.find((t) => t.id === search["tab"])?.id;
    return tab ? { tab } : {};
  },
});

function HealthPage() {
  const { tab = "devices" } = Route.useSearch();
  const { processingAllowed } = useSession();
  return (
    <>
      <PageHeader
        title="Health"
        description="Where your data comes from, and everything GLIMPSE keeps alongside your meals."
      />
      <div className="-mx-4 mb-6 overflow-x-auto px-4 sm:mx-0 sm:px-0">
        <div
          role="tablist"
          aria-label="Health sections"
          className="flex w-max gap-1 rounded-xl bg-muted p-1"
        >
          {TABS.map((t) => (
            <Link
              key={t.id}
              to="/health"
              search={{ tab: t.id }}
              role="tab"
              aria-selected={tab === t.id}
              data-testid={`health-tab-${t.id}`}
              className={cn(
                "flex h-10 items-center whitespace-nowrap rounded-lg px-4 text-sm font-medium",
                tab === t.id
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
            </Link>
          ))}
        </div>
      </div>
      {!processingAllowed ? (
        <Panel>
          <EmptyState
            icon={ShieldAlert}
            title="GLIMPSE needs your permission"
            action={
              <Button asChild>
                <Link to="/settings" hash="privacy">
                  Review permissions
                </Link>
              </Button>
            }
          >
            Processing of your health data is switched off, so there is nothing to show here.
          </EmptyState>
        </Panel>
      ) : (
        <div role="tabpanel" aria-label={TABS.find((t) => t.id === tab)?.label}>
          {tab === "devices" && <DeviceCards />}
          {tab === "glucose" && <GlucoseTab />}
          {tab === "wearables" && <WearablesTab />}
          {tab === "medications" && <MedicationsTab />}
          {tab === "alerts" && <AlertsTab />}
        </div>
      )}
    </>
  );
}
