// Phone and dark-theme screenshots for the README. Empty screens only, so they hold no judgement text or names.
//   node qa/readme-shots.mjs [baseUrl]     (start qa/static-server.mjs first)
// Writes to docs/screenshots/, which is committed. Don't add result screens here without checking them by eye.
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { stubApi } from "./fixtures.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "../../docs/screenshots");
const BASE = process.argv[2] ?? "http://localhost:4173/";
const SHOTS = [
  ["17-phone-qa-dark.png", "qa", 390, 844, "dark", 2],
  ["18-phone-predict-dark.png", "predict", 390, 844, "dark", 2],
  ["19-phone-docs-light.png", "docs", 390, 844, "light", 2],
  ["20-desktop-dark.png", "qa", 1390, 870, "dark", 1],
];

const browser = await chromium.launch({ channel: "chrome", headless: true });
for (const [name, tab, width, height, theme, scale] of SHOTS) {
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: scale,
    colorScheme: theme,
    hasTouch: width < 768,
    isMobile: width < 768,
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  const page = await context.newPage();
  await stubApi(page);
  await page.goto(`${BASE}#${tab}`, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(500);
  await page.screenshot({ path: join(OUT, name) });
  await context.close();
  console.log(name);
}
await browser.close();
