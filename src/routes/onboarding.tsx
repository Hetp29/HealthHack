import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { QuestionnaireForm } from '@/components/onboarding/QuestionnaireForm'
import { useAppState } from '@/state/AppState'
import { patchOnboarding, putConsent } from '@/services/v2Client'

export const Route = createFileRoute('/onboarding')({
  component: OnboardingPage,
})

function OnboardingPage() {
  const navigate = useNavigate()
  const { completeOnboarding } = useAppState()
  return (
    <div className="container mx-auto py-6">
      <QuestionnaireForm
        onComplete={(answers) => {
          void patchOnboarding(answers as unknown as Record<string, unknown>)
            .then(() => putConsent("necessary_processing", "granted"))
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