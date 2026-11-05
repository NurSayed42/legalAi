// Gzipped size of what the first page load downloads, read from the built index.html. Budget: 120 KB of JS.
//   node qa/bundle-size.mjs
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const HERE = dirname(fileURLToPath(import.meta.url));
const DIST = join(HERE, "../dist");
const BUDGET_KB = 120;
const gz = (file) => gzipSync(readFileSync(file)).length / 1024;
const html = readFileSync(join(DIST, "index.html"), "utf-8");
const initial = [...html.matchAll(/(?:src|href)="\/(assets\/[^"]+\.js)"/g)].map((m) => m[1]);

// The default tab's chunk also loads on first paint, so it counts.
const all = readdirSync(join(DIST, "assets"));
const chatChunks = all.filter((f) => /^(ChatTab|AnswerCard)[-.].*\.js$/.test(f)).map((f) => `assets/${f}`);
const css = all.filter((f) => f.endsWith(".css")).map((f) => [f, gz(join(DIST, "assets", f))]);

let total = 0;
for (const f of [...new Set([...initial, ...chatChunks])]) {
  const size = gz(join(DIST, f));
  total += size;
  console.log(`${size.toFixed(1).padStart(7)} KB  ${f}`);
}
console.log(`${total.toFixed(1).padStart(7)} KB  initial JS, gzipped (budget ${BUDGET_KB} KB)`);
console.log("\nall JS chunks:");
for (const f of all.filter((n) => n.endsWith(".js")))
  console.log(
    `${gz(join(DIST, "assets", f))
      .toFixed(1)
      .padStart(7)} KB  ${f}`,
  );
console.log("\nCSS:");
for (const [f, s] of css) console.log(`${s.toFixed(1).padStart(7)} KB  ${f}`);
process.exit(total > BUDGET_KB ? 1 : 0);
