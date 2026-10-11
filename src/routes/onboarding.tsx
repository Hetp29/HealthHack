import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { QuestionnaireForm } from '@/components/onboarding/QuestionnaireForm'
import { useAppState } from '@/state/AppState'

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
          completeOnboarding()
          void navigate({ to: "/" })
        }}
      />
    </div>
  )
}