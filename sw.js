/*
  BORAKWAN 서비스 워커
  - 앱 껍데기(HTML/CSS/JS/아이콘)만 캐싱해서 다음 실행부터 빠르게 뜨게 합니다.
  - Supabase 등 외부 API 요청은 건드리지 않고 그대로 네트워크로 흘려보냅니다.
  - HTML 페이지와 ALWAYS_FRESH 목록(nav.js 등 모든 페이지가 공유하는 파일)은
    network-first(항상 최신 우선, 네트워크 실패 시에만 캐시 사용)로 처리해서
    배포 직후에도 새로고침 한 번으로 바로 최신 내용이 보이게 합니다.
  - 나머지 정적 파일만 stale-while-revalidate(캐시 먼저 보여주고 백그라운드 갱신)로 처리합니다.
*/
const CACHE_NAME = "borakwan-shell-v18";

/*
  어떤 페이지에 들어가도 항상 최신이어야 하는 공용 파일들.

  nav.js는 모든 페이지가 공유하는 메뉴라서, 캐시본 하나가 낡으면 모든 페이지의
  메뉴가 옛날 것으로 보입니다. 예전에는 stale-while-revalidate였던 탓에
  캐시본을 먼저 돌려주고 백그라운드로만 갱신해서 "항상 한 번 늦게" 반영됐고,
  HTML의 ?v= 번호를 올려야만 확실히 갱신되는 구조라 그 번호를 깜빡하면
  메뉴가 계속 옛날 상태로 남았습니다. (충전소 → 주식 교체가 안 보이던 원인)
*/
const ALWAYS_FRESH = [
  "/nav.js",
  "/common.js",
  "/common.css",
  "/panel-common.css",
];

function isAlwaysFresh(url) {
  // ?v=7 같은 쿼리는 무시하고 경로만 비교합니다.
  return ALWAYS_FRESH.some((path) => url.pathname.endsWith(path));
}

// cache:'no-store'로 브라우저 HTTP 캐시까지 건너뛰고 항상 원본 서버에서 새로 받아온다.
// (안 하면 network-first라고 해도 GitHub Pages의 Cache-Control 때문에
//  브라우저가 자체 HTTP 캐시에서 응답을 내줘버려 배포 직후에도 옛 화면이 보일 수 있음)
function networkFirst(req) {
  return fetch(req, { cache: "no-store" })
    .then((res) => {
      if (res && res.ok) {
        caches.open(CACHE_NAME).then((cache) => cache.put(req, res.clone()));
      }
      return res;
    })
    .catch(() =>
      // 오프라인 대비. ignoreSearch를 켜야 ?v= 가 붙지 않은 설치 캐시본으로도 떨어집니다.
      caches.open(CACHE_NAME).then((cache) => cache.match(req, { ignoreSearch: true }))
    );
}

const SHELL_FILES = [
  "./index.html",
  "./ledger.html",
  "./review.html",
  "./docs.html",
  "./routine.html",
  "./stocks.html",
  "./task.html",
  "./trip.html",
  "./common.css",
  "./common.js",
  "./nav.js",
  "./stocks.css",
  "./stocks.js",
  "./stocks-data.js",
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

  // req.url 대신 pathname으로 비교해야 ?v=7 같은 쿼리가 붙어도 제대로 걸립니다.
  const url = new URL(req.url);
  const isHtml = req.mode === "navigate" || url.pathname.endsWith(".html");

  if (isHtml || isAlwaysFresh(url)) {
    event.respondWith(networkFirst(req));
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
