-- routine_logs: 사람(관/보라) × 날짜별 루틴 체크 + 업무 메모/AI 요약
-- (routine.html 전용) — (log_date, owner) 조합당 1행이며 upsert로 저장합니다.
create table if not exists public.routine_logs (
  log_date date not null,
  owner text not null check (owner in ('관', '보라')),
  -- 체크 항목은 사람마다 다를 수 있어 고정 컬럼 대신 jsonb로 둡니다.
  -- 항목 정의는 DB가 아니라 routine.html의 ROUTINE_ITEMS에 있습니다.
  -- 예: {"wake_up": true, "task_blog": false}
  checks jsonb not null default '{}'::jsonb,
  work_memo text,                          -- 하루 중 쌓아둔 메모(원본)
  work_summary text,                       -- AI가 요약/정리한 결과
  updated_at timestamptz default now(),
  primary key (log_date, owner)
);

-- 최근 날짜부터 훑는 조회(날짜별 히스토리)를 위한 인덱스
create index if not exists routine_logs_owner_date_idx
  on public.routine_logs (owner, log_date desc);

-- 업데이트 시각 자동 갱신
-- search_path를 고정해두지 않으면 Supabase 보안 린터가 경고합니다.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_routine_logs_updated on public.routine_logs;
create trigger trg_routine_logs_updated
before update on public.routine_logs
for each row execute function public.set_updated_at();

-- RLS: documents / projects / workouts 등 기존 테이블과 동일한 정책을 씁니다.
-- (로그인한 사용자만 읽고 쓸 수 있고, anon 키만으로는 접근 불가)
alter table public.routine_logs enable row level security;

drop policy if exists "authenticated full access" on public.routine_logs;
create policy "authenticated full access" on public.routine_logs
  for all
  using (auth.role() = 'authenticated')
  with check (auth.role() = 'authenticated');
