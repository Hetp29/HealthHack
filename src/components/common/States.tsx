import { AlertCircle, Inbox, RotateCw } from "lucide-react";
import type { ReactNode } from "react";
import { describeError } from "@/services/apiClient";
import { cn } from "@/lib/utils";

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn("card-surface p-5 md:p-6", className)}>{children}</section>;
}

export function CardTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-[17px] font-semibold tracking-tight text-foreground">{children}</h2>
      {aside}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  unit,
  hint,
  loading,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: ReactNode;
  loading?: boolean;
}) {
  return (
    <div className="card-surface p-5">
      <div className="text-[13px] font-medium text-subtle">{label}</div>
      {loading ? (
        <div className="mt-3 h-8 w-24 animate-pulse rounded-md bg-muted" />
      ) : (
        <div className="mt-2 flex items-baseline gap-1.5">
          <span className="tabular text-[28px] font-semibold leading-none tracking-tight text-foreground">{value}</span>
          {unit && <span className="text-[13px] text-muted-foreground">{unit}</span>}
        </div>
      )}
      {hint && <div className="mt-2 text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-lg bg-muted", className)} />;
}

export function ErrorState({ error, onRetry, title = "Could not load data" }: { error: unknown; onRetry?: () => void; title?: string }) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 px-4 py-10 text-center">
      <AlertCircle className="h-6 w-6 text-destructive" aria-hidden />
      <div>
        <div className="font-medium text-foreground">{title}</div>
        <div className="mt-1 max-w-md text-[13px] text-subtle">{describeError(error)}</div>
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-[13px] font-medium text-foreground transition-colors duration-150 hover:bg-muted"
        >
          <RotateCw className="h-3.5 w-3.5" /> Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ icon, title, body }: { icon?: ReactNode; title: string; body?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      <div className="text-muted-foreground">{icon ?? <Inbox className="h-6 w-6" aria-hidden />}</div>
      <div className="font-medium text-foreground">{title}</div>
      {body && <div className="max-w-sm text-[13px] text-subtle">{body}</div>}
    </div>
  );
}

export function PageHeader({ title, subtitle, aside }: { title: string; subtitle: string; aside?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
      <div>
        <h1 className="text-[28px] font-semibold tracking-tight text-foreground md:text-[30px]">{title}</h1>
        <p className="mt-1 text-[14px] text-subtle">{subtitle}</p>
      </div>
      {aside}
    </div>
  );
}
