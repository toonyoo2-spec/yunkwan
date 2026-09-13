#!/usr/bin/env python3
"""보유 종목 판단 — 계속 들고 갈 것인가, 팔고 갈아탈 것인가.

사용자가 사이트에서 입력한 실제 매수 기록을 읽어, 매일 아침 하나씩 판정합니다.

판단의 중심은 기회비용입니다.
  갈아타는 것은 공짜가 아닙니다. 팔고 사면 왕복 비용(수수료·거래세·슬리피지)을
  다시 냅니다. 그래서 "새 종목이 조금 더 좋아 보인다"만으로 팔면 비용만 쌓입니다.
  새 후보의 강도가 보유 종목보다 '교체 비용을 넘어설 만큼' 높아야 갈아탑니다.

판정 순서 (앞의 조건이 걸리면 뒤는 보지 않습니다):
  1. 손절 도달        → 매도. 예외 없습니다.
  2. 목표 도달        → 매도. 계획대로 이익 실현.
  3. 보유 기간 만료   → 매도. 근거가 유효한 기간을 넘겼습니다.
  4. 강도 붕괴        → 매도. 살 때의 근거가 사라졌습니다.
  5. 더 나은 대안     → 교체. 기회비용이 교체 비용을 넘을 때만.
  6. 그 외            → 보유.

현재가는 토스에서 받아 맥북 안에서만 씁니다. 사이트에는 판정과 비율(%)만 올라갑니다.
"""
import sys

import goal
import strength
from features import number
from signals import round_trip_cost_pct
from store import STATE, read_json, write_json
from tossapi import Client, now

POSITIONS_TABLE = 'stock_positions'
POSITIONS_FILE = STATE / 'positions.json'

MAX_HOLD_DAYS = 10          # 이 거래일을 넘기면 근거가 유효하지 않다고 봅니다
STRENGTH_COLLAPSE = 4       # 강도가 이 아래로 떨어지면 근거가 사라진 것으로 봅니다
SWITCH_STRENGTH_GAP = 2     # 갈아타려면 새 후보 강도가 이만큼은 높아야 합니다

VERDICT_HOLD = 'hold'
VERDICT_SELL = 'sell'
VERDICT_SWITCH = 'switch'


def trading_days_between(client, start_date, end_date):
    """두 날짜 사이의 거래일 수. 휴장일은 세지 않습니다."""
    from datetime import date, timedelta
    begin = date.fromisoformat(start_date)
    finish = date.fromisoformat(end_date)
    count = 0
    cursor = begin
    while cursor < finish:
        cursor += timedelta(days=1)
        if client.regular_session(cursor.isoformat()):
            count += 1
    return count


def current_price(client, symbol):
    """현재가. 맥북 안에서만 씁니다."""
    result = client.get_optional('/api/v1/prices', symbols=symbol)
    rows = result if isinstance(result, list) else (result or {}).get('prices', [])
    for row in rows or []:
        if row.get('symbol') == symbol:
            return number(row.get('lastPrice'))
    return None


def net_pnl_pct(entry_price, price):
    """비용을 뺀 현재 손익률. 지금 팔면 실제로 남는 값입니다."""
    if not entry_price or not price:
        return None
    gross = (price / entry_price - 1) * 100
    return gross - round_trip_cost_pct(entry_price, price)


def find_candidate(candidates, symbol):
    for row in candidates:
        if row['symbol'] == symbol:
            return row
    return None


def judge(position, price, candidates, best_alternative, held_days):
    """한 종목의 판정. 사유를 함께 돌려줍니다."""
    entry_price = number(position.get('entry_price'))
    pnl = net_pnl_pct(entry_price, price)
    gross_pct = ((price / entry_price - 1) * 100) if (entry_price and price) else None
    base = {'symbol': position['symbol'], 'name': position.get('name'),
            'held_days': held_days, 'net_pnl_pct': pnl}

    stop_pct = number(position.get('stop_pct'))
    target_pct = number(position.get('target_pct'))

    if stop_pct and gross_pct is not None and gross_pct <= -abs(stop_pct):
        return {**base, 'verdict': VERDICT_SELL, 'trigger': 'stop',
                'reason': f'손절선 -{abs(stop_pct):.1f}%에 닿았습니다. 예외 없이 정리합니다.'}

    if target_pct and gross_pct is not None and gross_pct >= target_pct:
        return {**base, 'verdict': VERDICT_SELL, 'trigger': 'target',
                'reason': f'목표 +{target_pct:.1f}%에 도달했습니다. 계획대로 실현합니다.'}

    if held_days >= MAX_HOLD_DAYS:
        return {**base, 'verdict': VERDICT_SELL, 'trigger': 'timeout',
                'reason': f'{MAX_HOLD_DAYS}거래일을 넘겼습니다. 살 때의 근거가 유효한 기간이 지났습니다.'}

    current = find_candidate(candidates, position['symbol'])
    level = (current or {}).get('strength', {}).get('level')
    base['current_strength'] = level

    if level is not None and level <= STRENGTH_COLLAPSE:
        return {**base, 'verdict': VERDICT_SELL, 'trigger': 'strength_collapse',
                'reason': f'현재 강도 {level}/10. 살 때의 근거가 사라졌습니다.'}

    if best_alternative and level is not None:
        gap = best_alternative['strength']['level'] - level
        cost = round_trip_cost_pct(entry_price, price) if entry_price and price else 0.2
        if gap >= SWITCH_STRENGTH_GAP:
            return {**base, 'verdict': VERDICT_SWITCH, 'trigger': 'better_option',
                    'switch_to': {'symbol': best_alternative['symbol'],
                                  'name': best_alternative.get('name'),
                                  'strength': best_alternative['strength']['level']},
                    'reason': (f'{best_alternative.get("name", best_alternative["symbol"])} '
                               f'강도 {best_alternative["strength"]["level"]} vs 보유 {level} '
                               f'({gap}단계 차이). 교체 비용 {cost:.2f}%를 감안해도 유리합니다.')}
        if level is not None:
            base['best_alternative'] = {
                'symbol': best_alternative['symbol'],
                'name': best_alternative.get('name'),
                'strength': best_alternative['strength']['level'],
            }

    reason = f'현재 강도 {level}/10.' if level is not None else '오늘 후보에 없어 강도를 재지 못했습니다.'
    if level is not None and best_alternative:
        reason += (f' 최고 대안({best_alternative["strength"]["level"]})과의 차이가'
                   f' {SWITCH_STRENGTH_GAP}단계 미만이라 교체 비용이 아깝습니다.')
    return {**base, 'verdict': VERDICT_HOLD, 'trigger': 'none', 'reason': reason}


def evaluate(client, positions, candidates):
    """보유 종목 전체를 판정합니다. candidates는 오늘 아침의 후보 목록입니다."""
    today = now().date().isoformat()
    ranked = [row for row in candidates if row.get('strength')]
    ranked.sort(key=lambda row: row['strength']['level'], reverse=True)
    held_symbols = {row['symbol'] for row in positions}
    best_alternative = next((row for row in ranked if row['symbol'] not in held_symbols), None)

    verdicts = []
    for position in positions:
        price = current_price(client, position['symbol'])
        held_days = trading_days_between(client, position['entry_date'], today)
        verdict = judge(position, price, candidates, best_alternative, held_days)
        verdict['id'] = position.get('id')
        verdict['decided_at'] = now().isoformat()
        verdicts.append(verdict)
    return verdicts


def summarize(verdicts):
    labels = {VERDICT_HOLD: '보유', VERDICT_SELL: '매도', VERDICT_SWITCH: '교체'}
    lines = []
    for row in verdicts:
        pnl = f"{row['net_pnl_pct']:+.2f}%" if row.get('net_pnl_pct') is not None else '—'
        lines.append(f"  [{labels.get(row['verdict'], row['verdict'])}] "
                     f"{row.get('name') or row['symbol']} · 보유 {row['held_days']}일 "
                     f"· 비용 차감 {pnl}\n      {row['reason']}")
    return '\n'.join(lines) if lines else '  보유 종목이 없습니다.'


def run(client, candidates=None):
    """사이트에서 보유 종목을 읽어 판정하고, 결과를 다시 사이트로 돌려줍니다."""
    import publish
    positions = publish.fetch_open_positions()
    if not positions:
        print('보유 종목이 없습니다.')
        write_json(POSITIONS_FILE, {'checked_at': now().isoformat(), 'verdicts': []})
        return []
    if candidates is None:
        date = now().date().isoformat()
        prepared = read_json(STATE / 'prepared' / f'{date}.json', {}) or {}
        candidates = prepared.get('candidates', [])
    verdicts = evaluate(client, positions, candidates)
    write_json(POSITIONS_FILE, {'checked_at': now().isoformat(), 'verdicts': verdicts})
    print(f'보유 종목 {len(verdicts)}건 판정:')
    print(summarize(verdicts))

    # 하루 목표(3%) 달성 여부를 계좌 기준으로 기록합니다.
    prices = {row['symbol']: current_price(client, row['symbol']) for row in positions}
    snapshot = goal.daily_snapshot(positions, prices)
    summary = goal.record(snapshot)
    print()
    print(goal.describe(snapshot, summary))

    publish.push_position_verdicts(verdicts)
    return verdicts


if __name__ == '__main__':
    api = Client()
    try:
        run(api)
    except RuntimeError as exc:
        print('보유 종목 판정 실패:', exc)
        sys.exit(1)
    finally:
        api.save_archive()
