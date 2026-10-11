import { createFileRoute, redirect } from "@tanstack/react-router";

// An address from the earlier design. Kept so saved links still arrive somewhere useful.
export const Route = createFileRoute("/predict")({
  beforeLoad: () => {
    throw redirect({ to: "/meals/log", replace: true });
  },
});
