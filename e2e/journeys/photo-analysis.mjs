// Journey C with a REAL OpenAI analysis. This costs money, so it runs only when
// E2E_OPENAI=1, with a photo you supply in E2E_MEAL_PHOTO, and makes exactly one call.
import { existsSync } from "node:fs";

import {
  BASE,
  DEMO_EMAILS,
  DEMO_PASSWORD,
  expect,
  get,
  journey,
  launch,
  shot,
  signIn,
} from "../lib.mjs";

export default async function photoAnalysis() {
  const j = journey("C · photo meal with a real analysis (paid, opt-in)");
  const photo = process.env.E2E_MEAL_PHOTO;
  if (process.env.E2E_OPENAI !== "1") {
    j.skip("real photo analysis", "set E2E_OPENAI=1 and E2E_MEAL_PHOTO to run this paid test");
    return { ...j, problems: [] };
  }
  if (!photo || !existsSync(photo)) {
    j.skip("real photo analysis", "E2E_MEAL_PHOTO does not point to a file");
    return { ...j, problems: [] };
  }
  const { browser, page, problems } = await launch();
  let analyses = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/analyze")) analyses += 1;
  });

  await j.check("the server reports photo analysis as configured", async () => {
    await signIn(page, DEMO_EMAILS.new_user, DEMO_PASSWORD);
    await page.waitForURL(`${BASE}/`);
    expect(
      (await get(page, "/status")).meal_analysis.configured === true,
      "analysis is not configured on this server",
    );
  });
  await j.check("preview first; nothing is analysed until asked", async () => {
    await page.goto(`${BASE}/meals/log`);
    await page.getByTestId("photo-input").setInputFiles(photo);
    await page.getByAltText("The meal photo you chose").waitFor();
    expect(analyses === 0, "analysis ran before being asked");
  });
  await j.check("one real analysis returns foods, portions and editable estimates", async () => {
    const started = Date.now();
    await page.getByTestId("analyze-photo").click();
    await page.getByTestId("estimate").waitFor({ timeout: 120_000 });
    console.log(`        (analysis took ${((Date.now() - started) / 1000).toFixed(1)} s)`);
    expect((await page.getByTestId("estimate").locator("li").count()) > 0, "no foods listed");
    expect((await page.locator("#macro-carbs_g").inputValue()) !== "", "estimate not prefilled");
    await page.getByText("AI estimate").first().waitFor();
    // Not saved to the artifacts folder: the photo may not be yours to share.
  });
  await j.check(
    "edit, confirm, forecast: the stored values are the user's, not the model's",
    async () => {
      await page.locator("#macro-fiber_g").fill("4");
      await page.getByTestId("confirm-forecast").click();
      await page.waitForURL(/\/meals\/meal_/, { timeout: 60_000 });
      await page.getByText("Forecast peak").first().waitFor();
      await page.getByText("AI estimate, edited by you").waitFor();
      const meal = await get(page, `/meals/${page.url().split("/").pop()}`);
      expect(
        meal.confirmation.fiber_g === 4 && meal.confirmation.source === "ai_edited",
        JSON.stringify(meal.confirmation),
      );
      expect(meal.prediction_id !== null, "no forecast stored");
    },
  );
  await j.check("exactly one analysis request was made", async () => {
    expect(analyses === 1, `analysis requests: ${analyses}`);
  });

  await browser.close();
  return { ...j, problems };
}
