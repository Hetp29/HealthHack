import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Apple } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAppState } from "@/state/AppState";

export const Route = createFileRoute("/connect-devices")({ component: ConnectDevicesPage });
type Status = "Not connected" | "Connected" | "Demo / Fixture Data" | "Permissions Denied";

function ConnectDevicesPage() {
  const navigate = useNavigate();
  const { completeOnboarding } = useAppState();
  const [statuses, setStatuses] = useState<Record<string, Status>>({});
  const connect = (name: string) => {
    setStatuses((current) => ({ ...current, [name]: "Connected" }));
    if (name === "Apple Health") {
      completeOnboarding();
      void navigate({ to: "/" });
    }
  };
  const devices: readonly [string, string, string][] = [
    ["Apple Health", "Connect with Apple Health", "Native bridge sync / OAuth mock"],
    ["CGM", "Connect Continuous Glucose Monitor (CGM)", "Dexcom, Libre, or CSV import"],
    ["Wearable", "Connect Smartwatch / Wearable", "Oura, Garmin, or Fitbit"],
  ];

  return (
    <main className="mx-auto max-w-3xl px-4 py-12">
      <p className="text-sm font-medium text-primary">STEP 2 OF 3</p>
      <h1 className="mt-2 text-3xl font-semibold">Connect your health data</h1>
      <p className="mt-2 text-muted-foreground">Choose what you want to share. You can change these choices later.</p>
      <div className="mt-8 grid gap-4">
        {devices.map(([key, label, detail]) => (
          <Card key={key}><CardHeader><CardTitle className="flex items-center justify-between text-lg">{key === "Apple Health" ? <span className="flex items-center gap-2"><Apple className="h-5 w-5" aria-hidden />{label}</span> : label}<span className="rounded-full border px-2 py-1 text-xs font-normal">{statuses[key] ?? "Not connected"}</span></CardTitle></CardHeader><CardContent><p className="mb-4 text-sm text-muted-foreground">{detail}</p><Button onClick={() => connect(key)}>{key === "Apple Health" ? "Connect with Apple Health" : "Authorize"}</Button></CardContent></Card>
        ))}
      </div>
      <div className="mt-8 flex flex-wrap gap-3">
        <Button onClick={() => void navigate({ to: "/onboarding" })}>Skip for Now / Continue to Onboarding</Button>
        <Button variant="outline" onClick={() => void navigate({ to: "/onboarding" })}>Continue</Button>
      </div>
    </main>
  );
}
