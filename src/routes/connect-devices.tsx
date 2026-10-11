import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Apple, HeartPulse, Loader2, Watch } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/services/apiClient";
import { activateDemoDevice, getDemoDevices } from "@/services/v2Client";
import type { DemoDevices, DemoDeviceProfile } from "@/services/v2Types";

export const Route = createFileRoute("/connect-devices")({ component: ConnectDevicesPage });

const icons = { apple_health: Apple, apple_watch: Watch, fitbit: Watch, whoop: HeartPulse };

function ConnectDevicesPage() {
  const navigate = useNavigate();
  const [devices, setDevices] = useState<DemoDevices | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [demoUnavailable, setDemoUnavailable] = useState(false);

  const refresh = async () => {
    try {
      setDevices(await getDemoDevices());
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 404) setDemoUnavailable(true);
      else setError(reason instanceof Error ? reason.message : "Unable to load demo device status.");
    }
  };
  useEffect(() => { void refresh(); }, []);

  const activate = async (profile: DemoDeviceProfile["profile"]) => {
    setBusy(profile);
    setError("");
    try {
      setDevices(await activateDemoDevice(profile));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to activate simulated data.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-4 py-10">
      <p className="text-sm font-medium text-primary">SAMPLE DATA CONNECTIONS</p>
      <h1 className="mt-2 text-3xl font-semibold">Choose simulated health sources</h1>
      <p className="mt-2 max-w-2xl text-muted-foreground">These controls add generated sample wearable observations to the demo account. No Apple, Fitbit, WHOOP, or other vendor account is contacted.</p>
      {devices && <p className="mt-4 rounded-lg border border-amber-400/60 bg-amber-50 p-3 text-sm text-amber-900">{devices.disclaimer}</p>}
      {error && <p role="alert" className="mt-4 text-sm text-destructive">{error}</p>}
      {demoUnavailable && <p className="mt-6 rounded-lg border p-4 text-sm text-muted-foreground">Demo device controls are unavailable on this server. Continue to the dashboard to use the authenticated account.</p>}
      <div className="mt-6 grid gap-4 md:grid-cols-2">
        {devices?.profiles.map((device) => {
          const Icon = icons[device.profile];
          const active = device.status === "demo_simulated";
          return <Card key={device.profile}><CardHeader><CardTitle className="flex items-center justify-between text-lg"><span className="flex items-center gap-2"><Icon className="h-5 w-5" aria-hidden />{device.display_label}</span><span className="rounded-full border px-2 py-1 text-xs font-normal">{active ? "Simulated sample" : "Inactive"}</span></CardTitle></CardHeader><CardContent><p className="text-sm text-muted-foreground">{device.description}</p><p className="mt-2 text-xs text-muted-foreground">{device.metrics.map((metric) => `${metric.metric}: ${metric.observations_visible}`).join(" · ")}</p><Button className="mt-4" variant={active ? "outline" : "default"} disabled={active || busy !== null} onClick={() => void activate(device.profile)}>{busy === device.profile ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Simulating…</> : active ? "Simulated sample active" : `Simulate ${device.label} connection`}</Button></CardContent></Card>;
        })}
      </div>
      {devices && <Card className="mt-4"><CardHeader><CardTitle className="text-lg">Glucose source</CardTitle></CardHeader><CardContent><p className="font-medium">{devices.glucose_source.label}</p><p className="mt-1 text-sm text-muted-foreground">{devices.glucose_source.description}</p><span className="mt-3 inline-flex rounded-full border px-2 py-1 text-xs">Sample data source · research dataset</span></CardContent></Card>}
      <div className="mt-8 flex flex-wrap gap-3"><Button onClick={() => void navigate({ to: "/" })}>Continue to dashboard</Button><Button variant="outline" onClick={() => void navigate({ to: "/onboarding" })}>Review profile setup</Button></div>
    </div>
  );
}
