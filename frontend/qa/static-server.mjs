// Serves dist/ the way evals/serve_live.py does (same cache rules, gzip), without loading the 3 GB pipeline.
// Only /api/stats answers, with made-up data. QA pages stub the other endpoints themselves.
//   node qa/static-server.mjs [port]
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";
import { createGzip } from "node:zlib";
import { stats } from "./fixtures.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const DIST = join(HERE, "../dist");
const PORT = Number(process.argv[2] ?? 4173);
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff2": "font/woff2",
  ".webmanifest": "application/manifest+json",
  ".txt": "text/plain; charset=utf-8",
};

createServer((req, res) => {
  const path = new URL(req.url, "http://x").pathname;
  if (path === "/api/stats") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(stats));
    return;
  }
  if (path.startsWith("/api/")) {
    res.writeHead(404, { "content-type": "application/json" });
    res.end('{"detail":"stub server"}');
    return;
  }
  let file = join(DIST, normalize(path));
  if (!file.startsWith(DIST) || !existsSync(file) || statSync(file).isDirectory()) file = join(DIST, "index.html");
  const type = TYPES[extname(file)] ?? "application/octet-stream";
  const immutable = path.startsWith("/assets/");
  const headers = {
    "content-type": type,
    "cache-control": immutable ? "public, max-age=31536000, immutable" : "no-cache",
  };
  const compress = /text|javascript|svg|json/.test(type) && (req.headers["accept-encoding"] ?? "").includes("gzip");
  if (compress) headers["content-encoding"] = "gzip";
  res.writeHead(200, headers);
  const stream = createReadStream(file);
  (compress ? stream.pipe(createGzip()) : stream).pipe(res);
}).listen(PORT, "127.0.0.1", () => console.log(`static http://localhost:${PORT}`));
