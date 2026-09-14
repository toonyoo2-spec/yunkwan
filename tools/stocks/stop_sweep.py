#!/usr/bin/env python3
"""손절 폭을 직접 재탐색.

tuned_sweep.py에서 ATR 배수 0.8·1.0·1.4가 **완전히 같은 숫자**를 냈습니다.
우연이 아닙니다. signals.MAX_STOP_PCT = 2.5가 셋 다 잘라내고 있었습니다.
선별된 집단(거래량 2배+ 돌파 종목)은 ATR이 대개 3%를 넘으므로
0.8×3.1 = 2.5, 1.0×3.1 = 3.1, 1.4×3.1 = 4.3 — 전부 2.5로 고정됩니다.

즉 앞선 두 실험은 손절 축을 한 번도 실제로 움직여보지 못했습니다.
목표 2.0%(비용 뺀 실수령 ~1.7%)에 손절 2.5%(실수령 손실 ~2.7%)면
손익비가 0.63이고 본전 적중률이 61.4%입니다. 검증 승률 60.0%는
바로 그 선을 1.4%p 못 넘긴 숫자였습니다.

그래서 이 파일은 ATR 배수 대신 **손절 폭 자체**를 축으로 놓고,
목표와 함께 격자로 훑습니다. 손절을 조이면 승률은 떨어지지만
손익비가 좋아져 본전선이 같이 내려갑니다. 둘의 차이(여유)가 판정 기준입니다.

규율은 같습니다: 학습 구간에서만 고르고 검증은 한 번만 봅니다.
"""
from itertools import product

import confidence
import evaluate
import history
import signals
from features import build as build_features, number, opening_range
from store import STATE, read_json

SELECT = {'volume_surge': 2.0, 'price_position': 0.95}

# stop_pct = None 은 '손절 없음'입니다. 목표나 청산시각까지 들고 갑니다.
# 손절이 도움이 되는지 자체를 확인하려고 넣었습니다.
GRID = {
    'target_pct': (1.5, 2.0, 2.5, 3.0, 4.0),
    'stop_pct': (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, None),
}
RANGE_MINUTES = 30
ENTRY_DEADLINE = '13:00'
EXIT_TIME = '15:00'
MIN_TRADES = 30


def passes(f):
    return ((f.get('relative_strength') or -1) > 0
            and (f.get('volume_surge') or 0) >= SELECT['volume_surge']
            and ((f.get('foreigner_net') or 0) > 0 or (f.get('institution_net') or 0) > 0)
            and (f.get('price_position') or 0) >= SELECT['price_position'])


def bar_time(bar):
    return bar['timestamp'][11:16]


def simulate(bars, index_bars, config):
    """손절 폭이 설정값 그대로 들어갑니다. ATR 배수도 상한도 끼지 않습니다."""
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
    stop = config['stop_pct']
    stop_price = None if stop is None else entry_price * (1 - stop / 100)
    target_price = entry_price * (1 + config['target_pct'] / 100)
    for bar in bars[index:]:
        low, high = number(bar['lowPrice']), number(bar['highPrice'])
        if low is None or high is None:
            continue
        if stop_price is not None and low <= stop_price:   # 같은 봉에서 둘 다 닿으면 손절 (보수적)
            exit_price, exit_at = stop_price, bar['timestamp']
            break
        if high >= target_price:
            exit_price, exit_at = target_price, bar['timestamp']
            break
        if bar_time(bar) >= EXIT_TIME:
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
    daily = {s: (read_json(history.daily_path(s), {}) or {}).get('candles', []) for s in symbols}
    flows = {s: read_json(history.flow_path(s), {}) or {} for s in symbols}
    index_daily = (read_json(STATE / 'daily' / '_KOSPI.json', {}) or {}).get('candles', [])
    picked = []
    for date in dates:
        index_bars = (read_json(history.index_path('KOSPI', date), {}) or {}).get('candles', [])
        for symbol in symbols:
            bars = (read_json(history.history_path(symbol, date), {}) or {}).get('candles', [])
            if len(bars) < 90:
                continue
            row = build_features(symbol, date, daily[symbol], index_daily, flows[symbol])
            if not row.get('usable') or not passes(row):
                continue
            picked.append((bars, index_bars))
    return picked


def score(picked, config):
    rows = [o for bars, index_bars in picked
            if (o := simulate(bars, index_bars, config)) is not None]
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
        print(f'  {label:30s} 표본 부족')
        return
    edge = (r['hit_rate'] - (r['breakeven'] or 1)) * 100
    flag = '  <<' if r['expectancy'] > 0 and (r['excess'] or -1) > 0 else ''
    print(f"  {label:30s} {r['trades']:>4}건  승률 {r['hit_rate']*100:>5.1f}%"
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

    print('선별 조건 고정 · 손절 폭을 ATR/상한 없이 직접 지정')
    print(f'학습 {train[0]}~{train[-1]} · 검증 {test[0]}~{test[-1]}\n')

    picked = selected_days(train, symbols)
    print(f'학습 구간 선별 통과: {len(picked)}건\n{"=" * 100}')
    print('학습 구간 — 목표 × 손절 격자')
    print('=' * 100)
    results = []
    for combo in product(*GRID.values()):
        config = dict(zip(GRID.keys(), combo))
        r = score(picked, config)
        if r:
            results.append((r['expectancy'], config, r))
    results.sort(reverse=True, key=lambda x: x[0])
    for _, config, r in results[:18]:
        stop = config['stop_pct']
        label = '없음' if stop is None else f'{stop}%'
        show(f"목표{config['target_pct']}% 손절{label}", r)

    if not results:
        print('  비교할 조합이 없습니다.')
        return

    best = results[0][1]
    print(f'\n{"=" * 100}\n검증 구간 — 고른 설정 하나만: {best}\n{"=" * 100}')
    verify = selected_days(test, symbols)
    print(f'검증 구간 선별 통과: {len(verify)}건\n')
    show('검증', score(verify, best))
    show('현행(목표2.0 손절2.5)', score(verify, {'target_pct': 2.0, 'stop_pct': 2.5}))


if __name__ == '__main__':
    main()
