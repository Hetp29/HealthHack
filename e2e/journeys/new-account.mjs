// Journey A: a first-time user registers, consents, onboards, reaches the dashboard,
// and is not onboarded again on the next sign-in.
import { BASE, expect, get, journey, launch, overflow, shot } from "../lib.mjs";

export default async function newAccount() {
  const j = journey("A · first-time user");
  const { browser, page, problems } = await launch();
  const email = `e2e-${Date.now()}@glimpse-test.example`;
  const password = `e2e-${Math.random().toString(36).slice(2)}-password`;
  const next = () => page.getByTestId("onboarding-continue").click();

  await j.check("an unauthenticated visit lands on sign-in", async () => {
    await page.goto(`${BASE}/`);
    await page.waitForURL("**/auth");
    await page.getByRole("heading", { name: "Welcome back" }).waitFor();
    await shot(page, "a01-auth");
  });
  await j.check("registration validates inline", async () => {
    await page.getByTestId("tab-register").click();
    await page.getByTestId("auth-submit").click();
    await page.getByText("Enter the name you'd like us to use.").waitFor();
    await page.getByLabel("Your name").fill("Avery Test");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill("short");
    await page.getByTestId("auth-submit").click();
    await page.getByText("Use at least 10 characters.").waitFor();
  });
  await j.check("registration opens onboarding and greets the user by name", async () => {
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByTestId("auth-submit").click();
    await page.waitForURL("**/onboarding");
    expect(
      (await page.getByTestId("step-welcome").innerText()).includes("Avery"),
      "greeting lacks the name",
    );
    await shot(page, "a02-welcome");
  });
  await j.check("required consent blocks; the two consents are sent separately", async () => {
    await next();
    await page.getByTestId("step-consent").waitFor();
    await next();
    await page.getByText("GLIMPSE needs the first permission to work").waitFor();
    await page.getByTestId("consent-necessary_processing").click();
    // Personalization is left OFF here: the optional consent must stay a separate choice.
    const puts = [];
    page.on("request", (r) => {
      if (r.method() === "PUT" && r.url().includes("/consents/"))
        puts.push(r.url().split("/consents/")[1]);
    });
    await next();
    await page.getByTestId("step-about").waitFor();
    expect(puts.join() === "necessary_processing", `consent requests: ${puts}`);
    const me = await get(page, "/me");
    const state = Object.fromEntries(me.consents.map((c) => [c.consent_type, c.state]));
    expect(
      state.necessary_processing === "granted" && state.personalization_history !== "granted",
      JSON.stringify(state),
    );
  });
  await j.check("about you: implausible values rejected, valid ones saved", async () => {
    await page.locator("#height").fill("9999");
    await next();
    await page.getByText("That doesn't look right").first().waitFor();
    await page.locator("#height").fill("172");
    await page.locator("#weight").fill("70");
    await page.locator("#date_of_birth").fill("1990-04-12");
    await page.locator("#sex_assigned_at_birth").selectOption("female");
    await page.locator('[data-choice="primary_goal:meal_responses"]').click();
    await next();
    await page.getByTestId("step-health").waitFor();
  });
  await j.check("health: conditional questions appear only when relevant", async () => {
    expect((await page.locator("#diagnosis_year").count()) === 0, "diagnosis year shown too early");
    await page.locator('[data-choice="diabetes_status:type_2"]').click();
    await page.locator("#diagnosis_year").fill("2018");
    await page.locator("#hba1c").fill("6.8");
    await page.getByText("I don't know").nth(1).click();
    await page.locator('[data-choice="monitoring_method:cgm"]').click();
    await page.locator('[data-choice="cgm_brand:dexcom"]').click();
    await shot(page, "a03-health");
    await next();
    await page.getByTestId("step-devices").waitFor();
  });
  await j.check("data sources: no device is offered as simulated or activated", async () => {
    expect(
      (await page.getByText("Simulate connection").count()) === 0,
      "simulated devices offered to an ordinary account",
    );
    await page.locator('[data-choice="cgm_connect_preference:later"]').click();
    await page.locator('[data-choice="uses_wearable:yes"]').click();
    await page.locator('[data-choice="wearable_platform:fitbit"]').click();
    await next();
    await page.getByTestId("step-routine").waitFor();
  });
  await j.check("Back keeps earlier inputs", async () => {
    await page.getByTestId("onboarding-back").click();
    expect(
      (await page
        .locator('[data-choice="wearable_platform:fitbit"]')
        .getAttribute("aria-checked")) === "true",
      "choice lost",
    );
    await next();
    await page.getByTestId("step-routine").waitFor();
  });
  await j.check("medications and meals saved; a reload resumes at the same step", async () => {
    await page.locator('[data-choice="takes_glucose_medication:yes"]').click();
    await page.locator("#medication_names").fill("Metformin");
    await page.locator('[data-choice="wants_medication_reminders:yes"]').click();
    await page.locator("#meals_per_day").fill("3");
    await page.locator('[data-choice="meal_logging_method:both"]').click();
    await next();
    await page.getByTestId("step-alerts").waitFor();
    await page.reload();
    await page.getByTestId("step-alerts").waitFor();
  });
  await j.check("alerts: level outside the supported range rejected", async () => {
    await page.locator('[data-choice="wants_spike_alerts:yes"]').click();
    await page.locator("#alert_threshold").fill("50");
    await next();
    await page.getByText("That doesn't look right").first().waitFor();
    await page.locator("#alert_threshold").fill("160");
    await next();
    await page.getByTestId("step-review").waitFor();
  });
  await j.check("review lists what was entered; finishing opens the dashboard", async () => {
    const text = await page.locator("main").innerText();
    for (const expected of [
      "Avery Test",
      "172 cm",
      "Type 2 diabetes",
      "2018",
      "6.8 %",
      "Fitbit",
      "Metformin",
      "160 mg/dL",
    ]) {
      expect(text.includes(expected), `review is missing "${expected}"`);
    }
    await shot(page, "a04-review");
    await next();
    await page.getByTestId("onboarding-done").waitFor();
    await page.getByTestId("onboarding-finish").click();
    await page.waitForURL(`${BASE}/`);
    await page.getByText("Getting started").waitFor();
    await page.getByText("No glucose readings yet").waitFor();
    expect((await overflow(page)) <= 0, "horizontal overflow");
    await shot(page, "a05-first-dashboard");
  });
  await j.check(
    "the server stored exactly what was entered, and no device connection",
    async () => {
      const onboarding = await get(page, "/onboarding");
      const answers = onboarding.answers;
      expect(onboarding.completed === true, "onboarding not completed");
      expect(answers.height.value === 172 && answers.height.unit === "cm", "height");
      expect(
        answers.hba1c.value === 6.8 && answers.fasting_glucose === "unknown",
        "HbA1c / fasting glucose",
      );
      expect(
        answers.diagnosis_year === 2018 && answers.medication_names[0] === "Metformin",
        "year / medications",
      );
      const alerts = await get(page, "/alert-settings");
      expect(alerts.enabled === true && alerts.threshold.value === 160, "alert rule");
      expect(
        (await get(page, "/devices")).connections.length === 0,
        "a device connection was created",
      );
    },
  );
  await j.check(
    "a meal can be saved without glucose data, and the refused forecast is explained",
    async () => {
      await page.goto(`${BASE}/meals/log`);
      await page.getByText("A forecast may not be possible yet").waitFor();
      await page.getByTestId("manual-entry").click();
      for (const [k, v] of Object.entries({
        carbs_g: 45,
        protein_g: 20,
        fat_g: 12,
        fiber_g: 6,
        calories_kcal: 370,
      }))
        await page.locator(`#macro-${k}`).fill(String(v));
      await page.getByTestId("confirm-forecast").click();
      await page.getByText("Your meal is saved, but it couldn't be forecast").waitFor();
      expect((await get(page, "/meals")).meals.length === 1, "meal not saved exactly once");
      await shot(page, "a06-forecast-refused");
    },
  );
  await j.check("optional consent can be granted later in Settings and persists", async () => {
    await page.goto(`${BASE}/settings`);
    await page.getByTestId("consent-personalization").click();
    await page.getByText("Permission switched on").waitFor();
    await page.reload();
    expect(
      (await page.getByTestId("consent-personalization").getAttribute("aria-checked")) === "true",
      "consent not persisted",
    );
  });
  await j.check(
    "sign out, sign in: saved state persists and onboarding is not repeated",
    async () => {
      await page.getByTestId("account-menu").click();
      await page.getByTestId("sign-out").click();
      await page.waitForURL("**/auth");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password", { exact: true }).fill("definitely-the-wrong-password");
      await page.getByTestId("auth-submit").click();
      await page.getByText("That email and password don't match.").waitFor();
      await page.getByLabel("Password", { exact: true }).fill(password);
      await page.getByTestId("auth-submit").click();
      await page.waitForURL(`${BASE}/`);
      await page.getByText("Good ").first().waitFor();
      expect((await page.locator("main h1").innerText()).includes("Avery"), "name not restored");
    },
  );
  await j.check("the test account deletes itself", async () => {
    await page.goto(`${BASE}/settings`);
    await page.getByRole("button", { name: "Delete my account" }).click();
    await page.locator("#delete-password").fill(password);
    await page.getByRole("button", { name: "Delete everything" }).click();
    await page.waitForURL("**/auth");
  });

  await browser.close();
  return { ...j, problems };
}
