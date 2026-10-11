import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { MedicationTabs } from '@/components/medications/MedicationTabs'
import type { DoseEvent, DoseStatus, MedicationRegimen } from '@/types/contracts'

export const Route = createFileRoute('/medications')({
  component: MedicationsPage,
})

function MedicationsPage() {
  const [doseHistory, setDoseHistory] = useState<DoseEvent[]>([
    { dose_id: 'dose-1', regimen_id: 'regimen-1', scheduled_at: new Date().toISOString(), status: 'unknown', actor: 'system' },
  ])
  const regimens: MedicationRegimen[] = [
    { regimen_id: 'regimen-1', user_id: 'local-user', drug_name: 'Sample glucose medication', dose: '1', unit: 'tablet', schedule: 'Daily with breakfast', status: 'active' },
  ]
  const logDose = (doseId: string, status: DoseStatus) => {
    setDoseHistory((current) => current.map((dose) => dose.dose_id === doseId ? { ...dose, status, actual_at: new Date().toISOString(), actor: 'user' } : dose))
  }
  return (
    <div className="container mx-auto py-6">
      <MedicationTabs
        regimens={regimens}
        doseHistory={doseHistory}
        onLogDose={logDose}
      />
    </div>
  )
}