import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import {
  Activity,
  Bell,
  ChevronDown,
  History,
  Home,
  LineChart,
  LogOut,
  Pill as PillIcon,
  Plus,
  Settings,
  SlidersHorizontal,
  Utensils,
} from "lucide-react";
import { useState, type ReactNode } from "react";

import { Logo, Pill } from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useAlerts, useDueReminders } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

import { DemoControls } from "./DemoControls";

const DESTINATIONS = [
  { to: "/", label: "Home", icon: Home },
  { to: "/meals", label: "Meals", icon: Utensils },
  { to: "/insights", label: "Insights", icon: LineChart },
  { to: "/health", label: "Health", icon: Activity },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

function isActive(pathname: string, to: string) {
  return to === "/" ? pathname === "/" : pathname === to || pathname.startsWith(`${to}/`);
}

function Notifications() {
  const { clock } = useSession();
  const alerts = useAlerts();
  const due = useDueReminders();
  const [open, setOpen] = useState(false);
  const active = (alerts.data?.alerts ?? []).filter(
    (a) => a.status === "active" && !a.acknowledged_at,
  );
  const reminders = due.data?.due ?? [];
  const count = active.length + reminders.length;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative h-10 w-10"
          aria-label={count ? `Notifications, ${count} need attention` : "Notifications"}
        >
          <Bell className="h-5 w-5" />
          {count > 0 && (
            <span className="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-foreground">
              {count}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(22rem,calc(100vw-2rem))] p-0">
        <div className="border-b border-border px-4 py-3 text-sm font-semibold">Notifications</div>
        <div className="max-h-96 overflow-y-auto">
          {count === 0 && (
            <p className="px-4 py-8 text-center text-sm text-muted-foreground">
              {alerts.isError || due.isError
                ? "Notifications couldn't be loaded."
                : "You're all caught up."}
            </p>
          )}
          {active.map((alert) => (
            <Link
              key={alert.alert_id}
              to="/health"
              search={{ tab: "alerts" }}
              onClick={() => setOpen(false)}
              className="flex gap-3 border-b border-border px-4 py-3 text-sm hover:bg-muted/50"
            >
              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-warning text-warning-foreground">
                <Bell className="h-3.5 w-3.5" />
              </span>
              <span className="min-w-0">
                <span className="block font-medium text-foreground">Forecast alert</span>
                <span className="block text-muted-foreground">{alert.payload.label}</span>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  {clock.dateTime(alert.created_at)}
                </span>
              </span>
            </Link>
          ))}
          {reminders.map((dose) => (
            <Link
              key={dose.dose_id}
              to="/health"
              search={{ tab: "medications" }}
              onClick={() => setOpen(false)}
              className="flex gap-3 border-b border-border px-4 py-3 text-sm hover:bg-muted/50"
            >
              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-accent-foreground">
                <PillIcon className="h-3.5 w-3.5" />
              </span>
              <span className="min-w-0">
                <span className="block font-medium text-foreground">Medication reminder</span>
                <span className="block text-muted-foreground">
                  {dose.regimen?.name ?? "Scheduled dose"}, scheduled{" "}
                  {clock.dateTime(dose.scheduled_at)}
                </span>
              </span>
            </Link>
          ))}
        </div>
        <Link
          to="/health"
          search={{ tab: "alerts" }}
          onClick={() => setOpen(false)}
          className="block px-4 py-3 text-center text-sm font-medium text-primary hover:bg-muted/50"
        >
          Alert history and settings
        </Link>
      </PopoverContent>
    </Popover>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { user, replay, clock, signOut, isDemoAccount } = useSession();
  const [demoOpen, setDemoOpen] = useState(false);
  const name = user?.display_name?.trim() || user?.email || "Account";
  const initials =
    name
      .replace(/\(.*?\)/g, "")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((p) => p[0]?.toUpperCase())
      .join("") || "G";

  return (
    <div className="min-h-screen bg-background lg:pl-64">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-card focus:px-3 focus:py-2 focus:shadow-raised"
      >
        Skip to content
      </a>

      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-border bg-card lg:flex">
        <div className="flex h-16 items-center px-5">
          <Link to="/" aria-label="GLIMPSE home">
            <Logo />
          </Link>
        </div>
        <nav aria-label="Primary" className="flex-1 space-y-1 px-3 py-2">
          {DESTINATIONS.map(({ to, label, icon: Icon }) => {
            const active = isActive(pathname, to);
            return (
              <Link
                key={to}
                to={to}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-11 items-center gap-3 rounded-lg px-3 text-[15px] font-medium transition-colors",
                  active
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <Icon className="h-5 w-5" /> {label}
              </Link>
            );
          })}
        </nav>
        <div className="space-y-3 p-4">
          <Button asChild className="h-11 w-full">
            <Link to="/meals/log">
              <Plus className="h-4 w-4" /> Log a meal
            </Link>
          </Button>
          <p className="px-1 text-xs leading-relaxed text-muted-foreground">
            GLIMPSE is a research prototype. Its forecasts are informational and are not medical
            advice.
          </p>
        </div>
      </aside>

      <header className="sticky top-0 z-20 flex h-16 items-center gap-2 border-b border-border bg-card/90 px-4 backdrop-blur sm:px-6 lg:px-8">
        <Link to="/" className="lg:hidden" aria-label="GLIMPSE home">
          <Logo wordmark={false} />
        </Link>
        <div className="min-w-0 flex-1">
          {isDemoAccount && replay && (
            <button
              type="button"
              onClick={() => setDemoOpen(true)}
              data-testid="provenance"
              className="flex max-w-full items-center gap-2 rounded-full border border-border bg-background py-1 pl-1 pr-3 text-left text-[13px] hover:border-border-strong"
            >
              <Pill tone="indigo" icon={History}>
                Historical replay
              </Pill>
              <span className="truncate text-muted-foreground">
                <span className="hidden sm:inline">Recording time </span>
                {clock.dateTime(replay.position)}
              </span>
            </button>
          )}
        </div>
        {isDemoAccount && (
          <Button
            variant="outline"
            size="sm"
            className="hidden h-9 sm:inline-flex"
            onClick={() => setDemoOpen(true)}
            data-testid="demo-controls-open"
          >
            <SlidersHorizontal className="h-4 w-4" /> Demo controls
          </Button>
        )}
        {isDemoAccount && (
          <Button
            variant="ghost"
            size="icon"
            className="h-10 w-10 sm:hidden"
            aria-label="Demo controls"
            onClick={() => setDemoOpen(true)}
          >
            <SlidersHorizontal className="h-5 w-5" />
          </Button>
        )}
        <Notifications />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="flex h-10 items-center gap-2 rounded-full pl-1 pr-1 hover:bg-muted sm:pr-2"
              aria-label="Account menu"
              data-testid="account-menu"
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
                {initials}
              </span>
              <ChevronDown className="hidden h-4 w-4 text-muted-foreground sm:block" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-64">
            <DropdownMenuLabel>
              <span className="block truncate text-sm font-semibold">{name}</span>
              <span className="block truncate text-xs font-normal text-muted-foreground">
                {user?.email}
              </span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => void navigate({ to: "/settings" })}>
              <Settings className="h-4 w-4" /> Settings
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void signOut()} data-testid="sign-out">
              <LogOut className="h-4 w-4" /> Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>

      <main
        id="main"
        className="mx-auto w-full max-w-6xl px-4 pb-28 pt-6 sm:px-6 lg:px-8 lg:pb-12 lg:pt-8"
      >
        {children}
      </main>

      <nav
        aria-label="Primary"
        className="safe-bottom fixed inset-x-0 bottom-0 z-30 border-t border-border bg-card/95 backdrop-blur lg:hidden"
      >
        <ul className="mx-auto grid max-w-lg grid-cols-5">
          {DESTINATIONS.map(({ to, label, icon: Icon }) => {
            const active = isActive(pathname, to);
            return (
              <li key={to}>
                <Link
                  to={to}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium",
                    active ? "text-primary" : "text-muted-foreground",
                  )}
                >
                  <Icon className="h-5 w-5" /> {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <DemoControls open={demoOpen} onOpenChange={setDemoOpen} />
    </div>
  );
}
