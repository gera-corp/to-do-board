"use strict";

// Версию подставляет сервер: отпечаток всех файлов интерфейса. Новый релиз —
// новый sw.js, браузер ставит его и заменяет оболочку целиком.
const VERSION = "__VERSION__";
const SHELL = `shell-${VERSION}`;
// Последние ответы API: по ним доска показывается без сети. Очищается при выходе.
const DATA = "data";
const FONTS = "fonts";

const SHELL_FILES = [
  "/",
  "/style.css",
  "/i18n.js",
  "/app.js",
  "/manifest.webmanifest",
  "/icons/icon.svg",
  "/icons/icon-192.png",
];
// Сохраняются только ответы на чтение доски.
const CACHEABLE_API = /^\/api\/(me|config|users|boards|tasks|stats|leaderboard)(\/|\?|$)/;

self.addEventListener("install", (event) => {
  // cache: "reload" — мимо HTTP-кеша браузера, иначе новая версия соберётся из старых файлов.
  const requests = SHELL_FILES.map((url) => new Request(url, { cache: "reload" }));
  event.waitUntil(
    caches.open(SHELL)
      .then((cache) => cache.addAll(requests))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith("shell-") && key !== SHELL) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  if (url.origin === self.location.origin) {
    if (url.pathname.startsWith("/api/")) {
      if (CACHEABLE_API.test(url.pathname + url.search)) event.respondWith(networkFirst(request));
      return;
    }
    if (url.pathname === "/sw.js") return;
    event.respondWith(fromShell(request, url));
  } else if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    event.respondWith(staleWhileRevalidate(request, FONTS));
  }
});

// HTML, CSS и JS берутся из одного релиза, иначе новая страница встретит старый скрипт.
async function fromShell(request, url) {
  const cache = await caches.open(SHELL);
  const key = request.mode === "navigate" && (url.pathname === "/" || url.pathname === "/index.html") ? "/" : request;
  return (await cache.match(key, { ignoreSearch: key === "/" })) || fetch(request);
}

// Данные — всегда свежие из сети; сохранённая копия только когда сети нет.
async function networkFirst(request) {
  const cache = await caches.open(DATA);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) {
      const headers = new Headers(cached.headers);
      headers.set("X-Offline", "1");
      return new Response(await cached.blob(), { status: cached.status, headers });
    }
    return new Response(JSON.stringify({ detail: "Нет связи с сервером" }), {
      status: 503,
      headers: { "Content-Type": "application/json", "X-Offline": "1" },
    });
  }
}

async function staleWhileRevalidate(request, name) {
  const cache = await caches.open(name);
  const cached = await cache.match(request);
  const fresh = fetch(request)
    .then((response) => {
      if (response.ok || response.type === "opaque") cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached);
  return cached || fresh;
}
