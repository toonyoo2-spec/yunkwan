#!/usr/bin/env python3
"""하루 목표 수익률 추적.

목표는 하루 +3%입니다. 이 파일은 그 목표를 설득하거나 반박하지 않고,
매일 실제로 달성했는지만 정직하게 기록합니다. 며칠 중 며칠 달성했는지가
쌓이면 그 자체가 답이 됩니다.

계좌 기준으로 잽니다. 종목 하나가 +3% 올라도 계좌의 20%만 넣었다면 계좌는
+0.6%입니다. 목표는 계좌 수익률이므로 투입 비중을 반영해야 의미가 있습니다.

복리 안내: 하루 +3%를 쉬지 않고 달성하면 20거래일에 원금의 1.8배,
250거래일에 1,600배가 됩니다. 이 숫자를 적어두는 이유는 목표를 꺾기 위해서가 아니라,
달성률이 몇 %일 때 무엇을 뜻하는지 판단할 기준을 남겨두기 위해서입니다.
"""
from datetime import date

from features import number
from signals import round_trip_cost_pct
from store import STATE, read_json, write_json
from tossapi import now

DAILY_TARGET_PCT = 3.0
GOAL_FILE = STATE / 'goal.json'
ACCOUNT_FILE = STATE / 'account.json'
DEFAULT_ACCOUNT_SIZE = 10_000_000       # 계좌 규모를 설정하지 않았을 때의 기본값(원)


def account_size():
    """계좌 규모. 계좌 기준 수익률을 내려면 필요합니다."""
    stored = read_json(ACCOUNT_FILE, {}) or {}
    return number(stored.get('account_size'), DEFAULT_ACCOUNT_SIZE) or DEFAULT_ACCOUNT_SIZE


def set_account_size(amount):
    write_json(ACCOUNT_FILE, {'account_size': amount, 'updated_at': now().isoformat()})


def position_value(position, price):
    """지금 팔면 손에 들어오는 금액과 투입 금액."""
    entry = number(position.get('entry_price'))
    quantity = number(position.get('quantity'), 1) or 1
    if not entry or not price:
        return None
    invested = entry * quantity
    gross = (price / entry - 1) * 100
    net_pct = gross - round_trip_cost_pct(entry, price)
    return {'invested': invested, 'net_pct': net_pct,
            'net_amount': invested * net_pct / 100}


def daily_snapshot(positions, prices, size=None):
    """오늘 시점의 계좌 기준 손익과 목표 달성 여부."""
    size = size or account_size()
    rows, invested_total, profit_total = [], 0.0, 0.0
    for position in positions:
        value = position_value(position, prices.get(position['symbol']))
        if not value:
            continue
        invested_total += value['invested']
        profit_total += value['net_amount']
        rows.append({'symbol': position['symbol'], 'name': position.get('name'),
                     'net_pct': value['net_pct']})
    account_pct = profit_total / size * 100 if size else None
    exposure_pct = invested_total / size * 100 if size else None
    return {
        'date': now().date().isoformat(),
        'checked_at': now().isoformat(),
        'target_pct': DAILY_TARGET_PCT,
        'account_return_pct': account_pct,
        'exposure_pct': exposure_pct,
        'position_count': len(rows),
        'achieved': account_pct is not None and account_pct >= DAILY_TARGET_PCT,
        'positions': rows,
        'shortfall_note': shortfall_note(account_pct, exposure_pct),
    }


def shortfall_note(account_pct, exposure_pct):
    """목표에 못 미쳤을 때, 무엇이 부족했는지 산수로 알려줍니다."""
    if account_pct is None or exposure_pct is None or exposure_pct <= 0:
        return '보유 종목이 없어 계좌 수익률을 계산할 수 없습니다.'
    if account_pct >= DAILY_TARGET_PCT:
        return f'목표 달성. 계좌 {account_pct:+.2f}% (투입 비중 {exposure_pct:.0f}%)'
    needed = DAILY_TARGET_PCT / (exposure_pct / 100)
    return (f'계좌 {account_pct:+.2f}% — 목표 {DAILY_TARGET_PCT}%에 미달. '
            f'지금 투입 비중 {exposure_pct:.0f}%로 목표를 채우려면 '
            f'보유 종목 평균 {needed:+.1f}%가 필요합니다.')


def record(snapshot):
    """하루치 기록을 누적합니다. 같은 날은 덮어씁니다."""
    history = read_json(GOAL_FILE, {'days': []}) or {'days': []}
    days = [row for row in history.get('days', []) if row['date'] != snapshot['date']]
    days.append(snapshot)
    days.sort(key=lambda row: row['date'])
    summary = achievement(days)
    write_json(GOAL_FILE, {'updated_at': now().isoformat(), 'days': days[-250:],
                           'summary': summary})
    return summary


def achievement(days):
    """달성률과 실제 복리 결과. 목표와 현실의 거리를 숫자로 남깁니다."""
    scored = [row for row in days if row.get('account_return_pct') is not None]
    if not scored:
        return {'days': 0, 'achieved_days': 0, 'achievement_rate_pct': None}
    achieved = sum(1 for row in scored if row['achieved'])
    compounded = 1.0
    for row in scored:
        compounded *= 1 + row['account_return_pct'] / 100
    ideal = (1 + DAILY_TARGET_PCT / 100) ** len(scored)
    return {
        'days': len(scored),
        'achieved_days': achieved,
        'achievement_rate_pct': 100 * achieved / len(scored),
        'actual_multiple': compounded,
        'target_multiple': ideal,
        'mean_daily_pct': sum(row['account_return_pct'] for row in scored) / len(scored),
        'note': (f'{len(scored)}일 중 {achieved}일 목표 달성. '
                 f'실제 누적 {(compounded - 1) * 100:+.1f}% vs '
                 f'매일 {DAILY_TARGET_PCT}% 달성 시 {(ideal - 1) * 100:+.0f}%'),
    }


def describe(snapshot, summary):
    lines = [f"하루 목표 {DAILY_TARGET_PCT}% — {'달성' if snapshot['achieved'] else '미달'}",
             f"  {snapshot['shortfall_note']}"]
    if summary.get('days'):
        lines.append(f"  누적: {summary['note']}")
    return '\n'.join(lines)
