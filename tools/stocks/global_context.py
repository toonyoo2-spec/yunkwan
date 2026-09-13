#!/usr/bin/env python3
"""새벽 해외 시장 상황.

왜 필요한가:
  08:30 판단 시점에서 미국장은 새벽 6시에 이미 끝나 있습니다. 그날 국내 시장에
  영향을 주는 정보 중 유일하게 '완전히 확정된 최신 정보'입니다. 일본·중국은
  09:00 / 10:30에 열려서 08:30에는 전일 종가밖에 없지만, 미국에 상장된 해당 국가
  ETF는 새벽까지 거래되며 그 나라 시장에 대한 미국 투자자들의 재평가를 담고 있습니다.

왜 크롤링을 쓰지 않는가:
  무료 지수 CSV 사이트(stooq 등)는 봇 차단이 걸려 있어 우회가 필요합니다. 우회는
  하지 않습니다. 대신 같은 토스 공식 API로 미국 상장 ETF를 조회합니다. 약관 안에
  있고, 차단 이슈가 없으며, 마감 시각이 더 늦어 정보가 오히려 신선합니다.

한계 (반드시 감안할 것):
  ETF는 원지수와 완전히 같지 않습니다. 환율 효과, 미국 거래시간 중의 재평가,
  ETF 자체 수급이 섞여 있습니다. 원지수의 대체물이 아니라 '미국 투자자들이 그
  시장을 어떻게 봤는지'를 나타내는 별개 지표로 다뤄야 합니다.
"""
from features import number
from store import STATE, read_json, write_json
from tossapi import now

# 심볼: (설명, 이 지표가 왜 국내 단타에 의미가 있는지)
PROXIES = {
    'EWY': '미국 상장 한국 ETF — 새벽에 한국 시장을 미리 반영',
    'SOXX': '미국 반도체 ETF — 삼성전자·SK하이닉스와 연동이 큼',
    'QQQ': '나스닥 100 — 성장·기술주 전반의 위험선호',
    'SPY': 'S&P 500 — 미국 시장 전체',
    'EWJ': '미국 상장 일본 ETF — 일본 시장에 대한 새벽 재평가',
    'FXI': '미국 상장 중국 대형주 ETF — 중국 시장에 대한 새벽 재평가',
}

# 이 값보다 EWY가 더 빠진 날은 매수 신호를 만들지 않습니다.
# 새벽에 한국 시장이 크게 밀린 날 상승 돌파를 노리는 것은 승률을 떨어뜨립니다.
RISK_OFF_EWY_PCT = -1.5

DAILY_COUNT = 10
CONTEXT_DIR = STATE / 'global'


def context_path(date):
    return CONTEXT_DIR / f'{date}.json'


def overnight_change(client, symbol):
    """가장 최근 일봉의 전일 대비 등락률(%). 받지 못하면 None."""
    page = client.get_optional('/api/v1/candles', symbol=symbol, interval='1d',
                               count=DAILY_COUNT, adjusted='false')
    bars = (page or {}).get('candles', [])
    if len(bars) < 2:
        return None
    # 응답은 최신순입니다. 첫 요소가 가장 최근 봉.
    last, previous = number(bars[0].get('closePrice')), number(bars[1].get('closePrice'))
    if not last or not previous:
        return None
    return {'change_pct': (last / previous - 1) * 100,
            'close': last, 'as_of': bars[0].get('timestamp')}


def exchange_rate(client):
    result = client.get_optional('/api/v1/exchange-rate')
    if not result:
        return None
    return {'rate': number(result.get('rate') or result.get('exchangeRate')),
            'as_of': result.get('dateTime') or result.get('validFrom')}


def collect(client, date=None):
    """08:30 직전에 1회 실행. 결과를 저장하고 돌려줍니다."""
    date = date or now().date().isoformat()
    cached = read_json(context_path(date))
    if cached:
        return cached
    markets = {}
    for symbol, meaning in PROXIES.items():
        row = overnight_change(client, symbol)
        if row:
            markets[symbol] = {**row, 'meaning': meaning}
    context = {
        'date': date,
        'collected_at': now().isoformat(),
        'markets': markets,
        'exchange_rate': exchange_rate(client),
        'regime': regime(markets),
        'note': 'ETF는 원지수와 다릅니다. 환율 효과와 미국 거래시간 재평가가 섞여 있습니다.',
    }
    write_json(context_path(date), context)
    return context


def regime(markets):
    """그날 매수 신호를 낼 만한 환경인지 판정합니다."""
    korea = (markets.get('EWY') or {}).get('change_pct')
    semis = (markets.get('SOXX') or {}).get('change_pct')
    nasdaq = (markets.get('QQQ') or {}).get('change_pct')
    if korea is None:
        return {'status': 'unknown', 'allow_long': False,
                'reason': 'EWY를 받지 못해 새벽 시장 상황을 확인할 수 없습니다 — 신호를 만들지 않습니다'}
    if korea <= RISK_OFF_EWY_PCT:
        return {'status': 'risk_off', 'allow_long': False,
                'reason': f'새벽 한국 ETF {korea:+.2f}% — 기준({RISK_OFF_EWY_PCT}%) 이하라 매수 신호를 만들지 않습니다'}
    parts = [f'EWY {korea:+.2f}%']
    if semis is not None:
        parts.append(f'반도체 {semis:+.2f}%')
    if nasdaq is not None:
        parts.append(f'나스닥 {nasdaq:+.2f}%')
    return {'status': 'normal', 'allow_long': True, 'reason': ' · '.join(parts)}


def features(context):
    """특징 묶음에 합칠 형태로 납작하게 폅니다."""
    markets = context.get('markets', {})
    flat = {f'overnight_{symbol.lower()}_pct': (markets.get(symbol) or {}).get('change_pct')
            for symbol in PROXIES}
    flat['overnight_regime'] = context.get('regime', {}).get('status')
    return flat


def describe(context):
    """화면에 그대로 보여줄 한 줄."""
    markets = context.get('markets', {})
    if not markets:
        return '새벽 해외 시장 데이터를 받지 못했습니다'
    order = ['EWY', 'SOXX', 'QQQ', 'SPY', 'EWJ', 'FXI']
    parts = [f'{symbol} {markets[symbol]["change_pct"]:+.2f}%'
             for symbol in order if symbol in markets]
    return ' · '.join(parts)
