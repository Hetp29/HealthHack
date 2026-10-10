import { createFileRoute, Link } from "@tanstack/react-router";
import { CheckCircle2, Lock } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Card, CardTitle, PageHeader } from "@/components/common/States";
import { fmtNumber } from "@/lib/formatting";
import {
  bmi,
  clearProfile,
  DEFAULT_PROFILE,
  DIABETES_LABELS,
  loadProfile,
  MEDICATION_LABELS,
  saveProfile,
  type DiabetesStatus,
  type Medication,
  type UserProfile,
} from "@/lib/profile";
import { peakThreshold } from "@/lib/spikeAlert";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/profile")({
  head: () => ({ meta: [{ title: "Glimpse — my profile" }] }),
  component: ProfilePage,
});

const NUMBERS = [
  { key: "age_years", label: "Age", unit: "years", min: 1, max: 120 },
  { key: "height_cm", label: "Height", unit: "cm", min: 50, max: 250 },
  { key: "weight_kg", label: "Weight", unit: "kg", min: 20, max: 350 },
  { key: "a1c_percent", label: "Latest A1c (if known)", unit: "%", min: 3, max: 20 },
] as const;
type NumKey = (typeof NUMBERS)[number]["key"];

const inputClass = "h-10 w-full rounded-lg border bg-card px-3 text-[14px] text-foreground";

function ProfilePage() {
  const [profile, setProfile] = useState<UserProfile>(DEFAULT_PROFILE);
  const [nums, setNums] = useState<Record<NumKey, string>>({
    age_years: "",
    height_cm: "",
    weight_kg: "",
    a1c_percent: "",
  });
  const [saved, setSaved] = useState<"idle" | "saved" | "failed">("idle");
  const [hasSaved, setHasSaved] = useState(false);

  useEffect(() => {
    const p = loadProfile();
    if (!p) return;
    setProfile(p);
    setHasSaved(true);
    setNums({
      age_years: p.age_years?.toString() ?? "",
      height_cm: p.height_cm?.toString() ?? "",
      weight_kg: p.weight_kg?.toString() ?? "",
      a1c_percent: p.a1c_percent?.toString() ?? "",
    });
  }, []);

  const numErrors = Object.fromEntries(
    NUMBERS.map((f) => {
      const raw = nums[f.key].trim();
      if (raw === "") return [f.key, null];
      const v = Number(raw);
      return [
        f.key,
        Number.isFinite(v) && v >= f.min && v <= f.max ? null : `Between ${f.min} and ${f.max}`,
      ];
    }),
  ) as Record<NumKey, string | null>;
  const valid = Object.values(numErrors).every((e) => e === null);

  const draft: UserProfile = {
    ...profile,
    ...(Object.fromEntries(
      NUMBERS.map((f) => [
        f.key,
        nums[f.key].trim() === "" || numErrors[f.key] ? null : Number(nums[f.key]),
      ]),
    ) as Pick<UserProfile, NumKey>),
  };
  const draftBmi = bmi(draft);

  const toggleMed = (m: Medication) => {
    setSaved("idle");
    setProfile((p) => ({
      ...p,
      medications: p.medications.includes(m)
        ? p.medications.filter((x) => x !== m)
        : [...p.medications, m],
    }));
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    const ok = saveProfile(draft);
    setSaved(ok ? "saved" : "failed");
    if (ok) setHasSaved(true);
  };

  const reset = () => {
    clearProfile();
    setProfile(DEFAULT_PROFILE);
    setNums({ age_years: "", height_cm: "", weight_kg: "", a1c_percent: "" });
    setHasSaved(false);
    setSaved("idle");
  };

  return (
    <div>
      <PageHeader
        title="My profile"
        subtitle="A few questions so forecasts and alerts are described for you."
      />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,640px)_minmax(280px,1fr)]">
        <Card>
          <form onSubmit={submit} noValidate className="flex flex-col gap-6">
            <fieldset>
              <legend className="mb-2 text-[15px] font-semibold text-foreground">
                Do you have diabetes?
              </legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {(Object.keys(DIABETES_LABELS) as DiabetesStatus[]).map((d) => (
                  <label
                    key={d}
                    className={cn(
                      "flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2.5 text-[14px] transition-colors duration-150",
                      profile.diabetes === d
                        ? "border-primary bg-accent text-accent-foreground"
                        : "bg-card hover:border-primary",
                    )}
                  >
                    <input
                      type="radio"
                      name="diabetes"
                      value={d}
                      checked={profile.diabetes === d}
                      onChange={() => {
                        setSaved("idle");
                        setProfile((p) => ({ ...p, diabetes: d }));
                      }}
                      className="accent-[var(--primary)]"
                    />
                    {DIABETES_LABELS[d]}
                  </label>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend className="mb-2 text-[15px] font-semibold text-foreground">
                Do you take any of these?
              </legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {(Object.keys(MEDICATION_LABELS) as Medication[]).map((m) => (
                  <label
                    key={m}
                    className="flex cursor-pointer items-center gap-2.5 rounded-lg border bg-card px-3 py-2.5 text-[14px] hover:border-primary"
                  >
                    <input
                      type="checkbox"
                      checked={profile.medications.includes(m)}
                      onChange={() => toggleMed(m)}
                      className="accent-[var(--primary)]"
                    />
                    {MEDICATION_LABELS[m]}
                  </label>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend className="mb-2 text-[15px] font-semibold text-foreground">
                About you <span className="font-normal text-muted-foreground">(optional)</span>
              </legend>
              <div className="grid gap-3 sm:grid-cols-2">
                {NUMBERS.map((f) => (
                  <label key={f.key} className="flex flex-col gap-1.5">
                    <span className="text-[13px] font-medium text-foreground">
                      {f.label}{" "}
                      <span className="font-normal text-muted-foreground">({f.unit})</span>
                    </span>
                    <input
                      type="number"
                      inputMode="decimal"
                      step="any"
                      min={f.min}
                      max={f.max}
                      value={nums[f.key]}
                      onChange={(e) => {
                        setSaved("idle");
                        setNums((n) => ({ ...n, [f.key]: e.target.value }));
                      }}
                      aria-invalid={!!numErrors[f.key]}
                      className={cn(inputClass, "tabular aria-[invalid=true]:border-destructive")}
                    />
                    {numErrors[f.key] && (
                      <span className="text-xs text-destructive">{numErrors[f.key]}</span>
                    )}
                  </label>
                ))}
              </div>
            </fieldset>

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="submit"
                disabled={!valid}
                className="inline-flex h-11 items-center rounded-lg bg-primary px-5 text-[14px] font-semibold text-primary-foreground transition-colors duration-150 hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-60"
              >
                Save profile
              </button>
              {hasSaved && (
                <button
                  type="button"
                  onClick={reset}
                  className="h-11 rounded-lg border bg-card px-4 text-[14px] font-medium text-subtle hover:text-foreground"
                >
                  Clear
                </button>
              )}
              {saved === "saved" && (
                <span
                  role="status"
                  className="inline-flex items-center gap-1.5 text-[13px] text-primary"
                >
                  <CheckCircle2 className="h-4 w-4" /> Saved.{" "}
                  <Link to="/predict" className="font-medium underline">
                    Forecast a meal
                  </Link>
                </span>
              )}
              {saved === "failed" && (
                <span role="alert" className="text-[13px] text-destructive">
                  This browser blocked saving. Your answers apply until you reload.
                </span>
              )}
            </div>
          </form>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <CardTitle>How this is used</CardTitle>
            <ul className="flex flex-col gap-2 text-[13px] text-subtle">
              <li>
                <span className="font-medium text-foreground">Spike alert threshold:</span>{" "}
                {peakThreshold(draft).reason}.
              </li>
              {draftBmi != null && (
                <li>
                  <span className="font-medium text-foreground">BMI:</span> {fmtNumber(draftBmi, 1)}
                </li>
              )}
              <li>Your answers do not change the model's prediction yet.</li>
            </ul>
          </Card>
          <div className="flex items-start gap-2 rounded-xl border bg-card p-4 text-[13px] text-subtle">
            <Lock className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            Stored only in this browser. Nothing here is sent to the server.
          </div>
        </div>
      </div>
    </div>
  );
}
