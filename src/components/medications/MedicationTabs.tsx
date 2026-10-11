import React, { useState } from 'react';
import { MedicationRegimen, DoseEvent, DoseStatus } from '@/types/contracts';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface MedicationTabsProps {
  regimens: MedicationRegimen[];
  doseHistory: DoseEvent[];
  onLogDose: (doseId: string, status: DoseStatus) => void;
}

export const MedicationTabs: React.FC<MedicationTabsProps> = ({ regimens, doseHistory, onLogDose }) => {
  return (
    <div className="max-w-3xl mx-auto space-y-4">
      {/* REQUIRED NON-CLINICAL DISCLAIMER */}
      <div className="p-3 bg-amber-50 border-l-4 border-amber-400 text-xs text-amber-800 rounded">
        <p className="font-bold">Informational Notice</p>
        <p>Glucose forecasts, spike notifications, medication logs, and nutritional estimates are strictly informational and do NOT constitute clinical diagnosis, medical advice, or insulin dose instructions.</p>
      </div>

      <Tabs defaultValue="current" className="w-full">
        <TabsList className="grid w-full grid-cols-4">
          <TabsTrigger value="current">Current Regimens</TabsTrigger>
          <TabsTrigger value="reminders">Reminders</TabsTrigger>
          <TabsTrigger value="history">Dose History</TabsTrigger>
          <TabsTrigger value="Information">Information</TabsTrigger>
        </TabsList>

        <TabsContent value="current">
          <Card>
            <CardHeader><CardTitle className="text-lg">Active Medications</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              {regimens.filter(r => r.status === 'active').map((r) => (
                <div key={r.regimen_id} className="flex justify-between items-center p-3 border rounded-lg">
                  <div>
                    <h4 className="font-semibold text-sm">{r.drug_name}</h4>
                    <p className="text-xs text-muted-foreground">{r.dose} {r.unit} • {r.schedule}</p>
                  </div>
                  <Badge variant="outline">Active</Badge>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="reminders">
          <Card>
            <CardHeader><CardTitle className="text-lg">Scheduled Doses</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              {doseHistory.filter(d => d.status === 'unknown').map((d) => (
                <div key={d.dose_id} className="flex justify-between items-center p-3 border rounded-lg">
                  <div>
                    <p className="text-xs text-muted-foreground">Scheduled for: {new Date(d.scheduled_at).toLocaleTimeString()}</p>
                  </div>
                  <div className="flex space-x-2">
                    <Button size="sm" variant="default" onClick={() => onLogDose(d.dose_id, 'taken')}>Taken</Button>
                    <Button size="sm" variant="outline" onClick={() => onLogDose(d.dose_id, 'snoozed')}>Snooze</Button>
                    <Button size="sm" variant="destructive" onClick={() => onLogDose(d.dose_id, 'skipped')}>Skip</Button>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="Information">
          <Card>
            <CardHeader><CardTitle className="text-lg">Medication & Glucose Information</CardTitle></CardHeader>
            <CardContent className="space-y-3 text-sm text-muted-foreground">
              <p>We do not provide individualized medication-effect forecasts. Below is general clinical reference literature regarding common metabolic drug classes.</p>
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="history">
        <Card>
          <CardHeader><CardTitle className="text-lg">Immutable Dose History</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {doseHistory.filter((dose) => dose.status !== "unknown").map((dose) => (
              <div key={dose.dose_id} className="flex justify-between rounded-lg border p-3 text-sm">
                <span>{new Date(dose.actual_at ?? dose.scheduled_at).toLocaleString()}</span>
                <Badge variant="outline">{dose.status}</Badge>
              </div>
            ))}
            {doseHistory.every((dose) => dose.status === "unknown") && <p className="text-sm text-muted-foreground">No completed dose events yet.</p>}
          </CardContent>
        </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
};