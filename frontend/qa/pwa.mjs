// The service worker promises two things: the app shell opens offline, and API responses are never cached.
//   node qa/pwa.mjs [baseUrl]
import { chromium } from "playwright";
import { stubApi } from "./fixtures.mjs";
import { INPUTS } from "./scenarios.mjs";
import { act } from "./actions.mjs";

const BASE = process.argv[2] ?? "http://localhost:4173/";
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
const page = await context.newPage();
await context.route("**/api/**", (route) => route.continue());
await page.goto(BASE, { waitUntil: "networkidle" });
const checks = {};

checks.registered = await page.evaluate(async () => {
  const registration = await navigator.serviceWorker.ready;
  return Boolean(registration.active);
});
await page.reload({ waitUntil: "networkidle" });
checks.controlsPage = await page.evaluate(() => Boolean(navigator.serviceWorker.controller));

await stubApi(page);
await act.search(page, INPUTS);

const cached = await page.evaluate(async () => {
  const names = await caches.keys();
  const urls = [];
  for (const name of names) {
    const cache = await caches.open(name);
    urls.push(...(await cache.keys()).map((r) => new URL(r.url).pathname));
  }
  return { names, urls };
});
checks.shellCached = cached.urls.length > 5;
checks.noApiInCache = !cached.urls.some((u) => u.startsWith("/api") || u === "/live-info");
checks.cachedFiles = cached.urls.length;
checks.cacheNames = cached.names.length;

await context.setOffline(true);
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForSelector("header");
checks.opensOffline = await page.getByText("LegalAI Bangladesh").first().isVisible();
await page
  .locator("text=আপনি এখন অফলাইনে আছেন")
  .waitFor({ timeout: 5000 })
  .catch(() => {});
checks.offlineBannerShows = await page.locator("text=আপনি এখন অফলাইনে আছেন").isVisible();

await browser.close();
console.log(JSON.stringify(checks, null, 1));
const failed = Object.entries(checks).filter(([, v]) => v === false);
if (failed.length) console.log("FAILED:", failed.map(([k]) => k).join(", "));
process.exit(failed.length ? 1 : 0);
