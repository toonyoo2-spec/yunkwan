/*
  BORAKWAN 서비스 워커
  - 앱 껍데기(HTML/CSS/JS/아이콘)만 캐싱해서 다음 실행부터 빠르게 뜨게 합니다.
  - Supabase 등 외부 API 요청은 건드리지 않고 그대로 네트워크로 흘려보냅니다.
  - HTML 페이지는 network-first(항상 최신 우선, 네트워크 실패 시에만 캐시 사용)로 처리해서
    배포 직후에도 새로고침 한 번으로 바로 최신 내용이 보이게 합니다.
  - CSS/JS/아이콘 등 정적 파일만 stale-while-revalidate(캐시 먼저 보여주고 백그라운드 갱신)로 처리합니다.
*/
const CACHE_NAME = "borakwan-shell-v9";

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

  const isHtml = req.mode === "navigate" || req.url.endsWith(".html");

  if (isHtml) {
    event.respondWith(
      // cache:'no-store'로 브라우저 HTTP 캐시까지 건너뛰고 항상 원본 서버에서 새로 받아온다.
      // (안 하면 network-first라고 해도 GitHub Pages의 Cache-Control 때문에
      //  브라우저가 자체 HTTP 캐시에서 응답을 내줘버려 배포 직후에도 옛 화면이 보일 수 있음)
      fetch(req, { cache: "no-store" })
        .then((res) => {
          if (res && res.ok) {
            caches.open(CACHE_NAME).then((cache) => cache.put(req, res.clone()));
          }
          return res;
        })
        .catch(() => caches.open(CACHE_NAME).then((cache) => cache.match(req)))
    );
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
