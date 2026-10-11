import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { useAppState } from "@/state/AppState";
import { getOnboarding, login, putConsent, register } from "@/services/v2Client";

export const Route = createFileRoute("/auth")({ component: AuthPage });

function AuthPage() {
  const navigate = useNavigate();
  const { authenticate, completeOnboarding } = useAppState();
  const [mode, setMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [consented, setConsented] = useState(false);
  const [email, setEmail] = useState("demo@glimpse.example");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const [busy, setBusy] = useState(false);
  const [scenario, setScenario] = useState<"new" | "returning">("new");

  const finish = async () => {
    setBusy(true);
    setError("");
    try {
      if (mode === "sign-in") {
        await login(email, password);
      } else {
        await register(email, password, scenario === "new" ? "Demo User" : "Returning Demo User");
      }
      await putConsent("necessary_processing", "granted");
      authenticate();
      if ((await getOnboarding()).completed) completeOnboarding();
      void navigate({ to: "/connect-devices" });
    } catch (error) {
      setError(error instanceof Error ? error.message : "Unable to authenticate with the v2 backend.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/30 px-4 py-12">
      <Card className="w-full max-w-md">
        <CardHeader>
          <p className="text-sm font-medium text-primary">GLIMPSE</p>
          <CardTitle className="text-2xl">Your metabolic picture, made clearer</CardTitle>
          <p className="text-sm text-muted-foreground">Sign in to continue to your private research prototype.</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 rounded-lg bg-muted p-1">
            {(["sign-in", "sign-up"] as const).map((item) => (
              <button key={item} onClick={() => setMode(item)} className={`rounded-md px-3 py-2 text-sm ${mode === item ? "bg-card font-semibold shadow-sm" : "text-muted-foreground"}`}>
                {item === "sign-in" ? "Sign in" : "Create account"}
              </button>
            ))}
          </div>
          <div className="space-y-2"><Label htmlFor="email">Demo account email</Label><Input id="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required /></div>
          <div className="space-y-2"><Label htmlFor="password">Password</Label><Input id="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /></div>
          {mode === "sign-in" && <div className="grid grid-cols-2 gap-2">
            <Button type="button" variant={scenario === "new" ? "default" : "outline"} onClick={() => { setScenario("new"); setEmail("demo@glimpse.example"); }}>New-user demo</Button>
            <Button type="button" variant={scenario === "returning" ? "default" : "outline"} onClick={() => { setScenario("returning"); setEmail("demo-returning@glimpse.example"); }}>Returning demo</Button>
          </div>}
          <label className="flex items-start gap-2 text-sm text-muted-foreground">
            <Checkbox checked={consented} onCheckedChange={(value) => setConsented(Boolean(value))} />
            <span>I agree to the Terms and Privacy Policy and consent to the processing described below.</span>
          </label>
          <Button className="w-full" disabled={!consented || busy || !email || !password} onClick={() => void finish()}>{busy ? "Connecting…" : mode === "sign-in" ? "Sign in" : "Create account"}</Button>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <p className="text-center text-xs text-muted-foreground">This prototype uses synthetic data and is not for clinical decision-making.</p>
        </CardContent>
      </Card>
    </main>
  );
}
