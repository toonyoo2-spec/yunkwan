-- routine_logs: 평일 루틴 체크 + 업무 메모/AI 요약 (routine.html 전용)
-- 하루에 1행만 존재하며, log_date를 기준으로 upsert 합니다.
create table if not exists public.routine_logs (
  log_date date primary key,               -- 하루에 1행
  wake_up boolean default false,           -- 05:45 기상
  leave_home boolean default false,        -- 06:00 출근(집에서 나옴)
  exercise_start boolean default false,    -- 07:20 운동 시작
  arrive_work boolean default false,       -- 08:30 출근(도착)
  leave_work boolean default false,        -- 17:30 퇴근
  sleep boolean default false,             -- 23:00 취침
  task_edit boolean default false,         -- 20:00 편집
  task_blog boolean default false,         -- 20:00 블로그쓰기
  work_memo text,                          -- 하루 중 쌓아둔 메모(원본)
  work_summary text,                       -- AI가 요약/정리한 결과
  updated_at timestamptz default now()
);

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
