# 🚀 농협 알림 자동분류 함수 배포 가이드

## 📋 변경 사항
- ✅ **입금/출금 자동 구분**: 알림 메시지에 "입금" 키워드가 있으면 수입으로, 없으면 지출로 분류
- ✅ **입금 시 자동 분류**: type=수입, main=급여, category=월급
- ✅ **출금 시 자동 분류**: type=지출, main=생활비, category=식비  
- ✅ **결제수단 고정**: 모든 농협 알림은 `농협[관]`으로 저장

---

## 🔧 배포 방법

### 1. Supabase 대시보드 열기
```
https://supabase.com/dashboard
```

### 2. 프로젝트 선택
- 프로젝트: `kblwddlquwlvumhwkirl`

### 3. SQL Editor 열기
- 좌측 메뉴에서 **SQL Editor** 클릭

### 4. 새 쿼리 생성
- **New query** 버튼 클릭

### 5. SQL 코드 복사 & 실행
아래 내용을 복사해서 붙여넣고 **Run** 버튼 클릭:

```sql
-- Supabase SQL 함수: 농협 알림 자동 등록 (기본값 + 결제수단 고정)
-- MacroDroid에서 호출되는 insert_transaction_from_device 함수

CREATE OR REPLACE FUNCTION insert_transaction_from_device(p_line TEXT)
RETURNS json AS $$
DECLARE
  v_scope TEXT := '관';
  v_type TEXT := '지출';  -- 기본값은 지출
  v_main TEXT := '생활비';
  v_category TEXT := '식비';
  v_note TEXT := p_line;
  v_amount NUMERIC;
  v_date DATE := CURRENT_DATE;
  v_payment TEXT := '농협[관]';  -- 농협 알림은 무조건 농협[관]
  result_id BIGINT;
BEGIN
  -- 입금/출금 구분
  IF p_line ~* '입금' THEN
    v_type := '수입';
    v_main := '급여';  -- 입금이면 main을 급여로
    v_category := '월급';  -- 입금이면 category를 월급으로
  END IF;

  -- 금액 추출 (숫자만 - 쉼표 제거)
  v_amount := (regexp_match(p_line, '(\d{1,3}(,\d{3})*|\d+)'))[1]::TEXT;
  v_amount := REPLACE(v_amount, ',', '')::NUMERIC;

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
$$ LANGUAGE plpgsql;
```

### 6. 실행 결과 확인
- "Success. No rows returned" 메시지가 나오면 성공!

---

## 🧪 테스트 방법

### 1. MacroDroid에서 테스트
- 폰에서 농협 알림을 다시 받아보세요
- 또는 MacroDroid 매크로를 수동으로 실행

### 2. ledger.html에서 확인
```
https://yunkwan.github.io/ledger.html
```
- 새로고침 후 최근 거래 확인
- **입금**은 "수입"으로 표시되어야 함
- **출금**은 "지출"로 표시되어야 함
- 결제수단은 "농협[관]"으로 표시되어야 함

---

## 🐛 문제 해결

### 여전히 "미지정"으로 나온다면?
1. **payment 컬럼명 확인**: transactions 테이블에 `payment` 컬럼이 존재하는지 확인
2. **대괄호 이스케이프**: `농협[관]` 대신 다른 이름으로 시도해보기
3. **MacroDroid 함수 호출 확인**: 함수 이름이 정확히 `insert_transaction_from_device`인지 확인

### 입금/출금이 여전히 구분 안 된다면?
1. **알림 메시지 확인**: p_line에 정확히 "입금" 문자가 포함되어 있는지 확인
2. **대소문자**: 현재는 대소문자 구분 없음 (`~*` 연산자 사용)
3. **로그 확인**: Supabase Logs에서 실제 들어온 p_line 값 확인

---

## 📞 완료 후
배포 완료되면 다음 농협 알림부터 자동으로 입금/출금이 구분됩니다!
