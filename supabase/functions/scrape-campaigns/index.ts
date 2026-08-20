// 체험단확인(campaigns.html) 페이지용 수집기.
// 디너의여왕 / 강남맛집 / 놀러와체험단 / 리뷰노트 4곳의 모집중 목록 페이지를
// 그대로 fetch해서 파싱한 뒤, 관심 지역(용산구/마포구/영등포구/서대문구/강서구/
// 고양시/파주시) 키워드로 태깅해 campaign_listings 테이블에 upsert한다.
// pg_cron이 하루 1회 이 함수를 호출한다 (invoke-scrape-campaigns 참고).

import { createClient } from "npm:@supabase/supabase-js@2";
import * as cheerio from "npm:cheerio@1.0.0";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

// ---------------------------------------------------------------
// 지역 정규화: 원문 텍스트에 이 키워드가 있으면 해당 캐노니컬 지역으로 태깅
// ---------------------------------------------------------------
const REGION_KEYWORDS: [string, string[]][] = [
  ["마포구", ["마포", "홍대", "합정", "연남", "상수", "서교", "동교", "망원"]],
  ["서대문구", ["서대문", "신촌", "이대", "연희"]],
  ["영등포구", ["영등포", "여의도"]],
  ["용산구", ["용산", "이태원", "한남", "이촌", "삼각지"]],
  ["강서구", ["강서", "마곡", "발산", "화곡"]],
  ["고양시", ["고양", "일산", "킨텍스", "백석", "마두", "주엽", "화정"]],
  ["파주시", ["파주", "운정", "금촌", "문산"]],
];

function matchRegion(text: string): string {
  if (!text) return "미확정";
  for (const [canon, keywords] of REGION_KEYWORDS) {
    if (keywords.some((k) => text.includes(k))) return canon;
  }
  return "미확정";
}

// ---------------------------------------------------------------
// 마감일 정규화: "D-6", "D-day 1", "N일 남음", "오늘마감", "선착순" 등을
// {deadline_type, deadline_date} 로 변환
// ---------------------------------------------------------------
function parseDeadline(raw: string): { type: string; date: string | null } {
  const t = (raw || "").trim();
  const addDays = (n: number) => {
    const d = new Date();
    d.setDate(d.getDate() + n);
    return d.toISOString().slice(0, 10);
  };
  if (!t) return { type: "unknown", date: null };
  if (/선착순/.test(t)) return { type: "rolling", date: null };
  if (/오늘/.test(t) && /마감/.test(t)) return { type: "dated", date: addDays(0) };
  let m = t.match(/D-?[Dd]ay\s*(\d+)/) || t.match(/D-(\d+)/i);
  if (m) return { type: "dated", date: addDays(parseInt(m[1], 10)) };
  m = t.match(/(\d+)\s*일\s*남음/);
  if (m) return { type: "dated", date: addDays(parseInt(m[1], 10)) };
  if (/마감/.test(t)) return { type: "dated", date: addDays(0) };
  return { type: "unknown", date: null };
}

function extractBracketRegion(title: string): string {
  const m = title.match(/^\[([^\]]+)\]/);
  return m ? m[1] : "";
}

type Listing = {
  id: string;
  source: string;
  source_id: string;
  title: string;
  url: string;
  category: string;
  raw_category: string;
  region_area: string;
  region_raw: string;
  deadline_date: string | null;
  deadline_type: string;
  raw_deadline_text: string;
  reward_text: string;
};

function categorize(text: string): string {
  if (/맛집|레스토랑|식당|브런치|디저트|베이커리/.test(text)) return "맛집";
  if (/카페|커피/.test(text)) return "카페";
  if (/숙박|호텔|펜션|스테이/.test(text)) return "숙박";
  if (/뷰티|헤어|피부|네일/.test(text)) return "뷰티";
  return "기타";
}

async function fetchHtml(url: string, timeoutMs = 10000): Promise<string> {
  const res = await fetch(url, {
    headers: {
      "User-Agent": UA,
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
      "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    },
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return await res.text();
}

// ---------------------------------------------------------------
// 디너의여왕: 지역 필터 URL 5개를 순회 (홍대/마포/신촌, 여의도/영등포/구로,
// 강서/목동/마곡, 명동/이태원, 일산/파주/고양/김포/포천)
// ---------------------------------------------------------------
async function scrapeDinnerQueen(): Promise<Listing[]> {
  const filters = [
    { area1: "서울", area2: "홍대/마포/신촌" },
    { area1: "서울", area2: "여의도/영등포/구로" },
    { area1: "서울", area2: "강서/목동/마곡" },
    { area1: "서울", area2: "명동/이태원" },
    { area1: "경기", area2: "일산/파주/고양/김포/포천" },
  ];
  const seen = new Map<string, Listing>();

  for (const f of filters) {
    const url = `https://dinnerqueen.net/taste?area1=${encodeURIComponent(f.area1)}&area2=${encodeURIComponent(f.area2)}`;
    let html: string;
    try {
      html = await fetchHtml(url);
    } catch (e) {
      console.error("dinnerqueen fetch error", url, e);
      continue;
    }
    const $ = cheerio.load(html);
    $(".qz-dq-card").each((_i, el) => {
      const $el = $(el);
      const linkEl = $el.find("a.qz-dq-card__link").first();
      const href = linkEl.attr("href") || "";
      const idMatch = href.match(/\/taste\/(\d+)/);
      if (!idMatch) return;
      const sourceId = idMatch[1];
      let title = (linkEl.attr("title") || linkEl.find("img").attr("alt") || "").trim();
      title = title.replace(/\s*신청하기\s*$/, "").trim();
      if (!title) return;

      const strongs = $el
        .find(".qz-wrap strong")
        .map((_j, s) => $(s).text().trim())
        .get();
      const rawDeadline = strongs[0] || "";
      const rawCategory = strongs.slice(1).join("/");

      const regionRaw = extractBracketRegion(title);
      const { type, date } = parseDeadline(rawDeadline);

      const listing: Listing = {
        id: `디너의여왕_${sourceId}`,
        source: "디너의여왕",
        source_id: sourceId,
        title,
        url: `https://dinnerqueen.net/taste/${sourceId}`,
        category: categorize(rawCategory + title),
        raw_category: rawCategory,
        region_area: matchRegion(regionRaw || title),
        region_raw: regionRaw,
        deadline_date: date,
        deadline_type: type,
        raw_deadline_text: rawDeadline,
        reward_text: "",
      };
      seen.set(listing.id, listing);
    });
  }
  return [...seen.values()];
}

// ---------------------------------------------------------------
// 놀러와체험단 (그누보드 계열 아이템 게시판: item.php?it_id=)
// ---------------------------------------------------------------
async function scrapeCometoplay(): Promise<Listing[]> {
  const html = await fetchHtml("https://www.cometoplay.kr/");
  const $ = cheerio.load(html);
  const seen = new Map<string, Listing>();

  $(".item_box_list li").each((_i, el) => {
    const $el = $(el);
    const linkEl = $el.find('a[href*="item.php"]').first();
    const href = linkEl.attr("href") || "";
    const idMatch = href.match(/it_id=(\d+)/);
    if (!idMatch) return;
    const sourceId = idMatch[1];
    const title = $el.find(".it_name").first().text().trim();
    if (!title) return;
    const rawDeadline = $el.find("span.txt_num").first().text().trim();
    const regionRaw = extractBracketRegion(title);
    const { type, date } = parseDeadline(rawDeadline);

    const listing: Listing = {
      id: `놀러와체험단_${sourceId}`,
      source: "놀러와체험단",
      source_id: sourceId,
      title,
      url: `https://www.cometoplay.kr/item.php?it_id=${sourceId}`,
      category: categorize(title),
      raw_category: "",
      region_area: matchRegion(regionRaw || title),
      region_raw: regionRaw,
      deadline_date: date,
      deadline_type: type,
      raw_deadline_text: rawDeadline,
      reward_text: "",
    };
    seen.set(listing.id, listing);
  });
  return [...seen.values()];
}

// ---------------------------------------------------------------
// 강남맛집 (놀러와체험단과 동일한 그누보드 아이템 게시판 템플릿으로 추정 —
// 우선 같은 셀렉터로 시도하고, 못 찾으면 대괄호+마감문구 정규식으로 대체 파싱)
// ---------------------------------------------------------------
async function scrapeGangnamMatzip(): Promise<Listing[]> {
  const html = await fetchHtml("https://xn--939au0g4vj8sq.net/");
  const $ = cheerio.load(html);
  const seen = new Map<string, Listing>();

  $(".it_name, .item_box_list li, li").each((_i, el) => {
    const $el = $(el);
    const linkEl = $el.closest("li").length
      ? $el.closest("li").find('a[href*="it_id="]').first()
      : $el.find('a[href*="it_id="]').first();
    const href = linkEl.attr("href") || "";
    const idMatch = href.match(/it_id=(\d+)/);
    if (!idMatch) return;
    const sourceId = idMatch[1];
    const container = $el.closest("li").length ? $el.closest("li") : $el;
    const title = (container.find(".it_name").first().text() || "").trim();
    if (!title || seen.has(`강남맛집_${sourceId}`)) return;
    const rawDeadline = container.find("span.txt_num").first().text().trim();
    const regionRaw = extractBracketRegion(title);
    const { type, date } = parseDeadline(rawDeadline);

    seen.set(`강남맛집_${sourceId}`, {
      id: `강남맛집_${sourceId}`,
      source: "강남맛집",
      source_id: sourceId,
      title,
      url: `https://xn--939au0g4vj8sq.net/item.php?it_id=${sourceId}`,
      category: categorize(title),
      raw_category: "",
      region_area: matchRegion(regionRaw || title),
      region_raw: regionRaw,
      deadline_date: date,
      deadline_type: type,
      raw_deadline_text: rawDeadline,
      reward_text: "",
    });
  });

  if (seen.size === 0) {
    // 폴백: 그누보드 셀렉터로 못 찾은 경우, "[지역] 제목" + 마감 문구 패턴을
    // 정규식으로 직접 스캔 (사이트 구조가 예상과 다를 때의 안전망)
    const re = /href="([^"]*it_id=(\d+)[^"]*)"[^>]*>[\s\S]{0,300}?\[([^\]]+)\]\s*([^<]{2,80})/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(html))) {
      const sourceId = m[2];
      const regionRaw = m[3];
      const title = `[${m[3]}] ${m[4]}`.trim();
      const id = `강남맛집_${sourceId}`;
      if (seen.has(id)) continue;
      seen.set(id, {
        id,
        source: "강남맛집",
        source_id: sourceId,
        title,
        url: `https://xn--939au0g4vj8sq.net/item.php?it_id=${sourceId}`,
        category: categorize(title),
        raw_category: "",
        region_area: matchRegion(regionRaw),
        region_raw: regionRaw,
        deadline_date: null,
        deadline_type: "unknown",
        raw_deadline_text: "",
        reward_text: "",
      });
    }
  }
  return [...seen.values()];
}

// ---------------------------------------------------------------
// 리뷰노트 (Next.js SSR). 페이지 안에 거대한 인라인 스크립트 JSON이 있어서인지
// cheerio(htmlparser2)로 DOM을 파싱하면 카드가 0개로 나옴 — 대신 카드 시작
// 마커 문자열로 원문을 직접 잘라서 파싱 (그누보드 사이트들과 같은 방식)
// ---------------------------------------------------------------
async function scrapeReviewnote(): Promise<Listing[]> {
  const html = await fetchHtml("https://www.reviewnote.co.kr/");
  const seen = new Map<string, Listing>();

  const marker = 'class="transform overflow-hidden rounded border';
  const chunks = html.split(marker).slice(1);
  for (const chunk of chunks) {
    const idMatch = chunk.match(/\/campaigns\/(\d+)/);
    if (!idMatch) continue;
    const sourceId = idMatch[1];
    const titleMatch = chunk.match(/class="truncate text-16m"[^>]*>([^<]+)</);
    const title = (titleMatch ? titleMatch[1] : "").trim();
    if (!title) continue;
    const deadlineMatch = chunk.match(/text-14b"[^>]*>([^<]*)</);
    const rawDeadline = (deadlineMatch ? deadlineMatch[1] : "").trim();
    const rewardMatch = chunk.match(/text-14r"[^>]*>([^<]*)</);
    const reward = (rewardMatch ? rewardMatch[1] : "").trim();
    const regionRaw = extractBracketRegion(title);
    const { type, date } = parseDeadline(rawDeadline);

    seen.set(`리뷰노트_${sourceId}`, {
      id: `리뷰노트_${sourceId}`,
      source: "리뷰노트",
      source_id: sourceId,
      title,
      url: `https://www.reviewnote.co.kr/campaigns/${sourceId}`,
      category: categorize(title + " " + reward),
      raw_category: "",
      region_area: matchRegion(regionRaw || title),
      region_raw: regionRaw,
      deadline_date: date,
      deadline_type: type,
      raw_deadline_text: rawDeadline,
      reward_text: reward,
    });
  }
  return [...seen.values()];
}

// ---------------------------------------------------------------
// 레뷰 (revu.net). www.revu.net 자체는 SPA라 목록이 없고, 실제 데이터는
// 로그인 후 api.weble.net에서 JWT로 받아온다.
// - 로그인: POST https://api.weble.net/tokens {username,password,remember}
// - 목록: GET https://api.weble.net/v1/campaigns?cat=지역&... (Bearer 필요)
// 로그인 정보는 vault에 저장해두고 get_revu_credentials() RPC로만 꺼낸다.
// ---------------------------------------------------------------
async function loginRevu(sb: ReturnType<typeof createClient>): Promise<string> {
  const { data, error } = await sb.rpc("get_revu_credentials");
  if (error) throw new Error(`레뷰 로그인 정보 조회 실패: ${error.message}`);
  const cred = Array.isArray(data) ? data[0] : data;
  if (!cred?.username || !cred?.password) throw new Error("레뷰 로그인 정보 없음 (vault 확인 필요)");

  const res = await fetch("https://api.weble.net/tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify({ username: cred.username, password: cred.password, remember: true }),
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) throw new Error(`레뷰 로그인 실패: HTTP ${res.status}`);
  const body = await res.json();
  if (!body.token) throw new Error("레뷰 로그인 응답에 token 없음");
  return body.token as string;
}

async function scrapeRevu(sb: ReturnType<typeof createClient>): Promise<Listing[]> {
  const token = await loginRevu(sb);
  const seen = new Map<string, Listing>();
  const maxPages = 20; // 최신순 정렬, 페이지당 30건 = 최신 600건까지 확인

  for (let page = 1; page <= maxPages; page++) {
    const url =
      `https://api.weble.net/v1/campaigns?cat=%EC%A7%80%EC%97%AD&limit=30` +
      `&media[]=blog&media[]=instagram&media[]=youtube&media[]=clip&media[]=etc` +
      `&page=${page}&sort=latest&type=play`;
    const res = await fetch(url, {
      headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) break;
    const data = await res.json();
    const items = data.items || [];
    if (items.length === 0) break;

    for (const it of items) {
      const addr = it.venue?.addressFirst || "";
      const localTag = (it.localTag || [])[0] || "";
      const title = it.item || it.venue?.name || "";
      if (!title) continue;
      const sourceId = String(it.id);
      const categoryList: string[] = it.category || [];

      seen.set(`레뷰_${sourceId}`, {
        id: `레뷰_${sourceId}`,
        source: "레뷰",
        source_id: sourceId,
        title,
        url: `https://www.revu.net/campaign/${sourceId}`,
        category: categorize(categoryList.join(" ") + " " + title),
        raw_category: categoryList.join("/"),
        region_area: matchRegion(addr || localTag),
        region_raw: addr || localTag,
        deadline_date: it.requestEndedOn || null,
        deadline_type: it.requestEndedOn ? "dated" : "unknown",
        raw_deadline_text: it.requestEndedOn || "",
        reward_text: it.campaignData?.reward || "",
      });
    }

    if (data.total && page * data.limit >= data.total) break;
  }
  return [...seen.values()];
}

Deno.serve(async (_req) => {
  const sb = createClient(SUPABASE_URL, SERVICE_ROLE_KEY);
  const results: Record<string, number | string> = {};
  let all: Listing[] = [];

  const scrapers: [string, () => Promise<Listing[]>][] = [
    ["디너의여왕", scrapeDinnerQueen],
    ["놀러와체험단", scrapeCometoplay],
    ["강남맛집", scrapeGangnamMatzip],
    ["리뷰노트", scrapeReviewnote],
    ["레뷰", () => scrapeRevu(sb)],
  ];

  for (const [name, fn] of scrapers) {
    try {
      const items = await fn();
      results[name] = items.length;
      all = all.concat(items);
    } catch (e) {
      console.error(name, "scrape failed", e);
      results[name] = `error: ${String(e)}`;
    }
  }

  // 관심 지역(미확정 제외)만 저장 — 나머지는 애초에 우리 관심사가 아님
  const interesting = all.filter((it) => it.region_area !== "미확정");

  if (interesting.length > 0) {
    const { error } = await sb.from("campaign_listings").upsert(interesting, { onConflict: "id" });
    if (error) {
      console.error("upsert error", error);
      return new Response(JSON.stringify({ ok: false, error: error.message, results }), { status: 500 });
    }
  }

  // 마감 지난 지 3일 넘은 항목은 정리
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 3);
  await sb
    .from("campaign_listings")
    .delete()
    .eq("deadline_type", "dated")
    .lt("deadline_date", cutoff.toISOString().slice(0, 10));

  return new Response(
    JSON.stringify({ ok: true, results, saved: interesting.length, totalScraped: all.length }),
    { headers: { "Content-Type": "application/json" } },
  );
});
