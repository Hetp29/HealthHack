import { useMutation } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Camera, Eye, EyeOff, History, LineChart, ShieldCheck, UserPlus } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";

import { Field, Logo, Notice, Pill, Spinner, inputClass } from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import * as api from "@/lib/api";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/auth")({ component: AuthPage });

/** The demonstration accounts the server seeds. Their password is set by whoever runs the server. */
export const DEMO_ACCOUNTS = [
  {
    email: "demo@glimpse.example",
    title: "New user demonstration",
    body: "Starts with no personal history, so you can watch forecasts become personal as meals are scored.",
  },
  {
    email: "demo-returning@glimpse.example",
    title: "Returning user demonstration",
    body: "Starts with weeks of scored meals already in place, so forecasts use personal history from the first meal.",
  },
] as const;

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const HIGHLIGHTS = [
  {
    icon: Camera,
    title: "Log a meal in seconds",
    body: "Photograph your plate or enter the nutrition yourself. You always confirm the numbers.",
  },
  {
    icon: LineChart,
    title: "See the next two hours",
    body: "A forecast of how your glucose may respond, drawn from your own readings.",
  },
  {
    icon: ShieldCheck,
    title: "Your data, your choice",
    body: "You decide what GLIMPSE may process, and you can change your mind at any time.",
  },
];

function AuthPage() {
  const session = useSession();
  const [mode, setMode] = useState<"sign_in" | "register">("sign_in");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [touched, setTouched] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  const errors = {
    name: mode === "register" && !name.trim() ? "Enter the name you'd like us to use." : null,
    email: !EMAIL.test(email.trim()) ? "Enter a valid email address." : null,
    password: !password
      ? "Enter your password."
      : mode === "register" && password.length < 10
        ? "Use at least 10 characters."
        : null,
  };
  const invalid = Object.values(errors).some(Boolean);

  const submit = useMutation({
    mutationFn: () =>
      mode === "sign_in"
        ? api.login(email.trim(), password)
        : api.register({ email: email.trim(), password, display_name: name.trim() }),
    onSuccess: () => session.signedIn(),
  });

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (!invalid && !submit.isPending) submit.mutate();
  };

  const chooseDemo = (demoEmail: string) => {
    setMode("sign_in");
    setEmail(demoEmail);
    setPassword("");
    submit.reset();
    setTouched(false);
    passwordRef.current?.focus();
  };

  const switchMode = (next: "sign_in" | "register") => {
    setMode(next);
    setTouched(false);
    submit.reset();
  };
  const demoMode = session.status?.demo_mode === true;
  const serverDown = session.status === null && session.phase !== "starting";

  return (
    <div className="min-h-screen bg-background lg:grid lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <aside className="relative hidden overflow-hidden bg-primary p-12 text-primary-foreground lg:flex lg:flex-col lg:justify-between">
        <Logo className="text-primary-foreground [&_rect]:fill-white/15" />
        <div>
          <h1 className="max-w-md text-4xl font-semibold leading-tight">
            Understand how each meal moves your glucose.
          </h1>
          <p className="mt-4 max-w-md text-lg text-primary-foreground/80">
            GLIMPSE turns your meals and glucose readings into a two-hour forecast, then shows you
            how it compared with what happened.
          </p>
          <ul className="mt-10 space-y-6">
            {HIGHLIGHTS.map(({ icon: Icon, title, body }) => (
              <li key={title} className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/12">
                  <Icon className="h-5 w-5" />
                </span>
                <span>
                  <span className="block font-semibold">{title}</span>
                  <span className="block text-primary-foreground/75">{body}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="max-w-md text-sm text-primary-foreground/70">
          A research prototype. Forecasts are informational, their uncertainty is not calibrated,
          and they are not medical advice.
        </p>
      </aside>

      <main className="flex min-h-screen flex-col justify-center px-4 py-10 sm:px-8">
        <div className="mx-auto w-full max-w-md">
          <Logo className="lg:hidden" />
          <h2 className="mt-8 text-2xl font-semibold lg:mt-0">
            {mode === "sign_in" ? "Welcome back" : "Create your account"}
          </h2>
          <p className="mt-1.5 text-muted-foreground lg:hidden">
            {mode === "sign_in"
              ? "Sign in to see your meals and forecasts."
              : "See how each meal may move your glucose over the next two hours."}
          </p>
          <p className="mt-1.5 hidden text-muted-foreground lg:block">
            {mode === "sign_in"
              ? "Sign in to see your meals and forecasts."
              : "It takes about four minutes to set up."}
          </p>

          <div
            role="tablist"
            aria-label="Sign in or create an account"
            className="mt-6 grid grid-cols-2 rounded-xl bg-muted p-1"
          >
            {(["sign_in", "register"] as const).map((value) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={mode === value}
                onClick={() => switchMode(value)}
                data-testid={`tab-${value}`}
                className={cn(
                  "h-10 rounded-lg text-sm font-medium transition-colors",
                  mode === value
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {value === "sign_in" ? "Sign in" : "Create account"}
              </button>
            ))}
          </div>

          {session.endedReason && (
            <Notice tone="warning" className="mt-5">
              Your session ended. Please sign in again.
            </Notice>
          )}
          {serverDown && (
            <Notice
              tone="danger"
              className="mt-5"
              title="Can't reach GLIMPSE"
              action={
                <Button size="sm" variant="outline" onClick={session.retry}>
                  Try again
                </Button>
              }
            >
              The server isn't responding, so signing in won't work yet.
            </Notice>
          )}

          <form onSubmit={onSubmit} noValidate className="mt-5 space-y-4">
            {mode === "register" && (
              <Field id="name" label="Your name" error={touched ? errors.name : null}>
                <input
                  id="name"
                  className={inputClass}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                  maxLength={80}
                  aria-invalid={touched && !!errors.name}
                />
              </Field>
            )}
            <Field id="email" label="Email" error={touched ? errors.email : null}>
              <input
                id="email"
                type="email"
                inputMode="email"
                className={inputClass}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                aria-invalid={touched && !!errors.email}
              />
            </Field>
            <Field
              id="password"
              label="Password"
              error={touched ? errors.password : null}
              hint={mode === "register" ? "At least 10 characters." : undefined}
            >
              <div className="relative">
                <input
                  id="password"
                  ref={passwordRef}
                  type={showPassword ? "text" : "password"}
                  className={cn(inputClass, "pr-11")}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={mode === "sign_in" ? "current-password" : "new-password"}
                  aria-invalid={touched && !!errors.password}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-muted-foreground hover:text-foreground"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </Field>

            {submit.isError && (
              <Notice tone="danger">
                {api.describeError(submit.error)}
                {api.isCode(submit.error, "email_taken") && (
                  <button
                    type="button"
                    className="ml-1 font-semibold underline"
                    onClick={() => switchMode("sign_in")}
                  >
                    Sign in
                  </button>
                )}
              </Notice>
            )}

            <Button
              type="submit"
              className="h-11 w-full text-[15px]"
              disabled={submit.isPending}
              data-testid="auth-submit"
            >
              {submit.isPending && <Spinner />}
              {mode === "sign_in"
                ? submit.isPending
                  ? "Signing in…"
                  : "Sign in"
                : submit.isPending
                  ? "Creating your account…"
                  : "Create account"}
            </Button>
          </form>

          {demoMode && (
            <section aria-labelledby="demo-heading" className="mt-8 border-t border-border pt-6">
              <div className="flex items-center gap-2">
                <h3 id="demo-heading" className="text-sm font-semibold">
                  Explore a demonstration
                </h3>
                <Pill tone="indigo" icon={History}>
                  Historical replay
                </Pill>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                Each demonstration replays a glucose recording made in 2020. Choose one, then enter
                the demonstration password you were given.
              </p>
              <div className="mt-3 grid gap-2">
                {DEMO_ACCOUNTS.map((demo) => (
                  <button
                    key={demo.email}
                    type="button"
                    onClick={() => chooseDemo(demo.email)}
                    data-testid={`demo-${demo.email}`}
                    className={cn(
                      "rounded-xl border p-3.5 text-left transition-colors hover:border-primary/60",
                      email === demo.email
                        ? "border-primary bg-accent"
                        : "border-border-strong bg-card",
                    )}
                  >
                    <span className="flex items-center gap-2 text-sm font-semibold">
                      <UserPlus className="h-4 w-4 text-primary" />
                      {demo.title}
                    </span>
                    <span className="mt-1 block text-[13px] text-muted-foreground">
                      {demo.body}
                    </span>
                    <span className="mt-1 block text-xs text-muted-foreground">{demo.email}</span>
                  </button>
                ))}
              </div>
            </section>
          )}

          <p className="mt-8 text-xs leading-relaxed text-muted-foreground lg:hidden">
            A research prototype. Forecasts are informational and are not medical advice.
          </p>
        </div>
      </main>
    </div>
  );
}
