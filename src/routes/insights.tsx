import { createFileRoute } from "@tanstack/react-router";
export const Route = createFileRoute("/insights")({
  head: () => ({ meta: [{ title: "Glimpse — insights" }] }),
  component: () => <p className="p-8 text-subtle">This page is not finished yet.</p>,
});
