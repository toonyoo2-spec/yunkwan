#!/usr/bin/env python3
"""우연 배제와 시장 레짐 판정.

왜 필요한가:
  적중률 70%라는 숫자 하나만으로는 아무것도 알 수 없습니다. 그 70%가
    - 동전을 몇 번 던져 운 좋게 나온 값인지
    - 한 종목·하루에서 몰아서 나온 값인지
    - 여러 조합을 시험하다 그중 하나가 우연히 좋아 보인 것인지
    - 특정 시장 상황에서만 되는 것인지
  를 구분하지 못하면, 그 70%를 믿고 돈을 넣게 됩니다.

이 파일은 그 네 가지를 각각 판정합니다. 모든 계산은 표준 라이브러리만 씁니다.
"""
import math
from statistics import fmean, pstdev

from features import number

# 동전 던지기 기준선. 단타 진입의 기본 성공률을 이보다 높여야 의미가 있습니다.
DEFAULT_BASELINE = 0.5
SIGNIFICANCE = 0.05
MIN_SAMPLES_FOR_TEST = 20
CONCENTRATION_WARN = 0.4        # 수익의 40% 이상이 한 곳에서 나오면 경고


# ---------- 1. 우연일 확률 (이항검정) ----------

def binomial_tail(hits, total, probability):
    """total번 중 hits번 이상 성공할 확률. 기준 성공률이 probability일 때.

    scipy 없이 정확 이항분포로 계산합니다. 이 값이 작을수록 '우연이라기엔 너무
    잘 나왔다'는 뜻입니다.
    """
    if total <= 0 or not 0 < probability < 1:
        return 1.0
    hits = max(0, min(total, int(hits)))
    return sum(math.comb(total, k) * probability ** k * (1 - probability) ** (total - k)
               for k in range(hits, total + 1))


def chance_verdict(hits, total, baseline=DEFAULT_BASELINE):
    """이 성적이 우연으로 설명되는지 판정합니다."""
    if total < MIN_SAMPLES_FOR_TEST:
        return {'p_value': None, 'verdict': 'insufficient',
                'reason': f'표본 {total}건 — {MIN_SAMPLES_FOR_TEST}건 미만이라 판정할 수 없습니다'}
    p_value = binomial_tail(hits, total, baseline)
    if p_value < SIGNIFICANCE:
        reason = (f'우연히 이 정도 나올 확률 {p_value * 100:.1f}% '
                  f'— 기준선 {baseline * 100:.0f}%보다 의미 있게 높습니다')
        verdict = 'significant'
    else:
        reason = (f'우연히 이 정도 나올 확률 {p_value * 100:.1f}% '
                  f'— 기준선 {baseline * 100:.0f}%와 구분되지 않습니다')
        verdict = 'chance'
    return {'p_value': p_value, 'verdict': verdict, 'reason': reason,
            'baseline': baseline}


# ---------- 2. 여러 조합을 시험한 대가 (다중비교 보정) ----------

def benjamini_hochberg(p_values, alpha=SIGNIFICANCE):
    """여러 셋업·목표 조합을 동시에 시험할 때의 보정.

    조합을 20개 시험하면 전부 무의미해도 그중 1개는 p<0.05로 나옵니다(우연히).
    보정 없이 '이 조합이 통과했다'고 말하면 거의 확실히 속는 것입니다.
    Benjamini-Hochberg는 통과 기준을 순위에 따라 낮춰 그걸 막습니다.
    """
    usable = [(key, value) for key, value in p_values.items() if value is not None]
    if not usable:
        return {}
    ordered = sorted(usable, key=lambda item: item[1])
    count = len(ordered)
    passed = set()
    for rank, (key, value) in enumerate(ordered, start=1):
        if value <= alpha * rank / count:
            passed = {name for name, _ in ordered[:rank]}
    return {key: {'p_value': value, 'survives': key in passed, 'tested': count}
            for key, value in usable}


# ---------- 3. 수익이 한 곳에서 몰아서 나왔는가 ----------

def concentration(rows):
    """수익의 집중도. 한 종목이나 하루가 전체를 만들어냈으면 그건 우연에 가깝습니다."""
    gains = [row for row in rows if (row.get('net_pct') or 0) > 0]
    total = sum(row['net_pct'] for row in gains)
    if not gains or total <= 0:
        return {'top_symbol_share': None, 'top_day_share': None,
                'reason': '이익 거래가 없어 집중도를 계산할 수 없습니다'}

    def share(key):
        buckets = {}
        for row in gains:
            buckets[row.get(key)] = buckets.get(row.get(key), 0) + row['net_pct']
        return max(buckets.values()) / total if buckets else None

    symbol_share = share('symbol')
    day_share = share('date')
    warnings = []
    if symbol_share and symbol_share > CONCENTRATION_WARN:
        warnings.append(f'이익의 {symbol_share * 100:.0f}%가 한 종목에서 나왔습니다')
    if day_share and day_share > CONCENTRATION_WARN:
        warnings.append(f'이익의 {day_share * 100:.0f}%가 하루에서 나왔습니다')
    return {'top_symbol_share': symbol_share, 'top_day_share': day_share,
            'reason': ' · '.join(warnings) if warnings else '특정 종목·날짜 쏠림 없음'}


# ---------- 4. 견딜 수 있는가 ----------

def streaks(rows):
    """최장 연속 손실과 누적 손익의 최대 낙폭.

    적중률이 좋아도 연속 손실 구간을 못 견디면 그 전략은 쓸 수 없습니다.
    거래당 수익률을 순서대로 더해가며 고점 대비 최대 하락을 잽니다.
    """
    ordered = sorted(rows, key=lambda row: (row.get('date') or '', row.get('symbol') or ''))
    equity, peak, drawdown = 0.0, 0.0, 0.0
    losing, worst_streak = 0, 0
    for row in ordered:
        net = row.get('net_pct') or 0.0
        equity += net
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
        losing = losing + 1 if net <= 0 else 0
        worst_streak = max(worst_streak, losing)
    return {'total_return_pct': equity, 'max_drawdown_pct': drawdown,
            'longest_losing_streak': worst_streak, 'trades': len(ordered)}


# ---------- 5. 시장 레짐 ----------

REGIME_VOLATILITY_HIGH = 1.8    # 최근 변동성이 장기 평균의 이 배를 넘으면 '이상 구간'
TREND_WINDOW = 20


def market_regime(index_candles, date):
    """그날의 시장 상태. 30년 분석가가 '이 장은 평소와 다르다'고 말하는 것을 수치로.

    한 레짐에서 얻은 성적을 다른 레짐에 그대로 적용하면 안 됩니다. 그래서 거래마다
    레짐을 기록해두고, 레짐별로 따로 채점합니다.
    """
    bars = [bar for bar in index_candles if bar['timestamp'][:10] < date]
    if len(bars) < TREND_WINDOW + 5:
        return {'label': 'unknown', 'reason': '지수 일봉이 부족합니다'}
    closes = [number(bar['closePrice']) for bar in bars]
    closes = [value for value in closes if value]
    returns = [(later / earlier - 1) * 100
               for earlier, later in zip(closes, closes[1:]) if earlier]
    if len(returns) < TREND_WINDOW + 1:
        return {'label': 'unknown', 'reason': '지수 수익률 표본이 부족합니다'}

    recent_volatility = pstdev(returns[-TREND_WINDOW:])
    long_volatility = pstdev(returns)
    average = fmean(closes[-TREND_WINDOW:])
    last = closes[-1]
    trend_pct = (last / average - 1) * 100 if average else 0.0
    ratio = recent_volatility / long_volatility if long_volatility else 1.0

    if ratio > REGIME_VOLATILITY_HIGH:
        label = 'volatile'
    elif trend_pct > 2:
        label = 'uptrend'
    elif trend_pct < -2:
        label = 'downtrend'
    else:
        label = 'range'
    return {
        'label': label,
        'volatility_ratio': ratio,
        'trend_pct': trend_pct,
        'reason': f'변동성 평소의 {ratio:.2f}배 · 20일선 대비 {trend_pct:+.1f}%',
    }


# ---------- 종합 ----------

def assess(rows, baseline=DEFAULT_BASELINE):
    """한 셋업의 성적을 네 가지 관점에서 한 번에 판정합니다."""
    wins = sum(1 for row in rows if row.get('win'))
    total = len(rows)
    return {
        'hits': wins,
        'total': total,
        'hit_rate': wins / total if total else None,
        'chance': chance_verdict(wins, total, baseline),
        'concentration': concentration(rows),
        'risk': streaks(rows),
    }


def summarize(assessment):
    """사람이 읽을 한 줄 판정."""
    chance = assessment['chance']
    risk = assessment['risk']
    parts = [chance['reason']]
    if assessment['concentration']['reason']:
        parts.append(assessment['concentration']['reason'])
    parts.append(f"최대 낙폭 {risk['max_drawdown_pct']:.1f}%p"
                 f" · 최장 연속 손실 {risk['longest_losing_streak']}회")
    return ' | '.join(parts)
