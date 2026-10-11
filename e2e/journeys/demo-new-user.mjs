// Journeys B, D and F on the new-user demonstration: simulated devices, the genuine
// historical replay through to active personal history, hypothetical-meal isolation,
// medications, alerts, navigation and phone layout.
import {
  BASE,
  DEMO_EMAILS,
  DEMO_PASSWORD,
  PHONE,
  DESKTOP,
  advanceInUi,
  api,
  expect,
  fillMacros,
  get,
  journey,
  launch,
  overflow,
  resetDemo,
  shot,
  syntheticPhoto,
} from "../lib.mjs";

const DEVICES = ["apple_health", "apple_watch", "fitbit", "whoop"];
const HYPOTHETICAL = { carbs_g: 95, protein_g: 10, fat_g: 8, fiber_g: 2, calories_kcal: 490 };

export default async function demoNewUser() {
  const j = journey("B/D/F · new-user demonstration");
  await resetDemo("new_user");
  const { browser, page, problems } = await launch();
  const state = { firstMeal: null, hypothetical: [], samplesBefore: 0 };
  const advanceTo = async (to) => {
    const index = (await get(page, "/demo/replay")).event_index;
    const result = await api(page, "/demo/replay/advance", {
      method: "POST",
      json: { to, expected_event_index: index },
    });
    expect(result.status === 200, `advance to ${to} failed: ${result.status}`);
    return result.body;
  };

  await j.check("the demo card fills only the email; sign-in skips onboarding", async () => {
    await page.goto(`${BASE}/auth`);
    await page.getByTestId(`demo-${DEMO_EMAILS.new_user}`).click();
    expect(
      (await page.getByLabel("Email").inputValue()) === DEMO_EMAILS.new_user,
      "email not filled",
    );
    expect(
      (await page.getByLabel("Password", { exact: true }).inputValue()) === "",
      "a password was prefilled",
    );
    await page.getByLabel("Password", { exact: true }).fill(DEMO_PASSWORD);
    await page.getByTestId("auth-submit").click();
    await page.waitForURL(`${BASE}/`);
    await page.getByTestId("latest-glucose").waitFor();
  });
  await j.check("history is shown as a labelled recording, never as today's data", async () => {
    const text = await page.locator("body").innerText();
    expect(
      text.includes("Historical replay") && text.includes("Historical recording"),
      "provenance labels missing",
    );
    expect(
      (await page.getByTestId("provenance").innerText()).includes("Day 1"),
      "recording day not shown",
    );
    expect(
      !/2020-0\d|May 8/.test(await page.locator("main").innerText()),
      "a 2020 calendar date is shown",
    );
    await page.locator(".recharts-line").first().waitFor();
    await shot(page, "b01-home");
  });
  await j.check(
    "no simulated device is active before the user asks; nothing claims a real connection",
    async () => {
      await page.goto(`${BASE}/health?tab=devices`);
      await page.getByTestId("device-apple_watch").waitFor();
      for (const device of DEVICES) {
        expect(
          (await page.getByTestId(`device-${device}`).getAttribute("data-status")) === "inactive",
          `${device} already active`,
        );
      }
      expect(
        !/\bAuthorized\b|Live connected|\bDisconnect\b/i.test(
          await page.locator("main").innerText(),
        ),
        "connection wording",
      );
      await page.getByText("Used by the forecast.").waitFor();
    },
  );
  await j.check(
    "Simulate connection activates each of the four sources, one at a time",
    async () => {
      for (const device of DEVICES) {
        await page.getByTestId(`simulate-${device}`).click();
        await page.waitForFunction(
          (d) =>
            document.querySelector(`[data-testid=device-${d}]`)?.getAttribute("data-status") ===
            "demo_simulated",
          device,
          { timeout: 60_000 },
        );
      }
      const server = await get(page, "/demo/devices");
      expect(
        server.profiles.every((p) => p.status === "demo_simulated" && p.simulated === true),
        "server state",
      );
      expect(
        server.glucose_source.simulated === false &&
          server.glucose_source.data_class === "research_dataset",
        "glucose source mislabelled",
      );
      state.samplesBefore = server.profiles
        .flatMap((p) => p.metrics)
        .reduce((n, m) => n + m.observations_visible, 0);
      await shot(page, "b02-devices");
      await page.getByTestId("health-tab-wearables").click();
      await page.getByText("Simulated sample").first().waitFor();
      await page.getByText("Shown for context only").waitFor();
      await shot(page, "b03-wearables");
      await page.goto(`${BASE}/`);
      await page.getByText("Shown for context. Not used by the forecast.").waitFor();
    },
  );
  await j.check("glucose view: recording badge, chart, and no data entry in a replay", async () => {
    await page.goto(`${BASE}/health?tab=glucose`);
    await page.getByText("Historical recording").first().waitFor();
    await page.getByText("This account's glucose comes from a recording").waitFor();
    await page.locator(".recharts-line").first().waitFor();
  });
  await j.check("advance: the first recorded meal is logged and genuinely forecast", async () => {
    await page.goto(`${BASE}/`);
    await page.getByTestId("latest-glucose").waitFor();
    await advanceInUi(page);
    const replay = await get(page, "/demo/replay");
    state.firstMeal = replay.checkpoints.find((c) => c.id === "meal_01_forecast").result.meal_id;
    await page.goto(`${BASE}/meals/${state.firstMeal}`);
    await page.getByText("Forecast peak").first().waitFor();
    const prediction = await get(
      page,
      `/predictions/${(await get(page, `/meals/${state.firstMeal}`)).prediction_id}`,
    );
    expect(prediction.forecast.points.length === 25, "not a 25-point curve");
    expect(
      prediction.model.model_id === "glucotwin-fs13-blend-personalized" &&
        prediction.model.feature_set_version === "fs-1.3",
      "not the frozen model",
    );
    expect(prediction.forecast.uncertainty_status === "not_calibrated", "uncertainty status");
    const text = await page.locator("main").innerText();
    expect(
      text.includes(String(Math.round(prediction.forecast.metrics.peak_mg_dl))),
      "peak differs from the server's",
    );
    expect(
      text.includes("Waiting for the two hours after this meal") && !text.includes("Observed peak"),
      "observed values shown before release",
    );
    expect(
      text.includes("General forecast") && !/confidence interval|probabilit/i.test(text),
      "personalization / uncertainty wording",
    );
    await page.getByText("View these values as a table").click();
    expect((await page.locator("table tbody tr").count()) >= 25, "table alternative incomplete");
    await shot(page, "d01-forecast-pending");
  });
  await j.check(
    "a hand-entered meal gets a real forecast but no outcome (server says not_applicable)",
    async () => {
      await page.goto(`${BASE}/meals/log`);
      await page.getByText("Adding your own meal to a replay").waitFor();
      await page.getByTestId("manual-entry").click();
      await page.getByTestId("confirm-forecast").click();
      await page.getByText("Enter carbohydrates").waitFor();
      await fillMacros(page, HYPOTHETICAL);
      await page.getByTestId("confirm-forecast").click();
      await page.waitForURL(/\/meals\/meal_/, { timeout: 60_000 });
      const mealId = page.url().split("/").pop();
      state.hypothetical.push(mealId);
      await page.getByText("Forecast peak").first().waitFor();
      await page.getByText("No comparison for this meal").waitFor();
      await page.getByText("Added by you").first().waitFor();
      const outcome = await get(page, `/meals/${mealId}/outcome`);
      expect(
        outcome.status === "not_applicable" &&
          outcome.observed === null &&
          outcome.eligible_for_personalization === false,
        JSON.stringify(outcome).slice(0, 200),
      );
      await shot(page, "d02-hypothetical-meal");
    },
  );
  await j.check(
    "photo meal with analysis declined: upload, enter values, confirm, forecast",
    async () => {
      await page.goto(`${BASE}/meals/log`);
      let analyses = 0;
      page.on("request", (r) => {
        if (r.url().includes("/analyze")) analyses += 1;
      });
      await page
        .getByTestId("photo-input")
        .setInputFiles({ name: "notes.heic", mimeType: "image/heic", buffer: Buffer.from("x") });
      await page.getByText("HEIC photos aren't supported yet").waitFor();
      await page.getByTestId("photo-input").setInputFiles(await syntheticPhoto(page));
      await page.getByAltText("The meal photo you chose").waitFor();
      if ((await get(page, "/status")).meal_analysis.configured) {
        await page.getByTestId("analyze-photo").waitFor();
      } else {
        // With no analysis provider the app must say so and still let the meal be logged.
        await page.getByText("Photo analysis isn't switched on for this server").first().waitFor();
        expect(
          (await page.getByTestId("analyze-photo").count()) === 0,
          "Analyze offered although analysis is not configured",
        );
        console.log(
          "        (photo analysis is NOT configured on this server: manual path verified)",
        );
      }
      await page.getByRole("button", { name: "Save photo, enter values myself" }).click();
      await page.locator("#macro-carbs_g").waitFor();
      await fillMacros(page, {
        carbs_g: 30,
        protein_g: 25,
        fat_g: 10,
        fiber_g: 5,
        calories_kcal: 310,
      });
      await page.getByTestId("confirm-forecast").click();
      await page.waitForURL(/\/meals\/meal_/, { timeout: 60_000 });
      state.hypothetical.push(page.url().split("/").pop());
      await page.getByAltText("Photo of this meal").waitFor();
      await page.getByText("Forecast peak").first().waitFor();
      await page.getByText("No comparison for this meal").waitFor();
      expect(analyses === 0, "an analysis request was made without being asked for");
    },
  );
  await j.check(
    "advance: the recorded outcome is revealed and compared; hypothetical meals did not disturb it",
    async () => {
      await page.goto(`${BASE}/meals/${state.firstMeal}`);
      await page.getByText("Forecast peak").first().waitFor();
      await advanceInUi(page);
      await page.getByText("Observed peak").first().waitFor();
      const outcome = await get(page, `/meals/${state.firstMeal}/outcome`);
      expect(outcome.status === "scored", outcome.status);
      // Two hypothetical meals were logged inside this meal's two-hour window.
      expect(
        outcome.eligible_for_personalization === true &&
          !outcome.ineligibility_reasons.includes("intervening_meal"),
        JSON.stringify(outcome.ineligibility_reasons),
      );
      expect(
        (await page.locator("main").innerText()).includes(
          String(Math.round(outcome.observed.peak_mg_dl)),
        ),
        "observed peak differs from the server's",
      );
      expect((await page.locator(".recharts-line").count()) >= 2, "observed curve not overlaid");
      await page.getByText("Counts towards your personal history").waitFor();
      await shot(page, "d03-outcome");
    },
  );
  await j.check("personal history activates at exactly five eligible recorded meals", async () => {
    const replay = await advanceTo("retrieval_activation");
    const forecasts = replay.checkpoints
      .filter((c) => c.kind === "meal_forecast" && c.done)
      .map((c) => c.result);
    const activation = forecasts.at(-1);
    expect(
      activation.retrieval_active === true && activation.admissible_prior_meals === 5,
      JSON.stringify(activation),
    );
    expect(
      forecasts
        .slice(0, -1)
        .every((f) => f.retrieval_active === false && f.admissible_prior_meals < 5),
      "active before five",
    );
    await page.goto(`${BASE}/meals/${activation.meal_id}`);
    await page.getByText("Uses your history · 5 earlier meals").waitFor();
    await page.goto(`${BASE}/`);
    expect(
      (await page.getByTestId("personalization-status").innerText()).includes("use your history"),
      "home status",
    );
    await shot(page, "d04-history-active");
  });
  await j.check(
    "replay to the end: hypothetical meals never scored, never history; more samples released",
    async () => {
      await advanceTo("end");
      for (const mealId of state.hypothetical) {
        const outcome = await get(page, `/meals/${mealId}/outcome`);
        expect(
          outcome.status === "not_applicable" && outcome.observed === null,
          `hypothetical meal ${mealId} has status ${outcome.status}`,
        );
      }
      await page.goto(`${BASE}/insights`);
      await page.getByText("Meal by meal").waitFor();
      await page.locator(".recharts-line").first().waitFor();
      const text = await page.locator("main").innerText();
      expect(
        !/retrain(s|ed|ing)? on your/i.test(text.replace("is not retrained on your data", "")),
        "retraining claim",
      );
      const now = (await get(page, "/demo/devices")).profiles
        .flatMap((p) => p.metrics)
        .reduce((n, m) => n + m.observations_visible, 0);
      expect(
        now > state.samplesBefore,
        `simulated samples did not grow (${state.samplesBefore} → ${now})`,
      );
      await shot(page, "d05-insights");
    },
  );
  await j.check(
    "medications: a due reminder is marked Taken and that survives a reload",
    async () => {
      await page.goto(`${BASE}/health?tab=medications`);
      await page.getByTestId("regimen").first().waitFor();
      await page.getByText("FICTIONAL DEMO RECORD").first().waitFor();
      const due = (await get(page, "/medication-reminders/due")).due;
      expect(due.length > 0, "no due reminder at the end of the replay");
      const slot = due[0].scheduled_at;
      await page.getByTestId("due-dose").first().getByTestId("dose-taken").click();
      await page.getByText("Recorded as taken").waitFor();
      await page.reload();
      await page.getByTestId("regimen").first().waitFor();
      const doses = (await get(page, "/medication-doses?limit=200")).doses;
      expect(
        doses.some((d) => d.scheduled_at === slot && d.action === "taken" && d.actor === "user"),
        "taken dose not stored",
      );
      expect(
        !(await get(page, "/medication-reminders/due")).due.some((d) => d.scheduled_at === slot),
        "still due after being taken",
      );
      await page.getByText("Taken", { exact: true }).first().waitFor();
      await page.getByText("A record, not advice").waitFor();
      await shot(page, "f01-medications");
    },
  );
  await j.check("medications: Skip works on another due dose, when one exists", async () => {
    const due = (await get(page, "/medication-reminders/due")).due;
    if (!due.length) return;
    await page.getByTestId("due-dose").first().getByRole("button", { name: "Skip" }).click();
    await page.getByText("Recorded as skipped").waitFor();
    expect(
      (await get(page, "/medication-doses?limit=200")).doses.some(
        (d) => d.scheduled_at === due[0].scheduled_at && d.action === "skipped",
      ),
      "skip not stored",
    );
  });
  await j.check(
    "alerts: settings validate and save; server-raised alerts are listed with the disclaimer",
    async () => {
      await page.goto(`${BASE}/health?tab=alerts`);
      await page.locator("#alert-level").fill("50");
      await page.getByTestId("save-alert-settings").click();
      await page.getByText("Choose a level between 100 and 400").waitFor();
      const level = (await get(page, "/alert-settings")).threshold.value;
      await page.locator("#alert-level").fill(String(level));
      await page.getByTestId("save-alert-settings").click();
      await page.getByText("Alert settings saved").waitFor();
      await page.getByText("not medical advice").first().waitFor();
      const alerts = (await get(page, "/alerts?limit=100")).alerts;
      console.log(`        (${alerts.length} alert(s) raised by the server in this replay)`);
      if (alerts.length) {
        expect(
          (await page.getByTestId("alert-row").count()) === alerts.length,
          "alert rows differ from the server's",
        );
        if (alerts.some((a) => a.status === "active" && !a.acknowledged_at)) {
          await page.getByTestId("acknowledge").first().click();
          await page.getByText("Acknowledged").first().waitFor();
        }
      } else {
        await page.getByText("No alerts yet").waitFor();
      }
      await shot(page, "f02-alerts");
    },
  );
  await j.check("settings: answers can be revisited without redoing onboarding", async () => {
    await page.goto(`${BASE}/settings`);
    await page.getByTestId("edit-questionnaire").click();
    await page.getByTestId("step-consent").waitFor();
    await page.getByRole("link", { name: "Close" }).click();
    await page.waitForURL("**/settings");
  });
  await j.check(
    "old addresses redirect; a reload keeps the session; unknown pages are handled",
    async () => {
      for (const [from, to] of [
        ["/predict", "/meals/log"],
        ["/medications", "/health?tab=medications"],
        ["/profile", "/settings"],
        ["/connect-devices", "/health?tab=devices"],
      ]) {
        await page.goto(BASE + from);
        await page.waitForURL(BASE + to);
      }
      await page.goto(`${BASE}/`);
      await page.reload();
      await page.getByTestId("latest-glucose").waitFor();
      await page.goto(`${BASE}/nope`);
      await page.getByText("We couldn't find that page").waitFor();
    },
  );
  await j.check("phone layout: every screen fits, with bottom navigation", async () => {
    await page.setViewportSize(PHONE);
    for (const path of [
      "/",
      "/meals",
      `/meals/${state.firstMeal}`,
      "/meals/log",
      "/insights",
      "/health?tab=devices",
      "/health?tab=glucose",
      "/health?tab=wearables",
      "/health?tab=medications",
      "/health?tab=alerts",
      "/settings",
    ]) {
      await page.goto(BASE + path);
      await page.locator("main h1").first().waitFor();
      await page.waitForTimeout(800);
      const extra = await overflow(page);
      expect(extra <= 0, `${path} overflows by ${extra}px`);
    }
    await page.goto(`${BASE}/`);
    await page.getByTestId("latest-glucose").waitFor();
    expect(
      (await page.locator("nav[aria-label=Primary] >> visible=true").count()) === 1,
      "bottom navigation",
    );
    await shot(page, "m01-home-phone");
    await page.goto(`${BASE}/meals/${state.firstMeal}`);
    await page.getByText("Forecast peak").first().waitFor();
    await shot(page, "m02-forecast-phone");
    await page.setViewportSize(DESKTOP);
  });
  await j.check(
    "an expired session returns to sign-in with an explanation; sign-out clears the token",
    async () => {
      await page.goto(`${BASE}/`);
      await page.getByTestId("latest-glucose").waitFor();
      await page.evaluate(() => sessionStorage.setItem("glimpse.v2.token", "not-a-real-token"));
      await page.goto(`${BASE}/meals`);
      await page.waitForURL("**/auth");
      await page.getByText("Your session ended").waitFor();
      await page.getByLabel("Email").fill(DEMO_EMAILS.new_user);
      await page.getByLabel("Password", { exact: true }).fill(DEMO_PASSWORD);
      await page.getByTestId("auth-submit").click();
      await page.waitForURL(`${BASE}/`);
      await page.getByTestId("account-menu").click();
      await page.getByTestId("sign-out").click();
      await page.waitForURL("**/auth");
      expect(
        (await page.evaluate(() => sessionStorage.getItem("glimpse.v2.token"))) === null,
        "token kept after sign-out",
      );
    },
  );

  await browser.close();
  return { ...j, problems };
}
