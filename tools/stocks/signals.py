#!/usr/bin/env python3
"""매매 계획 생성과 비용 모형.

08:30에 만드는 것은 '예상 상승률'이 아니라 실행 가능한 계획입니다.

  진입 조건 · 손절가 · 목표가 · 유효 시간

이렇게 해야 "추천대로 수익이 났는가"를 나중에 분봉으로 그대로 재현해 확인할 수 있습니다.
시가 대비 종가 수익률은 실제로 그렇게 매매할 수 없기 때문에 검증 대상이 될 수 없습니다.

목표는 하나로 고정하지 않고 사다리(1%·1.5%·2%·3%)로 함께 평가합니다.
"목표를 몇 %로 잡아야 적중률 70%가 나오는가"를 추측이 아니라 기록으로 답하기 위해서입니다.
"""
from features import number

# --- 비용 (실제 체결 손익을 재현하기 위한 값) ---
# 수수료는 /api/v1/commissions로 본인 실제 요율을 받아 덮어씁니다. 아래는 받기 전 기본값.
BUY_FEE_RATE = 0.00015      # KRX 체결 기준 0.015%
SELL_FEE_RATE = 0.00015
SELL_TAX_RATE = 0.0015      # 매도 시 거래세·농특세 합계. 연도별로 바뀌므로 확인 후 조정하세요.
SLIPPAGE_TICKS = 1          # 진입·청산 각각 1틱 불리하게 체결됐다고 가정

TARGET_LADDER = (1.0, 1.5, 2.0, 3.0)

OPENING_RANGE_MINUTES = 30  # 09:00~09:30 레인지
ENTRY_DEADLINE = '13:00'    # 이 시각까지 진입 조건이 안 나오면 그날은 포기
EXIT_TIME = '15:00'         # 목표·손절 미도달 시 이 시각에 정리. 종가 동시호가 혼잡을 피합니다.

MIN_STOP_PCT = 1.0
MAX_STOP_PCT = 2.5
ATR_STOP_MULTIPLE = 0.8


def tick_size(price):
    """KRX 호가 단위. 슬리피지를 원 단위로 환산할 때 씁니다."""
    for threshold, tick in ((2000, 1), (5000, 5), (20000, 10), (50000, 50),
                            (200000, 100), (500000, 500)):
        if price < threshold:
            return tick
    return 1000


def tick_cost_pct(price):
    """1틱이 가격의 몇 %인지.

    왜 따로 재는가: 검증 구간 40건의 비용을 쪼개보니 왕복 0.423%p 중
    슬리피지가 0.243%p로 가장 컸습니다. 거래세(0.151%p)보다 큽니다.
    그런데 이 값은 종목마다 다릅니다 — KRX 호가단위는 가격대별 계단이라
    계단 바로 위 가격(예: 20,050원, 200,500원)은 1틱이 0.25%나 되고
    계단 바로 아래(예: 199,000원)는 0.05%입니다. 같은 전략이라도
    어느 쪽에 걸리느냐로 거래당 0.2%p가 갈립니다.

    아직 선별 조건으로 쓰지는 않습니다. 학습 구간은 0.10% 이하를,
    검증 구간은 0.15% 이하를 가리켜 서로 엇갈렸고 표본이 각각 21건·25건뿐이라
    어느 쪽도 확인된 값이 아닙니다. 우선 기록만 남기고, 표본이 쌓이면 판정합니다.
    """
    if not price or price <= 0:
        return None
    return tick_size(price) / price * 100


def round_trip_cost_pct(entry_price, exit_price):
    """왕복 비용을 진입가 대비 %로. 수수료·거래세·슬리피지를 모두 포함합니다."""
    if not entry_price or not exit_price:
        return None
    tick = tick_size(entry_price)
    slippage = SLIPPAGE_TICKS * tick * 2 / entry_price * 100
    fees = (BUY_FEE_RATE + SELL_FEE_RATE) * 100
    tax = SELL_TAX_RATE * exit_price / entry_price * 100
    return slippage + fees + tax


def net_return_pct(entry_price, exit_price):
    """비용을 뺀 실제 수익률(%). 이 값이 양수여야 '적중'으로 셉니다."""
    if not entry_price or not exit_price:
        return None
    gross = (exit_price / entry_price - 1) * 100
    return gross - round_trip_cost_pct(entry_price, exit_price)


def stop_distance_pct(atr):
    """손절 폭을 종목 변동성에 맞춥니다. 변동성이 큰 종목에 같은 손절을 쓰면 계속 털립니다."""
    if atr is None:
        return MIN_STOP_PCT
    return min(MAX_STOP_PCT, max(MIN_STOP_PCT, atr * ATR_STOP_MULTIPLE))


# --- 셋업 정의 -----------------------------------------------------------
# 조건을 적게 유지합니다. 조건이 많을수록 과거에는 잘 맞고 앞으로는 안 맞습니다.

def _positive(value):
    return value is not None and value > 0


def setup_orb_flow(f):
    """개장 레인지 돌파 + 전일 수급이 들어온 종목."""
    reasons = []
    if not _positive(f.get('relative_strength')):
        reasons.append('전일 시장 대비 강도 미달')
    if not (_positive(f.get('foreigner_net')) or _positive(f.get('institution_net'))):
        reasons.append('외국인·기관 순매수 없음')
    if (f.get('volume_surge') or 0) < 1.2:
        reasons.append('거래량 증가 부족')
    return reasons


def setup_orb_strict(f):
    """위 조건에 신고가 근접과 강한 거래량을 더한 엄격판. 신호가 훨씬 적게 나옵니다."""
    reasons = setup_orb_flow(f)
    if (f.get('price_position') or 0) < 0.95:
        reasons.append('20일 고가 대비 위치 미달')
    if (f.get('volume_surge') or 0) < 2.0:
        reasons.append('거래량 급증 기준 미달')
    if _positive(f.get('short_ratio_pct')) and f['short_ratio_pct'] > 15:
        reasons.append('공매도 비중 과다')
    return reasons


SETUPS = {
    'orb_flow': setup_orb_flow,
    'orb_strict': setup_orb_strict,
}

# 진입은 정규장에서만 합니다. 프리마켓(NXT 08:00~)은 호가가 얇아 스프레드가 넓고,
# 검증할 과거 표본도 없습니다. premarket.py가 스프레드를 매일 재서 기록만 쌓고 있으며,
# 그 기록이 관문을 통과하기 전까지 프리마켓 진입은 추천에 들어가지 않습니다.
ENTRY_SESSION = 'regular_market'

# 변동성이 과하거나 너무 싼 종목은 어떤 셋업에서도 제외합니다.
MAX_VOLATILITY_PCT = 6.0
MIN_PRICE = 1000
# 대차 잔고가 5거래일 동안 이만큼 넘게 늘었으면 공매도 압력이 쌓이는 중으로 봅니다.
# 당일 공매도는 08:30에 알 수 없지만, 주식을 빌리는 단계는 전일까지 확정되어 미리 보입니다.
MAX_LENDING_INCREASE_PCT = 20.0
# 신용융자 잔고가 상장주식수 대비 이 비율을 넘으면 급락 시 반대매매로 하락이 증폭됩니다.
MAX_MARGIN_LOAN_RATE_PCT = 3.0
EXCLUDED_WARNINGS = {'LIQUIDATION_TRADING', 'OVERHEATED', 'INVESTMENT_WARNING',
                     'INVESTMENT_RISK', 'STOCK_WARRANTS'}


def universal_blocks(f, warnings):
    reasons = []
    if (f.get('previous_close') or 0) < MIN_PRICE:
        reasons.append(f'{MIN_PRICE}원 미만')
    if (f.get('volatility_pct') or 0) > MAX_VOLATILITY_PCT:
        reasons.append('변동성 과다')
    active = {w.get('warningType') for w in (warnings or [])} & EXCLUDED_WARNINGS
    if active:
        reasons.append('거래 유의 지정: ' + ', '.join(sorted(active)))
    # 유상증자·전환사채·감자처럼 방향이 분명한 악재 공시는 무조건 제외합니다.
    if f.get('has_negative_disclosure'):
        labels = ', '.join(f.get('negative_labels') or []) or '악재 공시'
        reasons.append(f'악재 공시: {labels}')
    lending = f.get('lending_change_pct')
    if lending is not None and lending > MAX_LENDING_INCREASE_PCT:
        reasons.append(f'대차 잔고 5일 {lending:+.0f}% — 공매도 압력 증가')
    margin = f.get('margin_loan_rate_pct')
    if margin is not None and margin > MAX_MARGIN_LOAN_RATE_PCT:
        reasons.append(f'신용융자 잔고 {margin:.2f}% — 반대매매 위험')
    return reasons


def plan(features_row, warnings, setup_name):
    """한 종목·한 셋업의 계획.

    조건을 통과하지 못해도 계획 자체는 만들어 돌려줍니다. 매일 5종목을 내보내되
    강도로 차등을 두는 구조라, 조건 미충족 종목도 순위 비교 대상이어야 하기 때문입니다.
    통과 여부는 tradeable, 사유는 blocks에 담습니다.
    """
    if not features_row.get('usable'):
        return {'symbol': features_row['symbol'], 'setup': setup_name, 'tradeable': False,
                'blocks': [features_row.get('reason', '특징 계산 불가')],
                'stop_pct': MIN_STOP_PCT, 'target_ladder': list(TARGET_LADDER),
                'opening_range_minutes': OPENING_RANGE_MINUTES,
                'entry_deadline': ENTRY_DEADLINE, 'exit_time': EXIT_TIME}
    blocks = universal_blocks(features_row, warnings) + SETUPS[setup_name](features_row)
    stop_pct = stop_distance_pct(features_row.get('atr_pct'))
    return {
        'symbol': features_row['symbol'],
        'setup': setup_name,
        'tradeable': not blocks,
        'blocks': blocks,
        'entry_rule': '09:00~09:30 고가를 위로 돌파하는 첫 1분봉에서 진입',
        'opening_range_minutes': OPENING_RANGE_MINUTES,
        'stop_pct': stop_pct,
        'target_ladder': list(TARGET_LADDER),
        'entry_deadline': ENTRY_DEADLINE,
        'exit_time': EXIT_TIME,
        'reference_close': features_row.get('previous_close'),
        'note': '진입가는 장중 실제 돌파 시점에 결정됩니다. 08:30에는 계획만 고정합니다.',
    }


# 리포트에 보여줄 수 있는 특징. 전부 비율(%)·배수·참거짓이며 원 단위 값이나
# 주식 수 같은 절대 수치는 들어 있지 않습니다(시세정보에 해당).
REPORT_FEATURES = (
    ('relative_strength', '시장 대비 강도', '%p', '전일 종목 등락률 − 지수 등락률. 양수면 시장보다 강했다는 뜻'),
    ('volume_surge', '거래량 증가', '배', '전일 거래량 ÷ 직전 20일 평균'),
    ('price_position', '20일 고가 대비', '비율', '1.0이면 신고가. 높을수록 추세 상단'),
    ('atr_pct', 'ATR', '%', '일간 평균 변동 폭. 손절 폭을 정하는 기준'),
    ('volatility_pct', '변동성', '%', '일간 수익률 표준편차. 6% 초과면 제외'),
    ('short_ratio_pct', '공매도 비중', '%', '전일 거래대금 중 공매도 비율. 15% 초과면 제외'),
    ('lending_change_pct', '대차 잔고 5일 증감', '%', '공매도의 선행지표. +20% 초과면 제외'),
    ('margin_loan_rate_pct', '신용융자 잔고', '%', '상장주식수 대비. 3% 초과면 반대매매 위험'),
    ('stock_loan_rate_pct', '신용대주 잔고', '%', '개인이 빌려 판 물량'),
    ('sample_days', '사용한 일봉', '일', '특징 계산에 쓴 과거 일봉 수'),
)

FLAG_FEATURES = (
    ('has_positive_disclosure', '호재 공시', 'positive_labels'),
    ('has_negative_disclosure', '악재 공시', 'negative_labels'),
)
FLOW_FEATURES = (
    ('foreigner_net', '외국인 수급'),
    ('institution_net', '기관 수급'),
    ('program_net', '프로그램 수급'),
)


def report_features(features_row):
    """리포트용 특징 묶음. 비율만 남기고 절대 수치는 제외합니다."""
    numbers = []
    for key, label, unit, note in REPORT_FEATURES:
        value = features_row.get(key)
        if value is None:
            continue
        numbers.append({'key': key, 'label': label, 'unit': unit,
                        'value': round(float(value), 4), 'note': note})
    flags = []
    for key, label, labels_key in FLAG_FEATURES:
        if features_row.get(key) is None:
            continue
        flags.append({'key': key, 'label': label, 'value': bool(features_row[key]),
                      'detail': ', '.join(features_row.get(labels_key) or [])})
    # 수급은 방향만 올립니다. 순매수 주식 수는 시세정보라 제외합니다.
    for key, label in FLOW_FEATURES:
        value = features_row.get(key)
        if value is None:
            continue
        flags.append({'key': key, 'label': label, 'value': value > 0,
                      'detail': '순매수' if value > 0 else '순매도'})
    # 비용은 전략과 무관하게 확정된 숫자입니다. 기대값이 0 근처일 때는
    # 이 값이 승패를 가르므로 근거 화면에 같이 띄웁니다. 둘 다 비율입니다.
    tick_cost = tick_cost_pct(features_row.get('previous_close'))
    if tick_cost is not None:
        numbers.append({'key': 'tick_cost_pct', 'label': '1틱 비용', 'unit': '%',
                        'value': round(tick_cost, 4),
                        'note': '호가 한 칸이 가격의 몇 %인지. 낮을수록 슬리피지가 싸다'})
        numbers.append({'key': 'round_trip_cost_pct', 'label': '왕복 비용(추정)', 'unit': '%',
                        'value': round(tick_cost * 2 * SLIPPAGE_TICKS
                                       + (BUY_FEE_RATE + SELL_FEE_RATE) * 100
                                       + SELL_TAX_RATE * 100, 4),
                        'note': '슬리피지 2틱 + 수수료 왕복 + 거래세. 이만큼은 이겨야 본전'})
    news_count = features_row.get('news_count')
    return {'numbers': numbers, 'flags': flags,
            'news_count': news_count if news_count is not None else None,
            'positive_labels': features_row.get('positive_labels') or [],
            'negative_labels': features_row.get('negative_labels') or []}


def describe(features_row):
    """화면에 그대로 보여줄 선정 근거.

    비율과 판정만 적습니다. 순매수 주식 수 같은 절대 수치는 시세정보에 해당해
    사이트로 나갈 수 없으므로 '순매수' 여부로만 표현합니다.
    """
    parts = []
    if features_row.get('relative_strength') is not None:
        parts.append(f'시장 대비 {features_row["relative_strength"]:+.2f}%p')
    if features_row.get('volume_surge'):
        parts.append(f'거래량 {features_row["volume_surge"]:.1f}배')
    flows = [name for name, key in (('외국인', 'foreigner_net'), ('기관', 'institution_net'))
             if (features_row.get(key) or 0) > 0]
    if flows:
        parts.append(' · '.join(flows) + ' 순매수')
    if features_row.get('atr_pct'):
        parts.append(f'ATR {features_row["atr_pct"]:.1f}%')
    if features_row.get('has_positive_disclosure'):
        parts.append('호재 공시: ' + ', '.join(features_row.get('positive_labels') or []))
    if features_row.get('news_count'):
        parts.append(f'관련 기사 {features_row["news_count"]}건')
    return ' · '.join(parts) if parts else '표시할 수치가 부족합니다'
