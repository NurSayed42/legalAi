// Runs the same user actions as qa/baseline.mjs on the new UI, then diffs every request body against the old UI's.
//   node qa/parity.mjs [baseUrl]
// Requests are stubbed, so no LLM call is made. Exits non-zero on any difference.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { isDeepStrictEqual } from "node:util";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { done, start } from "./actions.mjs";
import { stubApi } from "./fixtures.mjs";
import { INPUTS } from "./scenarios.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.argv[2] ?? "http://localhost:4173/";
const before = JSON.parse(readFileSync(join(HERE, "payloads.before.json"), "utf-8")).scenarios;
const browser = await chromium.launch({ channel: "chrome", headless: true });

async function capture(tab, run) {
  const record = [];
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, serviceWorkers: "block" });
  const page = await context.newPage();
  await stubApi(page, { record });
  await page.goto(`${BASE}#${tab}`, { waitUntil: "networkidle" });
  await run(page);
  await page.waitForTimeout(300);
  await context.close();
  return record;
}
const send = async (page, text) => {
  await page.fill("#chat-input", text);
  await page.locator('button[aria-label="পাঠান"]').click();
};
const pickRadix = async (page, label, option) => {
  await page.getByRole("combobox", { name: label }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
};

const scenarios = {
  chat_first_turn: [
    "qa",
    async (p) => {
      await send(p, INPUTS.chat1);
      await done.qa(p);
    },
  ],
  chat_second_turn_has_history: [
    "qa",
    async (p) => {
      await send(p, INPUTS.chat1);
      await done.qa(p);
      await send(p, INPUTS.chat2);
      await p.getByRole("article", { name: "উত্তর" }).nth(1).getByText("প্রযোজ্য আইন").waitFor();
    },
  ],
  chat_with_filters: [
    "qa",
    async (p) => {
      await p.getByRole("button", { name: "ছাঁকনি" }).click();
      await pickRadix(p, "মামলার ধরন", "রিট পিটিশন");
      await pickRadix(p, "আদালত", "হাইকোর্ট বিভাগ");
      await pickRadix(p, "বছর", "২০১৫ থেকে");
      await p.getByRole("button", { name: "প্রয়োগ করুন" }).click();
      await send(p, INPUTS.chat1);
      await done.qa(p);
    },
  ],
  chat_follow_up_option: [
    "qa",
    async (p) => {
      await send(p, INPUTS.chat1);
      await done.qa(p);
      await p.getByRole("button", { name: "৩০ দিনের মধ্যে" }).click();
      await p.getByRole("button", { name: "উত্তর জমা দিন" }).click();
      await p.getByRole("article", { name: "উত্তর" }).nth(1).getByText("প্রযোজ্য আইন").waitFor();
    },
  ],
  predict_auto_detect: [
    "predict",
    async (p) => {
      await start.predict(p, INPUTS);
      await done.predict(p);
    },
  ],
  predict_with_case_type: [
    "predict",
    async (p) => {
      await p
        .getByRole("group", { name: "মামলার ধরন" })
        .or(p.getByRole("radiogroup", { name: "মামলার ধরন" }))
        .getByText("রিট পিটিশন")
        .click();
      await start.predict(p, INPUTS);
      await done.predict(p);
    },
  ],
  search_both_no_filters: [
    "search",
    async (p) => {
      await start.search(p, INPUTS);
      await done.search(p);
    },
  ],
  search_cases_with_filters: [
    "search",
    async (p) => {
      await p.fill("#search-input", INPUTS.search);
      await p.getByRole("radio", { name: "মামলা", exact: true }).click();
      await pickRadix(p, "মামলার ধরন", "ফৌজদারি রিভিশন");
      await pickRadix(p, "বছর", "২০১৫ থেকে");
      await pickRadix(p, "ফল", "রুল চূড়ান্ত");
      await p.getByRole("search").getByRole("button", { name: "খুঁজুন" }).click();
      await done.search(p);
    },
  ],
  search_laws_only: [
    "search",
    async (p) => {
      await p.fill("#search-input", INPUTS.search);
      await p.getByRole("radio", { name: "আইন", exact: true }).click();
      await p.getByRole("search").getByRole("button", { name: "খুঁজুন" }).click();
      await p.getByRole("region", { name: "আইন" }).waitFor();
    },
  ],
  docs_rent: [
    "docs",
    async (p) => {
      await start.docs(p, INPUTS);
      await done.docs(p);
    },
  ],
};

const results = {};
const differences = [];
for (const [name, [tab, run]] of Object.entries(scenarios)) {
  const record = await capture(tab, run);
  results[name] = record;
  const same = isDeepStrictEqual(record, before[name]);
  console.log(
    `${same ? "same     " : "DIFFERENT"}  ${name}  (${record.length} request${record.length === 1 ? "" : "s"})`,
  );
  if (!same) differences.push({ name, before: before[name], after: record });
}
await browser.close();
writeFileSync(
  join(HERE, "payloads.after.json"),
  JSON.stringify(
    {
      note: "Request bodies from the new UI, captured with stubbed responses. Diffed against payloads.before.json.",
      scenarios: results,
    },
    null,
    2,
  ) + "\n",
);
if (differences.length) console.log(JSON.stringify(differences, null, 1).slice(0, 4000));
console.log(
  `${Object.keys(scenarios).length - differences.length} of ${Object.keys(scenarios).length} scenarios identical`,
);
process.exit(differences.length ? 1 : 0);
