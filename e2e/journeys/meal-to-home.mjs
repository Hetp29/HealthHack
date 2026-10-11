// The audit journey: a meal's photo, nutrition and forecast must reach the Home
// dashboard, and nothing that should not move may move. Every assertion compares what
// the browser shows with what the backend holds.
//
// Runs on the new-user demonstration (which it resets) plus one throwaway account.
// With E2E_OPENAI=1 and E2E_MEAL_PHOTO the photo is analysed by the real model (one
// paid call) and the estimate is then edited; otherwise the photo is uploaded and the
// values typed in.
import { existsSync } from "node:fs";

import {
  API,
  BASE,
  DEMO_EMAILS,
  DEMO_PASSWORD,
  advanceInUi,
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

const PROFILE_A = { carbs_g: 30, protein_g: 20, fat_g: 12, fiber_g: 5, calories_kcal: 308 };
const PROFILE_B = { carbs_g: 90, protein_g: 20, fat_g: 12, fiber_g: 5, calories_kcal: 548 };
const KEYS = Object.keys(PROFILE_A);
const pick = (source) => Object.fromEntries(KEYS.map((k) => [k, source[k]]));
const same = (a, b) => JSON.stringify(pick(a)) === JSON.stringify(pick(b));
const summary = (m) =>
  `${m.carbs_g} g carbs · ${m.protein_g} g protein · ${m.fat_g} g fat · ${m.calories_kcal} kcal`;

/** What Home shows right now, read from the page itself. */
async function home(page, { navigate = true } = {}) {
  if (navigate) await page.locator("aside nav a", { hasText: "Home" }).click(); // in-app navigation: no page reload
  await page.getByTestId("latest-glucose").waitFor();
  // Home refetches in the background after a change; read it once it has settled.
  let previous = null;
  for (let i = 0; i < 30; i += 1) {
    await page.waitForTimeout(500);
    const current = await readHome(page);
    if (previous !== null && JSON.stringify(current) === JSON.stringify(previous)) return current;
    previous = current;
  }
  return previous;
}

function readHome(page) {
  return page.evaluate(() => {
    const text = (el) => el?.innerText.replace(/\s+/g, " ").trim() ?? null;
    const panel = (title) =>
      text(
        [...document.querySelectorAll("section")].find(
          (s) => s.querySelector("h2")?.textContent === title,
        ),
      );
    const latest = document.querySelector("[data-testid=home-latest-forecast]");
    return {
      glucose: text(document.querySelector("[data-testid=latest-glucose]")),
      glucosePanel: panel("Glucose"),
      latestMealId: latest?.dataset.mealId ?? null,
      latestPredictionId: latest?.dataset.predictionId ?? null,
      latestMealText: text(document.querySelector("[data-testid=home-latest-forecast-meal]")),
      latestPeak: text(document.querySelector("[data-testid=home-latest-forecast-peak]")),
      latestPanel: panel("Latest forecast"),
      rows: [...document.querySelectorAll("[data-testid=home-meal-row]")].map((r) => ({
        id: r.dataset.mealId,
        text: text(r),
      })),
      personalization: panel("Personal history"),
      wearables: panel("Wearables"),
      medications: panel("Medications"),
      alerts: panel("Forecast alerts"),
    };
  });
}

/** The measured glucose for the 12 hours before a fixed instant, whatever "now" is. */
const readingsUpTo = async (page, instant) => {
  const start = new Date(new Date(instant).getTime() - 12 * 3_600_000).toISOString();
  const body = await get(
    page,
    `/cgm/readings?limit=5000&start=${encodeURIComponent(start)}&end=${encodeURIComponent(instant)}`,
  );
  return JSON.stringify(body.readings.map((r) => [r.observed_at, r.glucose_mg_dl]));
};

export default async function mealToHome() {
  const j = journey("Meal → nutrition → forecast → Home dashboard");
  await resetDemo("new_user");
  const { browser, page, problems } = await launch();
  const paid = process.env.E2E_OPENAI === "1" && existsSync(process.env.E2E_MEAL_PHOTO ?? "");
  const s = { sent: [], analyses: 0, report: {} };
  page.on("request", (r) => {
    const path = new URL(r.url()).pathname;
    if (r.method() === "PATCH" && path.endsWith("/confirm"))
      s.sent.push({ kind: "confirm", body: JSON.parse(r.postData()) });
    if (r.method() === "POST" && path.endsWith("/api/v2/meals"))
      s.sent.push({ kind: "create", body: JSON.parse(r.postData()) });
    if (r.method() === "POST" && path.endsWith("/analyze")) s.analyses += 1;
  });
  page.on("requestfinished", async (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname.endsWith("/meals/images")) {
      const headers = await r.allHeaders(); // the body is a file, which only the wire headers describe
      s.upload = { type: headers["content-type"], bytes: Number(headers["content-length"] ?? 0) };
    }
  });

  await j.check(
    "setup: sign in, switch on one simulated wearable, replay one recorded meal",
    async () => {
      await signIn(page, DEMO_EMAILS.new_user, DEMO_PASSWORD);
      await page.waitForURL(`${BASE}/`);
      await api(page, "/demo/devices/apple_watch/activate", { method: "POST" });
      await page.reload();
      await page.getByTestId("latest-glucose").waitFor();
      await advanceInUi(page);
      const replay = await get(page, "/demo/replay");
      s.position = replay.position;
      s.recorded = replay.checkpoints.find((c) => c.id === "meal_01_forecast").result;
      s.readings = await readingsUpTo(page, s.position);
      s.wearables = JSON.stringify(
        (await get(page, "/wearables/observations?limit=5000")).observations
          .map((o) => o.observation_id)
          .sort(),
      );
      s.before = await home(page);
      expect(
        s.before.latestMealId === s.recorded.meal_id,
        "Home does not show the recorded meal's forecast",
      );
      expect(
        s.before.rows.length === 1 && s.before.rows[0].id === s.recorded.meal_id,
        "recent meals before",
      );
      await shot(page, "h01-home-before");
    },
  );

  await j.check("upload: the photo's bytes reach the server and a draft meal exists", async () => {
    await page.goto(`${BASE}/meals/log`);
    const file = paid ? process.env.E2E_MEAL_PHOTO : await syntheticPhoto(page);
    await page.getByTestId("photo-input").setInputFiles(file);
    await page.getByAltText("The meal photo you chose").waitFor();
    expect(s.analyses === 0 && !s.upload, "something was sent before the user asked");
    if (paid) {
      await page.getByTestId("analyze-photo").click();
      await page.getByTestId("estimate").waitFor({ timeout: 120_000 });
    } else {
      await page.getByRole("button", { name: "Save photo, enter values myself" }).click();
      await page.locator("#macro-carbs_g").waitFor();
    }
    await page.waitForFunction(() => true); // let the request-finished handler run
    for (let i = 0; i < 20 && !s.upload; i += 1) await page.waitForTimeout(100);
    const size = paid ? null : file.buffer.length;
    expect(
      s.upload && /^image\/(jpeg|png|webp)$/.test(s.upload.type),
      `upload: ${JSON.stringify(s.upload)}`,
    );
    // The request body is the file itself: raw bytes, not a form or a re-encoding.
    if (size !== null)
      expect(s.upload.bytes === size, `sent ${s.upload.bytes} bytes for a ${size}-byte file`);
    const meals = (await get(page, "/meals?limit=50")).meals;
    s.photoMeal = meals.find((m) => m.entry_method === "photo");
    expect(
      s.photoMeal &&
        s.photoMeal.photo &&
        s.photoMeal.confirmation === null &&
        s.photoMeal.prediction_id === null,
      "draft meal record",
    );
    const image = await page.evaluate(
      async ({ url }) => {
        const r = await fetch(url, {
          headers: { Authorization: `Bearer ${sessionStorage.getItem("glimpse.v2.token")}` },
        });
        return {
          status: r.status,
          type: r.headers.get("content-type"),
          bytes: (await r.arrayBuffer()).byteLength,
        };
      },
      { url: `${API}/meals/${s.photoMeal.meal_id}/image` },
    );
    expect(image.status === 200 && image.bytes > 1000, `stored image: ${JSON.stringify(image)}`);
    if (!paid)
      expect(
        s.photoMeal.photo.width === 800 && s.photoMeal.photo.height === 600,
        "stored photo is not the uploaded picture",
      );
    s.report.upload = { sent: s.upload, stored: image };
  });

  await j.check(
    paid
      ? "analysis: the estimate shown is the server's stored estimate (real OpenAI, one call)"
      : "analysis: skipped (no paid call in this run)",
    async () => {
      if (!paid) return;
      const stored = (await get(page, `/meals/${s.photoMeal.meal_id}`)).estimate;
      expect(
        stored && stored.status === "ok" && stored.totals,
        `estimate status: ${stored?.status} ${stored?.not_food_reason ?? ""}`,
      );
      for (const key of KEYS) {
        expect(
          Number(await page.locator(`#macro-${key}`).inputValue()) === stored.totals[key],
          `${key} shown differs from the stored estimate`,
        );
      }
      expect(
        (await page.getByTestId("estimate").locator("li").count()) >= stored.items.length,
        "foods listed",
      );
      s.report.aiEstimate = {
        model: stored.provenance.ai_model_reported,
        totals: pick(stored.totals),
        items: stored.items.map((i) => i.name),
      };
      expect(s.analyses === 1, `analysis requests: ${s.analyses}`);
    },
  );

  await j.check(
    "before confirmation Home lists the unconfirmed meal and changes nothing else",
    async () => {
      const now = await home(page);
      const row = now.rows.find((r) => r.id === s.photoMeal.meal_id);
      expect(
        row &&
          row.text.includes("Nutrition not confirmed yet") &&
          !row.text.includes("Forecast peak"),
        `draft row: ${row?.text}`,
      );
      expect(
        now.latestMealId === s.before.latestMealId && now.latestPeak === s.before.latestPeak,
        "latest forecast moved before any confirmation",
      );
      for (const key of [
        "glucose",
        "glucosePanel",
        "personalization",
        "wearables",
        "medications",
      ]) {
        expect(now[key] === s.before[key], `${key} changed after an upload`);
      }
      expect(
        (await get(page, "/predictions?limit=100")).predictions.length === 1,
        "a forecast exists for an unconfirmed meal",
      );
    },
  );

  await j.check(
    "resume, edit to profile A, confirm: the edited values are what is sent and stored",
    async () => {
      await page.locator("aside nav a", { hasText: "Meals" }).click();
      await page.locator(`[data-testid=meal-row] a[href*="${s.photoMeal.meal_id}"]`).click();
      await page.getByAltText("The meal photo you chose").waitFor(); // the stored photo, fetched back from the server
      if (!paid)
        await page.getByRole("button", { name: "Save photo, enter values myself" }).click();
      await page.locator("#macro-carbs_g").waitFor();
      if (paid) {
        // The estimate came back from the server on resume: it is stored, not a placeholder.
        expect(
          Number(await page.locator("#macro-carbs_g").inputValue()) ===
            s.report.aiEstimate.totals.carbs_g,
          "estimate not restored on resume",
        );
      }
      await fillMacros(page, PROFILE_A);
      await page.getByTestId("confirm-forecast").click();
      await page.waitForURL(new RegExp(`/meals/${s.photoMeal.meal_id}$`), { timeout: 60_000 });
      await page.getByText("Forecast peak").first().waitFor();
      const sent = s.sent.filter((x) => x.kind === "confirm").at(-1);
      expect(sent && same(sent.body, PROFILE_A), `sent: ${JSON.stringify(sent?.body)}`);
      const meal = await get(page, `/meals/${s.photoMeal.meal_id}`);
      expect(
        same(meal.confirmation, PROFILE_A),
        `stored: ${JSON.stringify(pick(meal.confirmation))}`,
      );
      expect(
        meal.confirmation.source === (paid ? "ai_edited" : "manual"),
        `source ${meal.confirmation.source}`,
      );
      if (paid)
        expect(
          same(meal.estimate.totals, s.report.aiEstimate.totals) &&
            !same(meal.estimate.totals, PROFILE_A),
          "the AI estimate was overwritten by the edit",
        );
      s.a = { meal, prediction: await get(page, `/predictions/${meal.prediction_id}`) };
      expect(
        s.a.prediction.meal_id === meal.meal_id &&
          s.a.prediction.confirmation_id === meal.confirmation.confirmation_id,
        "forecast is not tied to this confirmation",
      );
      expect(
        s.a.prediction.inputs_confirmed === true &&
          s.a.prediction.model.feature_set_version === "fs-1.3",
        "forecast provenance",
      );
      const text = await page.locator("main").innerText();
      for (const [label, key] of [
        ["Carbohydrates", "carbs_g"],
        ["Protein", "protein_g"],
        ["Fat", "fat_g"],
        ["Fibre", "fiber_g"],
        ["Calories", "calories_kcal"],
      ]) {
        expect(
          new RegExp(`${label}\\s+${PROFILE_A[key]} `).test(text),
          `meal page shows the wrong ${label}`,
        );
      }
      s.report.confirmedA = pick(meal.confirmation);
    },
  );

  await j.check(
    "Home now shows that meal and that forecast, without a reload, matching the server",
    async () => {
      const now = await home(page);
      const peak = String(Math.round(s.a.prediction.forecast.metrics.peak_mg_dl));
      expect(
        now.latestMealId === s.a.meal.meal_id &&
          now.latestPredictionId === s.a.prediction.prediction_id,
        `latest forecast is for ${now.latestMealId}`,
      );
      expect(now.latestPeak === peak, `Home peak ${now.latestPeak}, server ${peak}`);
      expect(now.latestMealText === summary(PROFILE_A), `Home meal text: ${now.latestMealText}`);
      expect(
        now.latestPanel.includes(`${s.a.prediction.forecast.metrics.time_to_peak_minutes} min`),
        "time to peak",
      );
      const row = now.rows.find((r) => r.id === s.a.meal.meal_id);
      expect(
        row && row.text.includes(summary(PROFILE_A)) && row.text.includes(`Forecast peak ${peak}`),
        `recent row: ${row?.text}`,
      );
      expect(now.rows.length === 2, `recent meals: ${now.rows.length}`);
      // What must not move: measured glucose, wearables, medications.
      for (const key of ["glucose", "glucosePanel", "wearables", "medications"])
        expect(now[key] === s.before[key], `${key} changed because of a meal`);
      expect(
        (await readingsUpTo(page, s.position)) === s.readings,
        "observed glucose readings changed",
      );
      s.afterA = now;
      s.report.home = { before: s.before, afterA: now };
      await shot(page, "h02-home-after");
    },
  );

  await j.check("it is still there after a reload, and after signing out and back in", async () => {
    await page.reload();
    const reloaded = await home(page, { navigate: false });
    expect(JSON.stringify(reloaded) === JSON.stringify(s.afterA), "Home differs after a reload");
    await page.getByTestId("account-menu").click();
    await page.getByTestId("sign-out").click();
    await page.waitForURL("**/auth");
    await signIn(page, DEMO_EMAILS.new_user, DEMO_PASSWORD);
    await page.waitForURL(`${BASE}/`);
    const again = await home(page, { navigate: false });
    expect(
      JSON.stringify(again) === JSON.stringify(s.afterA),
      "Home differs after signing in again",
    );
    await page.locator("aside nav a", { hasText: "Meals" }).click();
    await page.getByText(summary(PROFILE_A)).first().waitFor();
  });

  await j.check(
    "profile B (3× the carbohydrate, same moment): one meal from a double click, its own values sent and stored",
    async () => {
      await page.goto(`${BASE}/meals/log`);
      await page.getByTestId("manual-entry").click();
      await fillMacros(page, PROFILE_B);
      const mealsBefore = (await get(page, "/meals?limit=50")).meals.length;
      await page.getByTestId("confirm-forecast").dblclick();
      await page.waitForURL(/\/meals\/meal_/, { timeout: 60_000 });
      await page.getByText("Forecast peak").first().waitFor();
      const meals = (await get(page, "/meals?limit=50")).meals;
      expect(
        meals.length === mealsBefore + 1,
        `a double click created ${meals.length - mealsBefore} meals`,
      );
      const sent = s.sent.filter((x) => x.kind === "create").at(-1);
      expect(sent && same(sent.body.macros, PROFILE_B), `sent: ${JSON.stringify(sent?.body)}`);
      const meal = await get(page, `/meals/${page.url().split("/").pop()}`);
      expect(same(meal.confirmation, PROFILE_B), "stored values");
      s.b = { meal, prediction: await get(page, `/predictions/${meal.prediction_id}`) };
      expect(
        s.b.prediction.confirmation_id === meal.confirmation.confirmation_id &&
          s.b.prediction.meal_id === meal.meal_id,
        "association",
      );
    },
  );

  await j.check(
    "the two forecasts start from the same glucose and differ because the nutrition differs",
    async () => {
      const [a, b] = [s.a.prediction.forecast, s.b.prediction.forecast];
      const [curveA, curveB] = [
        a.points.map((p) => p.glucose_mg_dl),
        b.points.map((p) => p.glucose_mg_dl),
      ];
      s.report.comparison = {
        same_replay_instant: s.a.meal.occurred_at === s.b.meal.occurred_at,
        start: [curveA[0], curveB[0]],
        A: {
          peak: a.metrics.peak_mg_dl,
          time_to_peak: a.metrics.time_to_peak_minutes,
          at: a.glucose_at_minutes,
        },
        B: {
          peak: b.metrics.peak_mg_dl,
          time_to_peak: b.metrics.time_to_peak_minutes,
          at: b.glucose_at_minutes,
        },
        max_difference_mg_dl: Math.max(...curveA.map((v, i) => Math.abs(v - curveB[i]))),
      };
      expect(
        s.a.meal.occurred_at === s.b.meal.occurred_at,
        "the two meals are not at the same instant",
      );
      expect(
        curveA.length === 25 && curveB.length === 25 && curveA[0] === curveB[0],
        "curves do not share a starting glucose",
      );
      expect(
        JSON.stringify(curveA) !== JSON.stringify(curveB),
        "identical curves for 30 g and 90 g of carbohydrate",
      );
      expect(
        s.a.prediction.personalization.admissible_prior_meals ===
          s.b.prediction.personalization.admissible_prior_meals,
        "history differs between the two",
      );
      // Meal A's stored forecast was not rewritten by meal B.
      expect(
        JSON.stringify(
          (await get(page, `/predictions/${s.a.prediction.prediction_id}`)).forecast,
        ) === JSON.stringify(a),
        "forecast A changed",
      );
    },
  );

  await j.check(
    "Home and Meals agree with the server for every meal now on the account",
    async () => {
      const now = await home(page);
      const meals = (await get(page, "/meals?limit=50")).meals;
      const predictions = Object.fromEntries(
        (await get(page, "/predictions?limit=100")).predictions.map((p) => [p.prediction_id, p]),
      );
      expect(now.rows.length === Math.min(4, meals.length), "recent meals count");
      for (const row of now.rows) {
        const meal = meals.find((m) => m.meal_id === row.id);
        const peak = Math.round(predictions[meal.prediction_id].forecast.metrics.peak_mg_dl);
        expect(
          row.text.includes(`${Math.round(meal.confirmation.carbs_g)} g carbs`) &&
            row.text.includes(`Forecast peak ${peak}`),
          `row ${row.text}`,
        );
      }
      // Both hand-added meals were forecast at one replay instant; whichever Home names, it must describe truthfully.
      const named = meals.find((m) => m.meal_id === now.latestMealId);
      expect(
        [s.a.meal.meal_id, s.b.meal.meal_id].includes(now.latestMealId),
        "latest forecast is not one of the newest meals",
      );
      expect(
        now.latestMealText === summary(pick(named.confirmation)),
        "latest forecast names the wrong nutrition",
      );
      expect(
        now.latestPeak ===
          String(Math.round(predictions[named.prediction_id].forecast.metrics.peak_mg_dl)),
        "latest forecast shows another meal's peak",
      );
      expect(
        now.glucose === s.before.glucose && now.wearables === s.before.wearables,
        "glucose or wearables moved",
      );
      expect(
        JSON.stringify(
          (await get(page, "/wearables/observations?limit=5000")).observations
            .map((o) => o.observation_id)
            .sort(),
        ) === s.wearables,
        "wearable observations changed",
      );
      await page.locator("aside nav a", { hasText: "Meals" }).click();
      await page.getByTestId("meal-row").first().waitFor();
      expect(
        (await page.getByTestId("meal-row").count()) === meals.length,
        "Meals and the server disagree on the number of meals",
      );
      for (const profile of [PROFILE_A, PROFILE_B])
        await page.getByText(summary(profile)).first().waitFor();
    },
  );

  await j.check(
    "replay integrity: the recorded meal's real outcome is unaffected; hand-added meals have none",
    async () => {
      await page.locator("aside nav a", { hasText: "Home" }).click();
      await page.getByTestId("latest-glucose").waitFor();
      await advanceInUi(page);
      const outcome = await get(page, `/meals/${s.recorded.meal_id}/outcome`);
      expect(
        outcome.status === "scored" &&
          outcome.eligible_for_personalization === true &&
          outcome.ineligibility_reasons.length === 0,
        JSON.stringify(outcome.ineligibility_reasons),
      );
      for (const meal of [s.a.meal, s.b.meal]) {
        const o = await get(page, `/meals/${meal.meal_id}/outcome`);
        expect(
          o.status === "not_applicable" &&
            o.observed === null &&
            o.eligible_for_personalization === false,
          `hand-added meal outcome: ${o.status}`,
        );
      }
      expect(
        (await readingsUpTo(page, s.position)) === s.readings,
        "earlier observed glucose changed",
      );
      const replay = await get(page, "/demo/replay");
      expect(
        replay.data.meals_logged === 1,
        `the replay counts ${replay.data.meals_logged} recorded meals`,
      );
      const now = await home(page);
      expect(
        now.glucose !== s.before.glucose || now.glucosePanel !== s.before.glucosePanel,
        "Home glucose did not move when the recording advanced",
      );
      s.report.afterAdvance = { glucose: now.glucose, personalization: now.personalization };
    },
  );

  // ---- a second, ordinary account: isolation and failure handling ----------------
  const other = await launch();
  const email = `e2e-audit-${Date.now()}@glimpse-test.example`;
  const password = `e2e-${Math.random().toString(36).slice(2)}-password`;
  await j.check("account isolation: another account sees none of it", async () => {
    const call = (path, init = {}) =>
      fetch(API + path, {
        ...init,
        headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
      });
    const registered = await (
      await call("/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password, timezone: "UTC", display_name: "Isolation Check" }),
      })
    ).json();
    const auth = { Authorization: `Bearer ${registered.session.token}` };
    await call("/consents/necessary_processing", {
      method: "PUT",
      headers: auth,
      body: JSON.stringify({ state: "granted" }),
    });
    await call("/onboarding", {
      method: "PATCH",
      headers: auth,
      body: JSON.stringify({ answers: {}, completed: true }),
    });
    for (const path of [
      `/meals/${s.a.meal.meal_id}`,
      `/meals/${s.a.meal.meal_id}/image`,
      `/predictions/${s.a.prediction.prediction_id}`,
      `/meals/${s.b.meal.meal_id}/outcome`,
    ]) {
      expect(
        (await call(path, { headers: auth })).status === 404,
        `${path} is readable by another account`,
      );
    }
    await signIn(other.page, email, password);
    await other.page.waitForURL(`${BASE}/`);
    await other.page.getByText("No meals logged").waitFor();
    await other.page.getByText("No forecasts yet").waitFor();
    await other.page.goto(`${BASE}/meals/${s.a.meal.meal_id}`);
    await other.page.getByText("This meal isn't here").waitFor();
    expect(
      (await get(other.page, "/meals")).meals.length === 0 &&
        (await get(other.page, "/predictions")).predictions.length === 0,
      "lists not empty",
    );
  });
  await j.check(
    "a refused forecast is reported as refused: Home shows the saved meal and no forecast",
    async () => {
      await other.page.goto(`${BASE}/meals/log`);
      await other.page.getByTestId("manual-entry").click();
      await fillMacros(other.page, PROFILE_A);
      await other.page.getByTestId("confirm-forecast").click();
      await other.page.getByText("Your meal is saved, but it couldn't be forecast").waitFor();
      expect(
        (await other.page.locator("[data-sonner-toast]").count()) === 0,
        "a success notification was shown",
      );
      await other.page.locator("aside nav a", { hasText: "Home" }).click();
      await other.page.getByTestId("home-meal-row").first().waitFor();
      const row = await other.page.getByTestId("home-meal-row").first().innerText();
      expect(row.includes("30 g carbs") && !row.includes("Forecast peak"), `row: ${row}`);
      await other.page.getByText("No forecasts yet").waitFor();
      expect(
        (await other.page.getByTestId("home-latest-forecast").count()) === 0,
        "a forecast is shown although none was made",
      );
      const meals = (await get(other.page, "/meals")).meals;
      expect(
        meals.length === 1 && meals[0].prediction_id === null && meals[0].status === "confirmed",
        "server state after a refusal",
      );
    },
  );
  await j.check("the throwaway account is deleted", async () => {
    const result = await api(other.page, "/account", { method: "DELETE", json: { password } });
    expect(result.status === 204, `delete: ${result.status}`);
  });
  await other.browser.close();

  if (process.env.E2E_EXPECT_ANALYSIS_FAILURE === "1") {
    await j.check(
      "a failed photo analysis says so, shows no estimate, and leaves Home untouched",
      async () => {
        const before = await home(page);
        await page.goto(`${BASE}/meals/log`);
        await page.getByTestId("photo-input").setInputFiles(await syntheticPhoto(page));
        await page.getByTestId("analyze-photo").click();
        await page.getByText("The photo couldn't be analyzed").waitFor({ timeout: 120_000 });
        expect(
          (await page.getByTestId("estimate").count()) === 0,
          "an estimate is shown after a failed analysis",
        );
        expect(
          (await page.locator("#macro-carbs_g").inputValue()) === "",
          "values were filled in after a failed analysis",
        );
        const draft = (await get(page, "/meals?limit=50")).meals.find((m) => m.status === "draft");
        expect(
          draft && draft.estimate === null && draft.prediction_id === null,
          "server state after a failed analysis",
        );
        const after = await home(page);
        expect(
          after.latestMealId === before.latestMealId && after.latestPeak === before.latestPeak,
          "latest forecast changed after a failed analysis",
        );
        expect(
          after.rows
            .find((r) => r.id === draft.meal_id)
            ?.text.includes("Nutrition not confirmed yet"),
          "draft row",
        );
      },
    );
  }

  console.log(`        REPORT ${JSON.stringify(s.report)}`);
  await browser.close();
  return { ...j, problems: [...problems, ...other.problems] };
}
