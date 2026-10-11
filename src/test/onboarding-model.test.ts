import { describe, expect, it } from "vitest";

import { answersFor, draftFrom, validate, type StepId } from "@/features/onboardingModel";

/** The question ids the server accepts (`backend/app/v2/routers/onboarding.py`). Any other id is a 422. */
const SERVER_QUESTION_IDS = new Set([
  "date_of_birth",
  "sex_assigned_at_birth",
  "height",
  "weight",
  "primary_goal",
  "diabetes_status",
  "diagnosis_year",
  "hba1c",
  "fasting_glucose",
  "clinician_plan",
  "monitoring_method",
  "cgm_brand",
  "cgm_connect_preference",
  "cgm_usage_duration",
  "takes_glucose_medication",
  "medication_names",
  "medication_schedule",
  "uses_insulin",
  "wants_medication_reminders",
  "discontinued_medications",
  "meals_per_day",
  "meal_time_ranges",
  "dietary_pattern",
  "dietary_restrictions",
  "meal_logging_method",
  "uses_wearable",
  "wearable_platform",
  "wearable_connect_preference",
  "wearable_permissions",
  "wants_spike_alerts",
  "alert_threshold",
  "alert_channels",
  "quiet_hours",
]);
const STEPS: StepId[] = [
  "welcome",
  "consent",
  "about",
  "health",
  "devices",
  "routine",
  "alerts",
  "review",
];
const empty = () => draftFrom({}, "Avery", "mg/dL");

describe("onboarding payloads", () => {
  it("only ever sends question ids the server recognises", () => {
    for (const step of STEPS) {
      for (const id of Object.keys(answersFor(step, empty())))
        expect(SERVER_QUESTION_IDS.has(id), `${step}:${id}`).toBe(true);
    }
  });

  it("sends measurements as exactly {value, unit}, or 'unknown', or null", () => {
    const draft = {
      ...empty(),
      height: "172",
      weight: "",
      hba1c: "",
      hba1c_unknown: true,
      fasting_glucose: "5.4",
      fasting_glucose_unit: "mmol/L" as const,
    };
    expect(answersFor("about", draft)["height"]).toEqual({ value: 172, unit: "cm" });
    expect(answersFor("about", draft)["weight"]).toBeNull();
    expect(answersFor("health", draft)["hba1c"]).toBe("unknown");
    expect(answersFor("health", draft)["fasting_glucose"]).toEqual({ value: 5.4, unit: "mmol/L" });
  });

  it("clears answers hidden by an earlier choice", () => {
    const draft = {
      ...empty(),
      diabetes_status: "none",
      diagnosis_year: "2018",
      monitoring_method: "fingerstick",
      cgm_brand: "dexcom",
    };
    const health = answersFor("health", draft);
    expect(health["diagnosis_year"]).toBeNull();
    expect(health["cgm_brand"]).toBeNull();
    const routine = answersFor("routine", {
      ...empty(),
      takes_glucose_medication: "no",
      medication_names: "Metformin",
    });
    expect(routine["medication_names"]).toBeNull();
  });

  it("never offers a notification channel the server cannot deliver", () => {
    expect(
      answersFor("alerts", { ...empty(), wants_spike_alerts: true, alert_threshold: "160" })[
        "alert_channels"
      ],
    ).toEqual(["in_app"]);
  });

  it("round-trips saved answers back into the form", () => {
    const saved = {
      height: { value: 68, unit: "in" },
      hba1c: "unknown",
      medication_names: ["Metformin", "Other"],
      wants_spike_alerts: true,
      alert_threshold: { value: 9.5, unit: "mmol/L" },
    };
    const draft = draftFrom(saved, "Avery", "mg/dL");
    expect(draft.height).toBe("68");
    expect(draft.height_unit).toBe("in");
    expect(draft.hba1c_unknown).toBe(true);
    expect(draft.medication_names).toBe("Metformin, Other");
    expect(draft.alert_threshold_unit).toBe("mmol/L");
  });
});

describe("onboarding validation", () => {
  it("leaves blank optional answers alone but rejects implausible values", () => {
    expect(validate("about", empty())).toEqual({});
    expect(validate("about", { ...empty(), height: "9999" }).height).toBeTruthy();
    expect(validate("about", { ...empty(), display_name: " " }).display_name).toBeTruthy();
    expect(
      validate("about", { ...empty(), date_of_birth: "2999-01-01" }).date_of_birth,
    ).toBeTruthy();
  });

  it("keeps the alert level inside the server's 100 to 400 mg/dL range", () => {
    const on = { ...empty(), wants_spike_alerts: true };
    expect(validate("alerts", { ...on, alert_threshold: "50" }).alert_threshold).toBeTruthy();
    expect(validate("alerts", { ...on, alert_threshold: "160" })).toEqual({});
    expect(
      validate("alerts", { ...on, alert_threshold: "3", alert_threshold_unit: "mmol/L" })
        .alert_threshold,
    ).toBeTruthy();
    expect(
      validate("alerts", { ...empty(), wants_spike_alerts: false, alert_threshold: "" }),
    ).toEqual({});
  });
});
