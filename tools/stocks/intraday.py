#!/usr/bin/env python3
"""장중 신호 감시. 5분마다 실행합니다.

08:30 계획은 "조건이 나오면 산다"까지만 정합니다. 실제로 언제 그 조건이 나왔는지는
장중에 봐야 알 수 있고, 그 시각이 곧 진짜 추천 시각입니다. 이 파일이 그걸 기록합니다.

장 시간이 아니면 아무것도 하지 않고 바로 끝납니다. 하루 종일 5분마다 돌려도 됩니다.

여기서도 주문은 하지 않습니다. 신호를 기록만 하고 매매는 사람이 직접 합니다.
"""
import sys
from datetime import datetime

import premarket
from features import number, opening_range
from signals import SLIPPAGE_TICKS, tick_size
from store import STATE, read_json, write_json
from tossapi import Client, now, parse_time

LIVE_DIR = STATE / 'live'
OPENING_RANGE_READY = '09:31'   # 이 시각 전에는 레인지가 확정되지 않습니다
TERMINAL_STATES = {'stopped', 'target_hit'}  # 이후 봉을 다시 봐도 바뀌지 않는 상태


def live_path(date):
    return LIVE_DIR / f'{date}.json'


def in_session(session, moment):
    start = parse_time(session['startTime'])
    end = parse_time(session['endTime'])
    return start <= moment <= end


def minute_bars_today(client, symbol, session):
    """오늘 정규장 시작부터 지금까지의 1분봉."""
    return client.candles_between(symbol, '1m', session['startTime'],
                                  now().isoformat())


def detect(plan, bars):
    """돌파가 이미 일어났는지 확인합니다. 일어났으면 그 시각과 체결 추정가를 돌려줍니다."""
    minutes = plan.get('opening_range_minutes', 30)
    if len(bars) <= minutes:
        return {'state': 'range_forming', 'note': '개장 레인지 형성 중'}
    window = opening_range(bars, minutes)
    if not window:
        return {'state': 'no_data', 'note': '레인지를 계산할 봉이 없습니다'}
    for bar in bars[minutes:]:
        high = number(bar['highPrice'])
        if high is not None and high > window['high']:
            fill = min(window['high'] + SLIPPAGE_TICKS * tick_size(window['high']), high)
            return {
                'state': 'triggered',
                'triggered_at': bar['timestamp'],
                'entry_price': fill,
                'stop_price': fill * (1 - plan['stop_pct'] / 100),
                'target_price': fill * (1 + plan.get('target_pct', 1.0) / 100),
                'note': '돌파 발생 — 계획대로라면 이 시점이 진입 시각입니다',
            }
    return {'state': 'waiting', 'range_high': window['high'],
            'note': '아직 개장 레인지 고가를 넘지 못했습니다'}


def current_status(plan, bars, signal):
    """진입 이후 목표·손절에 닿았는지 현재까지의 봉으로 확인합니다."""
    if signal.get('state') != 'triggered':
        return signal
    after = [bar for bar in bars if bar['timestamp'] > signal['triggered_at']]
    for bar in after:
        low, high = number(bar['lowPrice']), number(bar['highPrice'])
        if low is not None and low <= signal['stop_price']:
            return {**signal, 'state': 'stopped', 'closed_at': bar['timestamp'],
                    'note': '손절가에 닿았습니다'}
        if high is not None and high >= signal['target_price']:
            return {**signal, 'state': 'target_hit', 'closed_at': bar['timestamp'],
                    'note': '목표가에 닿았습니다'}
    return {**signal, 'state': 'open', 'note': '진입 조건 충족 후 보유 구간'}


def run(client):
    date = now().date().isoformat()
    session = client.regular_session(date)
    if not session:
        return
    moment = now()
    forecast = read_json(STATE / 'reports' / f'{date}-forecast.json')
    if not forecast:
        return
    pre = client.pre_market_session(date)
    if pre and in_session(pre, moment):
        # 프리마켓에서는 호가 스프레드를 재는 것이 전부입니다. 아직 추천하지 않습니다.
        symbols = [p['symbol'] for p in forecast.get('candidates', [])[:20]]
        samples = premarket.observe(client, symbols, date)
        print(f'프리마켓 호가 {len(samples)}건 기록 — 스프레드가 엣지보다 큰지 보려는 목적입니다.')
        return
    if not in_session(session, moment):
        return
    plans = [p for p in forecast.get('recommendations', []) if p.get('tradeable')]
    if not plans:
        print('오늘은 추천이 0건이라 감시할 대상이 없습니다.')
        return
    # 직전 판정에서 이미 손절·목표에 닿은 종목은 그 결과가 봉을 더 봐도 안 바뀝니다.
    # 다시 분봉을 받아 똑같은 결과를 매번 찍는 대신, 그 기록을 그대로 이어갑니다.
    previous = {row['symbol']: row for row in
                (read_json(live_path(date), {}) or {}).get('watching', [])}

    watching = []
    resolved = 0
    for plan in plans:
        prior = previous.get(plan['symbol'])
        if prior and prior.get('state') in TERMINAL_STATES:
            watching.append(prior)
            resolved += 1
            continue
        bars = minute_bars_today(client, plan['symbol'], session)
        signal = current_status(plan, bars, detect(plan, bars))
        watching.append({'symbol': plan['symbol'], 'name': plan.get('name'),
                         'setup': plan['setup'], 'target_pct': plan.get('target_pct'),
                         'stop_pct': plan.get('stop_pct'), **signal})
        print(f"  {plan['symbol']} {plan.get('name')}: {signal['state']} — {signal['note']}")
    if resolved:
        print(f'  (이미 종료된 {resolved}종목은 다시 확인하지 않았습니다)')
    write_json(live_path(date), {'trade_date': date, 'checked_at': moment.isoformat(),
                                 'watching': watching})


if __name__ == '__main__':
    api = Client()
    try:
        run(api)
    except RuntimeError as exc:
        print('장중 감시 실패:', exc)
        sys.exit(1)
    finally:
        api.save_archive()
