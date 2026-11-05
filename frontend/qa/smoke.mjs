// Quick check of a running instance: the page loads, all four tabs open, and one real search returns results.
// A search makes no LLM call. Results hold real judgement text, so the screenshot goes to qa/reports/ (gitignored).
//   node qa/smoke.mjs [baseUrl]
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
mkdirSync(join(HERE, "reports"), { recursive: true });
const BASE = process.argv[2] ?? "http://localhost:8001/";

const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, serviceWorkers: "block" });
const page = await context.newPage();
page.setDefaultTimeout(90_000);
const problems = [];
page.on("pageerror", (e) => problems.push(e.message.slice(0, 160)));
page.on("console", (m) => m.type() === "error" && problems.push(m.text().slice(0, 160)));
page.on("requestfailed", (r) => problems.push(`failed: ${new URL(r.url()).pathname}`));
page.on("response", (r) => r.status() >= 400 && problems.push(`HTTP ${r.status()} ${new URL(r.url()).pathname}`));

await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByText("সংযুক্ত").first().waitFor();
const out = { status: "connected", testCopyPill: await page.getByText("পরীক্ষার কপি").first().isVisible(), tabs: {} };
for (const [id, heading] of [
  ["qa", "বাংলাদেশ আইনি সহায়তা"],
  ["predict", "সম্ভাব্য ফলাফল"],
  ["search", "মামলা ও আইন অনুসন্ধান"],
  ["docs", "দলিল তৈরি"],
]) {
  await page.goto(`${BASE}#${id}`);
  out.tabs[id] = await page
    .getByRole("heading", { name: heading, level: 1 })
    .isVisible()
    .catch(() => false);
  if (!out.tabs[id]) {
    await page.getByRole("heading", { name: heading, level: 1 }).waitFor();
    out.tabs[id] = true;
  }
}
await page.goto(`${BASE}#search`);
await page.fill("#search-input", "dishonour of cheque section 138");
const t0 = Date.now();
await page.getByRole("search").getByRole("button", { name: "খুঁজুন" }).click();
await page.getByRole("region", { name: "মামলা" }).waitFor();
out.searchSeconds = Math.round((Date.now() - t0) / 1000);
out.casesShown = await page.getByRole("region", { name: "মামলা" }).locator("h3").count();
out.lawsShown = await page.getByRole("region", { name: "আইন" }).locator("h3").count();
out.overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
await page.waitForTimeout(400);
await page.screenshot({ path: join(HERE, "reports", "smoke_search.png") });
out.problems = problems;
await browser.close();
console.log(JSON.stringify(out, null, 1));
process.exit(problems.length || out.overflow ? 1 : 0);
