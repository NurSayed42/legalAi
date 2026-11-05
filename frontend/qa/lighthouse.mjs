// Lighthouse mobile run, using the installed Chrome.
//   node qa/lighthouse.mjs <url> <label>
// Full report: qa/reports/lighthouse.<label>.json (gitignored). Scores only: qa/scores.<label>.json (committed).
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as chromeLauncher from "chrome-launcher";
import lighthouse from "lighthouse";

const HERE = dirname(fileURLToPath(import.meta.url));
const [url = "http://localhost:8001/", label = "run"] = process.argv.slice(2);
mkdirSync(join(HERE, "reports"), { recursive: true });

const chrome = await chromeLauncher.launch({ chromeFlags: ["--headless=new", "--disable-gpu", "--no-first-run"] });
let result;
try {
  result = await lighthouse(url, {
    port: chrome.port,
    output: "json",
    logLevel: "error",
    onlyCategories: ["performance", "accessibility", "best-practices", "seo"],
  });
} finally {
  // On Windows the temp profile can be locked at exit. That doesn't affect the result.
  try {
    await chrome.kill();
  } catch {
    /* ignored on purpose */
  }
}

const { categories, audits } = result.lhr;
const scores = {
  url,
  label,
  lighthouseVersion: result.lhr.lighthouseVersion,
  formFactor: result.lhr.configSettings.formFactor,
  throttling: result.lhr.configSettings.throttlingMethod,
  categories: Object.fromEntries(Object.values(categories).map((c) => [c.id, Math.round((c.score ?? 0) * 100)])),
  metrics: {
    FCP_ms: Math.round(audits["first-contentful-paint"].numericValue),
    LCP_ms: Math.round(audits["largest-contentful-paint"].numericValue),
    TBT_ms: Math.round(audits["total-blocking-time"].numericValue),
    CLS: Number(audits["cumulative-layout-shift"].numericValue.toFixed(3)),
    SI_ms: Math.round(audits["speed-index"].numericValue),
    totalByteWeight_KB: Math.round(audits["total-byte-weight"].numericValue / 1024),
  },
  failedAudits: Object.values(categories).flatMap((c) =>
    c.auditRefs
      .filter((r) => r.weight > 0 && audits[r.id].score !== null && audits[r.id].score < 0.9)
      .map((r) => `${c.id}: ${r.id} (${audits[r.id].displayValue ?? audits[r.id].score})`),
  ),
};
writeFileSync(join(HERE, "reports", `lighthouse.${label}.json`), result.report);
writeFileSync(join(HERE, `scores.${label}.json`), JSON.stringify(scores, null, 2) + "\n");
console.log(JSON.stringify(scores, null, 1));
process.exit(0);
