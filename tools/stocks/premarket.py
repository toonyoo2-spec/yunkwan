#!/usr/bin/env python3
"""프리마켓(NXT 08:00~) 수집과 갭 전략 채점.

검증하려는 가설:
  "새벽 미국장이 강했던 날, 08:00 프리마켓에서 사서 정규장 개장 직후에 팔면 수익이 난다."

이게 성립하려면 두 가지가 동시에 참이어야 합니다.
  (1) 프리마켓 가격이 새벽 해외장 움직임을 아직 다 반영하지 않았다
  (2) 그 미반영분이 프리마켓의 매수·매도 호가 차이(스프레드)보다 크다

(2)가 이 가설의 생사를 가릅니다. 프리마켓은 호가가 얇아 스프레드가 벌어지기 쉽고,
스프레드가 엣지보다 크면 이론이 맞아도 계좌는 손해입니다. 그래서 가격뿐 아니라
호가 스프레드를 매일 함께 기록합니다.

지금은 추천하지 않습니다. 표본이 쌓여 confidence.py의 관문을 통과해야 추천에 들어갑니다.
"""
from features import number
from store import STATE, read_json, write_json
from tossapi import now

SPREAD_DIR = STATE / 'premarket'
SETUP_NAME = 'premarket_gap'

# 스프레드가 이보다 넓으면 그날 그 종목은 프리마켓 진입 대상에서 제외합니다.
# 왕복 스프레드 비용이 노리는 수익보다 커지는 지점입니다.
MAX_SPREAD_PCT = 0.6


def snapshot_path(date):
    return SPREAD_DIR / f'{date}.json'


def candles_path(symbol, date):
    return STATE / 'history' / '_premarket' / symbol / f'{date}.json'


def spread_pct(orderbook):
    """최우선 매수·매도 호가 차이를 중간가 대비 %로. 프리마켓 체결 비용의 실질 하한입니다."""
    asks, bids = orderbook.get('asks') or [], orderbook.get('bids') or []
    if not asks or not bids:
        return None
    best_ask = number(asks[0].get('price'))
    best_bid = number(bids[0].get('price'))
    if not best_ask or not best_bid or best_ask <= 0 or best_bid <= 0:
        return None
    mid = (best_ask + best_bid) / 2
    return (best_ask - best_bid) / mid * 100 if mid > 0 else None


def depth_won(orderbook, levels=3):
    """상위 호가 잔량의 금액 환산. 얼마나 사도 가격이 안 밀리는지의 대략치입니다."""
    total = 0.0
    for side in ('asks', 'bids'):
        for entry in (orderbook.get(side) or [])[:levels]:
            price, volume = number(entry.get('price')), number(entry.get('volume'))
            if price and volume:
                total += price * volume
    return total / 2 if total else None


def observe(client, symbols, date):
    """프리마켓 중 1회 호출. 종목별 스프레드와 호가 두께를 기록합니다."""
    stored = read_json(snapshot_path(date), {'date': date, 'samples': []})
    samples = list(stored.get('samples', []))
    moment = now().isoformat()
    for symbol in symbols:
        book = client.orderbook(symbol)
        if not book:
            continue
        samples.append({
            'symbol': symbol,
            'at': moment,
            'book_at': book.get('timestamp'),
            'spread_pct': spread_pct(book),
            'depth_won': depth_won(book),
        })
    write_json(snapshot_path(date), {'date': date, 'updated_at': moment, 'samples': samples})
    return samples


def collect_candles(client, symbol, date, session):
    """프리마켓 분봉. 나중에 이 구간 체결을 재현하려면 오늘 모아둬야 합니다."""
    path = candles_path(symbol, date)
    if read_json(path):
        return
    bars = client.candles_between(symbol, '1m', session['startTime'], session['endTime'])
    if bars:
        write_json(path, {'symbol': symbol, 'date': date, 'session': 'pre_market',
                          'collected_at': now().isoformat(), 'candles': bars})


def median_spread(date, symbol):
    """그날 그 종목의 프리마켓 스프레드 중앙값."""
    stored = read_json(snapshot_path(date), {}) or {}
    values = sorted(row['spread_pct'] for row in stored.get('samples', [])
                    if row.get('symbol') == symbol and row.get('spread_pct') is not None)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def tradeable_spread(date, symbol):
    """스프레드 기준 통과 여부. 측정값이 없으면 통과시키지 않습니다."""
    spread = median_spread(date, symbol)
    if spread is None:
        return False, '프리마켓 호가를 측정하지 못했습니다'
    if spread > MAX_SPREAD_PCT:
        return False, f'프리마켓 스프레드 {spread:.2f}% — 기준 {MAX_SPREAD_PCT}% 초과'
    return True, f'프리마켓 스프레드 {spread:.2f}%'


def score_day(date, symbol, target_pct):
    """프리마켓 진입 → 정규장 개장 후 청산을 재현합니다.

    진입은 프리마켓 마지막 봉 종가에 스프레드 절반을 더한 값으로 봅니다.
    (매도 호가로 사야 하므로 중간가보다 불리합니다.)
    """
    pre = read_json(candles_path(symbol, date), {}) or {}
    regular = read_json(STATE / 'history' / symbol / f'{date}.json', {}) or {}
    pre_bars, day_bars = pre.get('candles', []), regular.get('candles', [])
    if not pre_bars or not day_bars:
        return {'setup': SETUP_NAME, 'symbol': symbol, 'result': 'no_data'}
    spread = median_spread(date, symbol)
    if spread is None:
        return {'setup': SETUP_NAME, 'symbol': symbol, 'result': 'no_spread_data'}
    entry_reference = number(pre_bars[-1].get('closePrice'))
    if not entry_reference:
        return {'setup': SETUP_NAME, 'symbol': symbol, 'result': 'no_data'}
    entry_price = entry_reference * (1 + spread / 200)   # 매도 호가 쪽으로 반 스프레드
    open_price = number(day_bars[0].get('openPrice'))
    gap_pct = (open_price / entry_price - 1) * 100 if open_price else None
    return {
        'setup': SETUP_NAME,
        'symbol': symbol,
        'result': 'traded',
        'spread_pct': spread,
        'gap_pct': gap_pct,
        'target_pct': target_pct,
        'entry_price': entry_price,
        'open_price': open_price,
    }
