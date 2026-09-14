#!/usr/bin/env python3
"""특징 계산.

미래 정보 차단이 이 파일의 가장 중요한 규칙입니다.
08:30에 쓰는 특징은 전 거래일까지의 확정 데이터만 사용합니다. 당일 데이터는
단 한 줄도 들어가면 안 됩니다. 그게 섞이면 검증 결과가 통째로 무의미해집니다.
"""
from statistics import fmean, pstdev

ATR_WINDOW = 14
VOLUME_WINDOW = 20
FLOW_WINDOW = 5             # 수급 누적일수
HIGH_WINDOW = 20            # 가격 위치 계산용 고가 구간
MIN_DAILY_BARS = ATR_WINDOW + 2


def number(value, default=None):
    """API가 문자열로 주는 숫자를 안전하게 바꿉니다."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result and abs(result) != float('inf') else default


def bars_before(candles, date):
    """기준일 '이전'의 봉만 남깁니다. 기준일 당일은 제외합니다."""
    return [bar for bar in candles if bar['timestamp'][:10] < date]


def true_ranges(bars):
    ranges = []
    for previous, current in zip(bars, bars[1:]):
        high, low = number(current['highPrice']), number(current['lowPrice'])
        close = number(previous['closePrice'])
        if None in (high, low, close):
            continue
        ranges.append(max(high - low, abs(high - close), abs(low - close)))
    return ranges


def atr_pct(bars):
    """평균 실체 범위를 종가 대비 %로. 손절 폭을 종목 변동성에 맞추는 데 씁니다.

    일봉 이력이 짧은 종목(신규 상장 등)에서는 빈 목록이 들어올 수 있습니다.
    여기서 막지 않으면 호출하는 쪽 전체가 IndexError로 죽습니다.
    """
    if not bars:
        return None
    ranges = true_ranges(bars[-(ATR_WINDOW + 1):])
    close = number(bars[-1].get('closePrice'))
    if not ranges or not close:
        return None
    return fmean(ranges) / close * 100


def volume_surge(bars):
    """전일 거래량 ÷ 직전 20일 평균 거래량."""
    if not bars:
        return None
    volumes = [number(bar['volume'], 0) for bar in bars[-(VOLUME_WINDOW + 1):]]
    if len(volumes) < VOLUME_WINDOW + 1:
        return None
    baseline = fmean(volumes[:-1])
    return volumes[-1] / baseline if baseline > 0 else None


def price_position(bars):
    """전일 종가가 최근 20일 고가 대비 어디쯤인지. 1.0이면 신고가."""
    if not bars:
        return None
    window = bars[-HIGH_WINDOW:]
    highs = [number(bar['highPrice']) for bar in window]
    highs = [h for h in highs if h is not None]
    close = number(bars[-1]['closePrice'])
    if not highs or not close:
        return None
    peak = max(highs)
    return close / peak if peak > 0 else None


def daily_change_pct(bars):
    if len(bars) < 2:
        return None
    previous, last = number(bars[-2]['closePrice']), number(bars[-1]['closePrice'])
    if not previous or last is None:
        return None
    return (last / previous - 1) * 100


def realized_volatility_pct(bars):
    """일간 수익률의 표준편차. 변동성이 과한 종목을 거르는 데 씁니다."""
    closes = [number(bar['closePrice']) for bar in bars[-(VOLUME_WINDOW + 1):]]
    closes = [c for c in closes if c]
    if len(closes) < 5:
        return None
    returns = [(b / a - 1) * 100 for a, b in zip(closes, closes[1:]) if a]
    return pstdev(returns) if len(returns) > 1 else None


def net_flow(records, field, date, days=FLOW_WINDOW):
    """기준일 이전 N거래일의 순매수 합계 (주식 수)."""
    usable = [row for row in records if row.get('date', '') < date][:days]
    total = 0.0
    counted = 0
    for row in usable:
        bucket = row.get(field) or {}
        value = number(bucket.get('netBuyVolume'))
        if value is None:
            continue
        total += value
        counted += 1
    return total if counted else None


def short_ratio(records, date):
    """기준일 직전 거래일의 공매도 거래대금 비중 (%)."""
    for row in records:
        if row.get('date', '') < date:
            value = number(row.get('shortSellingAmountRate'))
            return value * 100 if value is not None else None
    return None


def lending_balance_change_pct(records, date, days=FLOW_WINDOW):
    """대차 잔고의 N거래일 증감률(%).

    대차거래는 기관이 주식을 빌리는 거래입니다. 공매도를 하려면 먼저 주식을 빌려야 하므로,
    잔고가 늘고 있으면 공매도 압력이 쌓이는 중이라는 뜻입니다. 당일 공매도는 08:30에
    알 수 없지만, 이 선행지표는 전일까지 확정되어 있어 미리 볼 수 있습니다.
    """
    usable = [row for row in records if row.get('date', '') < date][:days + 1]
    if len(usable) < 2:
        return None
    latest = number((usable[0] or {}).get('balanceQuantity'))
    oldest = number((usable[-1] or {}).get('balanceQuantity'))
    if not latest or not oldest or oldest <= 0:
        return None
    return (latest / oldest - 1) * 100


def credit_detail(records, date, field):
    """기준일 직전 거래일의 신용거래 항목. marginLoan(융자) 또는 stockLoan(대주)."""
    for row in records:
        if row.get('date', '') < date:
            return row.get(field) or {}
    return {}


def margin_loan_rate_pct(records, date):
    """신용융자 잔고 비율(%). 상장주식수 대비입니다.

    이 값이 크면 급락할 때 반대매매가 쏟아져 하락이 증폭됩니다. 단타에서는 손절이
    설계한 폭을 넘어 밀릴 위험으로 이어집니다.
    """
    value = number(credit_detail(records, date, 'marginLoan').get('balanceRate'))
    return value * 100 if value is not None else None


def stock_loan_rate_pct(records, date):
    """신용대주 잔고 비율(%). 개인이 주식을 빌려 매도한 잔고입니다."""
    value = number(credit_detail(records, date, 'stockLoan').get('balanceRate'))
    return value * 100 if value is not None else None


def relative_strength(stock_bars, index_bars, date):
    """전일 종목 등락률 − 같은 날 지수 등락률. 양수면 시장보다 강했다는 뜻."""
    stock_change = daily_change_pct(bars_before(stock_bars, date))
    index_change = daily_change_pct(bars_before(index_bars, date))
    if stock_change is None or index_change is None:
        return None
    return stock_change - index_change


def build(symbol, date, daily_candles, index_candles, flows):
    """08:30 판단에 쓰는 특징 묶음. 전 거래일까지의 데이터만 들어갑니다."""
    bars = bars_before(daily_candles, date)
    if len(bars) < MIN_DAILY_BARS:
        return {'symbol': symbol, 'date': date, 'usable': False,
                'reason': f'일봉 {len(bars)}개 — 최소 {MIN_DAILY_BARS}개 필요'}
    investor = flows.get('investor', [])
    return {
        'symbol': symbol,
        'date': date,
        'usable': True,
        'previous_close': number(bars[-1]['closePrice']),
        'previous_change_pct': daily_change_pct(bars),
        'atr_pct': atr_pct(bars),
        'volume_surge': volume_surge(bars),
        'price_position': price_position(bars),
        'volatility_pct': realized_volatility_pct(bars),
        'relative_strength': relative_strength(daily_candles, index_candles, date),
        'foreigner_net': net_flow(investor, 'foreigner', date),
        'institution_net': net_flow(investor, 'institution', date),
        'individual_net': net_flow(investor, 'individual', date),
        'short_ratio_pct': short_ratio(flows.get('short_selling', []), date),
        'program_net': net_flow(flows.get('program_trades', []), 'nonArbitrage', date),
        'lending_change_pct': lending_balance_change_pct(flows.get('securities_lending', []), date),
        'margin_loan_rate_pct': margin_loan_rate_pct(flows.get('credit_trades', []), date),
        'stock_loan_rate_pct': stock_loan_rate_pct(flows.get('credit_trades', []), date),
        'sample_days': len(bars),
    }


def opening_range(minute_bars, minutes):
    """장 시작 후 N분의 고가·저가. 진입 조건 판정에 씁니다. 그 이후 봉은 쓰지 않습니다."""
    window = minute_bars[:minutes]
    highs = [number(bar['highPrice']) for bar in window]
    lows = [number(bar['lowPrice']) for bar in window]
    volumes = [number(bar['volume'], 0) for bar in window]
    highs = [h for h in highs if h is not None]
    lows = [l for l in lows if l is not None]
    if not highs or not lows:
        return None
    return {
        'high': max(highs),
        'low': min(lows),
        'open': number(window[0]['openPrice']),
        'volume': sum(volumes),
        'bars': len(window),
    }
