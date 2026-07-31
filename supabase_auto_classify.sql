-- Supabase SQL 함수: 농협 알림 자동 등록 (기본값 + 결제수단 고정)
-- MacroDroid에서 호출되는 insert_transaction_from_device 함수

CREATE OR REPLACE FUNCTION insert_transaction_from_device(p_line TEXT)
RETURNS json AS $$
DECLARE
  v_scope TEXT := '관';
  v_type TEXT := '지출';
  v_main TEXT := '생활비';
  v_category TEXT := '식비';
  v_note TEXT := p_line;
  v_amount NUMERIC;
  v_date DATE := CURRENT_DATE;
  v_payment TEXT := '농협[관]';  -- 농협 알림은 무조건 농협[관]
  result_id BIGINT;
BEGIN
  -- 금액 추출 (숫자만 - 쉼표 제거)
  v_amount := (regexp_match(p_line, '(\d{1,3}(,\d{3})*|\d+)'))[1]::TEXT;
  v_amount := REPLACE(v_amount, ',', '')::NUMERIC;

  -- p_line에서 금액/단위 제거하고 상호명만 추출
  v_note := TRIM(regexp_replace(p_line, '\d{1,3}(,\d{3})*|\d+', '', 'g'));
  v_note := TRIM(regexp_replace(v_note, '원|결제|승인', '', 'gi'));

  -- 거래 삽입 (기본값: 관-지출-생활비-식비, payment: 농협[관])
  INSERT INTO transactions (date, type, scope, main, category, note, amount, payment)
  VALUES (v_date, v_type, v_scope, v_main, v_category, v_note, v_amount, v_payment)
  RETURNING id INTO result_id;

  -- 결과 반환
  RETURN json_build_object(
    'success', true,
    'id', result_id,
    'note', v_note,
    'amount', v_amount,
    'payment', v_payment
  );
END;
$$ LANGUAGE plpgsql;

