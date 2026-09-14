-- 폰(MacroDroid)에서 루틴 항목을 자동으로 체크하는 경로
-- ---------------------------------------------------------------
-- 가계부의 insert_transaction_from_device와 같은 방식입니다.
-- (SECURITY DEFINER 함수 + anon 실행 권한 + 공유 시크릿)
--
-- 쓰는 곳: MacroDroid → HTTP 요청(POST)
--   URL   : https://kblwddlquwlvumhwkirl.supabase.co/rest/v1/rpc/check_routine_from_device
--   헤더  : apikey / Authorization: Bearer <anon key> / Content-Type: application/json
--   본문  : {"p_secret":"...","p_key":"leave_home"}
--
-- ⚠️ 아래 CHANGE_ME_... 를 본인이 정한 긴 무작위 문자열로 바꾼 뒤 실행하세요.
--    (터미널에서 `openssl rand -hex 24` 로 하나 뽑으면 됩니다)
--    이 값은 폰의 MacroDroid에도 똑같이 넣습니다.

-- 1) 실제로 몇 시에 했는지를 담을 칸
--    checks 는 그대로 두고(기존 화면·집계가 그대로 동작), 시각만 따로 보관합니다.
--    예: {"leave_home": "06:03", "arrive_work": "08:24"}
alter table public.routine_logs
  add column if not exists check_times jsonb not null default '{}'::jsonb;

-- 2) 폰에서 호출하는 함수
create or replace function public.check_routine_from_device(
  p_secret text,
  p_key    text,
  p_owner  text default '관',
  p_at     text default null    -- "HH:MM". 비우면 호출 시점(한국시간)을 씁니다.
)
returns json
language plpgsql
security definer
set search_path = ''
as $$
declare
  -- ⚠️ 여기를 바꾸세요.
  v_expected_secret constant text := 'CHANGE_ME_긴_무작위_문자열';

  -- DB 서버는 UTC라 한국시간으로 바꿔야 새벽 기록이 전날로 들어가지 않습니다.
  v_date date := (now() at time zone 'Asia/Seoul')::date;
  v_time text := coalesce(p_at, to_char(now() at time zone 'Asia/Seoul', 'HH24:MI'));
begin
  if p_secret is distinct from v_expected_secret then
    raise exception 'unauthorized';
  end if;

  if p_owner not in ('관', '보라') then
    raise exception 'unknown owner: %', p_owner;
  end if;

  if p_key is null or btrim(p_key) = '' then
    raise exception 'key required';
  end if;

  -- 이 루틴은 평일 기준이라 주말은 기록하지 않습니다.
  -- (조용히 실패하면 왜 안 되는지 알 수 없으니 이유를 돌려줍니다)
  if extract(isodow from v_date) >= 6 then
    return json_build_object('ok', true, 'skipped', 'weekend', 'date', v_date);
  end if;

  insert into public.routine_logs as r (log_date, owner, checks, check_times)
  values (v_date, p_owner,
          jsonb_build_object(p_key, true),
          jsonb_build_object(p_key, v_time))
  on conflict (log_date, owner) do update
    set checks = r.checks || jsonb_build_object(p_key, true),
        -- 같은 항목이 두 번 들어오면 "처음 시각"을 남깁니다.
        -- (카카오맵을 두 번 열어도 실제 출발 시각이 밀리지 않도록)
        check_times = case
          when r.check_times ? p_key then r.check_times
          else r.check_times || jsonb_build_object(p_key, v_time)
        end;

  return json_build_object(
    'ok', true,
    'date', v_date,
    'owner', p_owner,
    'key', p_key,
    'at', v_time
  );
end;
$$;

-- 3) 폰은 anon 키로 부르므로 anon에게만 실행 권한을 줍니다.
--    (테이블 자체는 RLS로 계속 잠겨 있고, 이 함수를 통해서만 쓰기가 됩니다)
revoke all on function public.check_routine_from_device(text, text, text, text) from public;
grant execute on function public.check_routine_from_device(text, text, text, text) to anon, authenticated;

-- ---------------------------------------------------------------
-- 쓸 수 있는 p_key 목록 (routine.html의 ROUTINE_ITEMS와 같아야 합니다)
--   wake_up         기상          05:45
--   leave_home      출근(집에서 나옴) 06:00
--   exercise_start  운동 시작      07:20
--   arrive_work     출근(도착)     08:30
--   leave_work      퇴근          17:30
--   sleep           취침          23:00
--   task_edit       편집
--   task_blog       블로그쓰기
-- ---------------------------------------------------------------

-- 잘 들어갔는지 확인 (시크릿을 본인 값으로 바꿔서 실행)
-- select public.check_routine_from_device('본인시크릿', 'leave_home');
-- select log_date, owner, checks, check_times from public.routine_logs order by log_date desc limit 3;
