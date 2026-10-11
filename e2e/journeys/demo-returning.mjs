// Journey E: the returning-user demonstration starts with personal history in use.
import {
  BASE,
  DEMO_EMAILS,
  DEMO_PASSWORD,
  advanceInUi,
  expect,
  get,
  journey,
  launch,
  resetDemo,
  shot,
} from "../lib.mjs";

export default async function demoReturning() {
  const j = journey("E · returning-user demonstration");
  await resetDemo("returning_user");
  const { browser, page, problems } = await launch();
  let before;

  await j.check("signs in straight to the dashboard with history in use", async () => {
    await page.goto(`${BASE}/auth`);
    await page.getByTestId(`demo-${DEMO_EMAILS.returning_user}`).click();
    await page.getByLabel("Password", { exact: true }).fill(DEMO_PASSWORD);
    await page.getByTestId("auth-submit").click();
    await page.waitForURL(`${BASE}/`);
    await page.getByTestId("latest-glucose").waitFor();
    expect(
      (await page.getByTestId("personalization-status").innerText()).includes("use your history"),
      "history not in use",
    );
    await shot(page, "e01-home");
  });
  await j.check("earlier meals and their real outcomes are listed with provenance", async () => {
    await page.goto(`${BASE}/meals`);
    await page.getByTestId("meal-row").first().waitFor();
    expect((await page.getByTestId("meal-row").count()) >= 31, "earlier meals missing");
    await page.getByTestId("meal-row").nth(3).click();
    await page.getByText("From the recording").first().waitFor();
    await page.getByText("Observed peak").first().waitFor();
    await page.getByText("Historical recording").first().waitFor();
  });
  await j.check(
    "the next recorded meal's forecast uses personal history, matching the server",
    async () => {
      await page.goto(`${BASE}/`);
      await page.getByTestId("latest-glucose").waitFor();
      await advanceInUi(page);
      const newest = (await get(page, "/meals?limit=1")).meals[0];
      const prediction = await get(page, `/predictions/${newest.prediction_id}`);
      before = prediction.personalization.admissible_prior_meals;
      expect(prediction.personalization.retrieval_active === true, "retrieval not active");
      expect(prediction.personalization.calibration_applied === false, "calibration applied");
      await page.goto(`${BASE}/meals/${newest.meal_id}`);
      await page.getByText(`Uses your history · ${before} earlier meals`).waitFor();
      await shot(page, "e02-forecast");
    },
  );
  await j.check(
    "insights shows the scored history; the count follows the server's lookback",
    async () => {
      await page.goto(`${BASE}/insights`);
      expect(
        (await page.getByTestId("insights-personalization").innerText()) === "In use",
        "insights state",
      );
      await page.locator(".recharts-line").first().waitFor();
      const text = await page.locator("main").innerText();
      expect(text.includes(`${before}`), "eligible-meal count differs from the server");
      // The server only looks back a limited time, so the count is not simply "all scored meals".
      expect(before < 31, "the count ignores the lookback window");
      await shot(page, "e03-insights");
    },
  );
  await j.check("reset explains itself, signs out, and restores the starting state", async () => {
    await page.getByTestId("demo-controls-open").click();
    await page.getByRole("button", { name: "Reset demonstration" }).click();
    await page.getByText("Reset this demonstration?").waitFor();
    await page.getByRole("button", { name: "Reset and sign out" }).click();
    await page.waitForURL("**/auth", { timeout: 180_000 });
    await page.getByLabel("Email").fill(DEMO_EMAILS.returning_user);
    await page.getByLabel("Password", { exact: true }).fill(DEMO_PASSWORD);
    await page.getByTestId("auth-submit").click();
    await page.waitForURL(`${BASE}/`);
    expect(
      (await get(page, "/demo/replay")).data.meals_logged === 31,
      "starting state not restored",
    );
  });

  await browser.close();
  return { ...j, problems };
}
