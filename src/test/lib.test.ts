import { afterEach, describe, expect, it, vi } from "vitest";

import { macroErrors } from "@/features/MacroForm";
import { isRecordedMeal } from "@/features/mealText";
import * as api from "@/lib/api";
import { makeClock } from "@/lib/time";
import type { ReplayState } from "@/lib/types";
import { formatGlucose, toMgDl } from "@/lib/units";
import { DEMO_ACCOUNTS } from "@/routes/auth";

const replay = {
  position: "2020-05-08T13:26:00Z",
  window: { start: "2020-05-08T12:56:00Z", end: "2020-05-10T00:00:00Z", timezone: "UTC" },
} as ReplayState;

describe("clock", () => {
  it("shows a replayed recording as recording days, never as calendar dates", () => {
    const clock = makeClock(replay, "America/New_York");
    expect(clock.isReplay).toBe(true);
    expect(clock.dateTime("2020-05-08T13:26:00Z")).toBe("Day 1, 1:26 PM");
    expect(clock.day("2020-05-09T08:00:00Z")).toBe("Day 2");
    expect(clock.day("2020-05-07T08:00:00Z")).toBe("1 day before Day 1");
    expect(clock.dateTime("2020-05-08T13:26:00Z")).not.toContain("2020");
    expect(clock.now().toISOString()).toBe("2020-05-08T13:26:00.000Z");
    expect(clock.ago("2020-05-08T13:14:00Z")).toBe("12 min ago");
  });

  it("uses the account's timezone for an ordinary account", () => {
    const clock = makeClock(null, "America/New_York");
    expect(clock.isReplay).toBe(false);
    expect(clock.time("2026-09-20T16:00:00Z")).toBe("12:00 PM");
    expect(makeClock(null, "Not/AZone").time("2026-09-20T16:00:00Z")).toBe("4:00 PM");
  });
});

describe("glucose units", () => {
  it("converts for display without touching the stored value", () => {
    expect(formatGlucose(180, "mg/dL")).toBe("180");
    expect(formatGlucose(180, "mmol/L")).toBe("10.0");
    expect(formatGlucose(16.4, "mg/dL", true)).toBe("+16");
    expect(formatGlucose(null, "mg/dL")).toBe("–");
    expect(Math.round(toMgDl(10, "mmol/L"))).toBe(180);
  });
});

describe("meal rules", () => {
  it("requires all five nutrition values within the server's limits", () => {
    expect(
      Object.keys(
        macroErrors({
          carbs_g: "",
          protein_g: "x",
          fat_g: "-1",
          fiber_g: "201",
          calories_kcal: "10001",
        }),
      ),
    ).toHaveLength(5);
    expect(
      macroErrors({
        carbs_g: "45",
        protein_g: "20",
        fat_g: "0",
        fiber_g: "6",
        calories_kcal: "370",
      }),
    ).toEqual({});
  });

  it("tells a recorded replay meal from one the user typed in", () => {
    expect(
      isRecordedMeal({ note: "Research dataset meal record (CGMacros v1.0.0): cgmacros_001_meal" }),
    ).toBe(true);
    expect(isRecordedMeal({ note: null })).toBe(false);
    expect(isRecordedMeal({ note: "Lunch" })).toBe(false);
  });
});

describe("demonstration accounts", () => {
  it("uses the identifiers the backend seeds, and no password", () => {
    expect(DEMO_ACCOUNTS.map((d) => d.email)).toEqual([
      "demo@glimpse.example",
      "demo-returning@glimpse.example",
    ]);
    expect(JSON.stringify(DEMO_ACCOUNTS)).not.toMatch(/password"|glucotwin/i);
  });
});

describe("api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
  });
  const respond = (status: number, body: unknown) =>
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify(body), {
            status,
            headers: { "content-type": "application/json" },
          }),
      ),
    );

  it("keeps the backend's error code, message and details", async () => {
    respond(422, {
      code: "insufficient_premeal_context",
      message: "No CGM reading.",
      request_id: "req_1",
      details: { latest_cgm_age_minutes: 42 },
    });
    const error = await api.predictMeal("meal_1").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(api.ApiError);
    expect((error as api.ApiError).code).toBe("insufficient_premeal_context");
    expect((error as api.ApiError).details["latest_cgm_age_minutes"]).toBe(42);
    expect(api.describeError(error)).toContain("15 minutes before the meal");
  });

  it("ends the session on a 401 from a protected call, but not on a wrong password", async () => {
    const ended = vi.fn();
    window.addEventListener(api.SESSION_EVENT, ended);
    api.setToken("tok");
    respond(401, { code: "invalid_credentials", message: "no" });
    await api.login("a@b.co", "wrong-password").catch(() => undefined);
    expect(ended).not.toHaveBeenCalled();
    expect(api.getToken()).toBe("tok");
    respond(401, { code: "session_expired", message: "expired" });
    await api.getMe().catch(() => undefined);
    expect(ended).toHaveBeenCalledTimes(1);
    expect(api.getToken()).toBeNull();
    window.removeEventListener(api.SESSION_EVENT, ended);
  });

  it("sends a photo as raw bytes with its content type and the bearer token", async () => {
    api.setToken("tok");
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ meal_id: "meal_1" }), { status: 201 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const photo = new Blob([new Uint8Array([255, 216, 255])], { type: "image/jpeg" });
    await api.uploadMealPhoto(photo, "2026-09-20T16:00:00Z", "image/jpeg");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/v2/meals/images?occurred_at=2026-09-20T16%3A00%3A00Z");
    expect(init.body).toBe(photo);
    expect((init.headers as Headers).get("Content-Type")).toBe("image/jpeg");
    expect((init.headers as Headers).get("Authorization")).toBe("Bearer tok");
  });

  it("describes a network failure plainly", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("failed");
      }),
    );
    const error = await api.getStatus().catch((e: unknown) => e);
    expect((error as api.ApiError).kind).toBe("network");
    expect(api.describeError(error)).toContain("can't reach");
  });
});
