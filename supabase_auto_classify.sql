-- Supabase SQL 함수: 농협 알림 자동 등록 (기본값 + 결제수단 고정)
-- MacroDroid에서 호출되는 insert_transaction_from_device 함수
--
-- ⚠️ 이 저장소는 공개(public) 저장소입니다. 아래 v_expected_secret에는 절대
--    실제 값을 커밋하지 마세요 — 이 값만 알면 누구나 이 함수를 호출해 가짜
--    거래를 가계부에 넣을 수 있습니다. CHANGE_ME 그대로 두고, 실제 값은
--    Supabase 대시보드에서 라이브 함수 정의를 확인하거나 채팅 기록에서만
--    확인하세요(2026-09-22: 예전에 실수로 실제 값이 커밋됐다가 유출돼서 즉시
--    새 값으로 교체함 — 라이브 DB와 폰의 MacroDroid만 새 값을 알고 있음).
--
-- ⚠️ 이 파일은 문서 보관용입니다. 실제 반영은 이 파일을 실행하는 게 아니라
--    Supabase에 직접 적용(마이그레이션)하는 방식으로 이뤄지므로, 이 파일과
--    라이브 DB 정의가 어긋날 수 있습니다. 다음에 이 함수를 다시 만들 일이
--    있으면 반드시 라이브 DB의 현재 정의를 먼저 확인하고 그 위에서 고치세요
--    (아래 두 버그가 실제로 그렇게 재발했던 적이 있습니다).
--
-- 2026-09-21에 고친 버그 2건 (둘 다 이 함수가 호출될 때마다 100% 실패해서
-- 폰에서 자동으로 아무 거래도 안 쌓이던 원인 — MacroDroid는 정상적으로 계속
-- 호출하고 있었지만 서버 쪽에서 매번 에러가 나고 있었음):
--   1. v_amount를 NUMERIC으로 선언해놓고 그 값에 REPLACE(문자열 함수)를 호출함
--      → "function replace(numeric, unknown, unknown) does not exist" 에러.
--      콤마 제거는 반드시 TEXT 변수(v_amount_text)에서 끝내고 마지막에만 NUMERIC으로 캐스팅.
--   2. result_id를 BIGINT로 선언했는데 실제 transactions.id 컬럼은 UUID라
--      "invalid input syntax for type bigint" 에러. UUID로 선언해야 함.

CREATE OR REPLACE FUNCTION insert_transaction_from_device(p_secret TEXT, p_line TEXT)
RETURNS json AS $$
DECLARE
  v_expected_secret CONSTANT TEXT := 'CHANGE_ME_긴_무작위_문자열';  -- ⚠️ 절대 실제 값을 여기에 커밋하지 마세요. 폰의 MacroDroid에도 동일한 값이 설정돼 있어야 함
  v_scope TEXT := '관';
  v_type TEXT := '지출';  -- 기본값은 지출
  v_main TEXT := '생활비';
  v_category TEXT := '식비';
  v_note TEXT := p_line;
  v_amount_text TEXT;
  v_amount NUMERIC;
  v_date DATE := (now() AT TIME ZONE 'Asia/Seoul')::date;  -- DB 서버는 UTC라 CURRENT_DATE를 쓰면 한국시간 00~09시엔 하루 전 날짜가 저장됨
  v_payment TEXT := '농협[관]';  -- 농협 알림은 무조건 농협[관]
  result_id UUID;
BEGIN
  IF p_secret IS DISTINCT FROM v_expected_secret THEN
    RAISE EXCEPTION 'unauthorized';
  END IF;

  IF p_line IS NULL OR BTRIM(p_line) = '' THEN
    RAISE EXCEPTION 'line required';
  END IF;

  -- 입금/출금 구분
  IF p_line ~* '입금' THEN
    v_type := '수입';
    v_main := '급여';  -- 입금이면 main을 급여로
    v_category := '월급';  -- 입금이면 category를 월급으로
  END IF;

  -- 금액 추출 (숫자만 - 쉼표 제거는 TEXT 상태에서 끝내고 마지막에만 NUMERIC 캐스팅)
  v_amount_text := (regexp_match(p_line, '(\d{1,3}(,\d{3})*|\d+)'))[1];
  v_amount := REPLACE(v_amount_text, ',', '')::NUMERIC;

  -- p_line에서 금액/단위 제거하고 상호명만 추출
  v_note := TRIM(regexp_replace(p_line, '\d{1,3}(,\d{3})*|\d+', '', 'g'));
  v_note := TRIM(regexp_replace(v_note, '원|결제|승인|입금|출금', '', 'gi'));

  -- 거래 삽입 (기본값: 관-지출-생활비-식비, payment: 농협[관])
  INSERT INTO transactions (date, type, scope, main, category, note, amount, payment)
  VALUES (v_date, v_type, v_scope, v_main, v_category, v_note, v_amount, v_payment)
  RETURNING id INTO result_id;

  -- 결과 반환
  RETURN json_build_object(
    'success', true,
    'id', result_id,
    'type', v_type,
    'note', v_note,
    'amount', v_amount,
    'payment', v_payment
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = '';
