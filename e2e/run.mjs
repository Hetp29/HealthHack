// Runs the GLIMPSE browser journeys against a running frontend and backend.
//   node e2e/run.mjs                 all journeys
//   node e2e/run.mjs new-account     one or more by name
import { API, BASE, DEMO_PASSWORD } from "./lib.mjs";

const JOURNEYS = {
  "new-account": { file: "./journeys/new-account.mjs", demo: false },
  "demo-new-user": { file: "./journeys/demo-new-user.mjs", demo: true },
  "demo-returning": { file: "./journeys/demo-returning.mjs", demo: true },
  "photo-analysis": { file: "./journeys/photo-analysis.mjs", demo: true },
};

const wanted = process.argv.slice(2);
const names = wanted.length ? wanted : Object.keys(JOURNEYS);
for (const name of names) {
  if (!JOURNEYS[name]) {
    console.error(`Unknown journey "${name}". Choose from: ${Object.keys(JOURNEYS).join(", ")}`);
    process.exit(2);
  }
}

let status;
try {
  status = await (await fetch(`${API}/status`)).json();
  await fetch(`${BASE}/auth`);
} catch {
  console.error(
    `Cannot reach the application. Frontend: ${BASE}  Backend: ${API}\nStart both first (see e2e/README.md).`,
  );
  process.exit(2);
}
console.log(
  `GLIMPSE browser journeys\n  frontend ${BASE}\n  backend  ${API}\n  model    ${status.model.model_id} (integrity verified: ${status.model.integrity_verified})\n  demo mode ${status.demo_mode}, photo analysis configured: ${status.meal_analysis.configured}\n`,
);

let failed = 0;
const summary = [];
for (const name of names) {
  const { file, demo } = JOURNEYS[name];
  if (demo && !(status.demo_mode && DEMO_PASSWORD)) {
    console.log(`${name}: SKIPPED (needs a demo-mode server and GLUCOTWIN_DEMO_PASSWORD)\n`);
    summary.push(`${name}: skipped`);
    failed += 1; // Demo Mode is a release requirement: a run that could not test it has not passed.
    continue;
  }
  console.log(name);
  const result = await (await import(file)).default();
  const bad = result.results.filter((r) => !r.ok);
  const skipped = result.results.filter((r) => r.skipped).length;
  failed += bad.length;
  summary.push(
    `${name}: ${result.results.length - bad.length - skipped}/${result.results.length - skipped} passed${skipped ? `, ${skipped} skipped` : ""}`,
  );
  if (result.problems.length)
    console.log(`  browser problems:\n    ${[...new Set(result.problems)].join("\n    ")}`);
  failed += result.problems.length ? 1 : 0;
  console.log("");
}
console.log(summary.join("\n"));
console.log(failed ? "\nFAILED" : "\nALL JOURNEYS PASSED");
process.exit(failed ? 1 : 0);
