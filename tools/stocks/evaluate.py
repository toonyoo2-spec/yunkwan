#!/usr/bin/env python3
"""분봉 체결 시뮬레이션.

08:30에 고정한 계획을 그날의 1분봉에 그대로 얹어, 실제로 그렇게 매매했다면
어떻게 됐을지 재현합니다. "추천대로 수익이 났는가"를 추정이 아니라 재현으로 답하기 위한 파일입니다.

보수적으로 처리하는 부분 (결과를 좋게 보이게 만들지 않기 위해):
  - 한 봉 안에서 목표가와 손절가를 모두 건드렸으면 손절이 먼저 걸렸다고 봅니다.
    1분봉만으로는 순서를 알 수 없고, 유리한 쪽을 가정하면 검증이 거짓말이 됩니다.
  - 진입은 돌파가에 1틱 불리하게 체결됐다고 봅니다.
  - 모든 손익은 수수료·거래세·슬리피지를 뺀 값입니다.
"""
from features import number, opening_range
from signals import (EXIT_TIME, SLIPPAGE_TICKS, TARGET_LADDER, net_return_pct,
                     round_trip_cost_pct, tick_size)


def bar_time(bar):
    """봉 종료 시각의 HH:MM."""
    return bar['timestamp'][11:16]


def entry_fill(breakout_price, bar_high):
    """돌파가에 1틱 불리하게 체결. 봉 고가를 넘는 가격으로는 체결되지 않습니다."""
    worst = breakout_price + SLIPPAGE_TICKS * tick_size(breakout_price)
    return min(worst, bar_high) if bar_high else worst


def find_entry(bars, plan):
    """레인지 이후 첫 돌파 봉을 찾습니다. 진입 마감시각을 넘기면 진입하지 않습니다."""
    minutes = plan['opening_range_minutes']
    window = opening_range(bars, minutes)
    if not window:
        return None, 'no_range', '개장 레인지를 계산할 봉이 없습니다'
    for index, bar in enumerate(bars[minutes:], start=minutes):
        if bar_time(bar) > plan['entry_deadline']:
            return None, 'no_entry', f'{plan["entry_deadline"]}까지 돌파가 없었습니다'
        high = number(bar['highPrice'])
        if high is not None and high > window['high']:
            return ({'index': index, 'price': entry_fill(window['high'], high),
                     'at': bar['timestamp'], 'range': window},
                    'entered', None)
    return None, 'no_entry', '당일 돌파가 없었습니다'


def walk(bars, start_index, entry_price, stop_price, target_price):
    """진입 이후 봉을 따라가며 손절·목표 도달을 확인합니다."""
    for bar in bars[start_index:]:
        low, high = number(bar['lowPrice']), number(bar['highPrice'])
        if low is None or high is None:
            continue
        touched_stop = low <= stop_price
        touched_target = high >= target_price
        if touched_stop and touched_target:
            # 순서를 모르므로 불리한 쪽으로 처리합니다.
            return {'result': 'stop', 'price': stop_price, 'at': bar['timestamp'],
                    'ambiguous': True}
        if touched_stop:
            return {'result': 'stop', 'price': stop_price, 'at': bar['timestamp'],
                    'ambiguous': False}
        if touched_target:
            return {'result': 'target', 'price': target_price, 'at': bar['timestamp'],
                    'ambiguous': False}
        if bar_time(bar) >= EXIT_TIME:
            close = number(bar['closePrice'])
            return {'result': 'timeout', 'price': close, 'at': bar['timestamp'],
                    'ambiguous': False}
    last = bars[-1]
    return {'result': 'timeout', 'price': number(last['closePrice']),
            'at': last['timestamp'], 'ambiguous': False}


def simulate(plan, bars):
    """계획 하나를 그날 분봉으로 재현합니다. 목표 사다리 전부를 각각 평가합니다.

    조건을 통과하지 못한(tradeable=False) 계획도 채점합니다. 매일 정해진 수를
    강도 순으로 내보내는 구조라 그런 종목도 실제로 추천에 들어가고, 강도가 낮은
    구간의 실제 성적을 모아야 강도 계산이 맞는지 검증할 수 있기 때문입니다.
    """
    if not bars:
        return {'symbol': plan['symbol'], 'setup': plan['setup'],
                'result': 'no_data', 'note': '해당일 분봉을 모으지 못했습니다'}
    entry, status, note = find_entry(bars, plan)
    if not entry:
        return {'symbol': plan['symbol'], 'setup': plan['setup'],
                'result': status, 'note': note}
    entry_price = entry['price']
    stop_price = entry_price * (1 - plan['stop_pct'] / 100)
    ladder = {}
    for target_pct in plan.get('target_ladder', TARGET_LADDER):
        target_price = entry_price * (1 + target_pct / 100)
        outcome = walk(bars, entry['index'], entry_price, stop_price, target_price)
        net = net_return_pct(entry_price, outcome['price'])
        ladder[str(target_pct)] = {
            'target_pct': target_pct,
            'result': outcome['result'],
            'exit_price': outcome['price'],
            'exit_at': outcome['at'],
            'ambiguous_bar': outcome['ambiguous'],
            'net_pct': net,
            'win': net is not None and net > 0,
        }
    return {
        'symbol': plan['symbol'],
        'setup': plan['setup'],
        'strength_level': (plan.get('strength') or {}).get('level'),
        'condition_met': bool(plan.get('tradeable')),
        'blocks': plan.get('blocks', []),
        'result': 'traded',
        'entry_price': entry_price,
        'entry_at': entry['at'],
        'range_high': entry['range']['high'],
        'range_low': entry['range']['low'],
        'stop_price': stop_price,
        'stop_pct': plan['stop_pct'],
        'cost_pct': round_trip_cost_pct(entry_price, entry_price),
        'ladder': ladder,
    }


def flatten_for_scoreboard(simulations, conditions_only=True):
    """셋업·목표 조합별 집계를 위해 거래 결과를 한 줄씩 폅니다.

    conditions_only=True면 조건을 통과한 거래만 셉니다. 셋업의 관문은 그 셋업의
    조건이 실제로 먹혔는지를 재는 값이라, 조건 미충족 거래가 섞이면 흐려집니다.
    강도 보정은 전부 필요하므로 False로 호출합니다.
    """
    rows = []
    for run in simulations:
        if run.get('result') != 'traded':
            continue
        if conditions_only and not run.get('condition_met', True):
            continue
        for entry in run['ladder'].values():
            rows.append({
                'setup': f"{run['setup']}@{entry['target_pct']}%",
                'result': entry['result'],
                'net_pct': entry['net_pct'] if entry['net_pct'] is not None else 0.0,
                'symbol': run['symbol'],
                'level': run.get('strength_level'),
                'win': entry['win'],
            })
    return rows


def summarize(simulations):
    """하루치 요약. 거래하지 않은 날도 정직하게 0건으로 남깁니다."""
    traded = [s for s in simulations if s.get('result') == 'traded']
    skipped = [s for s in simulations if s.get('result') != 'traded']
    per_target = {}
    for target_pct in TARGET_LADDER:
        key = str(target_pct)
        rows = [s['ladder'][key] for s in traded if key in s['ladder']]
        wins = [r for r in rows if r['win']]
        nets = [r['net_pct'] for r in rows if r['net_pct'] is not None]
        per_target[key] = {
            'target_pct': target_pct,
            'trades': len(rows),
            'wins': len(wins),
            'hit_rate_pct': 100 * len(wins) / len(rows) if rows else None,
            'mean_net_pct': sum(nets) / len(nets) if nets else None,
        }
    return {
        'traded_count': len(traded),
        'skipped_count': len(skipped),
        'per_target': per_target,
        'note': '손익은 수수료·거래세·슬리피지를 뺀 값입니다. 계좌 전체 수익률이 아니라 거래당 수익률입니다.',
    }
