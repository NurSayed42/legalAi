// Renders the favicon set and Open Graph image from one source, using the installed Chrome.
//   node qa/make-icons.mjs
// Writes to public/, and the results are committed. Re-run only if the mark changes.
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = join(HERE, "../public");
const ICONS = join(PUBLIC, "icons");
mkdirSync(ICONS, { recursive: true });

const GLYPH = `<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>`;
const svg = (size, glyphScale, radius) => {
  const g = size * glyphScale;
  const offset = (size - g) / 2;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"><rect width="${size}" height="${size}" rx="${radius}" fill="#111317"/><rect x=".5" y=".5" width="${size - 1}" height="${size - 1}" rx="${radius}" fill="none" stroke="#A8843A" stroke-opacity=".6"/><svg x="${offset}" y="${offset}" width="${g}" height="${g}" viewBox="0 0 24 24" fill="none" stroke="#E2B95C" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${GLYPH}</svg></svg>`;
};

writeFileSync(join(PUBLIC, "favicon.svg"), svg(64, 0.68, 14) + "\n");

const browser = await chromium.launch({ channel: "chrome", headless: true });
async function render(name, width, height, html) {
  const page = await browser.newPage({ viewport: { width, height } });
  await page.setContent(`<!doctype html><html><body style="margin:0;background:transparent">${html}</body></html>`);
  await page.screenshot({ path: name, omitBackground: true });
  await page.close();
}
await render(join(ICONS, "icon-32.png"), 32, 32, svg(32, 0.72, 7));
await render(join(ICONS, "icon-192.png"), 192, 192, svg(192, 0.64, 40));
await render(join(ICONS, "icon-512.png"), 512, 512, svg(512, 0.64, 104));
await render(join(ICONS, "apple-touch-icon.png"), 180, 180, svg(180, 0.64, 0));
// Maskable: full bleed, with the glyph inside the central safe zone.
await render(join(ICONS, "maskable-512.png"), 512, 512, svg(512, 0.5, 0));

// Open Graph image. Fonts load from a temp file so file:// URLs are allowed.
const fonts = join(HERE, "../node_modules/@fontsource");
const face = (family, file, weight) =>
  `@font-face{font-family:'${family}';font-weight:${weight};src:url('${pathToFileURL(join(fonts, file)).href}')}`;
const og = `<!doctype html><html lang="bn"><head><meta charset="utf-8"><style>
${face("Fraunces", "fraunces/files/fraunces-latin-600-normal.woff2", 600)}
${face("Serif Bengali", "noto-serif-bengali/files/noto-serif-bengali-bengali-600-normal.woff2", 600)}
${face("Hind", "hind-siliguri/files/hind-siliguri-bengali-400-normal.woff2", 400)}
*{box-sizing:border-box}body{margin:0;width:1200px;height:630px;background:#0A0B0D;color:#F2EFE8;display:flex;flex-direction:column;justify-content:center;padding:0 96px;position:relative;overflow:hidden}
body::before{content:"";position:absolute;inset:0;background:radial-gradient(60% 70% at 18% 0%,rgba(201,161,74,.16),transparent 70%)}
.row{display:flex;align-items:center;gap:24px;position:relative}.word{font:600 44px Fraunces,serif}
h1{font:600 76px/1.3 'Serif Bengali',serif;margin:40px 0 16px;position:relative}p{font:400 34px/1.5 Hind,sans-serif;color:#B9B4A8;margin:0;position:relative}
.rule{position:absolute;left:96px;right:96px;bottom:64px;height:2px;background:linear-gradient(90deg,#C9A14A,transparent)}
</style></head><body><div class="row">${svg(84, 0.64, 18)}<span class="word">LegalAI Bangladesh</span></div><h1>বাংলাদেশ আইনি সহায়তা</h1><p>হাইকোর্ট বিভাগের রায় ও বাংলাদেশের আইন থেকে সূত্রসহ তথ্য</p><div class="rule"></div></body></html>`;
const tmp = join(HERE, "og.tmp.html");
writeFileSync(tmp, og);
const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
await page.goto(pathToFileURL(tmp).href, { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: join(PUBLIC, "og.png") });
await browser.close();
rmSync(tmp);
console.log("icons written");
