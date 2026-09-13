#!/usr/bin/env python3
"""보유 기간 비교.

"하루 단타가 나은가, 일주일이 나은가, 한 달이 나은가"를 의견이 아니라 같은 조건에서
재봅니다. 종목 선정 규칙을 하나로 고정하고 청산 시점만 바꿔가며 비교합니다.

비교가 공정하려면 비용이 제대로 들어가야 합니다. 왕복 비용은 회전율에 비례하므로,
보유 기간이 짧을수록 같은 수익률이라도 실제로 남는 돈이 줄어듭니다.

쓰는 데이터: 일봉만 씁니다(history.collect_daily가 120거래일치를 저장).
  분봉이 아니라 일봉을 쓰는 이유는 긴 보유 기간을 비교하려면 긴 과거가 필요한데,
  분봉은 보관 범위가 짧기 때문입니다.

보수적 처리:
  하루 안에 목표가와 손절가를 모두 건드렸으면 손절로 처리합니다. 일봉으로는
  순서를 알 수 없고, 유리한 쪽을 가정하면 비교가 거짓말이 됩니다.

한계: 장중 진입 타이밍은 반영되지 않습니다. 진입은 신호 다음 거래일 시가입니다.
"""
import sys
from statistics import fmean

import history
from features import atr_pct, bars_before, daily_change_pct, number, volume_surge
from robustness import assess, summarize
from signals import net_return_pct, stop_distance_pct
from store import STATE, read_json

HORIZONS = (1, 3, 5, 10, 20)        # 보유 거래일
TARGET_MULTIPLE = 2.0               # 목표는 손절 폭의 몇 배로 둘지 (손익비 2:1)
MIN_HISTORY = 25                    # 신호를 만들려면 필요한 최소 일봉 수

# 종목 선정 규칙. 보유 기간을 비교하는 것이 목적이므로 일부러 단순하게 유지합니다.
# 조건이 복잡하면 '어느 기간이 나은가'가 아니라 '어느 조건이 나은가'를 재게 됩니다.
MIN_RELATIVE_STRENGTH = 0.0
MIN_VOLUME_SURGE = 1.2


def signal_dates(candles, index_candles):
    """신호가 난 날들. 전일까지의 데이터만 보고 판단합니다."""
    dates = [bar['timestamp'][:10] for bar in candles]
    signals = []
    for position, date in enumerate(dates):
        if position < MIN_HISTORY or position + 1 >= len(dates):
            continue
        history_bars = candles[:position]
        stock_change = daily_change_pct(history_bars)
        index_change = daily_change_pct(bars_before(index_candles, date))
        if stock_change is None or index_change is None:
            continue
        if stock_change - index_change <= MIN_RELATIVE_STRENGTH:
            continue
        surge = volume_surge(history_bars)
        if not surge or surge < MIN_VOLUME_SURGE:
            continue
        signals.append(position)
    return signals


def simulate_hold(candles, entry_index, hold_days, stop_pct, target_pct):
    """신호 다음 거래일 시가에 사서, 손절·목표·기간 만료 중 먼저 오는 것으로 청산."""
    if entry_index + 1 >= len(candles):
        return None
    entry_price = number(candles[entry_index + 1].get('openPrice'))
    if not entry_price:
        return None
    stop_price = entry_price * (1 - stop_pct / 100)
    target_price = entry_price * (1 + target_pct / 100)
    window = candles[entry_index + 1: entry_index + 1 + hold_days]
    if not window:
        return None
    for bar in window:
        low, high = number(bar.get('lowPrice')), number(bar.get('highPrice'))
        if low is None or high is None:
            continue
        if low <= stop_price:
            # 같은 날 목표도 건드렸어도 손절로 처리합니다 (순서를 알 수 없음).
            return {'exit_price': stop_price, 'result': 'stop', 'days': hold_days}
        if high >= target_price:
            return {'exit_price': target_price, 'result': 'target', 'days': hold_days}
    close = number(window[-1].get('closePrice'))
    return {'exit_price': close, 'result': 'timeout', 'days': hold_days} if close else None


def run(symbols=None, horizons=HORIZONS):
    index_candles = (read_json(STATE / 'daily' / '_KOSPI.json', {}) or {}).get('candles', [])
    if not index_candles:
        print('코스피 일봉이 없습니다. `daily_runner.py prepare`를 한 번 실행하세요.')
        return
    if symbols is None:
        latest = sorted((STATE / 'universe').glob('*.json'))
        if not latest:
            print('유니버스 기록이 없습니다.')
            return
        symbols = [entry['symbol']
                   for entry in (read_json(latest[-1], {}) or {}).get('symbols', [])]

    results = {hold: [] for hold in horizons}
    signal_count = 0
    for symbol in symbols:
        candles = (read_json(history.daily_path(symbol), {}) or {}).get('candles', [])
        if len(candles) < MIN_HISTORY + max(horizons) + 2:
            continue
        for entry_index in signal_dates(candles, index_candles):
            signal_count += 1
            stop_pct = stop_distance_pct(atr_pct(candles[:entry_index]))
            target_pct = stop_pct * TARGET_MULTIPLE
            entry_date = candles[entry_index + 1]['timestamp'][:10]
            for hold in horizons:
                outcome = simulate_hold(candles, entry_index, hold, stop_pct, target_pct)
                if not outcome:
                    continue
                entry_price = number(candles[entry_index + 1].get('openPrice'))
                net = net_return_pct(entry_price, outcome['exit_price'])
                if net is None:
                    continue
                results[hold].append({
                    'symbol': symbol, 'date': entry_date, 'net_pct': net,
                    'win': net > 0, 'result': outcome['result'],
                    'stop_pct': stop_pct, 'target_pct': target_pct,
                })

    if not signal_count:
        print('신호가 하나도 나오지 않았습니다. 일봉이 더 필요합니다.')
        return
    print(f'종목 {len(symbols)}개 · 신호 {signal_count}건 · 손익비 {TARGET_MULTIPLE:.0f}:1 고정')
    print(f'진입은 신호 다음 거래일 시가, 비용은 왕복 수수료·거래세·슬리피지 차감.\n')
    print(f"{'보유':>4}  {'거래':>5}  {'적중률':>7}  {'거래당':>8}  {'합계':>9}  {'최대낙폭':>9}  {'연속손실':>6}")
    print('-' * 62)
    summary = {}
    for hold in horizons:
        rows = results[hold]
        if not rows:
            continue
        verdict = assess(rows)
        risk = verdict['risk']
        rate = verdict['hit_rate'] or 0
        mean_net = fmean(row['net_pct'] for row in rows)
        print(f'{hold:>3}일  {len(rows):>5}  {rate * 100:>6.1f}%  {mean_net:>+7.2f}%'
              f'  {risk["total_return_pct"]:>+8.1f}%  {risk["max_drawdown_pct"]:>+8.1f}%'
              f'  {risk["longest_losing_streak"]:>5}회')
        summary[hold] = (verdict, mean_net)

    print('\n판정 (우연 배제·집중도·견딜 수 있는 손실):')
    for hold, (verdict, _) in summary.items():
        print(f'  {hold:>2}일: {summarize(verdict)}')

    best = max(summary.items(), key=lambda item: item[1][1]) if summary else None
    if best:
        hold, (verdict, mean_net) = best
        print(f'\n거래당 기대값이 가장 높은 구간: {hold}일 보유 ({mean_net:+.2f}%/거래)')
        print('다만 기대값이 높아도 우연 판정을 통과하지 못했다면 근거가 되지 못합니다.')
    print('\n이 비교는 일봉 기준입니다. 장중 진입 타이밍은 반영되지 않았고,')
    print('하루 안에 목표·손절을 모두 건드린 경우는 손절로 처리했습니다.')


if __name__ == '__main__':
    run(horizons=tuple(int(value) for value in sys.argv[1:]) or HORIZONS)
