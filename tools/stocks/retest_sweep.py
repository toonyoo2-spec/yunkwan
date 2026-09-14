#!/usr/bin/env python3
"""돌파 직후 쫓아 사지 않고, 눌림에서 사면 나아지는가.

지금까지의 진입 방식은 개장범위 고가를 뚫는 첫 틱에 삽니다. 그런데 비용
분해에서 슬리피지가 왕복비용의 절반을 넘게 차지했습니다(0.243%p / 0.423%p).
돌파 순간은 매수가 몰려 체결가가 가장 나쁜 구간이기도 합니다.

트레이더들이 실제로 쓰는 대안: 돌파를 '확인'만 하고, 가격이 살짝 눌렸다가
다시 올라오는 지점에서 삽니다. 더 싸게 사는 대신, 눌림이 안 오면 그 거래는
포기합니다(기회비용). 이 실험은 그 교환이 순이익에 남는지를 잽니다.

같은 규율: 학습 구간에서만 판정 기준을 정하고, 검증은 한 번만 봅니다.
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
STOP_PCT = 2.5           # ATR×0.8 상한에 걸리는 경우가 대부분이라 상한값으로 고정 비교
MIN_TRADES = 25

# 눌림 판정: 돌파선 위 이 범위 안으로 저가가 들어오면 '눌렸다'고 봅니다.
PULLBACK_GRID = (0.2, 0.3, 0.5, 0.8)
# 눌림을 기다리는 최대 시간(돌파 후 몇 분). 너무 오래 기다리면 기회를 놓칩니다.
WAIT_GRID = (10, 20, 40)
# 눌림이 이 밑으로 빠지면 돌파가 실패한 것으로 보고 포기합니다.
INVALIDATE_PCT = 0.5


def passes(f):
    return ((f.get('relative_strength') or -1) > 0
            and (f.get('volume_surge') or 0) >= SELECT['volume_surge']
            and ((f.get('foreigner_net') or 0) > 0 or (f.get('institution_net') or 0) > 0)
            and (f.get('price_position') or 0) >= SELECT['price_position'])


def bar_time(bar):
    return bar['timestamp'][11:16]


def find_breakout(bars, window):
    for index, bar in enumerate(bars[RANGE_MINUTES:], start=RANGE_MINUTES):
        if bar_time(bar) > ENTRY_DEADLINE:
            return None
        high = number(bar['highPrice'])
        if high is not None and high > window['high']:
            return index
    return None


def exit_from(bars, start_index, entry_price, index_bars, entry_at):
    stop_price = entry_price * (1 - STOP_PCT / 100)
    target_price = entry_price * (1 + TARGET_PCT / 100)
    for bar in bars[start_index:]:
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
    net = signals.net_return_pct(entry_price, exit_price)
    if net is None:
        return None
    benchmark = evaluate.index_move_pct(index_bars, entry_at, exit_at)
    return {'net_pct': net, 'win': net > 0,
            'excess_pct': None if benchmark is None else net - benchmark}


def simulate_chase(bars, index_bars, window):
    """기존 방식: 돌파 첫 틱에 삽니다."""
    bi = find_breakout(bars, window)
    if bi is None:
        return None
    tick = signals.tick_size(window['high'])
    fill = min(window['high'] + signals.SLIPPAGE_TICKS * tick, number(bars[bi]['highPrice']))
    return exit_from(bars, bi, fill, index_bars, bars[bi]['timestamp'])


def simulate_retest(bars, index_bars, window, pullback_pct, wait_bars):
    """돌파를 확인한 뒤, 눌림에서 삽니다. 눌림이 없으면 포기합니다."""
    bi = find_breakout(bars, window)
    if bi is None:
        return None
    zone_high = window['high'] * (1 + pullback_pct / 100)
    invalid_low = window['high'] * (1 - INVALIDATE_PCT / 100)
    deadline_index = min(bi + wait_bars, len(bars))
    for index in range(bi + 1, deadline_index):
        bar = bars[index]
        if bar_time(bar) > ENTRY_DEADLINE:
            return None
        low = number(bar['lowPrice'])
        if low is None:
            continue
        if low < invalid_low:
            return None                          # 돌파 실패로 판정, 포기
        if low <= zone_high:
            tick = signals.tick_size(window['high'])
            fill = min(zone_high, low + signals.SLIPPAGE_TICKS * tick)
            fill = max(fill, window['high'])       # 개장범위 밑으로는 사지 않음
            return exit_from(bars, index, fill, index_bars, bar['timestamp'])
    return None                                    # 대기시간 내 눌림 없음, 포기


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


def show(label, r, attempts=None):
    if not r:
        extra = f' (시도 {attempts}건 중 체결 없음)' if attempts else ''
        print(f'  {label:34s} 표본 부족{extra}')
        return
    edge = (r['hit_rate'] - (r['breakeven'] or 1)) * 100
    flag = '  <<' if r['expectancy'] > 0 and (r['excess'] or -1) > 0 else ''
    fill_rate = f"  체결률 {r['trades']/attempts*100:>4.0f}%" if attempts else ''
    print(f"  {label:34s} {r['trades']:>4}건  승률 {r['hit_rate']*100:>5.1f}%"
          f"  본전선 {(r['breakeven'] or 0)*100:>5.1f}%  여유 {edge:>+6.1f}%p"
          f"  기대값 {r['expectancy']:>+6.2f}%  지수대비 "
          f"{(r['excess'] if r['excess'] is not None else 0):>+6.2f}%p{fill_rate}{flag}")


def main():
    dates = sorted({p.stem for d in (STATE / 'history').iterdir()
                    if d.is_dir() and not d.name.startswith('_')
                    for p in d.glob('*.json')})
    half = len(dates) // 2
    train, test = dates[:half], dates[half:]
    universe = sorted((STATE / 'universe').glob('*.json'))
    symbols = [e['symbol'] for e in (read_json(universe[-1], {}) or {}).get('symbols', [])]

    print('선별 조건 고정(현행) · 목표 2.0% · 손절 2.5% 고정, 진입 방식만 비교')
    print(f'학습 {train[0]}~{train[-1]} · 검증 {test[0]}~{test[-1]}\n')

    picked = selected_days(train, symbols)
    print(f'학습 구간 선별+돌파 후보: {len(picked)}건\n{"=" * 104}')
    print('학습 구간 — 쫓아사기 vs 눌림목 (대기시간 × 눌림폭)')
    print('=' * 104)

    chase_rows = [o for bars, ib, w in picked if (o := simulate_chase(bars, ib, w)) is not None]
    show('쫓아사기(현행)', score(chase_rows), attempts=len(picked))

    results = []
    for wait in WAIT_GRID:
        for pull in PULLBACK_GRID:
            rows = [o for bars, ib, w in picked
                    if (o := simulate_retest(bars, ib, w, pull, wait)) is not None]
            r = score(rows)
            label = f'눌림 {pull}% 대기{wait}분'
            show(label, r, attempts=len(picked))
            if r:
                results.append(((pull, wait), r))

    if not results:
        print('\n검증할 조합이 없습니다.')
        return

    # 선정 기준: 기대값이 플러스인 것들 중에서 승률이 가장 높은 걸 고릅니다.
    # 승률만 보고 고르면 목표를 낮춰 손익비를 깨는 결과를 다시 부를 수 있어서
    # (orb_strict@1.0%가 승률 77.3%였지만 본전선 79.3%로 지는 구조였던 사례),
    # '이길 수 있는 것들 중 가장 잘 이기는 것'으로 제한합니다.
    positive = [(pull_wait, r) for pull_wait, r in results if r['expectancy'] > 0]
    pool = positive if positive else results
    if not positive:
        print('\n  (참고) 학습 구간에서 기대값이 플러스인 조합이 없어, 전체 중 승률 1위를 대신 보여줍니다.'
              ' 검증에서 손실일 가능성이 높습니다.')
    pool.sort(reverse=True, key=lambda x: x[1]['hit_rate'])
    best_pull, best_wait = pool[0][0]
    print(f'\n{"=" * 104}\n검증 구간 — 학습에서 고른 설정 하나만: 눌림 {best_pull}% 대기{best_wait}분\n{"=" * 104}')
    verify = selected_days(test, symbols)
    v_chase = [o for bars, ib, w in verify if (o := simulate_chase(bars, ib, w)) is not None]
    v_retest = [o for bars, ib, w in verify
                if (o := simulate_retest(bars, ib, w, best_pull, best_wait)) is not None]
    show('쫓아사기(현행, 비교용)', score(v_chase), attempts=len(verify))
    show(f'눌림 {best_pull}% 대기{best_wait}분', score(v_retest), attempts=len(verify))


if __name__ == '__main__':
    main()
