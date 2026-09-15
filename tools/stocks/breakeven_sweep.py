#!/usr/bin/env python3
"""목표 도달 전에도, 이익이 쌓이면 손절선을 본전 쪽으로 당기면 나아지는가.

오늘(2026-09-15) 실거래 1건이 이 질문을 직접 던졌습니다. 후성(093370)은
+1.18%까지 올랐다가 전부 반납하고 -2.81% 손절로 마감했습니다. 어제 만든
분할+트레일링(trail_sweep.py)은 목표(2.0%)에 도달해야 작동하는데, 이 종목은
1.5%에서 반전했으니 그 장치로는 못 구했을 겁니다.

여기서는 목표 도달과 무관하게, 이익이 ARM_PCT만큼 쌓이면 손절선을
'본전 + 버퍼'로 끌어올립니다. 목표에 도달하면 그대로 팔고, 그 전에 반전하면
본전 근처에서 빠져나옵니다 — 손실을 승리로 바꾸진 못해도 큰 손실을 작은
손실이나 무승부로 바꿀 수 있는지가 관건입니다.

같은 규율: 학습 구간에서만 고르고 검증은 한 번만 봅니다.
선정 기준도 동일: 기대값이 플러스인 것들 중 승률이 가장 높은 걸 고릅니다.
"""
import confidence
import evaluate
import history
import signals
from features import build as build_features, number, opening_range
from store import STATE, read_json

SELECT = {'volume_surge': 2.0, 'price_position': 0.95}
RANGE_MINUTES = 30
ENTRY_DEADLINE = '13:00'
EXIT_TIME = '15:00'
TARGET_PCT = 2.0
STOP_PCT = 2.5
MIN_TRADES = 25

# 이 이익률(%)에 도달하면 손절선을 끌어올립니다.
ARM_GRID = (0.5, 0.8, 1.0, 1.2, 1.5)
# 끌어올린 손절선 = 진입가 * (1 + BUFFER_PCT/100). 비용을 감안해 0보다 약간 위/아래로.
BUFFER_GRID = (-0.3, 0.0, 0.3)


def passes(f):
    return ((f.get('relative_strength') or -1) > 0
            and (f.get('volume_surge') or 0) >= SELECT['volume_surge']
            and ((f.get('foreigner_net') or 0) > 0 or (f.get('institution_net') or 0) > 0)
            and (f.get('price_position') or 0) >= SELECT['price_position'])


def bar_time(bar):
    return bar['timestamp'][11:16]


def entry(bars, window):
    for index, bar in enumerate(bars[RANGE_MINUTES:], start=RANGE_MINUTES):
        if bar_time(bar) > ENTRY_DEADLINE:
            return None
        high = number(bar['highPrice'])
        if high is not None and high > window['high']:
            tick = signals.tick_size(window['high'])
            fill = min(window['high'] + signals.SLIPPAGE_TICKS * tick, high)
            return index, fill, bar['timestamp']
    return None


def finish(entry_price, exit_price, index_bars, entry_at, exit_at):
    net = signals.net_return_pct(entry_price, exit_price)
    if net is None:
        return None
    benchmark = evaluate.index_move_pct(index_bars, entry_at, exit_at)
    return {'net_pct': net, 'win': net > 0,
            'excess_pct': None if benchmark is None else net - benchmark}


def simulate_fixed(bars, index_bars, window):
    """기존 방식: 손절선 고정."""
    found = entry(bars, window)
    if not found:
        return None
    index, entry_price, entry_at = found
    stop_price = entry_price * (1 - STOP_PCT / 100)
    target_price = entry_price * (1 + TARGET_PCT / 100)
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
        if bar_time(bar) >= EXIT_TIME:
            exit_price, exit_at = number(bar['closePrice']), bar['timestamp']
            break
    else:
        last = bars[-1]
        exit_price, exit_at = number(last['closePrice']), last['timestamp']
    return finish(entry_price, exit_price, index_bars, entry_at, exit_at)


def simulate_breakeven(bars, index_bars, window, arm_pct, buffer_pct):
    """이익이 arm_pct 쌓이면 손절선을 본전+buffer_pct로 끌어올립니다."""
    found = entry(bars, window)
    if not found:
        return None
    index, entry_price, entry_at = found
    stop_price = entry_price * (1 - STOP_PCT / 100)
    target_price = entry_price * (1 + TARGET_PCT / 100)
    arm_price = entry_price * (1 + arm_pct / 100)
    armed_stop = entry_price * (1 + buffer_pct / 100)
    armed = False
    for bar in bars[index:]:
        low, high = number(bar['lowPrice']), number(bar['highPrice'])
        if low is None or high is None:
            continue
        if not armed and high >= arm_price:
            armed = True
            stop_price = max(stop_price, armed_stop)   # 손절선은 완화되지 않고 올라가기만 함
        if low <= stop_price:
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
    return finish(entry_price, exit_price, index_bars, entry_at, exit_at)


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
            window = opening_range(bars, RANGE_MINUTES)
            if not window:
                continue
            picked.append((bars, index_bars, window))
    return picked


def score(rows):
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

    print('선별 조건 고정(현행) · 목표 2.0% 고정, 손절만 이익 구간에서 끌어올림')
    print(f'학습 {train[0]}~{train[-1]} · 검증 {test[0]}~{test[-1]}\n')

    picked = selected_days(train, symbols)
    print(f'학습 구간 선별+돌파 통과: {len(picked)}건\n{"=" * 104}')
    print('학습 구간 — 고정 손절(현행) vs 본전 끌어올림(발동선 × 버퍼)')
    print('=' * 104)

    fixed_rows = [o for bars, ib, w in picked if (o := simulate_fixed(bars, ib, w)) is not None]
    show('고정 손절(현행)', score(fixed_rows))

    results = []
    for arm in ARM_GRID:
        for buf in BUFFER_GRID:
            rows = [o for bars, ib, w in picked
                    if (o := simulate_breakeven(bars, ib, w, arm, buf)) is not None]
            r = score(rows)
            show(f'발동+{arm}% 버퍼{buf:+.1f}%', r)
            if r:
                results.append(((arm, buf), r))

    if not results:
        print('\n검증할 조합이 없습니다.')
        return

    positive = [(cfg, r) for cfg, r in results if r['expectancy'] > 0]
    pool = positive if positive else results
    if not positive:
        print('\n  (참고) 기대값 플러스 조합이 없어 승률 1위를 대신 보여줍니다.'
              ' 검증에서 손실 가능성이 높습니다.')
    pool.sort(reverse=True, key=lambda x: x[1]['hit_rate'])
    best_arm, best_buf = pool[0][0]

    print(f'\n{"=" * 104}\n검증 구간 — 학습에서 고른 설정 하나만: 발동+{best_arm}% 버퍼{best_buf:+.1f}%\n{"=" * 104}')
    verify = selected_days(test, symbols)
    v_fixed = [o for bars, ib, w in verify if (o := simulate_fixed(bars, ib, w)) is not None]
    v_be = [o for bars, ib, w in verify
            if (o := simulate_breakeven(bars, ib, w, best_arm, best_buf)) is not None]
    show('고정 손절(현행, 비교용)', score(v_fixed))
    show(f'발동+{best_arm}% 버퍼{best_buf:+.1f}%', score(v_be))


if __name__ == '__main__':
    main()
