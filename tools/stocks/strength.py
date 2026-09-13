#!/usr/bin/env python3
"""추천 강도 (1~10).

왜 필요한가:
  매일 5종목을 내보내되, 그 5개가 다 똑같이 좋다고 말하지 않기 위해서입니다.
  조건이 나쁜 날은 추천을 감추는 대신 강도 2~3짜리 5개가 나갑니다.
  숫자가 약하다는 사실을 숨기지 않으면서 항상 5개를 드리는 방식입니다.

어떻게 만드는가:
  느낌이 아니라 계산입니다. 항목마다 점수와 상한을 정해두고 더하고 뺍니다.
  각 항목이 몇 점을 냈는지 화면에 그대로 보여주므로, 강도가 왜 높은지 낮은지
  항상 추적할 수 있습니다.

가장 중요한 것 — 이 값은 아직 검증되지 않았습니다:
  강도 9가 강도 4보다 실제로 잘 맞는지는 기록이 쌓여야 압니다. 그래서 모든 거래에
  강도를 함께 저장하고, 나중에 강도 구간별 실제 적중률을 계산해 보정합니다
  (calibration 함수). 보정 결과가 나오기 전까지 강도는 '근거가 얼마나 모였는가'이지
  '얼마나 오를 것인가'가 아닙니다.
"""
from features import number

MAX_SCORE = 100
LEVELS = 10

# 셋업의 과거 성적. 가장 비중이 큽니다 — 이 셋업이 실제로 먹혔는지가 제일 중요한 근거입니다.
SETUP_POINTS = {'passed': 40, 'observing': 20, 'blocked': 8}
SETUP_UNKNOWN = 14

# 그날의 종목 상태. 각 항목의 상한을 명시해 한 지표가 점수를 독점하지 못하게 합니다.
FEATURE_CAPS = {
    'relative_strength': 10,    # 시장 대비 강도
    'volume_surge': 10,         # 거래량 증가
    'institutional_flow': 10,   # 외국인·기관 수급
    'price_position': 5,        # 20일 고가 대비 위치
    'disclosure': 5,            # 호재 공시
}

# 위험 요소. 빼기만 합니다.
RISK_PENALTIES = {
    'lending': -10,             # 대차 잔고 급증 = 공매도 압력
    'margin_loan': -8,          # 신용융자 과다 = 반대매매 위험
    'short_ratio': -7,          # 공매도 비중 과다
    'volatility': -5,           # 변동성 과다
}

REGIME_PENALTY = {'risk_off': -18, 'unknown': -8, 'normal': 0}


def scaled(value, low, high, cap):
    """low에서 0점, high에서 만점이 되도록 선형 환산합니다. 범위 밖은 잘라냅니다."""
    if value is None:
        return 0.0
    if high == low:
        return 0.0
    ratio = (value - low) / (high - low)
    return max(0.0, min(1.0, ratio)) * cap


def feature_points(f):
    """종목 상태에서 얻는 점수. 각 항목이 왜 그 점수인지 함께 돌려줍니다."""
    parts = []

    relative = f.get('relative_strength')
    parts.append(('시장 대비 강도', scaled(relative, 0, 2.0, FEATURE_CAPS['relative_strength']),
                  f'{relative:+.2f}%p' if relative is not None else '자료 없음'))

    surge = f.get('volume_surge')
    parts.append(('거래량 증가', scaled(surge, 1.0, 3.0, FEATURE_CAPS['volume_surge']),
                  f'{surge:.1f}배' if surge else '자료 없음'))

    # 외국인과 기관 중 더 강한 쪽을 씁니다. 둘 다 양수면 가산점이 붙습니다.
    foreigner, institution = f.get('foreigner_net'), f.get('institution_net')
    flows = [v for v in (foreigner, institution) if v is not None and v > 0]
    flow_cap = FEATURE_CAPS['institutional_flow']
    flow_points = scaled(max(flows) if flows else None, 0, 2_000_000, flow_cap * 0.7)
    if len(flows) == 2:
        flow_points += flow_cap * 0.3      # 외국인·기관이 같이 샀으면 더 신뢰합니다
    parts.append(('외국인·기관 수급', min(flow_points, flow_cap),
                  '둘 다 순매수' if len(flows) == 2 else '한쪽 순매수' if flows else '순매수 없음'))

    position = f.get('price_position')
    parts.append(('20일 고가 대비', scaled(position, 0.85, 1.0, FEATURE_CAPS['price_position']),
                  f'{position * 100:.0f}%' if position else '자료 없음'))

    has_positive = f.get('has_positive_disclosure')
    parts.append(('호재 공시', FEATURE_CAPS['disclosure'] if has_positive else 0.0,
                  ', '.join(f.get('positive_labels') or []) if has_positive else '없음'))

    return parts


def risk_points(f):
    """위험 요소 감점."""
    parts = []

    lending = f.get('lending_change_pct')
    hit = lending is not None and lending > 20
    parts.append(('대차 잔고 급증', RISK_PENALTIES['lending'] if hit else 0.0,
                  f'{lending:+.0f}%' if lending is not None else '자료 없음'))

    margin = f.get('margin_loan_rate_pct')
    hit = margin is not None and margin > 3.0
    parts.append(('신용융자 과다', RISK_PENALTIES['margin_loan'] if hit else 0.0,
                  f'{margin:.2f}%' if margin is not None else '자료 없음'))

    short = f.get('short_ratio_pct')
    hit = short is not None and short > 15
    parts.append(('공매도 비중', RISK_PENALTIES['short_ratio'] if hit else 0.0,
                  f'{short:.1f}%' if short is not None else '자료 없음'))

    volatility = f.get('volatility_pct')
    hit = volatility is not None and volatility > 5.0
    parts.append(('변동성 과다', RISK_PENALTIES['volatility'] if hit else 0.0,
                  f'{volatility:.1f}%' if volatility is not None else '자료 없음'))

    return parts


def setup_points(scoreboard_record):
    if not scoreboard_record:
        return ('셋업 과거 성적', float(SETUP_UNKNOWN), '기록 없음 — 관찰 중')
    status = scoreboard_record.get('status')
    points = float(SETUP_POINTS.get(status, SETUP_UNKNOWN))
    return ('셋업 과거 성적', points, scoreboard_record.get('reason', status))


def score(features_row, scoreboard_record, regime_status):
    """1~10 강도와 그 근거 내역."""
    components = [setup_points(scoreboard_record)]
    components += feature_points(features_row)
    components += risk_points(features_row)
    penalty = REGIME_PENALTY.get(regime_status, REGIME_PENALTY['unknown'])
    components.append(('새벽 해외시장', float(penalty), regime_status or '확인 불가'))

    total = max(0.0, min(float(MAX_SCORE), sum(points for _, points, _ in components)))
    level = max(1, min(LEVELS, int(total / MAX_SCORE * LEVELS) + (1 if total % (MAX_SCORE / LEVELS) else 0)))
    return {
        'level': level,
        'raw_score': round(total, 1),
        'components': [{'label': label, 'points': round(points, 1), 'detail': detail}
                       for label, points, detail in components if points or detail != '자료 없음'],
        'note': '강도는 근거가 얼마나 모였는지를 나타냅니다. 상승 확률이 아닙니다.',
    }


def calibration(rows, require_features=True):
    """강도 구간별 실제 적중률. 강도가 의미 있는 값인지 검증하는 유일한 방법입니다.

    rows는 {'level': 1~10, 'win': bool} 형태입니다. 강도가 높을수록 적중률이
    높아지지 않으면 이 점수 체계는 틀린 것이므로 가중치를 고쳐야 합니다.

    특징을 만들지 못한 날(수급·일봉이 없어 usable=False)의 거래는 기본으로 제외합니다.
    그런 거래는 강도가 '근거가 약해서 낮은 것'이 아니라 '잴 수가 없어서 낮은 것'이라,
    섞으면 강도가 작동하는지 아닌지를 판별할 수 없게 됩니다.
    """
    buckets = {}
    for row in rows:
        level = row.get('level')
        if not level:
            continue
        if require_features and not row.get('features_usable', True):
            continue
        bucket = buckets.setdefault(level, {'total': 0, 'wins': 0})
        bucket['total'] += 1
        bucket['wins'] += 1 if row.get('win') else 0
    return {level: {**value,
                    'hit_rate_pct': 100 * value['wins'] / value['total'] if value['total'] else None}
            for level, value in sorted(buckets.items())}
