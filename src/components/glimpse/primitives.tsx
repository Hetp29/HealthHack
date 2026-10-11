/** The GLIMPSE building blocks every screen is assembled from. */
import {
  AlertTriangle,
  ChevronDown,
  CloudOff,
  Info,
  Loader2,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, describeError } from "@/lib/api";
import type { DataClass } from "@/lib/types";
import { cn } from "@/lib/utils";

type IconType = ComponentType<{ className?: string }>;

export function Logo({ className, wordmark = true }: { className?: string; wordmark?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5 text-foreground", className)}>
      <svg viewBox="0 0 32 32" className="h-8 w-8 shrink-0" aria-hidden="true">
        <rect width="32" height="32" rx="9" className="fill-primary" />
        <path
          d="M5 20c3.2 0 4-9 7.5-9s3.6 10 7 10 3.3-5 7.5-5"
          fill="none"
          stroke="white"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
        <circle cx="23.8" cy="16.3" r="2.1" fill="white" />
      </svg>
      {wordmark && <span className="text-lg font-semibold tracking-tight">GLIMPSE</span>}
    </span>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1.5 text-sm text-muted-foreground">{eyebrow}</div>}
        <h1 className="text-2xl font-semibold text-foreground sm:text-[28px]">{title}</h1>
        {description && (
          <p className="mt-1.5 max-w-2xl text-[15px] text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Panel({
  title,
  description,
  action,
  children,
  className,
  padded = true,
  id,
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  padded?: boolean;
  id?: string;
}) {
  return (
    <section id={id} className={cn("card-surface min-w-0", padded && "p-5 sm:p-6", className)}>
      {(title || action) && (
        <div
          className={cn(
            "flex flex-wrap items-start justify-between gap-3",
            children ? "mb-4" : "",
            !padded && "px-5 pt-5 sm:px-6 sm:pt-6",
          )}
        >
          <div className="min-w-0">
            {title && <h2 className="text-base font-semibold text-foreground">{title}</h2>}
            {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  unit,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: ReactNode;
  tone?: "predicted" | "observed";
}) {
  return (
    <div className="min-w-0">
      <div className="text-[13px] font-medium text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span
          className={cn(
            "tabular text-2xl font-semibold",
            tone === "predicted" && "text-predicted",
            tone === "observed" && "text-observed",
          )}
        >
          {value}
        </span>
        {unit && <span className="text-sm text-muted-foreground">{unit}</span>}
      </div>
      {hint && <div className="mt-0.5 text-[13px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

const TONES = {
  neutral: "bg-muted text-foreground",
  teal: "bg-predicted-soft text-accent-foreground",
  indigo: "bg-observed-soft text-observed",
  amber: "bg-warning text-warning-foreground",
  red: "bg-destructive-soft text-destructive",
  green: "bg-success-soft text-success",
} as const;
export type Tone = keyof typeof TONES;

export function Pill({
  tone = "neutral",
  icon: Icon,
  children,
  className,
}: {
  tone?: Tone;
  icon?: IconType;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium",
        TONES[tone],
        className,
      )}
    >
      {Icon && <Icon className="h-3 w-3" />}
      {children}
    </span>
  );
}

const SOURCE: Record<string, { label: string; tone: Tone; title: string }> = {
  research_dataset: {
    label: "Historical recording",
    tone: "indigo",
    title: "Recorded in a 2020 research study and replayed here. Not a live sensor.",
  },
  demo: {
    label: "Sample data",
    tone: "amber",
    title: "Generated for the demonstration. Not measured from a person.",
  },
  simulated: {
    label: "Simulated sample",
    tone: "amber",
    title: "Generated sample values. No device or provider was contacted.",
  },
  user_device: {
    label: "Your data",
    tone: "teal",
    title: "Supplied by you or a source you connected.",
  },
};

/** Where a value came from. Shown beside anything that is not the user's own measurement. */
export function SourceBadge({
  source,
  className,
}: {
  source: DataClass | "simulated" | null | undefined;
  className?: string;
}) {
  const entry = source ? SOURCE[source] : undefined;
  if (!entry) return null;
  return (
    <span title={entry.title}>
      <Pill tone={entry.tone} {...(className ? { className } : {})}>
        {entry.label}
      </Pill>
    </span>
  );
}

export function Notice({
  tone = "info",
  title,
  children,
  action,
  className,
}: {
  tone?: "info" | "warning" | "danger" | "success";
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const styles = {
    info: "border-border bg-muted/60 text-foreground",
    warning: "border-warning-border/60 bg-warning text-warning-foreground",
    danger: "border-destructive/30 bg-destructive-soft text-destructive",
    success: "border-success/30 bg-success-soft text-success",
  }[tone];
  const Icon = tone === "info" ? Info : tone === "success" ? Info : AlertTriangle;
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cn("flex gap-3 rounded-xl border p-3.5 text-sm", styles, className)}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && <div className="font-semibold">{title}</div>}
        {children && <div className={cn(title && "mt-0.5", "leading-relaxed")}>{children}</div>}
        {action && <div className="mt-2.5">{action}</div>}
      </div>
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
  compact,
}: {
  icon?: IconType;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={cn("flex flex-col items-center text-center", compact ? "py-6" : "py-10")}>
      {Icon && (
        <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Icon className="h-5 w-5" />
        </span>
      )}
      <h3 className="text-[15px] font-semibold text-foreground">{title}</h3>
      {children && <p className="mt-1 max-w-sm text-sm text-muted-foreground">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  title,
  compact,
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  compact?: boolean;
}) {
  const offline = error instanceof ApiError && error.kind !== "http";
  const consent = error instanceof ApiError && error.code === "consent_required";
  const Icon = offline ? CloudOff : consent ? ShieldAlert : AlertTriangle;
  return (
    <div
      role="alert"
      className={cn("flex flex-col items-center text-center", compact ? "py-6" : "py-10")}
    >
      <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-destructive-soft text-destructive">
        <Icon className="h-5 w-5" />
      </span>
      <h3 className="text-[15px] font-semibold text-foreground">
        {title ??
          (offline ? "Can't reach GLIMPSE" : consent ? "Permission needed" : "This didn't load")}
      </h3>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">{describeError(error)}</p>
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-4" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" /> Try again
        </Button>
      )}
    </div>
  );
}

export function SkeletonLines({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn("space-y-2.5", className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, index) => (
        <div
          key={index}
          className="h-4 animate-pulse rounded bg-muted"
          style={{ width: `${92 - index * 14}%` }}
        />
      ))}
    </div>
  );
}

export function SkeletonBlock({ className }: { className?: string }) {
  return (
    <div
      aria-busy="true"
      aria-label="Loading"
      className={cn("animate-pulse rounded-xl bg-muted", className)}
    />
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin", className)} aria-hidden="true" />;
}

/** One query's three states, handled the same way everywhere. */
export function Async<T>({
  query,
  children,
  skeleton,
  errorTitle,
  compact,
}: {
  query: {
    data: T | undefined;
    isPending: boolean;
    isError: boolean;
    error: unknown;
    refetch: () => unknown;
    fetchStatus?: string;
  };
  children: (data: T) => ReactNode;
  skeleton?: ReactNode;
  errorTitle?: string;
  compact?: boolean;
}) {
  if (query.data !== undefined) return <>{children(query.data)}</>;
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        onRetry={() => void query.refetch()}
        {...(errorTitle ? { title: errorTitle } : {})}
        {...(compact ? { compact } : {})}
      />
    );
  }
  return <>{skeleton ?? <SkeletonLines />}</>;
}

export function Disclosure({
  title,
  children,
  className,
  defaultOpen,
}: {
  title: ReactNode;
  children: ReactNode;
  className?: string;
  defaultOpen?: boolean;
}) {
  return (
    <details
      className={cn("group rounded-xl border border-border bg-card", className)}
      {...(defaultOpen ? { open: true } : {})}
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-xl px-4 py-3 text-sm font-medium text-foreground hover:bg-muted/50 [&::-webkit-details-marker]:hidden">
        <span>{title}</span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      <div className="border-t border-border px-4 py-3.5 text-sm leading-relaxed text-muted-foreground">
        {children}
      </div>
    </details>
  );
}

export function KeyValue({
  items,
  className,
}: {
  items: Array<[string, ReactNode]>;
  className?: string;
}) {
  return (
    <dl className={cn("divide-y divide-border text-sm", className)}>
      {items.map(([key, value]) => (
        <div key={key} className="flex items-baseline justify-between gap-4 py-2.5">
          <dt className="text-muted-foreground">{key}</dt>
          <dd className="min-w-0 text-right font-medium text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A labelled form control with its hint and error wired up for assistive technology. */
export function Field({
  id,
  label,
  hint,
  error,
  optional,
  children,
  className,
}: {
  id: string;
  label: string;
  hint?: ReactNode;
  error?: string | null | undefined;
  optional?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <label
        htmlFor={id}
        className="mb-1.5 flex items-baseline justify-between gap-2 text-sm font-medium text-foreground"
      >
        <span>{label}</span>
        {optional && <span className="text-xs font-normal text-muted-foreground">Optional</span>}
      </label>
      {children}
      {hint && !error && (
        <p id={`${id}-hint`} className="mt-1.5 text-[13px] text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p
          id={`${id}-error`}
          role="alert"
          className="mt-1.5 text-[13px] font-medium text-destructive"
        >
          {error}
        </p>
      )}
    </div>
  );
}

export const inputClass =
  "block h-11 w-full min-w-0 rounded-lg border border-border-strong bg-card px-3 text-[15px] text-foreground shadow-sm placeholder:text-muted-foreground/70 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/25 disabled:cursor-not-allowed disabled:opacity-60 aria-[invalid=true]:border-destructive";

/** A single-choice set of large tap targets. */
export function ChoiceGroup<T extends string>({
  label,
  value,
  onChange,
  options,
  columns = 2,
  name,
}: {
  label: string;
  name: string;
  value: T | null;
  onChange: (value: T) => void;
  options: Array<{ value: T; label: string; description?: string }>;
  columns?: 1 | 2 | 3;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn(
        "grid gap-2",
        columns === 2 && "sm:grid-cols-2",
        columns === 3 && "grid-cols-2 sm:grid-cols-3",
      )}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            data-choice={`${name}:${option.value}`}
            onClick={() => onChange(option.value)}
            className={cn(
              "min-h-11 rounded-lg border px-3.5 py-2.5 text-left text-sm transition-colors",
              selected
                ? "border-primary bg-accent text-accent-foreground ring-1 ring-primary"
                : "border-border-strong bg-card text-foreground hover:border-primary/50 hover:bg-muted/40",
            )}
          >
            <span className="font-medium">{option.label}</span>
            {option.description && (
              <span className="mt-0.5 block text-[13px] text-muted-foreground">
                {option.description}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
