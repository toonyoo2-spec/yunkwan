-- 주식 기록: 사이트 업로드용 테이블
--
-- 여기에는 토스 '시세정보'를 넣지 않습니다.
-- 올라가는 것: 종목명·코드, 셋업 이름, 목표/손절 비율(%), 판정 결과, 비용 차감 손익률(%),
--              적중률·표본수 같은 집계 지표.
-- 올라가지 않는 것: 원(₩) 단위 가격 전부, 시가·고가·저가·종가, 분봉, 거래량·거래대금,
--                   호가·잔량, 투자자별 순매수 주식 수.
-- 업로드 직전 tools/stocks/publish.py의 화이트리스트와 가격 검사를 통과한 값만 들어옵니다.
--
-- Supabase 대시보드 → SQL Editor에 붙여넣고 Run 하세요.

create table if not exists public.stock_reports (
  id           bigint generated always as identity primary key,
  owner_id     uuid not null default auth.uid() references auth.users(id) on delete cascade,
  trade_date   date not null,
  forecast     jsonb not null,
  assessment   jsonb,
  uploaded_at  timestamptz not null default now(),
  unique (owner_id, trade_date)
);

create index if not exists stock_reports_owner_date_idx
  on public.stock_reports (owner_id, trade_date desc);

alter table public.stock_reports enable row level security;

-- 본인이 올린 기록만 읽고 쓸 수 있습니다.
-- 다른 계정으로 로그인하면 조회 결과가 0건이 됩니다. 화면에서 가리는 것이 아니라
-- 서버가 아예 내려주지 않습니다.
drop policy if exists stock_reports_select_own on public.stock_reports;
create policy stock_reports_select_own on public.stock_reports
  for select using (auth.uid() = owner_id);

drop policy if exists stock_reports_insert_own on public.stock_reports;
create policy stock_reports_insert_own on public.stock_reports
  for insert with check (auth.uid() = owner_id);

drop policy if exists stock_reports_update_own on public.stock_reports;
create policy stock_reports_update_own on public.stock_reports
  for update using (auth.uid() = owner_id) with check (auth.uid() = owner_id);

drop policy if exists stock_reports_delete_own on public.stock_reports;
create policy stock_reports_delete_own on public.stock_reports
  for delete using (auth.uid() = owner_id);

-- 분석 요약 1행. 과거 채점·보유기간 비교·강도 보정·우연 배제 판정을 담습니다.
-- 여기에도 시세는 들어가지 않습니다. publish.sanitize_research의 화이트리스트를
-- 통과한 비율(%)과 판정 문구만 올라옵니다.
create table if not exists public.stock_research (
  owner_id    uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  payload     jsonb not null,
  updated_at  timestamptz not null default now()
);

alter table public.stock_research enable row level security;

drop policy if exists stock_research_select_own on public.stock_research;
create policy stock_research_select_own on public.stock_research
  for select using (auth.uid() = owner_id);

drop policy if exists stock_research_insert_own on public.stock_research;
create policy stock_research_insert_own on public.stock_research
  for insert with check (auth.uid() = owner_id);

drop policy if exists stock_research_update_own on public.stock_research;
create policy stock_research_update_own on public.stock_research
  for update using (auth.uid() = owner_id) with check (auth.uid() = owner_id);

drop policy if exists stock_research_delete_own on public.stock_research;
create policy stock_research_delete_own on public.stock_research
  for delete using (auth.uid() = owner_id);

-- 실제 보유 종목. 사용자가 직접 입력한 본인의 체결 기록입니다.
-- 토스가 제공한 시세가 아니라 본인이 적은 본인 거래이므로 여기에 담깁니다.
-- 현재가·호가 같은 토스 시세는 여전히 맥북 밖으로 나가지 않습니다.
create table if not exists public.stock_positions (
  id            bigint generated always as identity primary key,
  owner_id      uuid not null default auth.uid() references auth.users(id) on delete cascade,
  symbol        text not null check (symbol ~ '^[A-Za-z0-9]{6}$'),
  name          text,
  market        text,
  entry_date    date not null,
  entry_price   numeric not null check (entry_price > 0),
  quantity      integer not null default 1 check (quantity > 0),
  target_pct    numeric,
  stop_pct      numeric,
  status        text not null default 'open' check (status in ('open', 'closed')),
  exit_date     date,
  exit_price    numeric check (exit_price is null or exit_price > 0),
  note          text,
  verdict       jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists stock_positions_owner_status_idx
  on public.stock_positions (owner_id, status, entry_date desc);

alter table public.stock_positions enable row level security;

drop policy if exists stock_positions_select_own on public.stock_positions;
create policy stock_positions_select_own on public.stock_positions
  for select using (auth.uid() = owner_id);

drop policy if exists stock_positions_insert_own on public.stock_positions;
create policy stock_positions_insert_own on public.stock_positions
  for insert with check (auth.uid() = owner_id);

drop policy if exists stock_positions_update_own on public.stock_positions;
create policy stock_positions_update_own on public.stock_positions
  for update using (auth.uid() = owner_id) with check (auth.uid() = owner_id);

drop policy if exists stock_positions_delete_own on public.stock_positions;
create policy stock_positions_delete_own on public.stock_positions
  for delete using (auth.uid() = owner_id);

-- 보유 성격 구분.
--   swing: 단타·스윙. 손절·목표·보유기간 규칙을 그대로 적용합니다.
--   long : 장기 보유. 보유기간 만료와 강도 붕괴로 매도 신호를 내지 않습니다.
alter table public.stock_positions
  add column if not exists strategy text not null default 'swing';
alter table public.stock_positions
  drop constraint if exists stock_positions_strategy_check;
alter table public.stock_positions
  add constraint stock_positions_strategy_check check (strategy in ('swing', 'long'));
