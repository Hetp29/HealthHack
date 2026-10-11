// Everything a careful or careless person might do that the main journeys do not:
// invalid input, double clicks, reloads mid-request, outages, consent withdrawal,
// units, imports, deletion, a second tab, and the far end of the replay.
// One throwaway account plus the new-user demonstration (which it resets).
import {
  API,
  BASE,
  DEMO_EMAILS,
  DEMO_PASSWORD,
  api,
  expect,
  fillMacros,
  get,
  journey,
  launch,
  resetDemo,
  shot,
  signIn,
  syntheticPhoto,
} from "../lib.mjs";

const MEAL = { carbs_g: 45, protein_g: 20, fat_g: 12, fiber_g: 6, calories_kcal: 370 };
const nav = (page, name) => page.locator("aside nav a", { hasText: name }).click();
const settle = (page) => page.waitForTimeout(900);

/** Glucose every five minutes for the last three hours, sent as the user's own manual entries. */
async function seedGlucose(page) {
  const now = Date.now();
  const readings = [];
  for (let minutes = 180; minutes >= 1; minutes -= 5) {
    readings.push({
      observed_at: new Date(now - minutes * 60_000).toISOString(),
      value: 100 + Math.round(8 * Math.sin(minutes / 20)),
      unit: "mg/dL",
    });
  }
  const result = await api(page, "/cgm/readings", {
    method: "POST",
    json: { source: "manual_entry", device_id: "manual", readings },
  });
  expect(
    result.status === 200 && result.body.counts.inserted > 30,
    `seed glucose: ${result.status} ${JSON.stringify(result.body).slice(0, 200)}`,
  );
}

export default async function edgeCases() {
  const j = journey("Edge cases and recovery");
  const { browser, page, problems } = await launch();
  const email = `e2e-edge-${Date.now()}@glimpse-test.example`;
  const password = `e2e-${Math.random().toString(36).slice(2)}-password`;
  const s = {};

  await j.check("a double click on Create account makes one account", async () => {
    await page.goto(`${BASE}/auth`);
    await page.getByTestId("tab-register").click();
    await page.getByLabel("Your name").fill("Edge Case");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByTestId("auth-submit").dblclick();
    await page.waitForURL("**/onboarding");
    await page.getByTestId("step-welcome").waitFor();
    expect(
      !(await page.locator("body").innerText()).includes("already exists"),
      "the second click reported a duplicate account",
    );
  });

  await j.check(
    "unfinished onboarding survives signing out: answers kept, app still gated",
    async () => {
      await page.getByTestId("onboarding-continue").click();
      await page.getByTestId("consent-necessary_processing").click();
      await page.getByTestId("onboarding-continue").click();
      await page.getByTestId("step-about").waitFor();
      await page.locator("#height").fill("180");
      await page.getByTestId("onboarding-continue").click();
      await page.getByTestId("step-health").waitFor();
      await page.getByRole("button", { name: "Sign out" }).click();
      await page.waitForURL("**/auth");
      await signIn(page, email, password);
      await page.waitForURL("**/onboarding");
      await page.goto(`${BASE}/meals`); // a deep link must not get past unfinished onboarding
      await page.waitForURL("**/onboarding");
      const answers = (await get(page, "/onboarding")).answers;
      expect(answers.height?.value === 180, "saved answer lost");
      // It resumes where it stopped, with the saved values back in the form.
      await page.getByTestId("step-health").waitFor();
      await page.getByTestId("onboarding-back").click();
      await page.getByTestId("step-about").waitFor();
      expect(
        (await page.locator("#height").inputValue()) === "180",
        "height not restored into the form",
      );
      await page.getByTestId("onboarding-back").click();
      await page.getByTestId("step-consent").waitFor();
      expect(
        (await page.getByTestId("consent-necessary_processing").getAttribute("aria-checked")) ===
          "true",
        "consent not restored",
      );
      await page.getByTestId("onboarding-continue").click();
      await page.getByTestId("step-about").waitFor();
      await page.getByTestId("onboarding-continue").click();
      // Every optional step can be skipped.
      for (const step of ["health", "devices", "routine", "alerts"]) {
        await page.getByTestId(`step-${step}`).waitFor();
        await page.getByTestId("onboarding-skip").click();
      }
      await page.getByTestId("step-review").waitFor();
      await page.getByTestId("onboarding-continue").click();
      await page.getByTestId("onboarding-finish").click();
      await page.waitForURL(`${BASE}/`);
      await page.getByText("No glucose readings yet").waitFor();
    },
  );

  await j.check(
    "glucose: invalid readings refused inline; a valid one is stored and shown on Home",
    async () => {
      await page.goto(`${BASE}/health?tab=glucose`);
      const add = page.getByRole("button", { name: "Add reading" });
      await add.click();
      await page.getByText("Enter the reading as a number.").waitFor();
      await page.locator("#reading-value").fill("5");
      await add.click();
      await page.getByText("Enter a reading between 20 and 600").waitFor();
      await page.locator("#reading-value").fill("112");
      await add.click();
      await page.getByText("Reading saved.").waitFor();
      expect((await get(page, "/cgm/status")).latest.glucose_mg_dl === 112, "reading not stored");
      await nav(page, "Home");
      await page.getByTestId("latest-glucose").waitFor();
      expect(
        (await page.getByTestId("latest-glucose").innerText()) === "112",
        "Home does not show the reading",
      );
      await page.getByText("Your data").first().waitFor();
    },
  );

  await j.check(
    "glucose: a Dexcom Clarity export imports and reports exactly what was stored",
    async () => {
      await page.goto(`${BASE}/health?tab=glucose`);
      await page
        .locator("#cgm-file")
        .setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
      await page.getByText("Choose the .csv file").waitFor();
      const day = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
      const header =
        "Index,Timestamp (YYYY-MM-DDThh:mm:ss),Event Type,Event Subtype,Patient Info,Device Info,Source Device ID,Glucose Value (mg/dL),Insulin Value (u),Carb Value (grams),Duration (hh:mm:ss),Glucose Rate of Change (mg/dL/min),Transmitter Time (Long Integer),Transmitter ID";
      const rows = [0, 5, 10, 15, 20, 25].map(
        (m, i) =>
          `${i + 3},${day}T08:${String(m).padStart(2, "0")}:10,EGV,,,,iPhone G6,${110 + i},,,,,${7000 + i},8ABC12`,
      );
      const csv =
        [
          header,
          "1,,FirstName,,Test,,,,,,,,,",
          "2,,Device,,,Dexcom G6 Mobile App,iPhone G6,,,,,,,",
          ...rows,
        ].join("\n") + "\n";
      await page
        .locator("#cgm-file")
        .setInputFiles({ name: "clarity.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
      await page.getByRole("button", { name: "Import readings" }).click();
      await page.getByText("6 readings imported").waitFor();
      await page
        .locator("#cgm-file")
        .setInputFiles({ name: "clarity.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
      await page.getByRole("button", { name: "Import readings" }).click();
      await page.getByText("No new readings").waitFor(); // the same file twice stores nothing twice
      await page
        .locator("#cgm-file")
        .setInputFiles({
          name: "broken.csv",
          mimeType: "text/csv",
          buffer: Buffer.from("not,a,real,export\n1,2,3,4\n"),
        });
      await page.getByRole("button", { name: "Import readings" }).click();
      await page.getByText("The file wasn't imported").waitFor();
    },
  );

  await j.check(
    "units: mmol/L converts every glucose value and is remembered; the stored data is unchanged",
    async () => {
      await seedGlucose(page);
      await page.goto(`${BASE}/settings`);
      await page.locator('[data-choice="unit:mmol/L"]').click();
      await nav(page, "Home");
      await page.getByTestId("latest-glucose").waitFor();
      const status = await get(page, "/cgm/status");
      const shown = await page.getByTestId("latest-glucose").innerText();
      expect(
        shown === (status.latest.glucose_mg_dl / 18.016).toFixed(1),
        `Home shows ${shown} for ${status.latest.glucose_mg_dl} mg/dL`,
      );
      await page.getByText("mmol/L").first().waitFor();
      await page.reload();
      await page.getByTestId("latest-glucose").waitFor();
      expect(
        (await page.getByTestId("latest-glucose").innerText()) === shown,
        "unit not remembered after a reload",
      );
      s.mmol = true;
    },
  );

  await j.check(
    "meal form: negative, non-numeric and over-limit values refused; zero and the maximum accepted",
    async () => {
      await page.goto(`${BASE}/meals/log`);
      await page.getByTestId("manual-entry").click();
      await fillMacros(page, {
        carbs_g: -5,
        protein_g: "abc",
        fat_g: 1001,
        fiber_g: 201,
        calories_kcal: 10001,
      });
      await page.getByTestId("confirm-forecast").click();
      await page.getByText("This can't be negative.").waitFor();
      await page.getByText("Enter a number.").waitFor();
      expect((await page.getByText(/Enter a value up to/).count()) === 3, "limit messages");
      expect((await get(page, "/meals")).meals.length === 0, "an invalid meal was saved");
      await fillMacros(page, { carbs_g: 0, protein_g: 0, fat_g: 0, fiber_g: 0, calories_kcal: 0 });
      await page.getByTestId("confirm-forecast").click();
      await page.waitForURL(/\/meals\/meal_/, { timeout: 60_000 });
      await page.getByText("Forecast peak").first().waitFor();
      s.zeroMeal = page.url().split("/").pop();
      const prediction = await get(
        page,
        `/predictions/${(await get(page, `/meals/${s.zeroMeal}`)).prediction_id}`,
      );
      expect(
        prediction.forecast.points.every(
          (p) => Number.isFinite(p.glucose_mg_dl) && p.glucose_mg_dl > 20 && p.glucose_mg_dl < 600,
        ),
        "implausible forecast for an empty meal",
      );
      // In mmol/L the forecast page shows the converted peak.
      const peak = (prediction.forecast.metrics.peak_mg_dl / 18.016).toFixed(1);
      expect(
        (await page.locator("main").innerText()).includes(peak),
        `forecast page does not show ${peak} mmol/L`,
      );
      await page.goto(`${BASE}/settings`);
      await page.locator('[data-choice="unit:mg/dL"]').click();
    },
  );

  await j.check(
    "a reload in the middle of Confirm and forecast leaves one meal and a consistent screen",
    async () => {
      await page.goto(`${BASE}/meals/log`);
      await page.getByTestId("manual-entry").click();
      await fillMacros(page, MEAL);
      const before = (await get(page, "/meals")).meals.length;
      await page.getByTestId("confirm-forecast").click();
      await page.reload();
      await settle(page);
      const meals = (await get(page, "/meals")).meals;
      expect(
        meals.length - before <= 1,
        `the interrupted submit created ${meals.length - before} meals`,
      );
      if (meals.length > before) {
        const meal = meals[0];
        await page.goto(`${BASE}/meals/${meal.meal_id}`);
        if (!meal.prediction_id && !(await get(page, `/meals/${meal.meal_id}`)).prediction_id) {
          await page.getByTestId("get-forecast").click(); // the saved meal can still be forecast
        }
        await page.getByText("Forecast peak").first().waitFor();
      }
      s.meals = (await get(page, "/meals")).meals.length;
    },
  );

  await j.check(
    "photo: a corrupt file and an oversized file are refused, and no stray meal is left behind",
    async () => {
      await page.goto(`${BASE}/meals/log`);
      const before = (await get(page, "/meals?include_voided=true")).meals.length;
      await page
        .getByTestId("photo-input")
        .setInputFiles({
          name: "huge.jpg",
          mimeType: "image/jpeg",
          buffer: Buffer.alloc(10 * 1024 * 1024 + 10, 1),
        });
      await page.getByText(/Choose one under 10 MB/).waitFor();
      await page
        .getByTestId("photo-input")
        .setInputFiles({
          name: "broken.jpg",
          mimeType: "image/jpeg",
          buffer: Buffer.from("this is not a jpeg at all, just text pretending"),
        });
      await page.getByRole("button", { name: "Save photo, enter values myself" }).click();
      await page.getByText("The photo wasn't uploaded").waitFor();
      expect(
        (await page.locator("main").innerText()).match(
          /couldn't be opened|doesn't look like|isn't supported/,
        ) !== null,
        "no plain explanation for the corrupt photo",
      );
      expect(
        (await get(page, "/meals?include_voided=true")).meals.length === before,
        "a meal was created for a rejected photo",
      );
      // The way out works: enter the values without the photo.
      await page.getByRole("button", { name: "Enter values without the photo" }).click();
      await page.locator("#macro-carbs_g").waitFor();
    },
  );

  await j.check(
    "deleting a meal removes it from Meals and Home; its old address explains itself",
    async () => {
      await page.goto(`${BASE}/meals/${s.zeroMeal}`);
      await page.getByRole("button", { name: "Delete" }).click();
      await page.getByRole("button", { name: "Delete meal" }).click();
      await page.waitForURL(`${BASE}/meals`);
      await settle(page);
      expect(
        (await page.getByTestId("meal-row").count()) === s.meals - 1,
        "Meals still lists the deleted meal",
      );
      await nav(page, "Home");
      await settle(page);
      expect(
        (await page
          .locator(`[data-testid=home-meal-row][data-meal-id="${s.zeroMeal}"]`)
          .count()) === 0,
        "Home still lists the deleted meal",
      );
      expect(
        (await page.getByTestId("home-latest-forecast").getAttribute("data-meal-id")) !==
          s.zeroMeal,
        "Home's latest forecast is the deleted meal's",
      );
      await page.goto(`${BASE}/meals/${s.zeroMeal}`);
      await page.getByText("This meal isn't here").waitFor();
    },
  );

  await j.check(
    "medications: validation, add with a time, reminder switch, one dose per double click, previous and back",
    async () => {
      await page.goto(`${BASE}/health?tab=medications`);
      await page.getByTestId("add-medication").click();
      await page.getByRole("button", { name: "Save medication" }).click();
      await page.getByText("Enter the medication's name.").waitFor();
      await page.locator("#med-name").fill("Edge Medication");
      await page.locator("#med-amount").fill("-3");
      await page.getByRole("button", { name: "Save medication" }).click();
      await page.getByText("Enter the dose as a number above 0.").waitFor();
      await page.locator("#med-amount").fill("500");
      await page.getByRole("button", { name: "Add time" }).click();
      await page.getByRole("button", { name: "Save medication" }).click();
      await page.getByTestId("regimen").first().waitFor();
      let regimen = (await get(page, "/medications")).regimens[0];
      expect(
        regimen.name === "Edge Medication" &&
          regimen.dose.amount === 500 &&
          regimen.schedule_times.length === 1 &&
          regimen.reminders_enabled === true,
        JSON.stringify(regimen),
      );
      await page.getByRole("switch", { name: "Reminders for Edge Medication" }).click();
      await settle(page);
      expect(
        (await get(page, "/medications")).regimens[0].reminders_enabled === false,
        "reminder switch not saved",
      );
      await page.getByRole("button", { name: "Log a dose taken now" }).dblclick();
      await page.getByText("Recorded as taken").first().waitFor();
      await settle(page);
      expect(
        (await get(page, "/medication-doses")).doses.filter((d) => d.action === "taken").length ===
          1,
        "a double click recorded two doses",
      );
      await page.getByRole("button", { name: "Move to previous" }).click();
      await page.getByText("Previous medications (1)").waitFor();
      await page.reload();
      await page.getByText("Previous medications (1)").click();
      await page.getByRole("button", { name: "Move back to current" }).click();
      await settle(page);
      regimen = (await get(page, "/medications")).regimens[0];
      expect(regimen.status === "current", "not moved back");
    },
  );

  await j.check("settings: profile changes validate and persist", async () => {
    await page.goto(`${BASE}/settings`);
    await page.locator("#settings-timezone").fill("Mars/Olympus");
    await page.getByRole("button", { name: "Save profile" }).click();
    await page.getByText("Enter a timezone such as").waitFor();
    await page.locator("#settings-timezone").fill("Asia/Tokyo");
    await page.locator("#settings-name").fill("Renamed Person");
    await page.getByRole("button", { name: "Save profile" }).click();
    await page.getByText("Profile saved").waitFor();
    const me = await get(page, "/me");
    expect(
      me.display_name === "Renamed Person" && me.timezone === "Asia/Tokyo",
      JSON.stringify([me.display_name, me.timezone]),
    );
    await page.reload();
    expect(
      (await page.locator("#settings-name").inputValue()) === "Renamed Person",
      "name not shown after reload",
    );
    await nav(page, "Home");
    await page.getByText("Renamed").first().waitFor();
    // Times are now shown in the account's timezone.
    const latest = (await get(page, "/cgm/status")).latest.observed_at;
    const tokyo = new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
      timeZone: "Asia/Tokyo",
    }).format(new Date(latest));
    expect(
      (await page.locator("main").innerText()).includes(tokyo),
      `Home does not show ${tokyo} (Tokyo time)`,
    );
  });

  await j.check(
    "withdrawing the required consent really stops processing, and granting it brings everything back",
    async () => {
      await page.goto(`${BASE}/settings`);
      await page.getByTestId("consent-necessary").click();
      await page.getByRole("button", { name: "Switch off" }).click();
      await page.getByText("Permission switched off").waitFor();
      for (const path of ["/meals", "/cgm/status", "/predictions", "/medications", "/alerts"]) {
        const result = await api(page, path);
        expect(
          result.status === 403 && result.body.code === "consent_required",
          `${path} answered ${result.status} without consent`,
        );
      }
      const blocked = await api(page, "/meals", {
        method: "POST",
        json: { occurred_at: new Date().toISOString(), macros: MEAL },
      });
      expect(blocked.status === 403, "a meal could be logged without consent");
      for (const [name, path] of [
        ["Home", "/"],
        ["Meals", "/meals"],
        ["Insights", "/insights"],
        ["Health", "/health"],
      ]) {
        await nav(page, name);
        await page.waitForURL(BASE + path);
        await settle(page);
        const text = await page.locator("main").innerText();
        expect(/permission/i.test(text), `${name} does not explain that permission is off`);
        expect(
          !/didn't load|Something went wrong/i.test(text),
          `${name} shows an error instead of the permission state`,
        );
      }
      for (const path of ["/meals/log", `/meals/${s.zeroMeal}`, "/health?tab=medications"]) {
        await page.goto(BASE + path);
        await page.getByTestId("permission-required").waitFor();
      }
      await page.getByRole("link", { name: "Review permissions" }).click();
      await page.waitForURL(/\/settings/);
      await page.getByTestId("consent-necessary").click();
      await page.getByText("Permission switched on").waitFor();
      await nav(page, "Home");
      await page.getByTestId("latest-glucose").waitFor();
      expect(
        (await get(page, "/meals")).meals.length === s.meals - 1,
        "data did not come back intact",
      );
    },
  );

  await api(page, "/consents/necessary_processing", { method: "PUT", json: { state: "granted" } });

  await j.check(
    "server outage: screens say so, nothing stale is passed off as current, and Try again recovers",
    async () => {
      await nav(page, "Meals");
      await page.getByTestId("meal-row").first().waitFor();
      await page.route("**/api/**", (route) => route.abort("connectionrefused"));
      await page.reload();
      await page.getByText("Can't reach GLIMPSE").first().waitFor();
      await shot(page, "x01-outage");
      await page.unroute("**/api/**");
      await page.getByRole("button", { name: "Try again" }).first().click();
      await page.getByTestId("meal-row").first().waitFor();
      // An outage in the middle of a session: the open screen reports it and recovers in place.
      await page.route("**/api/v2/meals**", (route) => route.abort("connectionrefused"));
      await nav(page, "Home");
      await nav(page, "Meals");
      await page.waitForTimeout(2500);
      await page.unroute("**/api/v2/meals**");
      const retry = page.getByRole("button", { name: "Try again" });
      if (await retry.count()) await retry.first().click();
      await page.getByTestId("meal-row").first().waitFor();
    },
  );

  await j.check("a second tab on the same account sees what the first one did", async () => {
    const second = await launch();
    await signIn(second.page, email, password);
    await second.page.waitForURL(`${BASE}/`);
    await second.page.getByTestId("latest-glucose").waitFor();
    const before = await second.page.getByTestId("home-meal-row").count();
    await page.goto(`${BASE}/meals/log`);
    await page.getByTestId("manual-entry").click();
    await fillMacros(page, { ...MEAL, carbs_g: 77 });
    await page.getByTestId("confirm-forecast").click();
    await page.waitForURL(/\/meals\/meal_/, { timeout: 60_000 });
    await second.page.locator("aside nav a", { hasText: "Meals" }).click();
    await second.page.getByText("77 g carbs").first().waitFor();
    await second.page.locator("aside nav a", { hasText: "Home" }).click();
    await second.page.getByText("77 g carbs").first().waitFor();
    expect(
      (await second.page.getByTestId("home-meal-row").count()) >= before,
      "second tab lost meals",
    );
    // Signing out in one tab ends only that tab's session.
    await second.page.getByTestId("account-menu").click();
    await second.page.getByTestId("sign-out").click();
    await second.page.waitForURL("**/auth");
    await nav(page, "Home");
    await page.getByTestId("latest-glucose").waitFor();
    problems.push(...second.problems);
    await second.browser.close();
  });

  await j.check(
    "account deletion needs the right password and really ends the account",
    async () => {
      await page.goto(`${BASE}/settings`);
      await page.getByRole("button", { name: "Delete my account" }).click();
      await page.locator("#delete-password").fill("not-the-password-at-all");
      await page.getByRole("button", { name: "Delete everything" }).click();
      await page.getByText("That email and password don't match.").waitFor();
      expect((await get(page, "/me")).email === email, "the session ended after a wrong password");
      await page.locator("#delete-password").fill(password);
      await page.getByRole("button", { name: "Delete everything" }).click();
      await page.waitForURL("**/auth");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password", { exact: true }).fill(password);
      await page.getByTestId("auth-submit").click();
      await page.getByText("That email and password don't match.").waitFor();
    },
  );

  // ---- the demonstration's edges -------------------------------------------------
  await resetDemo("new_user");
  await j.check(
    "replay: a double click advances exactly one event; +30 minutes moves time, not events",
    async () => {
      await signIn(page, DEMO_EMAILS.new_user, DEMO_PASSWORD);
      await page.waitForURL(`${BASE}/`);
      await page.getByTestId("latest-glucose").waitFor();
      await page.getByTestId("demo-controls-open").click();
      await page.getByTestId("demo-advance").dblclick();
      await page.waitForFunction(
        async (url) =>
          (
            await (
              await fetch(url, {
                headers: { Authorization: `Bearer ${sessionStorage.getItem("glimpse.v2.token")}` },
              })
            ).json()
          ).event_index >= 1,
        `${API}/demo/replay`,
        { timeout: 120_000, polling: 500 },
      );
      await page.waitForTimeout(4000);
      let replay = await get(page, "/demo/replay");
      expect(
        replay.event_index === 1 && replay.data.meals_logged === 1,
        `event_index ${replay.event_index}, meals ${replay.data.meals_logged}`,
      );
      const before = new Date(replay.position).getTime();
      await page.getByRole("button", { name: "+30 minutes" }).click();
      await page.getByText("Moved 30 minutes forward").waitFor();
      replay = await get(page, "/demo/replay");
      expect(
        replay.event_index === 1 && new Date(replay.position).getTime() - before === 30 * 60_000,
        "+30 minutes",
      );
      expect(
        (await page.getByTestId("replay-position").innerText()).includes("1:56"),
        "sheet shows the old position",
      );
      // Nothing from after the replay position is visible.
      const readings = (await get(page, "/cgm/readings?limit=5000")).readings;
      expect(
        readings.every((r) => r.observed_at <= replay.position),
        "a reading from the replay's future is visible",
      );
      await page.keyboard.press("Escape");
    },
  );

  await j.check("replay end: nothing left to advance, and the app says so everywhere", async () => {
    const index = (await get(page, "/demo/replay")).event_index;
    const end = await api(page, "/demo/replay/advance", {
      method: "POST",
      json: { to: "end", expected_event_index: index },
    });
    expect(
      end.status === 200 && end.body.status === "complete" && end.body.next_checkpoint === null,
      "did not reach the end",
    );
    await page.reload();
    await page.getByText("The recording has been replayed to its end").first().waitFor();
    await page.getByTestId("demo-controls-open").click();
    expect(await page.getByTestId("demo-advance").isDisabled(), "Advance is enabled at the end");
    expect(
      await page.getByRole("button", { name: "+30 minutes" }).isDisabled(),
      "+30 minutes is enabled at the end",
    );
    const again = await api(page, "/demo/replay/advance", {
      method: "POST",
      json: { expected_event_index: end.body.event_index },
    });
    expect(
      again.status === 200 &&
        again.body.advanced === false &&
        again.body.event_index === end.body.event_index,
      "advancing past the end changed something",
    );
    await page.keyboard.press("Escape");
    // Personal history switches on at the first forecast with five eligible earlier meals, never before,
    // and the count only grows by meals whose outcome the server scored as eligible.
    const forecasts = end.body.checkpoints
      .filter((c) => c.kind === "meal_forecast")
      .map((c) => c.result);
    const counts = forecasts.map((f) => f.admissible_prior_meals);
    expect(
      forecasts.every((f) => f.retrieval_active === f.admissible_prior_meals >= 5),
      `activation does not follow the five-meal rule: ${counts}`,
    );
    expect(
      counts[0] === 0 &&
        counts.includes(5) &&
        counts.every((c, i) => i === 0 || c >= counts[i - 1]),
      `eligible-meal counts: ${counts}`,
    );
    const eligible = end.body.checkpoints.filter(
      (c) => c.kind === "outcome_reveal" && c.result.eligible_for_personalization,
    ).length;
    expect(
      counts.at(-1) <= eligible,
      `more eligible meals counted (${counts.at(-1)}) than were scored eligible (${eligible})`,
    );
    await nav(page, "Insights");
    await page.getByText("Meal by meal").waitFor();
    await settle(page);
    expect(
      (await page.locator("table tbody tr").count()) === 9,
      "Insights does not list the nine recorded meals",
    );
  });

  await j.check(
    "medications in the demo: Snooze hides a due dose until later and is stored",
    async () => {
      await page.goto(`${BASE}/health?tab=medications`);
      await page.getByTestId("due-dose").first().waitFor();
      const due = (await get(page, "/medication-reminders/due")).due;
      await page
        .getByTestId("due-dose")
        .first()
        .getByRole("button", { name: "Snooze 15 min" })
        .click();
      await page.getByText("Snoozed for 15 minutes").waitFor();
      await settle(page);
      const after = (await get(page, "/medication-reminders/due")).due;
      expect(
        after.length === due.length - 1 &&
          !after.some((d) => d.scheduled_at === due[0].scheduled_at),
        "the snoozed dose is still due",
      );
      const stored = (await get(page, "/medication-doses?limit=200")).doses.find(
        (d) => d.scheduled_at === due[0].scheduled_at && d.action === "snoozed",
      );
      expect(stored && stored.snoozed_until, "snooze not stored");
      expect(
        (await page.getByTestId("due-dose").count()) === after.length,
        "the list still shows the snoozed dose",
      );
    },
  );

  await j.check("another account cannot use the demonstration's controls or data", async () => {
    const register = await fetch(`${API}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: `e2e-x-${Date.now()}@glimpse-test.example`,
        password,
        timezone: "UTC",
      }),
    });
    const token = (await register.json()).session.token;
    const auth = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
    await fetch(`${API}/consents/necessary_processing`, {
      method: "PUT",
      headers: auth,
      body: JSON.stringify({ state: "granted" }),
    });
    for (const [method, path] of [
      ["GET", "/demo/replay"],
      ["POST", "/demo/replay/advance"],
      ["POST", "/demo/replay/reset"],
      ["GET", "/demo/devices"],
      ["POST", "/demo/devices/fitbit/activate"],
    ]) {
      const r = await fetch(API + path, { method, headers: auth });
      expect(r.status === 403, `${method} ${path} answered ${r.status} for an ordinary account`);
    }
    expect(
      (await fetch(`${API}/demo/replay`)).status === 401,
      "replay readable without signing in",
    );
    await fetch(`${API}/account`, {
      method: "DELETE",
      headers: auth,
      body: JSON.stringify({ password }),
    });
  });

  await browser.close();
  return { ...j, problems };
}
