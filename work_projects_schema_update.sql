-- 곽현 프로젝트 누적 방식을 위한 컬럼 추가
-- 기존 projects 테이블에 다음 컬럼들을 추가합니다.

alter table public.projects
  add column if not exists "workDates" jsonb default '[]'::jsonb,
  add column if not exists "currentAmount" numeric default 0,
  add column if not exists "settlementHistory" jsonb default '[]'::jsonb,
  add column if not exists "isAccumulative" boolean not null default false;

-- workDates: 현재 누적 중인 작업일 배열 ["2026-07-26", "2026-07-27"]
-- currentAmount: 현재 누적 금액 (workDates 개수 × 일당)
-- settlementHistory: 정산 히스토리 배열
--   [{ "settledAt": "2026-09-02", "amount": 200000, "workDates": [...], "paymentMethod": "현금" }]
-- isAccumulative: 누적 방식 프로젝트 여부 (곽현 프로젝트는 true)

comment on column public.projects."workDates" is '현재 누적 중인 작업일 배열';
comment on column public.projects."currentAmount" is '현재 누적 금액';
comment on column public.projects."settlementHistory" is '정산 히스토리 (JSON 배열)';
comment on column public.projects."isAccumulative" is '누적 방식 프로젝트 여부';
