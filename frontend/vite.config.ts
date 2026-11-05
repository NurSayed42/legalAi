import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const API_TARGET = process.env.VITE_PROXY_TARGET ?? "http://localhost:8001";

/** Preloads the two Bangla weights the first screen needs, using their hashed names. */
function preloadCriticalFonts(): Plugin {
  const critical = /hind-siliguri-bengali-(400|600)-normal[^/]*\.woff2$/;
  return {
    name: "preload-critical-fonts",
    enforce: "post",
    transformIndexHtml: {
      order: "post",
      handler(_html, ctx) {
        if (!ctx.bundle) return [];
        return Object.keys(ctx.bundle)
          .filter((name) => critical.test(name))
          .map((name) => ({
            tag: "link",
            attrs: { rel: "preload", as: "font", type: "font/woff2", href: `/${name}`, crossorigin: "" },
            injectTo: "head-prepend" as const,
          }));
      },
    },
  };
}

/**
 * Writes dist/sw.js from public/sw.template.js, with the app shell's hashed files and a version
 * derived from them. API responses are never cached: the template doesn't handle /api at all.
 */
function serviceWorker(): Plugin {
  let outDir = "dist";
  const shell: string[] = [];
  return {
    name: "service-worker-shell",
    apply: "build",
    configResolved(config) {
      outDir = config.build.outDir;
    },
    generateBundle(_options, bundle) {
      shell.length = 0;
      for (const [name, file] of Object.entries(bundle)) {
        const isChunk = file.type === "chunk" && name.endsWith(".js");
        const isShellAsset =
          file.type === "asset" && /\.(css|woff2)$/.test(name) && !/latin-ext|cyrillic|vietnamese|greek/.test(name);
        if (isChunk || isShellAsset) shell.push(`/${name}`);
      }
    },
    closeBundle() {
      const template = readFileSync(resolve("sw.template.js"), "utf-8");
      const version = createHash("sha256").update(shell.sort().join("\n")).digest("hex").slice(0, 12);
      const output = template
        .replace("__SHELL_VERSION__", version)
        .replace('"__SHELL_FILES__"', JSON.stringify(["/", ...shell]));
      writeFileSync(resolve(outDir, "sw.js"), output);
    },
  };
}

export default defineConfig({
  plugins: [react(), preloadCriticalFonts(), serviceWorker()],
  resolve: { alias: { "@": resolve("src") } },
  server: { port: 5173, proxy: { "/api": API_TARGET } },
  preview: { port: 4173 },
  build: { outDir: "dist", target: "es2020", assetsInlineLimit: 0, sourcemap: false, chunkSizeWarningLimit: 300 },
});
