#!/usr/bin/env python3
"""선별된 집단에 맞춘 목표·손절 재탐색.

앞선 두 실험에서 나온 것:
  1. 진입·청산 파라미터를 243조합 돌려도 전부 손실이었다. 단, 그건 40종목
     '전부'에 돌파 규칙을 적용한 결과였다.
  2. 선별 조건을 조이면 단조롭게 좋아진다. 거래량 배수가 가장 강한 축이었고
     (1.2배 44.9% → 2배 51.4% → 3배 54.1%), 조건을 겹치면 학습 구간에서
     기대값이 처음으로 0을 넘었다(+0.02%).

그래서 이 파일은 선별 조건을 고정하고, '그 선별된 집단에 맞는' 목표·손절·청산을
다시 찾습니다. 앞선 파라미터 탐색은 선별 전 집단 기준이라 최적값이 다를 수 있습니다.

같은 규율: 학습 구간에서만 고르고 검증은 한 번만 봅니다.
"""
import sys
from itertools import product

import confidence
import evaluate
import history
import signals
from features import build as build_features, number, opening_range
from store import STATE, read_json

# 학습 구간에서 가장 좋았던 선별 조건. 여기서는 고정합니다.
SELECT = {
    'relative_strength': 0.0,     # 초과
    'volume_surge': 2.0,          # 이상
    'flow': True,                 # 외국인 또는 기관 순매수
    'price_position': 0.95,       # 이상
}

GRID = {
    'target_pct': (1.5, 2.0, 2.5, 3.0, 4.0),
    'atr_multiple': (0.6, 0.8, 1.0, 1.4),
    'exit_time': ('13:00', '15:00'),
}
RANGE_MINUTES = 30
ENTRY_DEADLINE = '13:00'
MIN_TRADES = 30


def passes(f):
    return ((f.get('relative_strength') or -1) > SELECT['relative_strength']
            and (f.get('volume_surge') or 0) >= SELECT['volume_surge']
            and ((f.get('foreigner_net') or 0) > 0 or (f.get('institution_net') or 0) > 0)
            and (f.get('price_position') or 0) >= SELECT['price_position'])


def bar_time(bar):
    return bar['timestamp'][11:16]


def simulate(bars, index_bars, atr, config):
    if len(bars) <= RANGE_MINUTES:
        return None
    window = opening_range(bars, RANGE_MINUTES)
    if not window:
        return None
    entry = None
    for index, bar in enumerate(bars[RANGE_MINUTES:], start=RANGE_MINUTES):
        if bar_time(bar) > ENTRY_DEADLINE:
            return None
        high = number(bar['highPrice'])
        if high is not None and high > window['high']:
            fill = min(window['high'] + signals.SLIPPAGE_TICKS
                       * signals.tick_size(window['high']), high)
            entry = (index, fill, bar['timestamp'])
            break
    if not entry:
        return None
    index, entry_price, entry_at = entry
    stop_pct = min(signals.MAX_STOP_PCT,
                   max(signals.MIN_STOP_PCT, (atr or 1.25) * config['atr_multiple']))
    stop_price = entry_price * (1 - stop_pct / 100)
    target_price = entry_price * (1 + config['target_pct'] / 100)
    for bar in bars[index:]:
        low, high = number(bar['lowPrice']), number(bar['highPrice'])
        if low is None or high is None:
            continue
        if low <= stop_price:
            exit_price, exit_at = stop_price, bar['timestamp']
            break
        if high >= target_price:
            exit_price, exit_at = target_price, bar['timestamp']
            break
        if bar_time(bar) >= config['exit_time']:
            exit_price, exit_at = number(bar['closePrice']), bar['timestamp']
            break
    else:
        last = bars[-1]
        exit_price, exit_at = number(last['closePrice']), last['timestamp']
    net = signals.net_return_pct(entry_price, exit_price)
    if net is None:
        return None
    benchmark = evaluate.index_move_pct(index_bars, entry_at, exit_at)
    return {'net_pct': net, 'win': net > 0,
            'excess_pct': None if benchmark is None else net - benchmark}


def selected_days(dates, symbols):
    """선별 조건을 통과한 (날짜, 종목)만 미리 골라둡니다."""
    daily = {s: (read_json(history.daily_path(s), {}) or {}).get('candles', []) for s in symbols}
    flows = {s: read_json(history.flow_path(s), {}) or {} for s in symbols}
    index_daily = (read_json(STATE / 'daily' / '_KOSPI.json', {}) or {}).get('candles', [])
    picked = []
    for date in dates:
        index_bars = (read_json(history.index_path('KOSPI', date), {}) or {}).get('candles', [])
        for symbol in symbols:
            stored = read_json(history.history_path(symbol, date), {}) or {}
            bars = stored.get('candles', [])
            if len(bars) < 90:
                continue
            row = build_features(symbol, date, daily[symbol], index_daily, flows[symbol])
            if not row.get('usable') or not passes(row):
                continue
            picked.append((date, symbol, bars, index_bars, row.get('atr_pct')))
    return picked


def score(picked, config):
    rows = []
    for date, symbol, bars, index_bars, atr in picked:
        outcome = simulate(bars, index_bars, atr, config)
        if outcome:
            rows.append(outcome)
    if len(rows) < MIN_TRADES:
        return None
    wins = sum(1 for r in rows if r['win'])
    nets = [r['net_pct'] for r in rows]
    excess = [r['excess_pct'] for r in rows if r['excess_pct'] is not None]
    profile = confidence.win_loss_profile([n for n in nets if n > 0],
                                          [n for n in nets if n <= 0])
    return {'trades': len(rows), 'hit_rate': wins / len(rows),
            'expectancy': sum(nets) / len(nets),
            'excess': sum(excess) / len(excess) if excess else None,
            'breakeven': profile.get('breakeven_hit_rate')}


def show(label, r):
    if not r:
        print(f'  {label:38s} 표본 부족')
        return
    edge = (r['hit_rate'] - (r['breakeven'] or 1)) * 100
    flag = '  ✅' if r['expectancy'] > 0 and (r['excess'] or -1) > 0 else ''
    print(f"  {label:38s} {r['trades']:>4}건  승률 {r['hit_rate']*100:>5.1f}%"
          f"  본전선 {(r['breakeven'] or 0)*100:>5.1f}%  여유 {edge:>+6.1f}%p"
          f"  기대값 {r['expectancy']:>+6.2f}%  지수대비 "
          f"{(r['excess'] if r['excess'] is not None else 0):>+6.2f}%p{flag}")


def main():
    dates = sorted({p.stem for d in (STATE / 'history').iterdir()
                    if d.is_dir() and not d.name.startswith('_')
                    for p in d.glob('*.json')})
    half = len(dates) // 2
    train, test = dates[:half], dates[half:]
    universe = sorted((STATE / 'universe').glob('*.json'))
    symbols = [e['symbol'] for e in (read_json(universe[-1], {}) or {}).get('symbols', [])]

    print('선별 조건 고정: 상대강도>0 · 거래량 2배+ · 외인/기관 순매수 · 20일고가 95%+')
    print(f'학습 {train[0]}~{train[-1]} · 검증 {test[0]}~{test[-1]}\n')

    picked = selected_days(train, symbols)
    print(f'학습 구간 선별 통과: {len(picked)}건\n')
    print('=' * 104)
    print('학습 구간 — 선별된 집단에 맞는 목표·손절 찾기')
    print('=' * 104)
    results = []
    for combo in product(*GRID.values()):
        config = dict(zip(GRID.keys(), combo))
        r = score(picked, config)
        if r:
            results.append((r['expectancy'], config, r))
    results.sort(reverse=True, key=lambda x: x[0])
    for _, config, r in results[:12]:
        show(f"목표{config['target_pct']}% ATR×{config['atr_multiple']} 청산{config['exit_time']}", r)

    if not results:
        print('  비교할 조합이 없습니다.')
        return

    best = results[0][1]
    print(f'\n{"=" * 104}\n검증 구간 — 고른 설정 하나만: {best}\n{"=" * 104}')
    verify = selected_days(test, symbols)
    print(f'검증 구간 선별 통과: {len(verify)}건\n')
    show('검증', score(verify, best))
    show('기존 설정(목표2.0 ATR×0.8 청산15:00)', score(
        verify, {'target_pct': 2.0, 'atr_multiple': 0.8, 'exit_time': '15:00'}))


if __name__ == '__main__':
    main()
