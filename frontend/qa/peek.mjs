// Quick look during development: one tab, one state, one viewport.
//   node qa/peek.mjs <out.png> <width> <height> <tab> <state> [theme] [fullPage]
// state: empty | result
import { chromium } from "playwright";
import { stubApi } from "./fixtures.mjs";
import { INPUTS } from "./scenarios.mjs";
import { act } from "./actions.mjs";

const [out, w, h, tab, state = "empty", theme = "dark", full = ""] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({
  viewport: { width: +w, height: +h },
  colorScheme: theme,
  hasTouch: +w < 768,
});
const page = await context.newPage();
const problems = [];
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text().slice(0, 200)}`));
await stubApi(page);
await page.goto(`http://localhost:4173/#${tab}`, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
if (state === "result") await act[tab](page, INPUTS);
await page.waitForTimeout(500);
await page.screenshot({ path: out, fullPage: full === "full" });
const size = await page.evaluate(() => [
  document.documentElement.scrollWidth,
  window.innerWidth,
  document.documentElement.scrollHeight,
]);
console.log(JSON.stringify({ scrollWidth: size[0], innerWidth: size[1], height: size[2], problems }));
await browser.close();
