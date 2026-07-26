-- work.html(프로젝트 관리 페이지)에서 사용하는 projects 테이블
-- kwan-tools Supabase 프로젝트(kblwddlquwlvumhwkirl)에는 이미 적용되어 있습니다.
-- 다른 환경에 똑같이 옮길 때는 이 파일 전체를 SQL Editor에서 실행하면 됩니다.
-- (trips/trip_items 테이블과 동일하게 camelCase 컬럼명을 그대로 사용합니다.)

create table if not exists public.projects (
  id text primary key,
  title text not null,
  manager text,
  contact text,
  "startDate" date not null,
  "endDate" date,
  "settlementDate" date,
  cost numeric not null default 0,
  "paymentMethod" text not null default '현금'
    check ("paymentMethod" in ('하나은행', '농협은행', '현금')),
  status text not null default '시작전'
    check (status in ('시작전', '작업중', '작업완료', '정산완료')),
  memo text,
  "colorHue" integer not null default 200,
  "ledgerSynced" boolean not null default false,
  "createdAt" timestamptz not null default now()
);

alter table public.projects enable row level security;

-- 로그인한(=authenticated) 사용자는 자유롭게 읽고 쓸 수 있도록 허용합니다.
-- (trips/transactions 등 다른 테이블과 동일한 정책을 그대로 맞췄습니다.)
drop policy if exists "authenticated full access" on public.projects;
create policy "authenticated full access" on public.projects
  for all
  to public
  using (auth.role() = 'authenticated'::text)
  with check (auth.role() = 'authenticated'::text);
