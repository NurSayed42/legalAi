// Gzipped JS on first load, against the 120 KB budget. The default tab is Q&A, so its chunk and everything it
// statically imports count too. Follows the real import graph, so a shared chunk can't be missed.
//   node qa/bundle-size.mjs      (run after a build)
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const HERE = dirname(fileURLToPath(import.meta.url));
const ASSETS = join(HERE, "../dist/assets");
const BUDGET_KB = 120;
const gz = (name) => gzipSync(readFileSync(join(ASSETS, name))).length / 1024;
const files = readdirSync(ASSETS);

const html = readFileSync(join(HERE, "../dist/index.html"), "utf-8");
const entry = html.match(/<script[^>]+src="\/assets\/([^"]+\.js)"/)?.[1];
const chat = files.find((f) => /^ChatTab-.*\.js$/.test(f));
if (!entry || !chat) throw new Error("couldn't find the entry or the Q&A chunk");

// Dynamic imports (lazy tabs, the bottom sheet) load later, so only static ones are followed.
const STATIC_IMPORT =
  /(?:^|[;}\s])(?:import\s*(?:[\w$*{}\s,]*?\s*from\s*)?|export\s*[\w$*{}\s,]*?\s*from\s*)["']\.\/([^"']+\.js)["']/g;
const seen = new Set();
const walk = (name) => {
  if (seen.has(name)) return;
  seen.add(name);
  for (const match of readFileSync(join(ASSETS, name), "utf-8").matchAll(STATIC_IMPORT)) walk(match[1]);
};
walk(entry);
walk(chat);

const initial = [...seen].map((f) => [f, gz(f)]).sort((a, b) => b[1] - a[1]);
const total = initial.reduce((sum, [, size]) => sum + size, 0);
const later = files.filter((f) => f.endsWith(".js") && !seen.has(f));
const css = files.filter((f) => f.endsWith(".css"));

const row = (size, label) => console.log(`${size.toFixed(1).padStart(7)} KB  ${label}`);
console.log("First load:");
initial.forEach(([f, size]) => row(size, f));
row(total, `initial JS, gzipped (budget ${BUDGET_KB} KB)`);
console.log("\nLoaded later:");
later.forEach((f) => row(gz(f), f));
console.log("\nCSS:");
css.forEach((f) => row(gz(f), f));
process.exit(total > BUDGET_KB ? 1 : 0);
