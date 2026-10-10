import { Link } from "@tanstack/react-router";
import { AlertTriangle, CheckCircle2, TrendingUp } from "lucide-react";
import type { SpikeAssessment } from "@/lib/spikeAlert";
import { cn } from "@/lib/utils";

const STYLE = {
  high: {
    box: "border-destructive/40 bg-destructive/10",
    icon: AlertTriangle,
    iconClass: "text-destructive",
  },
  rising: {
    box: "border-warning-border/60 bg-warning",
    icon: TrendingUp,
    iconClass: "text-warning-foreground",
  },
  steady: { box: "border-primary/30 bg-accent", icon: CheckCircle2, iconClass: "text-primary" },
} as const;

export function SpikeAlert({
  assessment,
  hasProfile,
}: {
  assessment: SpikeAssessment;
  hasProfile: boolean;
}) {
  const s = STYLE[assessment.level];
  const Icon = s.icon;
  return (
    <div
      role={assessment.level === "steady" ? "status" : "alert"}
      className={cn("flex items-start gap-3 rounded-xl border p-4", s.box)}
    >
      <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", s.iconClass)} aria-hidden />
      <div className="text-[13px]">
        <div className="text-[15px] font-semibold text-foreground">{assessment.headline}</div>
        <div className="mt-0.5 text-foreground">{assessment.detail}</div>
        <div className="mt-1.5 text-xs text-muted-foreground">
          Threshold {assessment.threshold_reason}.{" "}
          {!hasProfile && (
            <>
              <Link to="/profile" className="font-medium text-primary hover:text-primary-hover">
                Set up your profile
              </Link>{" "}
              to use the right threshold.{" "}
            </>
          )}
          Informational forecast, not medical advice.
        </div>
      </div>
    </div>
  );
}
