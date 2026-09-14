#!/usr/bin/env python3
"""승률을 올리는 설정을 찾는 실험.

바꿀 수 있는 손잡이가 여러 개인데, 어느 것이 실제로 승률을 움직이는지는
돌려봐야 압니다. 이 파일이 한 번에 하나씩 바꿔가며 전부 채점합니다.

  개장 레인지 길이 · 진입 마감 시각 · 청산 시각 · 손절 폭 · 목표

과적합을 막는 규율:
  학습 구간(앞쪽 절반)에서만 설정을 고릅니다. 검증 구간은 고른 뒤 한 번만
  확인합니다. 검증 구간을 보면서 설정을 고치면 그 구간도 학습 데이터가 되어
  '좋아 보이는 숫자'를 만들어낼 뿐입니다.

판단 기준은 승률 하나가 아닙니다:
  승률이 높아도 손익비가 나쁘면 집니다. 그래서 승률·거래당 기대값·지수 대비
  초과수익을 함께 보고, 본전 적중률을 넘겼는지로 판정합니다.
"""
import sys
from itertools import product

import confidence
import evaluate
import history
import robustness
import signals
from features import number, opening_range
from store import STATE, read_json

# 실험할 손잡이. 한 축만 바꿔가며 비교하려면 기본값에서 하나씩만 움직입니다.
GRID = {
    'range_minutes': (15, 30, 60),      # 개장 레인지 길이
    'entry_deadline': ('10:00', '11:30', '13:00'),
    'exit_time': ('11:00', '13:00', '15:00'),
    'atr_multiple': (0.5, 0.8, 1.2),    # 손절 폭 = ATR × 이 배수
    'target_pct': (1.0, 2.0, 3.0),
}
BASE = {'range_minutes': 30, 'entry_deadline': '13:00', 'exit_time': '15:00',
        'atr_multiple': 0.8, 'target_pct': 2.0}

MIN_TRADES = 40         # 이보다 적으면 비교 대상에서 뺍니다


def bar_time(bar):
    return bar['timestamp'][11:16]


def simulate(bars, index_bars, atr, config):
    """설정 하나로 하루치 분봉을 재현합니다. evaluate.py와 같은 보수적 규칙."""
    minutes = config['range_minutes']
    if len(bars) <= minutes:
        return None
    window = opening_range(bars, minutes)
    if not window:
        return None

    entry = None
    for index, bar in enumerate(bars[minutes:], start=minutes):
        if bar_time(bar) > config['entry_deadline']:
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
        if low <= stop_price:            # 같은 봉에서 둘 다 닿으면 손절 (보수적)
            exit_price, result, exit_at = stop_price, 'stop', bar['timestamp']
            break
        if high >= target_price:
            exit_price, result, exit_at = target_price, 'target', bar['timestamp']
            break
        if bar_time(bar) >= config['exit_time']:
            exit_price, result, exit_at = number(bar['closePrice']), 'timeout', bar['timestamp']
            break
    else:
        last = bars[-1]
        exit_price, result, exit_at = number(last['closePrice']), 'timeout', last['timestamp']

    net = signals.net_return_pct(entry_price, exit_price)
    if net is None:
        return None
    benchmark = evaluate.index_move_pct(index_bars, entry_at, exit_at)
    return {'net_pct': net, 'win': net > 0, 'result': result,
            'benchmark_pct': benchmark,
            'excess_pct': None if benchmark is None else net - benchmark}


def load_days(dates, symbols):
    """분봉·지수분봉·ATR을 미리 읽어둡니다. 설정을 바꿀 때마다 다시 읽지 않도록."""
    from features import atr_pct, bars_before
    cache = []
    for date in dates:
        index_bars = (read_json(history.index_path('KOSPI', date), {}) or {}).get('candles', [])
        for symbol in symbols:
            stored = read_json(history.history_path(symbol, date), {}) or {}
            bars = stored.get('candles', [])
            if len(bars) < 90:
                continue
            daily = (read_json(history.daily_path(symbol), {}) or {}).get('candles', [])
            atr = atr_pct(bars_before(daily, date)) if daily else None
            cache.append((date, symbol, bars, index_bars, atr))
    return cache


def score(cache, config):
    rows = []
    for date, symbol, bars, index_bars, atr in cache:
        outcome = simulate(bars, index_bars, atr, config)
        if outcome:
            rows.append({**outcome, 'date': date, 'symbol': symbol})
    if len(rows) < MIN_TRADES:
        return None
    wins = sum(1 for r in rows if r['win'])
    nets = [r['net_pct'] for r in rows]
    excess = [r['excess_pct'] for r in rows if r['excess_pct'] is not None]
    profile = confidence.win_loss_profile([n for n in nets if n > 0], [n for n in nets if n <= 0])
    return {
        'trades': len(rows),
        'hit_rate': wins / len(rows),
        'expectancy': sum(nets) / len(nets),
        'excess': sum(excess) / len(excess) if excess else None,
        'breakeven': profile.get('breakeven_hit_rate'),
        'payoff': profile.get('payoff_ratio'),
        'timeout_share': sum(1 for r in rows if r['result'] == 'timeout') / len(rows),
    }


def show(label, result):
    if not result:
        print(f'  {label:34s} 거래 부족')
        return
    edge = ((result['hit_rate'] - result['breakeven']) * 100
            if result['breakeven'] is not None else None)
    print(f"  {label:34s} {result['trades']:>5}건  승률 {result['hit_rate']*100:>5.1f}%"
          f"  기대값 {result['expectancy']:>+6.2f}%  지수대비 "
          f"{(result['excess'] if result['excess'] is not None else 0):>+6.2f}%p"
          f"  본전선 {(result['breakeven'] or 0)*100:>5.1f}%"
          f"  여유 {edge:>+5.1f}%p" if edge is not None else '')


def one_axis(cache, axis):
    """한 축만 바꿔가며 비교합니다. 다른 손잡이는 기본값에 고정."""
    print(f'\n[{axis}] 나머지는 기본값 고정')
    for value in GRID[axis]:
        config = {**BASE, axis: value}
        show(f'{axis}={value}', score(cache, config))


def full_grid(cache, top=12):
    """전체 조합. 기대값 순으로 상위만 보여줍니다."""
    print(f'\n[전체 조합] {len(list(product(*GRID.values())))}가지 중 기대값 상위 {top}개')
    results = []
    for combo in product(*GRID.values()):
        config = dict(zip(GRID.keys(), combo))
        result = score(cache, config)
        if result:
            results.append((result['expectancy'], config, result))
    results.sort(reverse=True, key=lambda x: x[0])
    for _, config, result in results[:top]:
        label = (f"R{config['range_minutes']} "
                 f"진입~{config['entry_deadline']} 청산{config['exit_time']} "
                 f"ATR×{config['atr_multiple']} 목표{config['target_pct']}%")
        show(label, result)
    return results


def split_dates():
    dates = sorted({p.stem for d in (STATE / 'history').iterdir()
                    if d.is_dir() and not d.name.startswith('_')
                    for p in d.glob('*.json')})
    half = len(dates) // 2
    return dates[:half], dates[half:]


def main():
    train, test = split_dates()
    universe = sorted((STATE / 'universe').glob('*.json'))
    symbols = [e['symbol'] for e in (read_json(universe[-1], {}) or {}).get('symbols', [])]
    print(f'학습 {train[0]}~{train[-1]} ({len(train)}일) · 검증 {test[0]}~{test[-1]} ({len(test)}일)')
    print(f'종목 {len(symbols)}개\n')

    print('=' * 100)
    print('학습 구간 — 여기서만 설정을 고릅니다')
    print('=' * 100)
    cache = load_days(train, symbols)
    for axis in GRID:
        one_axis(cache, axis)
    ranked = full_grid(cache)

    if not ranked:
        print('\n비교할 조합이 없습니다.')
        return

    best = ranked[0][1]
    print('\n' + '=' * 100)
    print(f'검증 구간 — 학습에서 고른 설정 하나만 확인합니다: {best}')
    print('=' * 100)
    verify = load_days(test, symbols)
    show('검증', score(verify, best))
    show('기본값(비교용)', score(verify, BASE))
    print('\n학습에서 좋았던 설정이 검증에서 무너지면 과적합입니다.')
    print('검증 숫자를 보고 설정을 또 고치면 그 구간도 학습 데이터가 됩니다.')


if __name__ == '__main__':
    main()
