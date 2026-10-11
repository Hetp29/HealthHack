import { Link, useNavigate } from "@tanstack/react-router";
import { FlaskConical, Info, LogOut } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useAppState, type Connectivity } from "@/state/AppState";
import { fmtSource } from "@/lib/formatting";
import { cn } from "@/lib/utils";
import { API_BASE_URL } from "@/services/apiClient";

function Logo() {
  return (
    <svg viewBox="0 0 32 32" className="h-8 w-8" aria-hidden>
      <rect width="32" height="32" rx="9" className="fill-accent" />
      <path
        d="M5 20 C 10 20, 11 9, 15 9 S 19 21, 22 21 S 25 16, 27 16"
        className="stroke-primary"
        strokeWidth="2.4"
        fill="none"
        strokeLinecap="round"
      />
    </svg>
  );
}

const CONN: Record<Connectivity, { label: string; dot: string }> = {
  checking: { label: "Checking backend…", dot: "bg-muted-foreground animate-pulse" },
  connected: { label: "Backend connected", dot: "bg-primary" },
  model_unavailable: { label: "Model unavailable", dot: "bg-warning-border" },
  fixture: { label: "Demo data", dot: "bg-warning-border" },
};

const tabs = [
  { to: "/", label: "Patient Overview" },
  { to: "/predict", label: "Predict Meal Trajectory" },
  { to: "/medications", label: "Medications" },
  { to: "/insights", label: "Model Insights" },
  { to: "/profile", label: "My Profile" },
] as const;

/** User-facing names for the two data modes (internal values stay "live" / "fixture"). */
const MODE_LABEL = { live: "Real data", fixture: "Demo data" } as const;

function SignOutButton() {
  const { isAuthenticated, signOut } = useAppState();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  if (!isAuthenticated) return null;
  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await signOut();
        } finally {
          setBusy(false);
        }
        void navigate({ to: "/auth" });
      }}
      className="inline-flex items-center gap-1.5 rounded-full border bg-card px-2.5 py-1 font-medium text-subtle transition-colors duration-150 hover:border-primary hover:text-foreground disabled:opacity-60"
    >
      <LogOut className="h-3.5 w-3.5" aria-hidden />
      {busy ? "Signing out…" : "Sign out"}
    </button>
  );
}

function StatusCluster() {
  const { mode, setMode, connectivity, dataSource, recheck } = useAppState();
  const c = CONN[connectivity];
  return (
    <div className="flex flex-wrap items-center gap-2 text-[12px]">
      <span className="inline-flex items-center gap-1.5 rounded-full border bg-card px-2.5 py-1 text-subtle" title={mode === "live" ? API_BASE_URL : undefined}>
        <span className={cn("h-2 w-2 rounded-full", c.dot)} />
        {c.label}
        {mode === "live" && connectivity !== "connected" && connectivity !== "checking" && (
          <button onClick={recheck} className="ml-1 font-medium text-primary hover:text-primary-hover">
            Retry
          </button>
        )}
      </span>
      {dataSource && (
        <span className="rounded-full border bg-card px-2.5 py-1 text-subtle">Source: {fmtSource(dataSource)}</span>
      )}
      <div role="group" aria-label="Data mode" className="inline-flex rounded-full border bg-muted p-0.5">
        {(["live", "fixture"] as const).map((m) => (
          <button
            key={m}
            aria-pressed={mode === m}
            onClick={() => setMode(m)}
            className={cn(
              "rounded-full px-2.5 py-0.5 font-medium transition-colors duration-150",
              mode === m ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {MODE_LABEL[m]}
          </button>
        ))}
      </div>
      <SignOutButton />
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { mode } = useAppState();
  return (
    <div className="min-h-screen">
      <div className="border-b bg-accent text-accent-foreground">
        <div className="mx-auto flex max-w-[1520px] items-center gap-2 px-4 py-1.5 text-[12px] md:px-8">
          <Info className="h-3.5 w-3.5 shrink-0" aria-hidden />
          Research prototype using synthetic data; not for clinical decision-making.
        </div>
      </div>
      <header className="sticky top-0 z-20 border-b bg-card/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1520px] flex-col gap-3 px-4 py-3 md:px-8 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-6">
            <Link to="/" className="flex items-center gap-2.5">
              <Logo />
              <div className="leading-tight">
                <div className="text-[17px] font-semibold tracking-tight text-foreground">Glimpse</div>
                <div className="text-[11px] text-muted-foreground">Personalized Metabolic Intelligence</div>
              </div>
            </Link>
            <nav aria-label="Primary" className="-mb-3 hidden gap-1 md:flex">
              {tabs.map((t) => (
                <Link
                  key={t.to}
                  to={t.to}
                  activeOptions={{ exact: true }}
                  className="border-b-2 border-transparent px-3 pb-3 pt-1 text-[14px] font-medium text-subtle transition-colors duration-150 hover:text-foreground"
                  activeProps={{ className: "!border-primary !text-foreground" }}
                >
                  {t.label}
                </Link>
              ))}
            </nav>
          </div>
          <StatusCluster />
          <nav aria-label="Primary mobile" className="flex gap-1 overflow-x-auto md:hidden">
            {tabs.map((t) => (
              <Link
                key={t.to}
                to={t.to}
                activeOptions={{ exact: true }}
                className="whitespace-nowrap rounded-lg px-3 py-2 text-[13px] font-medium text-subtle"
                activeProps={{ className: "!bg-accent !text-accent-foreground" }}
              >
                {t.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>
      {mode === "fixture" && (
        <div className="border-b border-warning-border/40 bg-warning">
          <div className="mx-auto flex max-w-[1520px] items-center gap-2 px-4 py-2 text-[13px] text-warning-foreground md:px-8">
            <FlaskConical className="h-4 w-4 shrink-0" aria-hidden />
            <span>
              <strong className="font-semibold">Demo data.</strong> Everything shown is sample data for demonstration,
              not real observations or model predictions. Switch to Real data to use the Glimpse model.
            </span>
          </div>
        </div>
      )}
      <main className="mx-auto max-w-[1520px] px-4 py-8 md:px-8">{children}</main>
    </div>
  );
}