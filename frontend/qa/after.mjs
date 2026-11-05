// The full screenshot matrix on the new UI: every tab, in its empty, loading, result and error states, at six sizes, in both themes.
// Every screenshot also asserts there's no horizontal scroll and nothing sticks out past the viewport.
//   node qa/after.mjs [baseUrl] [--only=WxH]
// Stubbed with made-up data, so no LLM call. Screenshots and the contact sheet stay local (gitignored).
import { mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { done, start } from "./actions.mjs";
import { chat, doc, predict, search, stats } from "./fixtures.mjs";
import { INPUTS } from "./scenarios.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "after");
const args = process.argv.slice(2);
const BASE = args.find((a) => a.startsWith("http")) ?? "http://localhost:4173/";
const ONLY = args.find((a) => a.startsWith("--only="))?.slice(7);
mkdirSync(OUT, { recursive: true });

const VIEWPORTS = [
  [320, 640],
  [360, 800],
  [390, 844],
  [768, 1024],
  [1280, 800],
  [1440, 900],
].filter(([w, h]) => !ONLY || ONLY === `${w}x${h}`);
const TABS = ["qa", "predict", "search", "docs"];
const RESPONSES = {
  "/api/stats": stats,
  "/api/chat": chat,
  "/api/predict": predict,
  "/api/search": search,
  "/api/generate-doc": doc,
};

const browser = await chromium.launch({ channel: "chrome", headless: true });
const failures = [];
const consoleProblems = [];
let shots = 0;

function layoutCheck() {
  const vw = window.innerWidth;
  const clipped = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) return true;
    }
    return false;
  };
  const offenders = [];
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const s = getComputedStyle(el);
    if (s.visibility === "hidden" || s.display === "none") continue;
    if ((r.right > vw + 0.5 || r.left < -0.5) && !clipped(el)) {
      offenders.push(
        `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 60)} [${Math.round(r.left)},${Math.round(r.right)}]`,
      );
    }
  }
  return { scrollWidth: document.documentElement.scrollWidth, innerWidth: vw, offenders: offenders.slice(0, 5) };
}

for (const [w, h] of VIEWPORTS) {
  for (const theme of ["dark", "light"]) {
    const context = await browser.newContext({
      viewport: { width: w, height: h },
      colorScheme: theme,
      hasTouch: w < 768,
      isMobile: w < 768,
      reducedMotion: "reduce",
      serviceWorkers: "block",
    });
    const page = await context.newPage();
    page.on("pageerror", (e) => consoleProblems.push(`${w}x${h} ${theme}: ${e.message}`));
    page.on("console", (m) => {
      if (m.type() === "error" && !/status of 503|net::ERR_FAILED/.test(m.text()))
        consoleProblems.push(`${w}x${h} ${theme}: ${m.text().slice(0, 160)}`);
    });
    let mode = "normal";
    let pending = [];
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path !== "/api/stats" && mode === "slow") {
        pending.push(route);
        return;
      }
      if (path !== "/api/stats" && mode === "fail") {
        await route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"busy"}' });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(RESPONSES[path] ?? {}),
      });
    });
    const release = async () => {
      await Promise.all(pending.map((r) => r.abort().catch(() => {})));
      pending = [];
    };
    const shot = async (tab, state) => {
      await page.waitForTimeout(250);
      const name = `${tab}_${w}x${h}_${theme}_${state}.png`;
      await page.screenshot({ path: join(OUT, name) });
      shots += 1;
      const check = await page.evaluate(layoutCheck);
      if (check.scrollWidth > check.innerWidth || check.offenders.length) failures.push({ shot: name, ...check });
    };
    for (const tab of TABS) {
      const open = async (m) => {
        await release();
        mode = m;
        await page.goto(`${BASE}#${tab}`, { waitUntil: "load" });
        await page.reload({ waitUntil: "networkidle" });
        await page.evaluate(() => document.fonts.ready);
      };
      await open("normal");
      await shot(tab, "empty");
      await open("slow");
      await start[tab](page, INPUTS);
      await shot(tab, "loading");
      await open("fail");
      await start[tab](page, INPUTS);
      await page.locator('[role="alert"]').first().waitFor();
      await shot(tab, "error");
      await open("normal");
      await start[tab](page, INPUTS);
      await done[tab](page);
      await shot(tab, "result");
    }
    await context.close();
    console.log(`${w}x${h} ${theme}: ${shots} shots so far, ${failures.length} layout failures`);
  }
}
await browser.close();

// Side-by-side contact sheet: old page beside the new one.
const beforeDir = join(HERE, "before");
const before = new Set(readdirSync(beforeDir));
const rows = [];
for (const tab of TABS) {
  rows.push(`<h2>${tab}</h2>`);
  for (const size of ["320x640", "360x800", "390x844", "768x1024", "1280x800", "1440x900"]) {
    for (const state of ["empty", "loading", "result", "error"]) {
      const old = `${tab}_${size}_${state}.png`;
      const cells = [
        before.has(old)
          ? `<figure><img loading="lazy" src="before/${old}"><figcaption>before</figcaption></figure>`
          : "",
        `<figure><img loading="lazy" src="after/${tab}_${size}_dark_${state}.png"><figcaption>after, dark</figcaption></figure>`,
        `<figure><img loading="lazy" src="after/${tab}_${size}_light_${state}.png"><figcaption>after, light</figcaption></figure>`,
      ];
      rows.push(`<h3>${size} · ${state}</h3><div class="row">${cells.join("")}</div>`);
    }
  }
}
writeFileSync(
  join(HERE, "index.html"),
  `<!doctype html><meta charset="utf-8"><title>LegalAI: before and after</title><style>body{font:14px system-ui;background:#111;color:#ddd;margin:24px}h2{margin-top:48px;text-transform:uppercase}h3{margin:24px 0 8px;font-weight:500}.row{display:flex;gap:16px;align-items:flex-start;overflow-x:auto}figure{margin:0}img{max-height:560px;border:1px solid #333;display:block}figcaption{padding:4px 0;color:#999}</style><h1>Before and after</h1>${rows.join("\n")}`,
);

const summary = {
  screenshots: shots,
  viewports: VIEWPORTS.map(([a, b]) => `${a}x${b}`),
  layoutFailures: failures.length,
  consoleProblems: [...new Set(consoleProblems)].length,
};
if (!ONLY) writeFileSync(join(HERE, "layout.summary.json"), JSON.stringify(summary, null, 2) + "\n");
console.log(JSON.stringify(summary));
if (failures.length) console.log(JSON.stringify(failures.slice(0, 12), null, 1).slice(0, 3500));
if (consoleProblems.length) console.log([...new Set(consoleProblems)].slice(0, 8).join("\n").slice(0, 1500));
process.exit(failures.length ? 1 : 0);
