import { createFileRoute } from "@tanstack/react-router";
export const Route = createFileRoute("/predict")({
  head: () => ({ meta: [{ title: "Glimpse — predict" }] }),
  component: () => <p className="p-8 text-subtle">This page is not finished yet.</p>,
});
