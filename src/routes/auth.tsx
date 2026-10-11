import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { useAppState } from "@/state/AppState";

export const Route = createFileRoute("/auth")({ component: AuthPage });

function AuthPage() {
  const navigate = useNavigate();
  const { authenticate } = useAppState();
  const [mode, setMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [consented, setConsented] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const finish = () => {
    if (username !== "user01" || password !== "pass1234") {
      setError("Use test username user01 and password pass1234.");
      return;
    }
    setError("");
    authenticate();
    void navigate({ to: "/connect-devices" });
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
          <div className="space-y-2"><Label htmlFor="username">Username</Label><Input id="username" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required /></div>
          <div className="space-y-2"><Label htmlFor="password">Password</Label><Input id="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /></div>
          <label className="flex items-start gap-2 text-sm text-muted-foreground">
            <Checkbox checked={consented} onCheckedChange={(value) => setConsented(Boolean(value))} />
            <span>I agree to the Terms and Privacy Policy and consent to the processing described below.</span>
          </label>
          <Button className="w-full" disabled={!consented} onClick={finish}>{mode === "sign-in" ? "Sign in" : "Create account"}</Button>
          <Button variant="outline" className="w-full" disabled={!consented} onClick={finish}>Sign in with Apple</Button>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <p className="text-center text-xs text-muted-foreground">This prototype uses synthetic data and is not for clinical decision-making.</p>
        </CardContent>
      </Card>
    </main>
  );
}
