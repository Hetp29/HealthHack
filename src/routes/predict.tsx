import { createFileRoute } from "@tanstack/react-router";
import { Calculator, Loader2, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Card, CardTitle, EmptyState, ErrorState, PageHeader } from "@/components/common/States";
import { PredictionResults } from "@/components/glucose/PredictionResults";
import { SpikeAlert } from "@/components/glucose/SpikeAlert";
import { isoToLocalInput, localInputToIso } from "@/lib/formatting";
import { atwaterKcal, MEAL_PRESETS } from "@/lib/mealPresets";
import { loadProfile, type UserProfile } from "@/lib/profile";
import { assessSpike } from "@/lib/spikeAlert";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/apiClient";
import type { MealMacros, PredictResponse } from "@/types/contracts";
import { analyzeMealPhoto, confirmMeal, createMeal, getReplay, predictMeal, uploadMealPhoto } from "@/services/v2Client";
import type { MealEstimate } from "@/services/v2Types";

export const Route = createFileRoute("/predict")({
  head: () => ({ meta: [{ title: "Glimpse — predict" }] }),
  component: PredictPage,
});

type Field = keyof MealMacros;
const FIELDS: { key: Field; label: string; unit: string; max: number }[] = [
  { key: "carbs_g", label: "Carbohydrates", unit: "g", max: 500 },
  { key: "protein_g", label: "Protein", unit: "g", max: 300 },
  { key: "fat_g", label: "Fat", unit: "g", max: 300 },
  { key: "fiber_g", label: "Fiber", unit: "g", max: 150 },
  { key: "calories_kcal", label: "Calories", unit: "kcal", max: 5000 },
];

type Form = Record<Field, string>;
const EMPTY: Form = { carbs_g: "", protein_g: "", fat_g: "", fiber_g: "", calories_kcal: "" };
const toForm = (m: MealMacros): Form =>
  Object.fromEntries(FIELDS.map((f) => [f.key, String(m[f.key])])) as Form;

type Run =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "success"; data: PredictResponse; key: string }
  | { state: "error"; error: unknown };

/** Parse and range-check the form. Returns macros, or a message per invalid field. */
function parse(form: Form): { macros: MealMacros | null; errors: Partial<Record<Field, string>> } {
  const errors: Partial<Record<Field, string>> = {};
  const out = {} as MealMacros;
  for (const f of FIELDS) {
    const raw = form[f.key].trim();
    const v = Number(raw);
    if (raw === "") errors[f.key] = "Required";
    else if (!Number.isFinite(v) || v < 0) errors[f.key] = "Enter 0 or more";
    else if (v > f.max) errors[f.key] = `At most ${f.max}`;
    else out[f.key] = v;
  }
  return { macros: Object.keys(errors).length ? null : out, errors };
}

function PredictPage() {
  const [form, setForm] = useState<Form>(EMPTY);
  const [presetId, setPresetId] = useState<string | null>(null);
  const [mealTime, setMealTime] = useState("");
  const [touched, setTouched] = useState(false);
  const [run, setRun] = useState<Run>({ state: "idle" });
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [photoMealId, setPhotoMealId] = useState<string | null>(null);
  const [photoEstimate, setPhotoEstimate] = useState<MealEstimate | null>(null);
  const [photoState, setPhotoState] = useState<"idle" | "uploading" | "analyzing" | "ready" | "confirming" | "error">("idle");
  const [photoError, setPhotoError] = useState("");
  const ctrl = useRef<AbortController | null>(null);

  useEffect(() => setProfile(loadProfile()), []);
  useEffect(() => () => ctrl.current?.abort(), []);

  // Use the replay clock so hand-entered demo meals share the backend's simulated timeline.
  useEffect(() => {
    ctrl.current?.abort();
    setRun({ state: "idle" });
    getReplay().then((state) => setMealTime(isoToLocalInput(state.position))).catch(() => setMealTime(""));
  }, []);

  const { macros, errors } = parse(form);
  const timeError = mealTime ? null : "Required";
  const requestKey = JSON.stringify({ mealTime, macros });
  const stale = run.state === "success" && run.key !== requestKey;
  const spike = useMemo(
    () => (run.state === "success" ? assessSpike(run.data, profile) : null),
    [run, profile],
  );

  const set = (k: Field, v: string) => {
    setForm((f) => ({ ...f, [k]: v }));
    setPresetId(null);
  };

  const fillCalories = () => {
    const n = (k: Field) => Number(form[k]) || 0;
    set(
      "calories_kcal",
      String(atwaterKcal({ carbs_g: n("carbs_g"), protein_g: n("protein_g"), fat_g: n("fat_g") })),
    );
  };

  const setEstimatedMacros = (totals: MealMacros) => {
    setForm(toForm(totals));
    setPresetId(null);
  };

  const analyzePhoto = async (file: File) => {
    setPhotoError("");
    setPhotoState("uploading");
    try {
      const uploaded = await uploadMealPhoto(file, localInputToIso(mealTime));
      setPhotoMealId(uploaded.meal_id);
      setPhotoState("analyzing");
      const analyzed = await analyzeMealPhoto(uploaded.meal_id);
      setPhotoEstimate(analyzed.estimate);
      setEstimatedMacros(analyzed.estimate.totals);
      setPhotoState("ready");
    } catch (error) {
      setPhotoState("error");
      setPhotoError(error instanceof Error ? error.message : "The meal photo analysis service is unavailable.");
    }
  };

  const confirmPhotoMeal = async () => {
    if (!photoMealId || !photoEstimate) return;
    const parsed = parse(form);
    if (!parsed.macros) {
      setTouched(true);
      return;
    }
    setPhotoState("confirming");
    setPhotoError("");
    try {
      await confirmMeal(photoMealId, parsed.macros);
      const prediction = await predictMeal(photoMealId);
      setRun({ state: "success", data: prediction.forecast, key: JSON.stringify({ mealTime, macros: parsed.macros }) });
      setPhotoState("ready");
    } catch (error) {
      setPhotoState("error");
      setPhotoError(error instanceof Error ? error.message : "The confirmed meal could not be saved.");
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!macros || timeError) return;
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    const key = requestKey;
    setRun({ state: "loading" });
    try {
      const meal = await createMeal(localInputToIso(mealTime), macros);
      const prediction = await predictMeal(meal.meal_id);
      if (!c.signal.aborted) setRun({ state: "success", data: prediction.forecast, key });
    } catch (error) {
      if (error instanceof ApiError && error.kind === "aborted") return;
      if (!c.signal.aborted) setRun({ state: "error", error });
    }
  };

  return (
    <div>
      <div className="mb-6 rounded-xl border border-amber-400/60 bg-amber-50 p-3 text-xs text-amber-900">
        Glucose forecasts, spike notifications, medication logs, and nutritional estimates are strictly informational and do NOT constitute clinical diagnosis, medical advice, or insulin dose instructions.
      </div>

      <div className="rounded-xl border border-primary/25 bg-accent/40 p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[15px] font-semibold text-foreground">Analyze a meal photo</div>
            <p className="mt-1 text-xs text-muted-foreground">OpenAI will estimate the foods and nutrition on the backend. Review and edit every value before confirming.</p>
          </div>
          <Sparkles className="h-5 w-5 shrink-0 text-primary" aria-hidden />
        </div>
        <label className="mt-3 inline-flex cursor-pointer items-center rounded-lg border bg-card px-3 py-2 text-[13px] font-medium text-foreground hover:border-primary">
          {photoState === "uploading" ? "Uploading photo…" : photoState === "analyzing" ? "Analyzing photo…" : "Upload or photograph food"}
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp"
            capture="environment"
            className="sr-only"
            disabled={photoState === "uploading" || photoState === "analyzing" || photoState === "confirming"}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void analyzePhoto(file);
              event.currentTarget.value = "";
            }}
          />
        </label>
        {(photoState === "uploading" || photoState === "analyzing") && <Loader2 className="ml-2 inline h-4 w-4 animate-spin text-primary" aria-label="Analysis in progress" />}
        {photoError && <p role="alert" className="mt-2 text-xs text-destructive">Meal photo analysis unavailable: {photoError}</p>}
      </div>

      {photoEstimate && (
        <div className="rounded-xl border bg-card p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-[15px] font-semibold text-foreground">Review photo estimate</div>
              <p className="mt-1 text-xs text-muted-foreground">These are estimates, not measured nutrition values. Edit the fields below before confirming.</p>
            </div>
            <span className="rounded-full border px-2 py-1 text-xs text-muted-foreground">{photoEstimate.overall_confidence ?? "estimated"} confidence</span>
          </div>
          <ul className="mt-3 space-y-2 text-xs text-muted-foreground">
            {photoEstimate.items.map((item) => <li key={`${item.name}-${item.estimated_weight_g}`}><span className="font-medium text-foreground">{item.name}</span> · {item.portion_description} · {item.confidence} confidence</li>)}
          </ul>
          {photoEstimate.assumptions?.length ? <p className="mt-3 text-xs text-muted-foreground"><strong>Assumptions:</strong> {photoEstimate.assumptions.join(" ")}</p> : null}
          <button type="button" onClick={() => void confirmPhotoMeal()} disabled={photoState === "confirming"} className="mt-4 inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-[13px] font-semibold text-primary-foreground disabled:opacity-60">
            {photoState === "confirming" && <Loader2 className="h-4 w-4 animate-spin" />}
            {photoState === "confirming" ? "Confirming meal…" : "Confirm meal and forecast"}
          </button>
        </div>
      )}
      <PageHeader
        title="Predict meal trajectory"
        subtitle="Enter a meal to create a real v2 forecast on the replay clock."
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(320px,420px)_minmax(0,1fr)]">
        <Card className="h-fit">
          <CardTitle>Meal</CardTitle>
          <form onSubmit={submit} noValidate className="flex flex-col gap-4">
            <div>
              <div className="mb-1.5 text-[12px] font-medium uppercase tracking-wide text-muted-foreground">
                Quick fill
              </div>
              <div className="flex flex-wrap gap-2">
                {MEAL_PRESETS.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    aria-pressed={presetId === p.id}
                    onClick={() => {
                      setForm(toForm(p.macros));
                      setPresetId(p.id);
                    }}
                    className={cn(
                      "rounded-full border px-3 py-1 text-[13px] font-medium transition-colors duration-150",
                      presetId === p.id
                        ? "border-primary bg-accent text-accent-foreground"
                        : "bg-card text-subtle hover:border-primary hover:text-foreground",
                    )}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              <p className="mt-1.5 text-xs text-muted-foreground">
                Median meals from the CGMacros study. Edit any value.
              </p>
            </div>

            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-foreground">Meal time</span>
              <input
                type="datetime-local"
                value={mealTime}
                onChange={(e) => setMealTime(e.target.value)}
                aria-invalid={touched && !!timeError}
                className="h-10 rounded-lg border bg-card px-3 text-[14px] text-foreground"
              />
              {touched && timeError && (
                <span className="text-xs text-destructive">{timeError}</span>
              )}
            </label>

            <div className="grid grid-cols-2 gap-3">
              {FIELDS.map((f) => (
                <label
                  key={f.key}
                  className={cn("flex flex-col gap-1.5", f.key === "calories_kcal" && "col-span-2")}
                >
                  <span className="text-[13px] font-medium text-foreground">
                    {f.label} <span className="font-normal text-muted-foreground">({f.unit})</span>
                  </span>
                  <div className="flex gap-2">
                    <input
                      type="number"
                      inputMode="decimal"
                      min={0}
                      max={f.max}
                      step="any"
                      value={form[f.key]}
                      onChange={(e) => set(f.key, e.target.value)}
                      aria-invalid={touched && !!errors[f.key]}
                      className="tabular h-10 w-full min-w-0 rounded-lg border bg-card px-3 text-[14px] text-foreground aria-[invalid=true]:border-destructive"
                    />
                    {f.key === "calories_kcal" && (
                      <button
                        type="button"
                        onClick={fillCalories}
                        title="Calculate from carbs, protein and fat"
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border bg-card px-3 text-[13px] font-medium text-subtle hover:text-foreground"
                      >
                        <Calculator className="h-3.5 w-3.5" /> From macros
                      </button>
                    )}
                  </div>
                  {touched && errors[f.key] && (
                    <span className="text-xs text-destructive">{errors[f.key]}</span>
                  )}
                </label>
              ))}
            </div>

            <button
              type="submit"
              disabled={run.state === "loading"}
              className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-[14px] font-semibold text-primary-foreground transition-colors duration-150 hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-60"
            >
              {run.state === "loading" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="h-4 w-4" />
              )}
              {run.state === "loading" ? "Forecasting…" : "Forecast glucose"}
            </button>
          </form>
        </Card>

        <div className="flex min-w-0 flex-col gap-4">
          {run.state === "idle" && (
            <Card>
              <EmptyState
                title="No forecast yet"
                body="Pick a quick-fill meal or enter macros, then select Forecast glucose."
              />
            </Card>
          )}
          {run.state === "loading" && (
            <Card>
              <EmptyState
                icon={<Loader2 className="h-6 w-6 animate-spin" />}
                title="Forecasting…"
              />
            </Card>
          )}
          {run.state === "error" && (
            <Card>
              <ErrorState error={run.error} title="Forecast failed" />
            </Card>
          )}
          {run.state === "success" && spike && (
            <>
              {stale && (
                <p className="text-[13px] text-subtle">
                  Inputs changed. Select Forecast glucose to update this result.
                </p>
              )}
              <div className={cn(stale && "opacity-50")}>
                <SpikeAlert assessment={spike} hasProfile={profile !== null} />
              </div>
              <PredictionResults result={run.data} stale={stale} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
