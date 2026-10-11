import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { MedicationTabs } from "@/components/medications/MedicationTabs";
import { getMedicationDoses, getMedicationReminders, getMedications, recordDose } from "@/services/v2Client";
import type { DoseEvent, DoseStatus, MedicationRegimen } from "@/types/contracts";
import type { V2Dose, V2Regimen } from "@/services/v2Types";

export const Route = createFileRoute("/medications")({ component: MedicationsPage });

const regimenAdapter = (regimen: V2Regimen): MedicationRegimen => ({
  regimen_id: regimen.regimen_id,
  user_id: "authenticated-user",
  drug_name: regimen.name,
  dose: String(regimen.dose.amount),
  unit: regimen.dose.unit,
  schedule: regimen.frequency,
  status: regimen.status === "current" ? "active" : "discontinued",
  ...(regimen.start_date ? { start_date: regimen.start_date } : {}),
  ...(regimen.end_date ? { end_date: regimen.end_date } : {}),
});

const doseAdapter = (dose: V2Dose): DoseEvent => ({
  dose_id: dose.dose_id,
  regimen_id: dose.regimen_id,
  scheduled_at: dose.scheduled_at ?? new Date().toISOString(),
  actual_at: dose.actual_at,
  status: dose.action === "scheduled" ? "unknown" : dose.action,
  actor: dose.actor,
});

function MedicationsPage() {
  const [regimens, setRegimens] = useState<MedicationRegimen[]>([]);
  const [doseHistory, setDoseHistory] = useState<DoseEvent[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const [medicationResult, reminders, doses] = await Promise.all([getMedications(), getMedicationReminders(), getMedicationDoses()]);
      setRegimens(medicationResult.regimens.map(regimenAdapter));
      const merged = [...reminders.due, ...doses.doses].reduce((map, dose) => map.set(dose.dose_id, dose), new Map<string, V2Dose>());
      setDoseHistory([...merged.values()].map(doseAdapter));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load medication data.");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const logDose = async (doseId: string, status: DoseStatus) => {
    const dose = doseHistory.find((item) => item.dose_id === doseId);
    if (!dose || status === "unknown") return;
    try {
      await recordDose({ regimen_id: dose.regimen_id, action: status, scheduled_at: dose.scheduled_at });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to record medication action.");
    }
  };

  return <div className="container mx-auto py-6">{error && <p role="alert" className="mb-4 text-sm text-destructive">{error}</p>}<MedicationTabs regimens={regimens} doseHistory={doseHistory} onLogDose={(doseId, status) => void logDose(doseId, status)} /></div>;
}
