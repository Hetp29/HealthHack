import { QueryClient } from "@tanstack/react-query";
import { createRouter, rootRouteId } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";

import { routeTree } from "@/routeTree.gen";

// Match routes without running loaders or rendering.
const router = () => createRouter({ routeTree, context: { queryClient: new QueryClient() } });

describe("App routing", () => {
  it.each([
    "/",
    "/auth",
    "/onboarding",
    "/meals",
    "/meals/log",
    "/insights",
    "/health",
    "/settings",
  ])("has a screen for %s", (path) => {
    expect(router().matchRoutes(path).at(-1)?.routeId).not.toBe(rootRouteId);
  });

  it("routes a meal id to the meal screen, not the log screen", () => {
    expect(router().matchRoutes("/meals/meal_123").at(-1)?.routeId).toBe("/meals/$mealId");
    expect(router().matchRoutes("/meals/log").at(-1)?.routeId).toBe("/meals/log");
  });

  it.each(["/predict", "/medications", "/profile", "/connect-devices"])(
    "keeps the old address %s alive as a redirect",
    (path) => {
      expect(router().matchRoutes(path).at(-1)?.routeId).toBe(path);
    },
  );
});
