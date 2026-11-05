// Phase 0: the old page as it is, before any change. Run once, against evals/serve_live.py serving legal_ui_v2.html.
//   node qa/baseline.mjs [baseUrl]
// Outputs: qa/before/*.png (screenshots), qa/payloads.before.json (request bodies).
// All requests are stubbed with made-up data, so this makes no LLM call and captures no judgement text.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { stubApi } from "./fixtures.mjs";
import { INPUTS } from "./scenarios.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "before");
const BASE = process.argv[2] ?? "http://localhost:8001/";
mkdirSync(OUT, { recursive: true });

const VIEWPORTS = [
  [360, 800],
  [390, 844],
  [768, 1024],
  [1280, 800],
  [1440, 900],
];
const TABS = [
  ["qa", "Legal Q&A"],
  ["predict", "Outcome Prediction"],
  ["search", "Case & Law Search"],
  ["docs", "Document Generator"],
];
const browser = await chromium.launch({ channel: "chrome", headless: true });

async function open(viewport, record) {
  const context = await browser.newContext({
    viewport: { width: viewport[0], height: viewport[1] },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  await stubApi(page, { record });
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForSelector(".tab");
  return { context, page };
}
const tab = (page, label) => page.locator(".tab", { hasText: label }).click();
const settle = (page) => page.waitForTimeout(400);

// Screenshots
const overflow = [];
for (const v of VIEWPORTS) {
  const { context, page } = await open(v);
  for (const [id, label] of TABS) {
    await tab(page, label);
    await settle(page);
    await page.screenshot({ path: join(OUT, `${id}_${v[0]}x${v[1]}_empty.png`) });
    const w = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
    if (w[0] > w[1]) overflow.push({ tab: id, viewport: v.join("x"), scrollWidth: w[0], innerWidth: w[1] });
  }
  await tab(page, "Legal Q&A");
  await page.fill(".chat-input", INPUTS.chat1);
  await page.click(".send-btn");
  await page.waitForSelector(".answer-summary");
  await settle(page);
  await page.screenshot({ path: join(OUT, `qa_${v[0]}x${v[1]}_result.png`) });
  await tab(page, "Outcome Prediction");
  await page.fill("textarea", INPUTS.predict);
  await page.click(".primary-btn");
  await page.waitForSelector(".answer-summary");
  await settle(page);
  await page.screenshot({ path: join(OUT, `predict_${v[0]}x${v[1]}_result.png`) });
  await tab(page, "Case & Law Search");
  await page.fill(".search-input", INPUTS.search);
  await page.click(".primary-btn");
  await page.waitForSelector(".result-card");
  await settle(page);
  await page.screenshot({ path: join(OUT, `search_${v[0]}x${v[1]}_result.png`) });
  await tab(page, "Document Generator");
  await page.locator(".doc-type-card", { hasText: "Rent" }).click();
  await page.fill(".docs-panel textarea", INPUTS.docs);
  await page.click(".docs-panel .primary-btn");
  await page.waitForSelector(".doc-output-area");
  await settle(page);
  await page.screenshot({ path: join(OUT, `docs_${v[0]}x${v[1]}_result.png`) });
  await context.close();
  console.log("screens", v.join("x"));
}

// Payloads: one fresh page per scenario
async function capture(name, run) {
  const record = [];
  const { context, page } = await open([1280, 800], record);
  await run(page);
  await page.waitForTimeout(300);
  await context.close();
  return [name, record];
}
const chatSend = async (page, text) => {
  await page.fill(".chat-input", text);
  await page.click(".send-btn");
};
const scenarios = [
  await capture("chat_first_turn", async (p) => {
    await chatSend(p, INPUTS.chat1);
    await p.waitForSelector(".answer-summary");
  }),
  await capture("chat_second_turn_has_history", async (p) => {
    await chatSend(p, INPUTS.chat1);
    await p.waitForSelector(".answer-summary");
    await chatSend(p, INPUTS.chat2);
    await p.waitForFunction(() => document.querySelectorAll(".answer-summary").length === 2);
  }),
  await capture("chat_with_filters", async (p) => {
    const selects = p.locator(".filter-row .filter-select");
    await selects.nth(0).selectOption("writ_petition");
    await selects.nth(1).selectOption("HCD");
    await selects.nth(2).selectOption("2015");
    await chatSend(p, INPUTS.chat1);
    await p.waitForSelector(".answer-summary");
  }),
  await capture("chat_follow_up_option", async (p) => {
    await chatSend(p, INPUTS.chat1);
    await p.waitForSelector(".answer-summary");
    await p.locator(".suggestion", { hasText: "৩০ দিনের মধ্যে" }).click();
    await p.locator(".answer-section .primary-btn").click();
    await p.waitForFunction(() => document.querySelectorAll(".answer-summary").length === 2);
  }),
  await capture("predict_auto_detect", async (p) => {
    await tab(p, "Outcome Prediction");
    await p.fill("textarea", INPUTS.predict);
    await p.click(".primary-btn");
    await p.waitForSelector(".answer-summary");
  }),
  await capture("predict_with_case_type", async (p) => {
    await tab(p, "Outcome Prediction");
    await p.locator(".select-chip", { hasText: "Writ Petition" }).click();
    await p.fill("textarea", INPUTS.predict);
    await p.click(".primary-btn");
    await p.waitForSelector(".answer-summary");
  }),
  await capture("search_both_no_filters", async (p) => {
    await tab(p, "Case & Law Search");
    await p.fill(".search-input", INPUTS.search);
    await p.click(".primary-btn");
    await p.waitForSelector(".result-card");
  }),
  await capture("search_cases_with_filters", async (p) => {
    await tab(p, "Case & Law Search");
    await p.fill(".search-input", INPUTS.search);
    await p.locator(".toggle-btn", { hasText: "Cases" }).click();
    const selects = p.locator(".filter-bar .filter-select");
    await selects.nth(0).selectOption("criminal_revision");
    await selects.nth(1).selectOption("2015");
    await selects.nth(2).selectOption("rule_absolute");
    await p.click(".primary-btn");
    await p.waitForSelector(".result-card");
  }),
  await capture("search_laws_only", async (p) => {
    await tab(p, "Case & Law Search");
    await p.fill(".search-input", INPUTS.search);
    await p.locator(".toggle-btn", { hasText: "Laws" }).click();
    await p.click(".primary-btn");
    await p.waitForSelector(".result-card, .law-result-card");
  }),
  await capture("docs_rent", async (p) => {
    await tab(p, "Document Generator");
    await p.locator(".doc-type-card", { hasText: "Rent" }).click();
    await p.fill(".docs-panel textarea", INPUTS.docs);
    await p.click(".docs-panel .primary-btn");
    await p.waitForSelector(".doc-output-area");
  }),
];
await browser.close();

writeFileSync(
  join(HERE, "payloads.before.json"),
  JSON.stringify(
    {
      note: "Request bodies from legal_ui_v2.html, captured with stubbed responses.",
      scenarios: Object.fromEntries(scenarios),
    },
    null,
    2,
  ) + "\n",
);
console.log(
  JSON.stringify({ overflowBefore: overflow, scenarios: scenarios.map(([n, r]) => `${n}:${r.length}`) }, null, 1),
);
