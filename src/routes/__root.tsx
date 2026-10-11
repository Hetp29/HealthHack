import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  HeadContent,
  Link,
  Outlet,
  Scripts,
  createRootRouteWithContext,
  useLocation,
  useNavigate,
  useRouter,
  type ErrorComponentProps,
} from "@tanstack/react-router";
import { CloudOff } from "lucide-react";
import { useEffect, type ReactNode } from "react";

import { Logo, Spinner } from "@/components/glimpse/primitives";
import { AppShell } from "@/components/shell/AppShell";
import { Button } from "@/components/ui/button";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { API_ROOT } from "@/lib/api";
import { SessionProvider, useSession } from "@/lib/session";
import { UnitsProvider } from "@/lib/units";

import { reportLovableError } from "../lib/lovable-error-reporting";
import appCss from "../styles.css?url";

function FullScreen({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      {children}
    </div>
  );
}

function NotFoundComponent() {
  return (
    <FullScreen>
      <div className="max-w-md text-center">
        <Logo className="justify-center" />
        <h1 className="mt-6 text-2xl font-semibold">We couldn't find that page</h1>
        <p className="mt-2 text-muted-foreground">
          It may have moved, or the link may be incomplete.
        </p>
        <Button asChild className="mt-6">
          <Link to="/">Go to Home</Link>
        </Button>
      </div>
    </FullScreen>
  );
}

function ErrorComponent({ error, reset }: ErrorComponentProps) {
  const router = useRouter();
  useEffect(() => {
    console.error(error);
    reportLovableError(error, { boundary: "tanstack_root_error_component" });
  }, [error]);
  return (
    <FullScreen>
      <div className="max-w-md text-center" role="alert">
        <h1 className="text-xl font-semibold">This page didn't load</h1>
        <p className="mt-2 text-muted-foreground">
          Something went wrong while showing this screen. Your data is unaffected.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <Button
            onClick={() => {
              void router.invalidate();
              reset();
            }}
          >
            Try again
          </Button>
          <Button variant="outline" asChild>
            <a href="/">Go to Home</a>
          </Button>
        </div>
      </div>
    </FullScreen>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1, viewport-fit=cover" },
      { title: "GLIMPSE" },
      {
        name: "description",
        content:
          "GLIMPSE forecasts how your glucose may respond to a meal, from your own meals and glucose data.",
      },
      { name: "theme-color", content: "#176B68" },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "icon", href: "/favicon.ico", type: "image/x-icon" },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <HeadContent />
      </head>
      <body suppressHydrationWarning>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  return (
    <QueryClientProvider client={queryClient}>
      <SessionProvider>
        <UnitsProvider>
          <TooltipProvider delayDuration={200}>
            <Gate />
            <Toaster position="top-center" richColors closeButton />
          </TooltipProvider>
        </UnitsProvider>
      </SessionProvider>
    </QueryClientProvider>
  );
}

function Splash() {
  return (
    <FullScreen>
      <div className="flex flex-col items-center gap-4" role="status" aria-label="Loading GLIMPSE">
        <Logo />
        <Spinner className="h-5 w-5 text-muted-foreground" />
      </div>
    </FullScreen>
  );
}

/** Decides which of sign-in, onboarding and the app a visitor belongs in. */
function Gate() {
  const session = useSession();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const onAuth = pathname === "/auth";
  const onOnboarding = pathname === "/onboarding";
  const completed = session.onboarding?.completed === true;

  let target: "/auth" | "/onboarding" | "/" | null = null;
  if (session.phase === "signed_out" && !onAuth) target = "/auth";
  else if (session.phase === "ready" && onAuth) target = completed ? "/" : "/onboarding";
  else if (session.phase === "ready" && !completed && !onOnboarding) target = "/onboarding";

  useEffect(() => {
    if (target) void navigate({ to: target, replace: true });
  }, [target, navigate]);

  if (session.phase === "unreachable") {
    return (
      <FullScreen>
        <div className="max-w-md text-center" role="alert">
          <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-destructive-soft text-destructive">
            <CloudOff className="h-6 w-6" />
          </span>
          <h1 className="mt-4 text-xl font-semibold">Can't reach GLIMPSE</h1>
          <p className="mt-2 text-muted-foreground">
            The server{API_ROOT ? ` at ${API_ROOT}` : ""} isn't responding. Your account and data
            are safe; this screen will work again once it is back.
          </p>
          <div className="mt-6 flex flex-wrap justify-center gap-2">
            <Button onClick={session.retry}>Try again</Button>
            <Button variant="outline" onClick={() => void session.signOut()}>
              Sign out
            </Button>
          </div>
        </div>
      </FullScreen>
    );
  }
  if (session.phase === "starting" || target) return <Splash />;
  if (onAuth || onOnboarding) return <Outlet />;
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
