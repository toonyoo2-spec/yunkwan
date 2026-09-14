#!/usr/bin/env python3
"""목표가에서 전량 매도 대신, 절반만 팔고 나머지는 따라 붙이면 나아지는가.

프랍 트레이더들이 흔히 쓰는 규칙입니다: 손절은 짧게 고정하고, 익절은
목표에서 절반만 확정한 뒤 나머지는 고점 대비 일정폭 트레일링 스탑으로
더 태웁니다. 승률은 그대로거나 조금 낮아져도, 크게 가는 날의 수익을
더 가져가 손익비를 개선하는 게 목적입니다.

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

# 나머지 절반을 고점 대비 이만큼 밑으로 트레일링합니다.
TRAIL_GRID = (0.8, 1.2, 1.6, 2.0)
# 트레일링을 시작하기 전, 최소 이만큼은 추가로 더 올라야 트레일을 겁니다.
# (목표 도달 직후 바로 트레일 거리에 걸려 잘리는 걸 막기 위함)
ARM_GRID = (0.0, 0.5, 1.0)


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


def simulate_fixed(bars, index_bars, window):
    """기존 방식: 목표가에서 전량 청산."""
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


def simulate_split(bars, index_bars, window, trail_pct, arm_pct):
    """절반은 목표가에서, 절반은 트레일링 스탑으로 청산.

    비용은 %로 계산되므로(수수료·세금·슬리피지 모두 진입가 대비 %),
    두 절반의 순수익률을 단순평균하면 전체 순수익률과 같습니다.
    """
    found = entry(bars, window)
    if not found:
        return None
    index, entry_price, entry_at = found
    stop_price = entry_price * (1 - STOP_PCT / 100)
    target_price = entry_price * (1 + TARGET_PCT / 100)
    arm_price = target_price * (1 + arm_pct / 100)

    half1_price = half1_at = None
    peak = entry_price
    armed = False
    half2_price = half2_at = None

    for bar in bars[index:]:
        low, high = number(bar['lowPrice']), number(bar['highPrice'])
        if low is None or high is None:
            continue
        peak = max(peak, high)

        if half1_price is None:
            if low <= stop_price:
                half1_price, half1_at = stop_price, bar['timestamp']
                half2_price, half2_at = stop_price, bar['timestamp']
                break
            if high >= target_price:
                half1_price, half1_at = target_price, bar['timestamp']
                continue          # 절반 확정, 나머지 절반은 이 봉부터 계속 관찰
        else:
            if not armed and peak >= arm_price:
                armed = True
            if armed:
                trail_price = peak * (1 - trail_pct / 100)
                if low <= trail_price:
                    half2_price, half2_at = max(trail_price, stop_price), bar['timestamp']
                    break
            if low <= stop_price:                 # 트레일 걸리기 전에 원래 손절까지 밀리면
                half2_price, half2_at = stop_price, bar['timestamp']
                break
        if bar_time(bar) >= EXIT_TIME:
            close = number(bar['closePrice'])
            if half1_price is None:
                half1_price, half1_at = close, bar['timestamp']
            half2_price, half2_at = close, bar['timestamp']
            break
    else:
        last = bars[-1]
        close = number(last['closePrice'])
        if half1_price is None:
            half1_price, half1_at = close, last['timestamp']
        if half2_price is None:
            half2_price, half2_at = close, last['timestamp']

    if half1_price is None or half2_price is None:
        return None
    net1 = signals.net_return_pct(entry_price, half1_price)
    net2 = signals.net_return_pct(entry_price, half2_price)
    if net1 is None or net2 is None:
        return None
    blended_net = (net1 + net2) / 2
    exit_at = half2_at
    benchmark = evaluate.index_move_pct(index_bars, entry_at, exit_at)
    return {'net_pct': blended_net, 'win': blended_net > 0,
            'excess_pct': None if benchmark is None else blended_net - benchmark}


def finish(entry_price, exit_price, index_bars, entry_at, exit_at):
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

    print('선별 조건 고정(현행) · 목표 2.0% · 손절 2.5% 고정, 청산 방식만 비교')
    print(f'학습 {train[0]}~{train[-1]} · 검증 {test[0]}~{test[-1]}\n')

    picked = selected_days(train, symbols)
    print(f'학습 구간 선별+돌파 통과: {len(picked)}건\n{"=" * 104}')
    print('학습 구간 — 전량청산(현행) vs 분할익절+트레일링')
    print('=' * 104)

    fixed_rows = [o for bars, ib, w in picked if (o := simulate_fixed(bars, ib, w)) is not None]
    show('전량청산(현행)', score(fixed_rows))

    results = []
    for arm in ARM_GRID:
        for trail in TRAIL_GRID:
            rows = [o for bars, ib, w in picked
                    if (o := simulate_split(bars, ib, w, trail, arm)) is not None]
            r = score(rows)
            show(f'분할+트레일{trail}% 발동+{arm}%', r)
            if r:
                results.append(((trail, arm), r))

    if not results:
        print('\n검증할 조합이 없습니다.')
        return

    positive = [(cfg, r) for cfg, r in results if r['expectancy'] > 0]
    pool = positive if positive else results
    if not positive:
        print('\n  (참고) 기대값 플러스 조합이 없어 승률 1위를 대신 보여줍니다.'
              ' 검증에서 손실 가능성이 높습니다.')
    pool.sort(reverse=True, key=lambda x: x[1]['hit_rate'])
    best_trail, best_arm = pool[0][0]

    print(f'\n{"=" * 104}\n검증 구간 — 학습에서 고른 설정 하나만: 트레일{best_trail}% 발동+{best_arm}%\n{"=" * 104}')
    verify = selected_days(test, symbols)
    v_fixed = [o for bars, ib, w in verify if (o := simulate_fixed(bars, ib, w)) is not None]
    v_split = [o for bars, ib, w in verify
               if (o := simulate_split(bars, ib, w, best_trail, best_arm)) is not None]
    show('전량청산(현행, 비교용)', score(v_fixed))
    show(f'분할+트레일{best_trail}% 발동+{best_arm}%', score(v_split))


if __name__ == '__main__':
    main()
