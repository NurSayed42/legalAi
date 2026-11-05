// A Tailwind class that isn't in the config produces no CSS and no error, so the mistake only shows up by eye.
// The palette and spacing scale are deliberately restricted here, which makes this easy to do. This finds them.
//   node qa/unknown-classes.mjs      (run after a build)
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, "../src");
const ASSETS = join(HERE, "../dist/assets");
const css = readdirSync(ASSETS)
  .filter((f) => f.endsWith(".css"))
  .map((f) => readFileSync(join(ASSETS, f), "utf-8"))
  .join("\n");

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : /\.tsx$/.test(name) || /tabs\.ts$/.test(name) ? [path] : [];
  });
}

// Markers that style through other classes, with no CSS of their own.
const MARKERS = new Set(["group", "group/nav", "group/disclaimer", "no-print", "print-area"]);
const TOKEN = /^(?:[a-z0-9-]+(?:\[[^\]]*\])?:|\[[^\]]+\]:)*!?-?(?:[a-z][a-z0-9/.-]*(?:\[[^\]]+\])?|\[[^\]]+\])$/;
const WORDS_THAT_ARENT_CLASSES = new Set([
  "true",
  "false",
  "button",
  "submit",
  "type",
  "use",
  "strict",
  "single",
  "search",
  "none",
  "dialog",
  "step",
  "polite",
  "additions",
  "start",
  "end",
  "top",
  "auto",
  "manual",
  "popper",
  "dark",
  "light",
  "ltr",
  "en",
  "bn",
  "ol",
  "ul",
  "up",
  "down",
  "text",
]);

const missing = new Map();
let checked = 0;
for (const file of walk(SRC)) {
  const source = readFileSync(file, "utf-8");
  // Class strings: anything in className=, cn(...), or the style maps at the top of a component.
  const strings = [...source.matchAll(/"([^"\n]*)"/g)].map((m) => m[1]);
  for (const text of strings) {
    if (
      !/(^|\s)(?:[a-z-]+:)*(?:-?(?:p[xytblr]?|m[xytblr]?|gap|h|w|min-h|min-w|max-w|max-h|text|bg|border|rounded|flex|grid|inline-flex|items|justify|font|shadow|z|top|left|right|bottom|inset|hit-target|press|shrink|truncate|absolute|relative|fixed|sticky|hidden|block|sr-only|overflow|leading|transition|duration|opacity|animate|rise-in|tab-panel-in|clamp-3|glass|whitespace|select|cursor|list|snap|scroll|self|flex-1|mt|mb|ml|mr|pt|pb|pl|pr|px|py))(-|\s|$)/.test(
        text,
      )
    )
      continue;
    for (const token of text.split(/\s+/).filter(Boolean)) {
      if (!TOKEN.test(token) || WORDS_THAT_ARENT_CLASSES.has(token) || MARKERS.has(token)) continue;
      checked += 1;
      const escaped = token.replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`);
      if (!css.includes(`.${escaped}`)) {
        const list = missing.get(token) ?? new Set();
        list.add(file.slice(SRC.length + 1));
        missing.set(token, list);
      }
    }
  }
}
console.log(`${checked} class uses checked, ${missing.size} with no CSS`);
for (const [token, files] of [...missing].sort()) console.log(`  ${token}   <- ${[...files].join(", ")}`);
process.exit(missing.size ? 1 : 0);
