-- Supabase SQL 함수: 농협 알림 자동 등록 (결제수단 고정 + 기본 분류)
-- MacroDroid에서 호출되는 insert_transaction_from_device 함수

CREATE OR REPLACE FUNCTION insert_transaction_from_device(p_line TEXT)
RETURNS json AS $$
DECLARE
  v_scope TEXT := '관';  -- 기본값: 농협은 관의 계좌
  v_type TEXT := '지출';
  v_main TEXT := NULL;   -- 미지정으로 시작 (프론트엔드에서 학습 분류)
  v_category TEXT := NULL;
  v_note TEXT := p_line;
  v_amount NUMERIC;
  v_date DATE := CURRENT_DATE;
  v_payment TEXT := '농협[관]';  -- 농협 알림은 무조건 농협[관]
  result_id BIGINT;
BEGIN
  -- 금액 추출 (숫자만 - 쉼표 제거)
  v_amount := (regexp_match(p_line, '(\d{1,3}(,\d{3})*|\d+)'))[1]::TEXT;
  v_amount := REPLACE(v_amount, ',', '')::NUMERIC;

  -- p_line에서 마지막 항목을 메모로 사용 (기존 로직 유지)
  -- 예: "GS25강남점 4500원" → v_note = "GS25강남점"
  v_note := TRIM(regexp_replace(p_line, '\d{1,3}(,\d{3})*|\d+', '', 'g'));
  v_note := TRIM(regexp_replace(v_note, '원|결제|승인', '', 'gi'));

  -- 거래 삽입 (scope, main, category는 NULL로 → 프론트엔드에서 학습 분류)
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

