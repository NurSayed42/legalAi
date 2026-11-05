// Screenshots of what only exists when opened: sheets, dialogs, popovers, selects, and the keyboard-up layout.
//   node qa/overlays.mjs [baseUrl]
// Stubbed with made-up data. Writes to qa/after/ (gitignored).
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { act } from "./actions.mjs";
import { stubApi } from "./fixtures.mjs";
import { INPUTS } from "./scenarios.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "after");
mkdirSync(OUT, { recursive: true });
const BASE = process.argv[2] ?? "http://localhost:4173/";
const browser = await chromium.launch({ channel: "chrome", headless: true });

async function open(tab, w, h, theme = "dark") {
  const context = await browser.newContext({
    viewport: { width: w, height: h },
    colorScheme: theme,
    hasTouch: w < 768,
    isMobile: w < 768,
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  const page = await context.newPage();
  await stubApi(page);
  await page.goto(`${BASE}#${tab}`, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  return { context, page };
}
const shot = async (page, name) => {
  await page.waitForTimeout(450);
  await page.screenshot({ path: join(OUT, `overlay_${name}.png`) });
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  console.log(`${name}${wide ? "  OVERFLOW" : ""}`);
};

// Phone
let { context, page } = await open("qa", 390, 844);
await page.getByRole("button", { name: "ফিল্টার" }).click();
await page.getByRole("button", { name: "প্রয়োগ করুন" }).waitFor();
await shot(page, "chat_filter_sheet_390");
await context.close();

({ context, page } = await open("qa", 390, 844));
await page.getByRole("button", { name: "আরও" }).click();
await page.getByRole("dialog").waitFor();
await shot(page, "overflow_sheet_390");
await context.close();

({ context, page } = await open("qa", 390, 844, "light"));
await page.getByRole("button", { name: "আরও" }).click();
await page.getByRole("dialog").waitFor();
await page.getByRole("button", { name: "এই অ্যাপ সম্পর্কে" }).click();
await page.getByRole("heading", { name: "এই অ্যাপ সম্পর্কে" }).waitFor();
await shot(page, "about_dialog_390_light");
await context.close();

({ context, page } = await open("search", 390, 844));
await page.getByRole("button", { name: "ফিল্টার" }).click();
await page.getByRole("combobox", { name: "ফল" }).click();
await page.getByRole("listbox").waitFor();
await shot(page, "search_select_in_sheet_390");
await page.getByRole("option", { name: "রুল চূড়ান্ত" }).click();
await page.getByRole("combobox", { name: "বছর" }).click();
await page.getByRole("option", { name: "২০১৫ থেকে" }).click();
await page.getByRole("button", { name: "প্রয়োগ করুন" }).click();
await page.waitForTimeout(400);
await shot(page, "search_filters_applied_390");
await context.close();

({ context, page } = await open("qa", 390, 844));
await page.locator("#chat-input").focus();
await shot(page, "composer_focused_390");
await context.close();

({ context, page } = await open("qa", 320, 640));
await act.qa(page, INPUTS);
await page.getByRole("button", { name: "আলাপ মুছুন" }).click();
await page.getByRole("dialog").waitFor();
await shot(page, "clear_dialog_320");
await context.close();

// Desktop
({ context, page } = await open("qa", 1280, 800));
await page.getByRole("button", { name: "ফিল্টার" }).click();
await page.getByRole("button", { name: "প্রয়োগ করুন" }).waitFor();
await shot(page, "chat_filter_popover_1280");
await context.close();

({ context, page } = await open("qa", 1280, 800, "light"));
await page.getByRole("button", { name: /সংগ্রহের বিবরণ/ }).click();
await page.getByText("শুধু হাইকোর্ট বিভাগের রায়").waitFor();
await shot(page, "stats_popover_1280_light");
await context.close();

({ context, page } = await open("search", 1280, 800));
await page.getByRole("combobox", { name: "ফল" }).click();
await page.getByRole("listbox").waitFor();
await shot(page, "search_select_1280");
await page.getByRole("option", { name: "রুল চূড়ান্ত" }).click();
await page.waitForTimeout(250);
await page.fill("#search-input", INPUTS.search);
await page.getByRole("search").getByRole("button", { name: "খুঁজুন" }).click();
await page.getByRole("region", { name: "মামলা" }).waitFor();
await shot(page, "search_filtered_result_1280");
await context.close();

({ context, page } = await open("docs", 1280, 800));
await page.getByRole("button", { name: /ভাড়া চুক্তি/ }).click();
await page.locator("#doc-details").waitFor();
await shot(page, "docs_details_1280");
await context.close();

({ context, page } = await open("docs", 1280, 800));
await act.docs(page, INPUTS);
await page.emulateMedia({ media: "print" });
await shot(page, "docs_print_media_1280");
await context.close();

// Keyboard: the focus ring on each kind of control
({ context, page } = await open("qa", 1280, 800));
for (let i = 0; i < 8; i += 1) await page.keyboard.press("Tab");
await shot(page, "focus_ring_1280");
await context.close();

await browser.close();
