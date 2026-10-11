import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { QuestionnaireForm } from '@/components/onboarding/QuestionnaireForm'
import { useAppState } from '@/state/AppState'
import { patchOnboarding, putConsent } from '@/services/v2Client'
import type { QuestionnaireAnswers } from '@/types/contracts'

export const Route = createFileRoute('/onboarding')({
  component: OnboardingPage,
})

function toBackendAnswers(answers: QuestionnaireAnswers): Record<string, unknown> {
  const mapped: Record<string, unknown> = {
    date_of_birth: answers.dob,
    sex_assigned_at_birth: answers.sex_assigned_at_birth,
    height: answers.height_cm === undefined ? undefined : { value: answers.height_cm, unit: "cm" },
    weight: answers.weight_kg === undefined ? undefined : { value: answers.weight_kg, unit: "kg" },
    primary_goal: answers.primary_goal,
    diabetes_status: answers.diagnosis_status,
    diagnosis_year: answers.diagnosis_year,
    hba1c: answers.hba1c_value === undefined ? undefined : { value: answers.hba1c_value, unit: answers.hba1c_unit ?? "%" },
    fasting_glucose: answers.fasting_glucose_value === undefined ? undefined : { value: answers.fasting_glucose_value, unit: answers.fasting_glucose_unit ?? "mg/dL" },
    clinician_plan: answers.clinician_plan_summary,
    monitoring_method: answers.monitoring_method,
    cgm_brand: answers.cgm_brand,
    cgm_connect_preference: answers.connect_preference,
    cgm_usage_duration: answers.cgm_usage_duration,
    takes_glucose_medication: answers.takes_glucose_meds,
    medication_names: answers.prescribed_medications?.filter((medication) => medication.name.trim()).map((medication) => medication.name.trim()),
    medication_schedule: answers.prescribed_medications?.filter((medication) => medication.name.trim()).map((medication) => ({
      name: medication.name.trim(),
      dose: medication.dose,
      unit: medication.unit,
      times_per_day: medication.times_per_day,
      since: medication.since,
    })),
    uses_insulin: answers.uses_insulin,
    wants_medication_reminders: answers.wants_medication_reminders,
    meals_per_day: answers.meals_per_day ?? answers.meals?.filter((meal) => meal.name.trim()).length,
    dietary_pattern: answers.dietary_pattern,
    dietary_restrictions: answers.dietary_restrictions,
    meal_logging_method: answers.meal_logging_method,
    uses_wearable: answers.uses_wearable,
    wearable_platform: answers.wearable_brand,
    wearable_connect_preference: answers.wearable_connect_preference,
    wearable_permissions: answers.wearable_version
      ? { device: answers.wearable_version, other: answers.wearable_other }
      : answers.wearable_other,
    wants_spike_alerts: answers.enable_spike_alerts,
    alert_threshold: answers.spike_threshold_value === undefined ? undefined : {
      value: answers.spike_threshold_value,
      unit: answers.spike_threshold_unit ?? "mg/dL",
    },
    alert_channels: answers.alert_channels,
    quiet_hours: answers.quiet_hours_start || answers.quiet_hours_end
      ? { start: answers.quiet_hours_start, end: answers.quiet_hours_end }
      : undefined,
  }
  return Object.fromEntries(Object.entries(mapped).filter(([, value]) => value !== undefined))
}

function OnboardingPage() {
  const navigate = useNavigate()
  const { completeOnboarding } = useAppState()
  return (
    <div className="container mx-auto py-6">
      <QuestionnaireForm
        onComplete={(answers) => {
          void patchOnboarding(toBackendAnswers(answers))
            .then(() => putConsent("necessary_processing", "granted"))
            .then(() => putConsent("personalization_history", answers.optional_personal_history_consent ? "granted" : "withdrawn"))
            .then(() => {
              completeOnboarding()
              void navigate({ to: "/" })
            })
            .catch((error) => {
              window.alert(error instanceof Error ? error.message : "Unable to save onboarding to the v2 backend.")
            })
        }}
      />
    </div>
  )
}