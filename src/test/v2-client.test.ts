import { beforeEach, describe, expect, it, vi } from "vitest";
import { advanceReplay, login, recordDose } from "@/services/v2Client";

describe("v2 API client", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("logs in against the v2 endpoint and stores the bearer session", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({
        user: { user_id: "u1" },
        session: { token: "session-token", token_type: "Bearer", expires_at: "2026-10-11T00:00:00Z" },
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    ));
    vi.stubGlobal("fetch", fetchMock);

    await login("demo@glucotwin.example", "demo-password");

    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v2/auth/login"), expect.objectContaining({ method: "POST" }));
    expect(sessionStorage.getItem("glimpse.v2.token")).toBe("session-token");
  });

  it("sends replay event indexes and authenticated medication actions", async () => {
    sessionStorage.setItem("glimpse.v2.token", "session-token");
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } }),
    ));
    vi.stubGlobal("fetch", fetchMock);

    await advanceReplay({ expected_event_index: 4 });
    await recordDose({ regimen_id: "rgm-1", action: "taken", scheduled_at: null });

    expect(fetchMock.mock.calls[0]?.[0]).toContain("/api/v2/demo/replay/advance");
    expect(fetchMock.mock.calls[1]?.[0]).toContain("/api/v2/medication-doses");
  });
});
