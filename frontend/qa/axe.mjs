// axe-core on every tab and state, including open dialogs and sheets, in both themes. Target: 0 serious or critical.
//   node qa/axe.mjs [baseUrl]
// Full results go to qa/reports/ (gitignored). Counts go to qa/axe.summary.json.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { AxeBuilder } from "@axe-core/playwright";
import { chromium } from "playwright";
import { done, start } from "./actions.mjs";
import { stubApi } from "./fixtures.mjs";
import { INPUTS } from "./scenarios.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.argv[2] ?? "http://localhost:4173/";
mkdirSync(join(HERE, "reports"), { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const TABS = ["qa", "predict", "search", "docs"];
const all = [];
let runs = 0;

async function scan(page, label) {
  await page.waitForTimeout(350);
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"])
    .analyze();
  runs += 1;
  for (const v of result.violations) {
    all.push({
      label,
      id: v.id,
      impact: v.impact,
      nodes: v.nodes.length,
      sample: v.nodes[0]?.target?.join(" ")?.slice(0, 120),
      why: v.nodes[0]?.failureSummary?.slice(0, 200),
    });
  }
}

const SIZES = [
  [390, 844],
  [1280, 800],
];
for (const [w, h] of SIZES) {
  for (const theme of ["dark", "light"]) {
    const desktop = w >= 768;
    const newPage = async (tab, fail) => {
      const context = await browser.newContext({
        viewport: { width: w, height: h },
        colorScheme: theme,
        hasTouch: !desktop,
        isMobile: !desktop,
        reducedMotion: "reduce",
        serviceWorkers: "block",
      });
      const page = await context.newPage();
      await stubApi(page, { fail });
      await page.goto(`${BASE}#${tab}`, { waitUntil: "networkidle" });
      await page.evaluate(() => document.fonts.ready);
      return { context, page };
    };
    const tag = `${w}x${h} ${theme}`;
    for (const tab of TABS) {
      let { context, page } = await newPage(tab);
      await scan(page, `${tag} ${tab} empty`);
      await start[tab](page, INPUTS);
      await done[tab](page);
      await scan(page, `${tag} ${tab} result`);
      await context.close();
      ({ context, page } = await newPage(tab, 503));
      await start[tab](page, INPUTS);
      await page.locator('[role="alert"]').first().waitFor();
      await scan(page, `${tag} ${tab} error`);
      await context.close();
    }
    // Things that only exist when opened
    let { context, page } = await newPage("qa");
    await page.getByRole("button", { name: "ফিল্টার" }).click();
    await page.getByRole("button", { name: "প্রয়োগ করুন" }).waitFor();
    await scan(page, `${tag} chat filter panel`);
    await page.getByRole("combobox", { name: "মামলার ধরন" }).click();
    await page.getByRole("listbox").waitFor();
    await scan(page, `${tag} select open`);
    await context.close();

    ({ context, page } = await newPage("qa"));
    await start.qa(page, INPUTS);
    await done.qa(page);
    await page.getByRole("button", { name: "আলাপ মুছুন" }).click();
    await page.getByRole("dialog").waitFor();
    await scan(page, `${tag} clear dialog`);
    await context.close();

    ({ context, page } = await newPage("docs"));
    await page.getByRole("button", { name: /ভাড়া চুক্তি/ }).click();
    await page.locator("#doc-details").waitFor();
    await scan(page, `${tag} docs details step`);
    await context.close();

    ({ context, page } = await newPage("search"));
    await page.fill("#search-input", INPUTS.search);
    await scan(page, `${tag} search typed`);
    await context.close();

    ({ context, page } = await newPage("qa"));
    if (desktop) {
      await page.getByRole("button", { name: "এই অ্যাপ সম্পর্কে" }).click();
      await page.getByRole("dialog").waitFor();
      await scan(page, `${tag} about dialog`);
    } else {
      await page.getByRole("button", { name: "আরও" }).click();
      await page.getByRole("dialog").waitFor();
      await scan(page, `${tag} overflow sheet`);
    }
    await context.close();
    console.log(`${tag}: ${runs} scans, ${all.length} violations so far`);
  }
}
await browser.close();

const byImpact = {};
for (const v of all) byImpact[v.impact] = (byImpact[v.impact] ?? 0) + 1;
const byRule = {};
for (const v of all) byRule[`${v.impact}: ${v.id}`] = (byRule[`${v.impact}: ${v.id}`] ?? 0) + 1;
writeFileSync(join(HERE, "reports", "axe.json"), JSON.stringify(all, null, 1));
writeFileSync(
  join(HERE, "axe.summary.json"),
  JSON.stringify({ scans: runs, violations: all.length, byImpact, byRule }, null, 2) + "\n",
);
console.log(JSON.stringify({ scans: runs, violations: all.length, byImpact, byRule }, null, 1));
const seen = new Set();
for (const v of all) {
  const key = `${v.id}|${v.sample}`;
  if (seen.has(key)) continue;
  seen.add(key);
  console.log(
    `${v.impact} ${v.id} @ ${v.label}: ${v.sample} (${v.nodes}) -- ${(v.why ?? "").replace(/\s+/g, " ").slice(0, 150)}`,
  );
}
const serious = (byImpact.serious ?? 0) + (byImpact.critical ?? 0);
process.exit(serious ? 1 : 0);
