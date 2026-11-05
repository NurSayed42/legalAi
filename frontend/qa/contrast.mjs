// WCAG contrast for every text token on every surface, in both themes. Reads src/styles/tokens.css, the single source.
//   node qa/contrast.mjs
// Exits non-zero if any pair misses its target, so it can guard later token changes.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(HERE, "../src/styles/tokens.css"), "utf-8");

function block(selector) {
  const start = css.indexOf(selector);
  if (start < 0) throw new Error(`no block for ${selector}`);
  const open = css.indexOf("{", start);
  const body = css.slice(open + 1, css.indexOf("}", open));
  return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}
function parse(value) {
  if (value.startsWith("#")) {
    const h = value.slice(1);
    const hex = h.length === 3 ? [...h].map((c) => c + c).join("") : h;
    return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)).concat(1);
  }
  const m = value.match(/rgba?\(([^)]+)\)/);
  if (!m) throw new Error(`can't parse ${value}`);
  const p = m[1].split(",").map((x) => parseFloat(x));
  return [p[0], p[1], p[2], p[3] ?? 1];
}
const over = (fg, bg) => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
function luminance([r, g, b]) {
  const f = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function ratio(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

const SHARED = block(":root {");
const THEMES = { dark: block(':root[data-theme="dark"]'), light: block(':root[data-theme="light"]') };
let failures = 0;
let checks = 0;
const worst = {};

for (const [name, t] of Object.entries(THEMES)) {
  const color = (k) => parse(t[k] ?? SHARED[k]);
  const base = color("bg-0");
  const surfaces = {
    "bg-0": base,
    "bg-1": color("bg-1"),
    "bg-2": color("bg-2"),
    "bg-3": color("bg-3"),
    glass: over(color("glass"), color("bg-1")),
  };
  const text = ["text-1", "text-2", "text-3", "accent-text", "success", "danger", "warning", "info"];
  const check = (fgName, bgName, fg, bg, target) => {
    checks += 1;
    const r = ratio(fg, bg);
    const key = `${name} ${fgName}`;
    if (!worst[key] || r < worst[key].r) worst[key] = { r, bg: bgName };
    if (r < target) {
      failures += 1;
      console.log(`FAIL ${name}: ${fgName} on ${bgName} = ${r.toFixed(2)} (needs ${target})`);
    }
  };
  for (const [sName, s] of Object.entries(surfaces)) {
    for (const k of text) check(k, sName, color(k), s, 4.5);
    // Focus ring and meters are graphics: 3:1 is the WCAG 1.4.11 target.
    check("focus", sName, color("focus"), s, 3);
    check("gold-500 graphic", sName, color("gold-500"), s, 3);
    check("line-strong (control border)", sName, over(color("line-strong"), s), s, 3);
  }
  // Semantic text on its own tint, laid over each card surface.
  for (const tone of ["success", "danger", "warning", "info"]) {
    for (const sName of ["bg-0", "bg-1", "bg-2"]) {
      check(`${tone} on ${tone}-tint`, sName, color(tone), over(color(`${tone}-tint`), surfaces[sName]), 4.5);
      check(`text-1 on ${tone}-tint`, sName, color("text-1"), over(color(`${tone}-tint`), surfaces[sName]), 4.5);
      check(`text-2 on ${tone}-tint`, sName, color("text-2"), over(color(`${tone}-tint`), surfaces[sName]), 4.5);
    }
  }
  check("accent on gold-tint", "bg-1", color("accent-text"), over(color("gold-tint"), surfaces["bg-1"]), 4.5);
  // Ink on the gold button: rest is gold-500, hover is gold-400. gold-600 is never a button fill.
  for (const g of ["gold-400", "gold-500"]) check("on-gold", g, color("on-gold"), color(g), 4.5);
  // The paper panel keeps its light colours in both themes.
  check("paper-fg", "paper", color("paper-fg"), color("paper"), 4.5);
  check("paper-muted", "paper", color("paper-muted"), color("paper"), 4.5);
}

console.log(`${checks} pairs checked, ${failures} failed`);
for (const [k, v] of Object.entries(worst).sort()) console.log(`  ${k.padEnd(40)} worst ${v.r.toFixed(2)} on ${v.bg}`);
process.exit(failures ? 1 : 0);
