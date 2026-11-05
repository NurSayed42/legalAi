/* App-shell service worker. Built into dist/sw.js by vite.config.ts.
   It never touches /api: legal answers must always come from the server, never from a cache. */
const VERSION = "__SHELL_VERSION__";
const CACHE = `legalai-shell-${VERSION}`;
const SHELL = "__SHELL_FILES__";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key.startsWith("legalai-shell-") && key !== CACHE).map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/live-info") return;

  if (request.mode === "navigate") {
    // Network first, so a new build is picked up. The cached shell is only the offline fallback.
    event.respondWith(fetch(request).catch(() => caches.match("/")));
    return;
  }
  if (url.pathname.startsWith("/assets/")) {
    // Hashed files never change, so the cache can answer first.
    event.respondWith(caches.match(request).then((hit) => hit ?? fetch(request)));
  }
});
