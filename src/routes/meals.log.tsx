import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  Camera,
  ImagePlus,
  PencilLine,
  ScanSearch,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  EmptyState,
  ErrorState,
  Field,
  Notice,
  PageHeader,
  Panel,
  Pill,
  SkeletonLines,
  Spinner,
  inputClass,
} from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import {
  MacroForm,
  draftToMacros,
  emptyMacros,
  macroErrors,
  macrosToDraft,
  type MacroDraft,
} from "@/features/MacroForm";
import * as api from "@/lib/api";
import { useCgmStatus, useMeal, useRefreshMeals } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { formatSpan, toInstant } from "@/lib/time";
import type { Meal, MealEstimate } from "@/lib/types";
import { useGuardedMutation } from "@/lib/useGuardedMutation";

export const Route = createFileRoute("/meals/log")({
  component: LogMealPage,
  validateSearch: (search: Record<string, unknown>): { meal?: string } =>
    typeof search["meal"] === "string" ? { meal: search["meal"] } : {},
});

const MAX_BYTES = 10 * 1024 * 1024;
const ACCEPTED = ["image/jpeg", "image/png", "image/webp"];

function checkPhoto(file: File): string | null {
  if (/hei[cf]/i.test(file.type) || /\.hei[cf]$/i.test(file.name)) {
    return "HEIC photos aren't supported yet. Share or export the photo as a JPEG and try again.";
  }
  if (!ACCEPTED.includes(file.type)) return "Choose a JPEG, PNG or WebP photo.";
  if (file.size > MAX_BYTES)
    return `That photo is ${(file.size / 1048576).toFixed(1)} MB. Choose one under 10 MB.`;
  if (file.size === 0) return "That file is empty. Choose a different photo.";
  return null;
}

const localInputValue = (date: Date) => {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
};

/** A forecast that failed after the meal itself was saved. */
class ForecastFailed extends Error {
  constructor(
    public reason: unknown,
    public mealId: string,
  ) {
    super("forecast failed");
  }
}

function LogMealPage() {
  const session = useSession();
  const { clock, isDemoAccount } = session;
  const search = Route.useSearch();
  const navigate = useNavigate();
  const refreshMeals = useRefreshMeals();
  const cgm = useCgmStatus();
  const resumed = useMeal(search.meal);

  const [mode, setMode] = useState<"photo" | "manual" | null>(null);
  const [meal, setMeal] = useState<Meal | null>(null);
  const [estimate, setEstimate] = useState<MealEstimate | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [photoProblem, setPhotoProblem] = useState<string | null>(null);
  const [macros, setMacros] = useState<MacroDraft>(emptyMacros);
  const [showErrors, setShowErrors] = useState(false);
  const [eatenAt, setEatenAt] = useState(() => localInputValue(new Date()));
  const [phase, setPhase] = useState<"saving" | "forecasting">("saving");
  const confirmedAs = useRef<string | null>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const libraryInput = useRef<HTMLInputElement>(null);
  const analysisAvailable = session.status?.meal_analysis.configured === true;

  // Picking up a meal that was started earlier.
  const resumedMeal = resumed.data;
  useEffect(() => {
    if (!resumedMeal || meal) return;
    if (resumedMeal.status === "confirmed") {
      void navigate({
        to: "/meals/$mealId",
        params: { mealId: resumedMeal.meal_id },
        replace: true,
      });
      return;
    }
    setMeal(resumedMeal);
    setMode(resumedMeal.photo ? "photo" : "manual");
    if (resumedMeal.estimate) {
      setEstimate(resumedMeal.estimate);
      if (resumedMeal.estimate.totals) setMacros(macrosToDraft(resumedMeal.estimate.totals));
    }
    if (resumedMeal.photo) {
      let url: string | null = null;
      void api
        .getMealImage(resumedMeal.meal_id)
        .then((blob) => {
          url = URL.createObjectURL(blob);
          setPreview(url);
        })
        .catch(() => undefined);
      return () => {
        if (url) URL.revokeObjectURL(url);
      };
    }
    return undefined;
  }, [resumedMeal, meal, navigate]);

  const occurredAt = () => (isDemoAccount ? toInstant(clock.now()) : toInstant(new Date(eatenAt)));
  const errors = useMemo(() => macroErrors(macros), [macros]);
  const eatenProblem =
    !isDemoAccount &&
    (Number.isNaN(new Date(eatenAt).getTime())
      ? "Enter when you ate."
      : new Date(eatenAt).getTime() > Date.now() + 60_000
        ? "Choose a time that isn't in the future."
        : null);

  const choosePhoto = (chosen: File | null) => {
    if (!chosen) return;
    const problem = checkPhoto(chosen);
    setPhotoProblem(problem);
    if (problem) return;
    if (preview) URL.revokeObjectURL(preview);
    setFile(chosen);
    setPreview(URL.createObjectURL(chosen));
    setMode("photo");
    analyze.reset();
  };

  /**
   * Uploads the photo once, then asks for one analysis. Analysis is a paid call, so it
   * only ever runs from this button: never automatically and never as a silent retry.
   */
  const analyze = useGuardedMutation({
    retry: false,
    mutationFn: async (withAnalysis: boolean) => {
      let current = meal;
      if (!current) {
        current = await api.uploadMealPhoto(file!, occurredAt(), file!.type);
        setMeal(current);
      }
      if (!withAnalysis) return null;
      return (await api.analyzeMeal(current.meal_id)).estimate;
    },
    onSuccess: (result) => {
      if (result) {
        setEstimate(result);
        if (result.totals) setMacros(macrosToDraft(result.totals));
      }
      void refreshMeals();
    },
  });

  const finish = useGuardedMutation({
    retry: false,
    mutationFn: async () => {
      const values = draftToMacros(macros);
      const signature = JSON.stringify(values);
      let current = meal;
      setPhase("saving");
      if (!current) {
        current = await api.createMeal({ occurred_at: occurredAt(), macros: values });
      } else if (confirmedAs.current !== signature) {
        current = await api.confirmMeal(current.meal_id, values);
      }
      confirmedAs.current = signature;
      setMeal(current);
      setPhase("forecasting");
      try {
        await api.predictMeal(current.meal_id);
      } catch (error) {
        throw new ForecastFailed(error, current.meal_id);
      }
      return current.meal_id;
    },
    onSuccess: async (mealId) => {
      await refreshMeals();
      void navigate({ to: "/meals/$mealId", params: { mealId } });
    },
    onError: () => void refreshMeals(),
  });

  const submit = () => {
    setShowErrors(true);
    if (Object.keys(errors).length || eatenProblem) {
      document.getElementById(`macro-${Object.keys(errors)[0] ?? "carbs_g"}`)?.focus();
      return;
    }
    finish.mutate();
  };

  const startOver = () => {
    if (preview) URL.revokeObjectURL(preview);
    setMode(null);
    setMeal(null);
    setEstimate(null);
    setFile(null);
    setPreview(null);
    setMacros(emptyMacros());
    setShowErrors(false);
    setPhotoProblem(null);
    confirmedAs.current = null;
    analyze.reset();
    finish.reset();
    if (search.meal) void navigate({ to: "/meals/log", search: {}, replace: true });
  };

  if (!session.processingAllowed) {
    return (
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
          Logging a meal needs permission to process your meals and glucose data.
        </EmptyState>
      </Panel>
    );
  }
  if (search.meal && resumed.isError)
    return (
      <Panel>
        <ErrorState error={resumed.error} onRetry={() => void resumed.refetch()} />
      </Panel>
    );
  if (search.meal && !meal)
    return (
      <Panel>
        <SkeletonLines lines={5} />
      </Panel>
    );

  const forecastFailure = finish.error instanceof ForecastFailed ? finish.error : null;
  const reviewing =
    mode === "manual" ||
    estimate !== null ||
    (mode === "photo" &&
      meal !== null &&
      !analyze.isPending &&
      (analyze.isError || analyze.data === null));
  const cgmAge = cgm.data?.latest_age_minutes ?? null;
  const busy = analyze.isPending || finish.isPending;

  return (
    <>
      <PageHeader
        title="Log a meal"
        eyebrow={
          <Link to="/meals" className="inline-flex items-center gap-1 hover:text-foreground">
            <ArrowLeft className="h-3.5 w-3.5" /> Meals
          </Link>
        }
        description="Add the meal, confirm its nutrition, and GLIMPSE forecasts your glucose for the two hours after it."
      />

      <div className="mx-auto max-w-3xl space-y-5">
        {isDemoAccount && (
          <Notice title="Adding your own meal to a replay">
            This account replays a recorded glucose series. A meal you add here is logged at the
            current recording time, {clock.dateTime(clock.now().toISOString())}, and gets a real
            forecast from the recorded glucose. It can't be compared with an outcome, because the
            recording reflects what the participant actually ate.
          </Notice>
        )}
        {cgm.data && !cgm.data.forecast_ready && (
          <Notice
            tone="warning"
            title="A forecast may not be possible yet"
            action={
              <Button asChild size="sm" variant="outline">
                <Link to="/health" search={{ tab: "glucose" }}>
                  Add glucose data
                </Link>
              </Button>
            }
          >
            {cgm.data.freshness === "no_data"
              ? "There are no glucose readings on your account."
              : `Your latest glucose reading is ${cgmAge !== null ? formatSpan(cgmAge) : "too"} old.`}{" "}
            {cgm.data.forecast_requirement} You can still save the meal.
          </Notice>
        )}

        {mode === null && (
          <Panel title="How would you like to add it?">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl border border-border-strong p-5">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent text-accent-foreground">
                  <Camera className="h-5 w-5" />
                </span>
                <h3 className="mt-3 font-semibold">Use a photo</h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {analysisAvailable
                    ? "GLIMPSE estimates the nutrition from your photo. You check and correct it before anything is forecast."
                    : "Photo analysis isn't switched on for this server. You can still attach a photo and enter the values yourself."}
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button onClick={() => cameraInput.current?.click()} className="h-10">
                    <Camera className="h-4 w-4" /> Take a photo
                  </Button>
                  <Button
                    onClick={() => libraryInput.current?.click()}
                    variant="outline"
                    className="h-10"
                  >
                    <ImagePlus className="h-4 w-4" /> Choose a photo
                  </Button>
                </div>
                <input
                  ref={cameraInput}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  capture="environment"
                  className="sr-only"
                  tabIndex={-1}
                  aria-label="Take a photo of your meal"
                  onChange={(e) => {
                    choosePhoto(e.target.files?.[0] ?? null);
                    e.target.value = "";
                  }}
                />
                <input
                  ref={libraryInput}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  className="sr-only"
                  tabIndex={-1}
                  data-testid="photo-input"
                  aria-label="Choose a photo of your meal"
                  onChange={(e) => {
                    choosePhoto(e.target.files?.[0] ?? null);
                    e.target.value = "";
                  }}
                />
                <p className="mt-3 text-[13px] text-muted-foreground">
                  JPEG, PNG or WebP, up to 10 MB.
                </p>
                {photoProblem && (
                  <p role="alert" className="mt-2 text-sm font-medium text-destructive">
                    {photoProblem}
                  </p>
                )}
              </div>
              <div className="rounded-xl border border-border-strong p-5">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-muted text-foreground">
                  <PencilLine className="h-5 w-5" />
                </span>
                <h3 className="mt-3 font-semibold">Enter it yourself</h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  Type the carbohydrates, protein, fat, fibre and calories from a label or your own
                  estimate.
                </p>
                <Button
                  variant="outline"
                  className="mt-4 h-10"
                  onClick={() => setMode("manual")}
                  data-testid="manual-entry"
                >
                  <PencilLine className="h-4 w-4" /> Enter nutrition
                </Button>
              </div>
            </div>
          </Panel>
        )}

        {mode === "photo" && (
          <Panel
            title="Your photo"
            action={
              !busy && (
                <Button variant="ghost" size="sm" onClick={startOver}>
                  Start over
                </Button>
              )
            }
          >
            <div className="grid gap-5 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
              <div className="self-start overflow-hidden rounded-xl border border-border bg-muted">
                {preview ? (
                  <img
                    src={preview}
                    alt="The meal photo you chose"
                    className="aspect-square w-full object-cover"
                  />
                ) : (
                  <div className="aspect-square animate-pulse" />
                )}
              </div>
              <div className="min-w-0">
                {!estimate && !analyze.isPending && !analyze.isError && analyze.data !== null && (
                  <>
                    <p className="text-sm text-muted-foreground">
                      {analysisAvailable
                        ? "GLIMPSE sends this photo, and nothing else about you, to an AI model that estimates what's on the plate. It usually takes under ten seconds."
                        : "Photo analysis isn't switched on for this server, so the photo is saved with the meal and you enter the values."}
                    </p>
                    <div className="mt-4 flex flex-wrap gap-2">
                      {analysisAvailable && (
                        <Button
                          className="h-11"
                          onClick={() => analyze.mutate(true)}
                          data-testid="analyze-photo"
                        >
                          <ScanSearch className="h-4 w-4" /> Analyze this photo
                        </Button>
                      )}
                      <Button
                        variant="outline"
                        className="h-11"
                        onClick={() => analyze.mutate(false)}
                      >
                        Save photo, enter values myself
                      </Button>
                    </div>
                  </>
                )}
                {analyze.isPending && (
                  <div role="status" aria-live="polite">
                    <div className="flex items-center gap-2 font-medium">
                      <Spinner className="text-primary" />
                      {meal
                        ? analyze.variables
                          ? "Estimating the nutrition…"
                          : "Saving…"
                        : "Uploading your photo…"}
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">
                      Please keep this page open.
                    </p>
                    <SkeletonLines lines={4} className="mt-4" />
                  </div>
                )}
                {analyze.isError && (
                  <Notice
                    tone="danger"
                    title={meal ? "The photo couldn't be analyzed" : "The photo wasn't uploaded"}
                    action={
                      <div className="flex flex-wrap gap-2">
                        {(!meal ||
                          (analysisAvailable &&
                            !api.isCode(
                              analyze.error,
                              "vision_not_configured",
                              "analysis_limit_reached",
                            ))) && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => analyze.mutate(analysisAvailable)}
                          >
                            Try again
                          </Button>
                        )}
                        {!meal && (
                          <Button size="sm" variant="outline" onClick={() => setMode("manual")}>
                            Enter values without the photo
                          </Button>
                        )}
                      </div>
                    }
                  >
                    {api.describeError(analyze.error)}
                    {meal &&
                      " Your photo is saved with the meal. Nothing was charged twice: analysis only runs when you ask."}
                  </Notice>
                )}
                {estimate && <EstimateSummary estimate={estimate} />}
              </div>
            </div>
          </Panel>
        )}

        {reviewing && (
          <Panel
            title={estimate?.totals ? "Check the nutrition" : "Nutrition"}
            description={
              estimate?.totals
                ? "These are estimates. Correct anything that looks wrong before you confirm."
                : "Enter all five values. Use 0 where there is none."
            }
            action={
              mode === "manual" && !meal && !busy ? (
                <Button variant="ghost" size="sm" onClick={startOver}>
                  Start over
                </Button>
              ) : undefined
            }
          >
            {!isDemoAccount && !meal && (
              <Field
                id="eaten-at"
                label="When did you eat?"
                error={showErrors ? eatenProblem || null : null}
                className="mb-5 max-w-xs"
                hint="A forecast needs a glucose reading from the 15 minutes before this time."
              >
                <input
                  id="eaten-at"
                  type="datetime-local"
                  className={inputClass}
                  value={eatenAt}
                  max={localInputValue(new Date())}
                  onChange={(e) => setEatenAt(e.target.value)}
                />
              </Field>
            )}
            <MacroForm
              draft={macros}
              onChange={setMacros}
              errors={showErrors ? errors : {}}
              ranges={estimate?.ranges ?? null}
              disabled={finish.isPending}
            />

            {forecastFailure && (
              <Notice
                tone="warning"
                className="mt-5"
                title="Your meal is saved, but it couldn't be forecast"
                action={
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" onClick={() => finish.mutate()}>
                      Try the forecast again
                    </Button>
                    <Button size="sm" variant="outline" asChild>
                      <Link to="/meals/$mealId" params={{ mealId: forecastFailure.mealId }}>
                        View the meal
                      </Link>
                    </Button>
                  </div>
                }
              >
                {api.describeError(forecastFailure.reason)}
              </Notice>
            )}
            {finish.isError && !forecastFailure && (
              <Notice tone="danger" className="mt-5" title="The meal wasn't saved">
                {api.describeError(finish.error)}
              </Notice>
            )}

            <div className="mt-6 flex flex-wrap items-center gap-3">
              <Button
                className="h-11 text-[15px]"
                onClick={submit}
                disabled={busy}
                data-testid="confirm-forecast"
              >
                {finish.isPending ? <Spinner /> : <Sparkles className="h-4 w-4" />}
                {finish.isPending
                  ? phase === "saving"
                    ? "Saving your meal…"
                    : "Calculating your forecast…"
                  : "Confirm and forecast"}
              </Button>
              <p className="text-[13px] text-muted-foreground">
                Nothing is forecast until you confirm these values.
              </p>
            </div>
          </Panel>
        )}
      </div>
    </>
  );
}

function EstimateSummary({ estimate }: { estimate: MealEstimate }) {
  if (estimate.status === "not_food") {
    return (
      <Notice tone="warning" title="This doesn't look like a meal">
        {estimate.not_food_reason ?? "The photo couldn't be read as food."} You can enter the
        nutrition yourself below.
      </Notice>
    );
  }
  const checks = [...estimate.ambiguities, ...estimate.clarification_questions];
  return (
    <div data-testid="estimate">
      <div className="flex flex-wrap items-center gap-1.5">
        <Pill tone="amber" icon={Sparkles}>
          AI estimate
        </Pill>
        {estimate.overall_confidence && (
          <Pill tone="neutral">
            {estimate.overall_confidence[0]!.toUpperCase() + estimate.overall_confidence.slice(1)}{" "}
            confidence
          </Pill>
        )}
      </div>
      <ul className="mt-3 divide-y divide-border text-sm">
        {estimate.items.map((item, index) => (
          <li key={`${item.name}-${index}`} className="py-2">
            <div className="flex items-baseline justify-between gap-3">
              <span className="font-medium">{item.name}</span>
              {item.estimated_weight_g !== null && (
                <span className="tabular shrink-0 text-muted-foreground">
                  about {Math.round(item.estimated_weight_g)} g
                </span>
              )}
            </div>
            <div className="text-[13px] text-muted-foreground">
              {item.portion_description}
              {item.visibility !== "visible" &&
                ` · ${item.visibility === "assumed_hidden" ? "assumed, not visible" : "partly visible"}`}
            </div>
          </li>
        ))}
      </ul>
      {estimate.assumptions.length > 0 && (
        <div className="mt-3 text-[13px] text-muted-foreground">
          <div className="font-medium text-foreground">Assumptions made</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {estimate.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      )}
      {checks.length > 0 && (
        <div className="mt-3 text-[13px] text-muted-foreground">
          <div className="font-medium text-foreground">Worth checking</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {checks.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
