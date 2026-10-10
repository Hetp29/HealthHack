export const fmtGlucose = (v: number | null | undefined) =>
  v == null ? "—" : Math.round(v).toLocaleString("en-US");

export const fmtNumber = (v: number | null | undefined, digits = 0) =>
  v == null || !Number.isFinite(v)
    ? "—"
    : v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });

export const fmtDateTime = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
  }) + " UTC";
};

export const fmtDate = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
};

const SOURCE_LABELS: Record<string, string> = {
  synthetic_dev: "Synthetic (dev)",
  development_fixture: "Demo data",
  official_synthetic: "Official synthetic dataset",
};
export const fmtSource = (s: string | null | undefined) =>
  !s ? "Unknown source" : SOURCE_LABELS[s] ?? s.replace(/_/g, " ");

const FEATURE_LABELS: Record<string, string> = {
  carbs_g: "Carbohydrates",
  protein_g: "Protein",
  fat_g: "Fat",
  fiber_g: "Fiber",
  calories_kcal: "Calories",
};
export const fmtFeature = (f: string) => FEATURE_LABELS[f] ?? f.replace(/_/g, " ");

/** datetime-local value (interpreted as UTC) <-> RFC3339 */
export const isoToLocalInput = (iso: string) => iso.slice(0, 16);
export const localInputToIso = (v: string) => (v ? `${v}:00Z` : "");
