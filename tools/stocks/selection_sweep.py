#!/usr/bin/env python3
"""종목 선별 조건이 승률을 얼마나 움직이는지 재는 실험.

왜 이걸 따로 재는가:
  진입·청산 파라미터(레인지 길이·마감시각·손절폭·목표)를 243가지로 돌려봤더니
  전부 기대값이 음수였고, 최고(-0.39%)와 최저(-0.50%)의 차이가 0.11%p뿐이었습니다.
  타이밍을 아무리 조여도 안 된다는 뜻입니다.

  그 실험은 40종목 '전부'에 돌파 규칙을 적용했습니다. 반면 조건을 건 셋업
  (orb_strict)은 266일에 44건만 나왔고 기대값이 +0.27%였습니다.
  차이는 타이밍이 아니라 '어떤 종목에 적용했는가'입니다.

  그래서 이 파일은 진입·청산을 고정하고 선별 조건만 조여가며, 조건을 조일수록
  기대값이 실제로 올라가는지, 어디서 0을 넘는지를 잽니다.

과적합 방지: 학습 구간에서만 조건을 고르고 검증 구간은 한 번만 확인합니다.
"""
import sys

import confidence
import evaluate
import history
import signals
from features import (atr_pct, bars_before, build as build_features, number,
                      opening_range, price_position, relative_strength, volume_surge)
from store import STATE, read_json

# 진입·청산은 고정합니다. 이 실험에서 바꾸는 건 '어떤 종목을 고르는가'뿐입니다.
FIXED = {'range_minutes': 30, 'entry_deadline': '13:00', 'exit_time': '15:00',
         'atr_multiple': 0.8, 'target_pct': 2.0}
MIN_TRADES = 25

# 조건을 하나씩 더해가며 얼마나 걸러지는지 봅니다.
FILTERS = [
    ('전체 (조건 없음)', lambda f: True),
    ('상대강도 > 0', lambda f: (f.get('relative_strength') or -1) > 0),
    ('거래량 1.2배+', lambda f: (f.get('volume_surge') or 0) >= 1.2),
    ('거래량 2배+', lambda f: (f.get('volume_surge') or 0) >= 2.0),
    ('거래량 3배+', lambda f: (f.get('volume_surge') or 0) >= 3.0),
    ('수급(외인·기관)', lambda f: (f.get('foreigner_net') or 0) > 0
                              or (f.get('institution_net') or 0) > 0),
    ('수급 둘 다', lambda f: (f.get('foreigner_net') or 0) > 0
                          and (f.get('institution_net') or 0) > 0),
    ('20일 고가 95%+', lambda f: (f.get('price_position') or 0) >= 0.95),
    ('20일 고가 98%+', lambda f: (f.get('price_position') or 0) >= 0.98),
    ('저변동성 (ATR<2%)', lambda f: (f.get('atr_pct') or 99) < 2.0),
    ('고변동성 (ATR>3%)', lambda f: (f.get('atr_pct') or 0) > 3.0),
    ('공매도 낮음 (<5%)', lambda f: (f.get('short_ratio_pct') or 99) < 5.0),
]

# 조건을 겹쳐서 조일 때 기대값이 올라가는지 확인하는 조합.
STACKS = [
    ('강도+거래량', ('상대강도 > 0', '거래량 1.2배+')),
    ('강도+거래량2배', ('상대강도 > 0', '거래량 2배+')),
    ('강도+거래량+수급', ('상대강도 > 0', '거래량 1.2배+', '수급(외인·기관)')),
    ('강도+거래량2배+수급', ('상대강도 > 0', '거래량 2배+', '수급(외인·기관)')),
    ('orb_strict 재현', ('상대강도 > 0', '거래량 2배+', '수급(외인·기관)', '20일 고가 95%+')),
    ('가장 엄격', ('상대강도 > 0', '거래량 3배+', '수급 둘 다', '20일 고가 98%+')),
]

BY_NAME = dict(FILTERS)


def bar_time(bar):
    return bar['timestamp'][11:16]


def simulate(bars, index_bars, atr):
    """FIXED 설정으로 하루치를 재현합니다. sweep.py와 같은 보수적 규칙."""
    minutes = FIXED['range_minutes']
    if len(bars) <= minutes:
        return None
    window = opening_range(bars, minutes)
    if not window:
        return None
    entry = None
    for index, bar in enumerate(bars[minutes:], start=minutes):
        if bar_time(bar) > FIXED['entry_deadline']:
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
                   max(signals.MIN_STOP_PCT, (atr or 1.25) * FIXED['atr_multiple']))
    stop_price = entry_price * (1 - stop_pct / 100)
    target_price = entry_price * (1 + FIXED['target_pct'] / 100)
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
        if bar_time(bar) >= FIXED['exit_time']:
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


def build_trades(dates, symbols):
    """날짜×종목마다 '특징 + 그날 거래 결과'를 한 번만 계산해 재사용합니다."""
    daily_cache, flow_cache = {}, {}
    for symbol in symbols:
        daily_cache[symbol] = (read_json(history.daily_path(symbol), {}) or {}).get('candles', [])
        flow_cache[symbol] = read_json(history.flow_path(symbol), {}) or {}
    index_daily = (read_json(STATE / 'daily' / '_KOSPI.json', {}) or {}).get('candles', [])

    trades = []
    for date in dates:
        index_bars = (read_json(history.index_path('KOSPI', date), {}) or {}).get('candles', [])
        for symbol in symbols:
            stored = read_json(history.history_path(symbol, date), {}) or {}
            bars = stored.get('candles', [])
            if len(bars) < 90:
                continue
            row = build_features(symbol, date, daily_cache[symbol], index_daily, flow_cache[symbol])
            if not row.get('usable'):
                continue
            outcome = simulate(bars, index_bars, row.get('atr_pct'))
            if outcome:
                trades.append({**outcome, 'date': date, 'symbol': symbol, 'features': row})
    return trades


def score(trades, predicate):
    rows = [t for t in trades if predicate(t['features'])]
    if len(rows) < MIN_TRADES:
        return None, len(rows)
    wins = sum(1 for r in rows if r['win'])
    nets = [r['net_pct'] for r in rows]
    excess = [r['excess_pct'] for r in rows if r['excess_pct'] is not None]
    profile = confidence.win_loss_profile([n for n in nets if n > 0],
                                          [n for n in nets if n <= 0])
    return {
        'trades': len(rows),
        'hit_rate': wins / len(rows),
        'expectancy': sum(nets) / len(nets),
        'excess': sum(excess) / len(excess) if excess else None,
        'breakeven': profile.get('breakeven_hit_rate'),
    }, len(rows)


def show(label, result, count):
    if not result:
        print(f'  {label:26s} 거래 {count}건 — 표본 부족')
        return
    edge = (result['hit_rate'] - (result['breakeven'] or 1)) * 100
    flag = '  ✅' if result['expectancy'] > 0 and (result['excess'] or -1) > 0 else ''
    print(f"  {label:26s} {result['trades']:>5}건  승률 {result['hit_rate']*100:>5.1f}%"
          f"  본전선 {(result['breakeven'] or 0)*100:>5.1f}%  여유 {edge:>+6.1f}%p"
          f"  기대값 {result['expectancy']:>+6.2f}%"
          f"  지수대비 {(result['excess'] if result['excess'] is not None else 0):>+6.2f}%p{flag}")


def stacked(names):
    predicates = [BY_NAME[n] for n in names]
    return lambda f: all(p(f) for p in predicates)


def report(trades, title):
    print(f'\n{"=" * 104}\n{title}\n{"=" * 104}')
    print('\n[단일 조건] 하나씩만 걸었을 때')
    for name, predicate in FILTERS:
        result, count = score(trades, predicate)
        show(name, result, count)
    print('\n[조건 겹치기] 조일수록 좋아지는지')
    best = None
    for label, names in STACKS:
        result, count = score(trades, stacked(names))
        show(label, result, count)
        if result and (best is None or result['expectancy'] > best[1]['expectancy']):
            best = (names, result)
    return best


def main():
    dates = sorted({p.stem for d in (STATE / 'history').iterdir()
                    if d.is_dir() and not d.name.startswith('_')
                    for p in d.glob('*.json')})
    half = len(dates) // 2
    train, test = dates[:half], dates[half:]
    universe = sorted((STATE / 'universe').glob('*.json'))
    symbols = [e['symbol'] for e in (read_json(universe[-1], {}) or {}).get('symbols', [])]

    print(f'진입·청산 고정: 레인지 {FIXED["range_minutes"]}분 · 진입~{FIXED["entry_deadline"]}'
          f' · 청산 {FIXED["exit_time"]} · 손절 ATR×{FIXED["atr_multiple"]}'
          f' · 목표 {FIXED["target_pct"]}%')
    print(f'학습 {train[0]}~{train[-1]} ({len(train)}일) · 검증 {test[0]}~{test[-1]} ({len(test)}일)')

    train_trades = build_trades(train, symbols)
    best = report(train_trades, '학습 구간 — 여기서만 조건을 고릅니다')

    if not best:
        print('\n조건을 고를 만한 표본이 없습니다.')
        return
    names, _ = best
    print(f'\n{"=" * 104}\n검증 구간 — 학습에서 고른 조건 하나만 확인: {" + ".join(names)}\n{"=" * 104}')
    test_trades = build_trades(test, symbols)
    result, count = score(test_trades, stacked(names))
    show('검증', result, count)
    base, base_count = score(test_trades, lambda f: True)
    show('전체(비교용)', base, base_count)


if __name__ == '__main__':
    main()
