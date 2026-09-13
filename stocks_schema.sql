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
