import { useState } from "react";
import { ArrowDownUp } from "lucide-react";
import type { HistoricalMeal } from "@/types/contracts";
import { fmtDateTime, fmtNumber } from "@/lib/formatting";
import { EmptyState } from "@/components/common/States";

const cols: [keyof HistoricalMeal, string][] = [
  ["carbs_g", "Carbs (g)"],
  ["protein_g", "Protein (g)"],
  ["fat_g", "Fat (g)"],
  ["fiber_g", "Fiber (g)"],
  ["calories_kcal", "Calories (kcal)"],
];

export function HistoricalMeals({ meals }: { meals: HistoricalMeal[] }) {
  const [newest, setNewest] = useState(true);
  if (!meals.length)
    return <EmptyState title="No meals recorded" body="No meal entries are available for this participant and period." />;
  const sorted = [...meals].sort((a, b) => (Date.parse(a.timestamp) - Date.parse(b.timestamp)) * (newest ? -1 : 1));
  return (
    <div>
      <button
        onClick={() => setNewest((n) => !n)}
        className="mb-3 inline-flex items-center gap-1.5 rounded-lg border bg-card px-2.5 py-1 text-[12px] font-medium text-subtle transition-colors duration-150 hover:text-foreground"
      >
        <ArrowDownUp className="h-3.5 w-3.5" /> {newest ? "Newest first" : "Oldest first"}
      </button>
      <div className="max-h-[420px] overflow-auto rounded-lg border">
        <table className="w-full min-w-[620px] text-[13px]">
          <thead className="sticky top-0 bg-muted text-left text-[12px] text-subtle">
            <tr>
              <th className="px-3 py-2 font-medium">Time</th>
              {cols.map(([, l]) => (
                <th key={l} className="px-3 py-2 text-right font-medium">{l}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((m) => (
              <tr key={m.timestamp} className="border-t transition-colors hover:bg-accent/60">
                <td className="whitespace-nowrap px-3 py-2 text-foreground">{fmtDateTime(m.timestamp)}</td>
                {cols.map(([k]) => (
                  <td key={k} className="tabular px-3 py-2 text-right text-foreground">
                    {m[k] == null ? <span className="text-muted-foreground" title="Not recorded">—</span> : fmtNumber(m[k] as number)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
