// Shared helpers for the GLIMPSE browser journeys. Everything here drives the real
// application against a real backend: nothing is mocked or intercepted.
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

export const BASE = (process.env.E2E_BASE_URL ?? "http://localhost:5173").replace(/\/$/, "");
export const API =
  (process.env.E2E_API_URL ?? "http://localhost:8000").replace(/\/$/, "") + "/api/v2";
export const DEMO_PASSWORD = process.env.GLUCOTWIN_DEMO_PASSWORD ?? "";
export const DEMO_EMAILS = {
  new_user: "demo@glimpse.example",
  returning_user: "demo-returning@glimpse.example",
};
export const ARTIFACTS = fileURLToPath(new URL("./.artifacts/", import.meta.url));
mkdirSync(ARTIFACTS, { recursive: true });

export const DESKTOP = { width: 1440, height: 900 };
export const PHONE = { width: 390, height: 844 };

export async function launch(viewport = DESKTOP) {
  const browser = await chromium.launch({
    // The installed Chrome by default; set E2E_BROWSER_CHANNEL="" to use Playwright's own Chromium.
    ...((process.env.E2E_BROWSER_CHANNEL ?? "chrome")
      ? { channel: process.env.E2E_BROWSER_CHANNEL ?? "chrome" }
      : {}),
    headless: !process.env.E2E_HEADED,
  });
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  page.setDefaultTimeout(30_000);
  const problems = [];
  page.on("pageerror", (error) => problems.push(`page error: ${error.message.slice(0, 300)}`));
  page.on("response", (response) => {
    if (response.url().includes("/api/") && response.status() >= 500) {
      problems.push(
        `server error ${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`,
      );
    }
  });
  return { browser, page, problems };
}

export function expect(condition, message) {
  if (!condition) throw new Error(message);
}

/** Runs named checks in order, reporting each; later checks still run after a failure. */
export function journey(name) {
  const results = [];
  return {
    name,
    results,
    async check(title, fn) {
      try {
        await fn();
        results.push({ title, ok: true });
        console.log(`  ok    ${title}`);
      } catch (error) {
        results.push({ title, ok: false, error: String(error.message).split("\n")[0] });
        console.log(
          `  FAIL  ${title}\n        ${String(error.message).split("\n").slice(0, 3).join("\n        ")}`,
        );
      }
    },
    skip(title, why) {
      results.push({ title, ok: true, skipped: why });
      console.log(`  skip  ${title} (${why})`);
    },
  };
}

export const shot = (page, name) =>
  page.screenshot({ path: `${ARTIFACTS}${name}.png`, fullPage: true });

/** A JSON call made from inside the page, as the signed-in user. */
export function api(page, path, init = {}) {
  return page.evaluate(
    async ({ url, init }) => {
      const headers = {
        Authorization: `Bearer ${sessionStorage.getItem("glimpse.v2.token")}`,
        ...(init.json ? { "Content-Type": "application/json" } : {}),
      };
      const response = await fetch(url, {
        method: init.method ?? "GET",
        headers,
        body: init.json ? JSON.stringify(init.json) : undefined,
      });
      const text = await response.text();
      return { status: response.status, body: text ? JSON.parse(text) : null };
    },
    { url: API + path, init },
  );
}
export const get = async (page, path) => (await api(page, path)).body;

export async function signIn(page, email, password) {
  await page.goto(`${BASE}/auth`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByTestId("auth-submit").click();
}

/** Returns a demonstration account to its starting state through the real reset endpoint. */
export async function resetDemo(mode) {
  const login = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: DEMO_EMAILS[mode], password: DEMO_PASSWORD }),
  });
  if (!login.ok)
    throw new Error(
      `demo sign-in failed (${login.status}); is GLUCOTWIN_DEMO_PASSWORD the server's?`,
    );
  const { session } = await login.json();
  const reset = await fetch(`${API}/demo/replay/reset`, {
    method: "POST",
    headers: { Authorization: `Bearer ${session.token}` },
  });
  if (!reset.ok) throw new Error(`demo reset failed (${reset.status})`);
}

/** Presses "Advance to next event" in the demo controls and waits for the replay to move. */
export async function advanceInUi(page) {
  await page.getByTestId("demo-controls-open").click();
  const before = await page.getByTestId("replay-position").innerText();
  const events = (await get(page, "/demo/replay")).event_index;
  await page.getByTestId("demo-advance").click();
  await page.waitForFunction(
    async ({ url, events }) => {
      const r = await fetch(url, {
        headers: { Authorization: `Bearer ${sessionStorage.getItem("glimpse.v2.token")}` },
      });
      return (await r.json()).event_index > events;
    },
    { url: `${API}/demo/replay`, events },
    { timeout: 120_000, polling: 500 },
  );
  await page.waitForFunction(
    (b) => document.querySelector("[data-testid=replay-position]")?.textContent !== b,
    before,
    { timeout: 30_000 },
  );
  await page.keyboard.press("Escape");
  await page.getByTestId("replay-position").waitFor({ state: "hidden" });
}

export const overflow = (page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

/** A real JPEG made in the browser, so no photograph needs to live in the repository. */
export async function syntheticPhoto(page) {
  const base64 = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 800;
    canvas.height = 600;
    const g = canvas.getContext("2d");
    g.fillStyle = "#e9e2d0";
    g.fillRect(0, 0, 800, 600);
    g.fillStyle = "#ffffff";
    g.beginPath();
    g.arc(400, 300, 230, 0, 7);
    g.fill();
    g.fillStyle = "#c98b3a";
    g.beginPath();
    g.arc(340, 280, 90, 0, 7);
    g.fill();
    g.fillStyle = "#4f8f3a";
    g.beginPath();
    g.arc(480, 340, 70, 0, 7);
    g.fill();
    return canvas.toDataURL("image/jpeg", 0.9).split(",")[1];
  });
  return { name: "plate.jpg", mimeType: "image/jpeg", buffer: Buffer.from(base64, "base64") };
}

export async function fillMacros(page, values) {
  for (const [key, value] of Object.entries(values))
    await page.locator(`#macro-${key}`).fill(String(value));
}
