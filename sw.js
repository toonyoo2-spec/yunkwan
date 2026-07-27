/*
  BORAKWAN 서비스 워커
  - 앱 껍데기(HTML/CSS/JS/아이콘)만 캐싱해서 다음 실행부터 빠르게 뜨게 합니다.
  - Supabase 등 외부 API 요청은 건드리지 않고 그대로 네트워크로 흘려보냅니다.
  - 전략: stale-while-revalidate (캐시 먼저 보여주고, 백그라운드에서 최신 파일로 갱신)
*/
const CACHE_NAME = "borakwan-shell-v2";

const SHELL_FILES = [
  "./index.html",
  "./ledger.html",
  "./review.html",
  "./docs.html",
  "./report.html",
  "./work.html",
  "./task.html",
  "./trip.html",
  "./common.css",
  "./common.js",
  "./nav.js",
  "./panel-common.css",
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/icon-maskable-192.png",
  "./icons/icon-maskable-512.png",
  "./icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;

  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) {
    return;
  }

  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(req);
      const fetchPromise = fetch(req)
        .then((res) => {
          if (res && res.ok) cache.put(req, res.clone());
          return res;
        })
        .catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
