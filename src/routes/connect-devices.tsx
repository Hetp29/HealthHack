import { createFileRoute, redirect } from "@tanstack/react-router";

// An address from the earlier design. Kept so saved links still arrive somewhere useful.
export const Route = createFileRoute("/connect-devices")({
  beforeLoad: () => {
    throw redirect({ to: "/health", search: { tab: "devices" }, replace: true });
  },
});
