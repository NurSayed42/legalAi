// End to end on the real pipeline at :8001: one real request per feature, with the request body recorded and diffed.
//   node qa/live.mjs [baseUrl]
// Makes real LLM calls: about 2-3 each for Q&A and prediction, and 1 for the document. Search makes none.
// The outcome and screenshots hold real judgement text, so they go to qa/reports/ (gitignored). Only pass or fail is printed.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { isDeepStrictEqual } from "node:util";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { done, start } from "./actions.mjs";
import { INPUTS } from "./scenarios.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPORTS = join(HERE, "reports");
mkdirSync(REPORTS, { recursive: true });
const BASE = process.argv[2] ?? "http://localhost:8001/";
const before = JSON.parse(readFileSync(join(HERE, "payloads.before.json"), "utf-8")).scenarios;
const WAIT_MS = 280_000;

const CASES = [
  ["search", "search_both_no_filters"],
  ["docs", "docs_rent"],
  ["qa", "chat_first_turn"],
  ["predict", "predict_auto_detect"],
];

const browser = await chromium.launch({ channel: "chrome", headless: true });
const rows = [];
for (const [tab, scenario] of CASES) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, serviceWorkers: "block" });
  const page = await context.newPage();
  page.setDefaultTimeout(WAIT_MS); // Real answers take about a minute, far beyond Playwright's default wait.
  const problems = [];
  const requests = [];
  page.on("pageerror", (e) => problems.push(e.message.slice(0, 160)));
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/api/")) {
      requests.push({ path: new URL(r.url()).pathname, body: JSON.parse(r.postData() || "null") });
    }
  });
  let status = null;
  page.on("response", (r) => {
    if (r.request().method() === "POST" && r.url().includes("/api/")) status = r.status();
  });
  await page.goto(`${BASE}#${tab}`, { waitUntil: "networkidle" });
  const t0 = Date.now();
  await start[tab](page, INPUTS);
  // Either the finished result, or the UI's error state. A busy LLM provider shows the second.
  const outcome = await Promise.race([
    done[tab](page).then(() => "result"),
    page
      .locator('[role="alert"]')
      .first()
      .waitFor()
      .then(() => "error state"),
  ]).catch(() => "timed out");
  const seconds = Math.round((Date.now() - t0) / 1000);
  await page.waitForTimeout(600);
  await page.screenshot({ path: join(REPORTS, `live_${tab}.png`) });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  const same = isDeepStrictEqual(requests, before[scenario]);
  rows.push({ tab, outcome, status, seconds, payloadIdentical: same, overflow, problems });
  console.log(
    `${tab}: ${outcome}, HTTP ${status}, ${seconds}s, payload ${same ? "identical" : "DIFFERENT"}, overflow ${overflow}, page errors ${problems.length}`,
  );
  await context.close();
}
await browser.close();
writeFileSync(join(HERE, "live.summary.json"), JSON.stringify({ base: BASE, rows }, null, 2) + "\n");
process.exit(
  rows.every((r) => r.payloadIdentical && r.outcome === "result" && !r.overflow && !r.problems.length) ? 0 : 1,
);
