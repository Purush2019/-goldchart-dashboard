const APP_VERSION = "1.0.10";
const CACHE = `pulseplay-v${APP_VERSION}`;
const APP_SHELL = ["./", "./index.html", "./manifest.json", "./icon.svg", "./icon-180.png", "./icon-192.png", "./icon-512.png"];

self.addEventListener("message", event => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(APP_SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", event => {
  const req = event.request;
  const url = new URL(req.url);

  // Only handle same-origin GET requests
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  // Never cache media/audio ranges; let network handle them
  const range = req.headers.get("range");
  if (range || url.pathname.endsWith(".mp3") || url.pathname.endsWith(".m4a") || url.pathname.endsWith(".mp4") || url.pathname.endsWith(".webm")) return;

  // Network-first for core documents/scripts/styles to avoid stale mobile caches
  const isCoreAsset =
    req.mode === "navigate" ||
    url.pathname.endsWith(".html") ||
    url.pathname.endsWith(".js") ||
    url.pathname.endsWith(".css");

  if (isCoreAsset) {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req, { cache: "no-store" });
        const cache = await caches.open(CACHE);
        cache.put(req, fresh.clone());
        return fresh;
      } catch (_) {
        const cached = await caches.match(req);
        return cached || caches.match("./index.html");
      }
    })());
    return;
  }

  // Cache-first for other same-origin static assets
  event.respondWith((async () => {
    const cached = await caches.match(req);
    if (cached) return cached;
    try {
      const fresh = await fetch(req);
      const cache = await caches.open(CACHE);
      cache.put(req, fresh.clone());
      return fresh;
    } catch (_) {
      return caches.match("./index.html");
    }
  })());
});
