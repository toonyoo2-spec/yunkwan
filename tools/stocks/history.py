#!/usr/bin/env python3
"""장 마감 후 데이터 축적.

왜 매일 모아야 하나:
  토스 캔들 API는 과거 분봉의 보관 범위에 제한이 있습니다. 오늘 모아두지 않은 날의
  분봉은 나중에 되돌려 받을 수 없습니다. 검증에 쓸 데이터는 오늘부터 쌓기 시작해야
  하므로, 이 작업은 전략을 완성하기 전에 먼저 돌려야 합니다.

모으는 것 (모두 조회 전용):
  - 유니버스 종목의 정규장 1분봉         → 진입·청산 체결 시뮬레이션의 재료
  - 코스피·코스닥 지수 1분봉             → 상대강도 계산의 기준
  - 투자자별 매매동향(외국인·기관·개인)  → 수급
  - 공매도 비중, 프로그램매매 순매수      → 수급 보조
  - 일봉                                  → 갭·변동성(ATR)·거래량 비교

저장 위치는 맥북 내부뿐입니다.
"""
import sys
from datetime import timedelta

import premarket
from store import STATE, read_json, write_json
from tossapi import Client, TossError, now

UNIVERSE_SIZE = 40          # 하루에 이력을 모을 종목 수. 호출 한도와 소요 시간의 절충.
FLOW_DAYS = 30              # 수급 시계열을 몇 거래일치 받아둘지
DAILY_CANDLE_COUNT = 120    # ATR·거래량 비교에 쓸 일봉 수
INDEX_SYMBOLS = ('KOSPI', 'KOSDAQ')


def history_path(symbol, date):
    return STATE / 'history' / symbol / f'{date}.json'


def index_path(symbol, date):
    return STATE / 'history' / '_index' / symbol / f'{date}.json'


def flow_path(symbol):
    return STATE / 'flows' / f'{symbol}.json'


def daily_path(symbol):
    return STATE / 'daily' / f'{symbol}.json'


def universe_path(date):
    return STATE / 'universe' / f'{date}.json'


def eligible(stock):
    """정리매매·거래정지·우선주를 걸러냅니다. toss_collect.eligible과 같은 기준입니다."""
    detail = stock.get('koreanMarketDetail') or {}
    return (stock.get('market') in ('KOSPI', 'KOSDAQ')
            and stock.get('securityType') == 'STOCK'
            and stock.get('isCommonShare') is True
            and stock.get('status') == 'ACTIVE'
            and detail.get('liquidationTrading') is False
            and detail.get('krxTradingSuspended') is False)


def build_universe(client, date):
    """그날 거래대금 상위에서 이력을 모을 종목을 고릅니다."""
    cached = read_json(universe_path(date))
    if cached:
        return cached
    ranking = client.rankings('MARKET_TRADING_AMOUNT', duration='1d', count=100)
    rows = ranking.get('rankings', [])
    if not rows:
        raise TossError('거래대금 순위가 비어 있어 유니버스를 만들지 못했습니다.')
    stocks = {s['symbol']: s for s in client.stocks([r['symbol'] for r in rows])}
    universe = []
    for row in rows:
        stock = stocks.get(row['symbol'])
        if not stock or not eligible(stock):
            continue
        universe.append({'symbol': row['symbol'], 'name': stock['name'],
                         'trading_amount': str(row.get('tradingAmount', '0'))})
        if len(universe) >= UNIVERSE_SIZE:
            break
    record = {'date': date, 'ranked_at': ranking.get('rankedAt'),
              'collected_at': now().isoformat(), 'symbols': universe}
    write_json(universe_path(date), record)
    return record


def collect_minutes(client, symbol, date, session):
    """정규장 1분봉을 모읍니다. 이미 모은 날은 다시 받지 않습니다."""
    path = history_path(symbol, date)
    if read_json(path):
        return 'cached'
    bars = client.candles_between(symbol, '1m', session['startTime'], session['endTime'])
    if not bars:
        return 'empty'
    write_json(path, {'symbol': symbol, 'date': date, 'collected_at': now().isoformat(),
                      'session': session, 'candles': bars})
    return f'{len(bars)}봉'


def collect_index(client, date, session):
    for symbol in INDEX_SYMBOLS:
        path = index_path(symbol, date)
        if read_json(path):
            continue
        bars = client.index_candles_between(symbol, '1m', session['startTime'], session['endTime'])
        if bars:
            write_json(path, {'symbol': symbol, 'date': date,
                              'collected_at': now().isoformat(), 'candles': bars})


def merge_records(existing, incoming, key='date'):
    """날짜 키로 병합합니다. 기존 기록을 바꾸지 않고 새 목록을 만듭니다."""
    merged = {row[key]: row for row in existing}
    for row in incoming:
        merged[row[key]] = row
    return [merged[k] for k in sorted(merged, reverse=True)]


def collect_flows(client, symbol):
    """수급·공매도·프로그램매매·대차·신용 시계열을 갱신합니다.

    대차잔고와 신용잔고는 전일까지 확정되므로 08:30 판단에 쓸 수 있습니다.
    (대차는 당일 저녁, 신용은 다음 영업일 새벽에 확정됩니다.)
    """
    stored = read_json(flow_path(symbol), {}) or {}
    sources = {
        'investor': client.investor_trading(symbol, count=FLOW_DAYS),
        'short_selling': client.short_selling(symbol, count=FLOW_DAYS),
        'program_trades': client.program_trades(symbol, count=FLOW_DAYS),
        'securities_lending': client.securities_lending(symbol, count=FLOW_DAYS),
        'credit_trades': client.credit_trades(symbol, count=FLOW_DAYS),
    }
    record = {'symbol': symbol, 'updated_at': now().isoformat()}
    for key, payload in sources.items():
        record[key] = merge_records(stored.get(key, []), (payload or {}).get('records', []))
    write_json(flow_path(symbol), record)


def collect_daily(client, symbol):
    """일봉을 갱신합니다. 갭·ATR·거래량 비교에 씁니다."""
    stored = read_json(daily_path(symbol), {}) or {}
    page = client.candles(symbol, interval='1d', count=DAILY_CANDLE_COUNT)
    bars = page.get('candles', [])
    if not bars:
        return
    existing = {row['timestamp']: row for row in stored.get('candles', [])}
    for row in bars:
        existing[row['timestamp']] = row
    ordered = [existing[k] for k in sorted(existing)][-DAILY_CANDLE_COUNT:]
    write_json(daily_path(symbol), {'symbol': symbol, 'updated_at': now().isoformat(),
                                    'candles': ordered})


def run(client, date=None):
    """장 마감 후 1회 실행. 휴장일이면 아무것도 하지 않습니다."""
    date = date or now().date().isoformat()
    session = client.regular_session(date)
    if not session:
        print('휴장일: 축적 생략', date)
        return
    universe = build_universe(client, date)
    collect_index(client, date, session)
    pre_session = client.pre_market_session(date)
    print(f'{date} 유니버스 {len(universe["symbols"])}개 — 분봉·수급 축적 시작')
    for entry in universe['symbols']:
        symbol = entry['symbol']
        try:
            status = collect_minutes(client, symbol, date, session)
            if pre_session:
                # 프리마켓 진입 가설을 나중에 채점하려면 이 구간도 오늘 모아둬야 합니다.
                premarket.collect_candles(client, symbol, date, pre_session)
            collect_flows(client, symbol)
            collect_daily(client, symbol)
            print(f'  {symbol} {entry["name"]}: {status}')
        except RuntimeError as exc:
            # 한 종목이 실패해도 나머지 축적을 멈추지 않습니다.
            print(f'  {symbol} {entry["name"]}: 건너뜀 ({exc})')
    print('축적 완료. 저장 위치는 맥북 내부뿐입니다.')


MAX_BACKFILL_DAYS = 400         # 달력일 기준 상한. 보관 한계에 먼저 부딪히면 그 전에 멈춥니다.
STOP_AFTER_EMPTY_DAYS = 3       # 거래일 연속 이만큼 비면 보관 범위 밖으로 보고 중단


def backfill(client, max_days=MAX_BACKFILL_DAYS):
    """과거 분봉을 받을 수 있는 데까지 전부 끌어옵니다.

    토스가 과거 1분봉을 얼마나 보관하는지는 문서에 수치가 없습니다. 그래서 하루씩
    거슬러 올라가며 실제로 받아보고, 거래일 기준으로 연속 3일이 비면 보관 한계에
    닿았다고 보고 멈춥니다.

    받아온 봉의 날짜가 요청한 날짜와 다르면 저장하지 않습니다. API가 보관 범위를
    벗어난 요청에 가장 오래된 봉을 돌려주는 경우, 그걸 그날 데이터로 착각해 저장하면
    검증이 통째로 오염되기 때문입니다.

    호출 간격이 1.1초라 오래 걸립니다. 이미 받은 날짜는 건너뛰므로 중단했다가 다시
    실행해도 이어서 진행됩니다.
    """
    today = now().date()
    universe = build_universe(client, today.isoformat())
    symbols = [entry['symbol'] for entry in universe['symbols']]
    filled = 0
    empty_streak = 0
    trading_days = 0
    for offset in range(1, max_days + 1):
        date = (today - timedelta(days=offset)).isoformat()
        session = client.regular_session(date)
        if not session:
            continue                    # 휴장일은 연속 실패로 세지 않습니다.
        trading_days += 1
        pre_session = client.pre_market_session(date)
        day_filled = 0
        for symbol in symbols:
            if read_json(history_path(symbol, date)):
                day_filled += 1
                continue
            try:
                bars = client.candles_between(symbol, '1m', session['startTime'],
                                              session['endTime'])
            except RuntimeError:
                continue
            if not bars or bars[0]['timestamp'][:10] != date:
                continue
            write_json(history_path(symbol, date),
                       {'symbol': symbol, 'date': date, 'collected_at': now().isoformat(),
                        'session': session, 'candles': bars, 'backfilled': True})
            if pre_session:
                premarket.collect_candles(client, symbol, date, pre_session)
            day_filled += 1
            filled += 1
        print(f'  {date}: {day_filled}/{len(symbols)}종목 확보 (누적 {filled}건)')
        empty_streak = empty_streak + 1 if day_filled == 0 else 0
        if empty_streak >= STOP_AFTER_EMPTY_DAYS:
            print(f'거래일 {empty_streak}일 연속으로 받지 못했습니다. 보관 한계로 보고 중단합니다.')
            break
    print(f'소급 수집 완료 — 거래일 {trading_days}일 시도, 분봉 {filled}건 확보.')
    if not filled:
        print('과거 분봉을 전혀 받지 못했습니다. 오늘부터 매일 쌓는 수밖에 없습니다.')
    print('주의: 프리마켓 호가 스프레드는 소급할 수 없습니다. 과거 호가 조회 API가 없어 오늘부터만 쌓입니다.')


def depth_probe(client, symbol='005930'):
    """분봉을 얼마나 과거까지 받을 수 있는지 1회 확인합니다. 첫 실행 때 참고용."""
    page = client.candles(symbol, interval='1m')
    bars = page.get('candles', [])
    if not bars:
        print('분봉 응답이 비어 있습니다.')
        return
    oldest, newest = min(b['timestamp'] for b in bars), max(b['timestamp'] for b in bars)
    print(f'{symbol} 1분봉 한 페이지: {len(bars)}봉, {oldest} ~ {newest}')
    print('nextBefore:', page.get('nextBefore'))
    print('과거로 더 받을 수 있는지는 nextBefore가 null이 아닌지로 판단합니다.')


if __name__ == '__main__':
    api = Client()
    command = sys.argv[1] if len(sys.argv) > 1 else 'run'
    try:
        if command == 'probe':
            depth_probe(api)
        elif command == 'backfill':
            backfill(api, max_days=int(sys.argv[2]) if len(sys.argv) > 2 else MAX_BACKFILL_DAYS)
        else:
            run(api)
    finally:
        api.save_archive()
