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
import fcntl
import sys
from datetime import timedelta

import premarket
from store import STATE, read_json, write_json
from tossapi import Client, TossError, now

UNIVERSE_SIZE = 40          # 하루에 이력을 모을 종목 수. 호출 한도와 소요 시간의 절충.
FLOW_DAYS = 30              # 수급 시계열을 몇 거래일치 받아둘지
DAILY_CANDLE_COUNT = 200    # API 1회 응답 상한
DAILY_KEEP = 400            # 보관할 일봉 수.
# 매일 받은 것으로 덮어쓰면서 이 길이로 자르면, 소급 수집해둔 과거가 아침마다
# 지워집니다. 실제로 그래서 300일치가 120일치로 잘려 검증이 불가능해졌습니다.
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
                         'market': stock['market'],
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


DEEP_FLOW_DAYS = 300        # 분봉과 같은 깊이로 맞춥니다
FLOW_PAGE = 100             # API 1회 응답 상한
DEEP_DAILY_COUNT = 200      # 일봉 1회 응답 상한
MAX_FLOW_PAGES = 5


def paginate_flows(fetch, days=DEEP_FLOW_DAYS):
    """until 페이지네이션으로 과거까지 거슬러 모읍니다.

    수급·공매도·대차·신용은 1회 100건이 상한이라, 분봉과 같은 깊이(약 266거래일)를
    맞추려면 여러 번 나눠 받아야 합니다. 이게 없으면 과거 구간의 특징이 통째로 비어
    조건이 아예 통과하지 못하고, 검증이 무의미해집니다.
    """
    collected, until = {}, None
    for _ in range(MAX_FLOW_PAGES):
        page = fetch(FLOW_PAGE, until)
        rows = (page or {}).get('records', [])
        if not rows:
            break
        for row in rows:
            collected[row['date']] = row
        if len(collected) >= days:
            break
        next_until = (page or {}).get('nextUntil')
        if not next_until or next_until == until:
            break
        until = next_until
    return [collected[key] for key in sorted(collected, reverse=True)][:days]


def collect_deep_daily(client, symbol, count=300):
    """일봉을 before 페이지네이션으로 깊게 받습니다."""
    stored = read_json(daily_path(symbol), {}) or {}
    merged = {row['timestamp']: row for row in stored.get('candles', [])}
    before = None
    for _ in range(4):
        params = {'symbol': symbol, 'interval': '1d',
                  'count': DEEP_DAILY_COUNT, 'adjusted': 'false'}
        if before:
            params['before'] = before
        page = client.get_optional('/api/v1/candles', **params)
        rows = (page or {}).get('candles', [])
        if not rows:
            break
        for row in rows:
            merged[row['timestamp']] = row
        if len(merged) >= count or not (page or {}).get('nextBefore'):
            break
        before = page['nextBefore']
    ordered = [merged[key] for key in sorted(merged)][-count:]
    if ordered:
        write_json(daily_path(symbol), {'symbol': symbol, 'updated_at': now().isoformat(),
                                        'candles': ordered})
    return len(ordered)


def collect_deep_flows(client, symbol):
    """수급 계열 전부를 분봉과 같은 깊이로 맞춥니다."""
    stored = read_json(flow_path(symbol), {}) or {}
    sources = {
        'investor': lambda n, u: client.investor_trading(symbol, count=n, until=u),
        'short_selling': lambda n, u: client.short_selling(symbol, count=n, until=u),
        'program_trades': lambda n, u: client.program_trades(symbol, count=n, until=u),
        'securities_lending': lambda n, u: client.securities_lending(symbol, count=n, until=u),
        'credit_trades': lambda n, u: client.credit_trades(symbol, count=n, until=u),
    }
    record = {'symbol': symbol, 'updated_at': now().isoformat()}
    depths = {}
    for key, fetch in sources.items():
        rows = paginate_flows(fetch)
        record[key] = merge_records(stored.get(key, []), rows)
        depths[key] = len(record[key])
    write_json(flow_path(symbol), record)
    return depths


def deepen(client):
    """분봉과 기준 데이터의 깊이를 맞춥니다.

    분봉은 266거래일치가 있는데 수급이 30일치뿐이면, 나머지 236일은 특징을 만들 수 없어
    채점에서 통째로 빠집니다. 그 상태의 검증 결과는 의미가 없습니다.
    """
    universe = read_json(universe_path(now().date().isoformat()))
    if not universe:
        latest = sorted((STATE / 'universe').glob('*.json'))
        universe = read_json(latest[-1], {}) if latest else None
    if not universe:
        print('유니버스 기록이 없습니다.')
        return
    symbols = universe['symbols']
    print(f'기준 데이터 심화 수집: 종목 {len(symbols)}개', flush=True)
    collect_index_daily(client)
    for position, entry in enumerate(symbols, start=1):
        symbol = entry['symbol']
        try:
            bars = collect_deep_daily(client, symbol)
            depths = collect_deep_flows(client, symbol)
            print(f'  [{position}/{len(symbols)}] {symbol} {entry.get("name", "")}: '
                  f'일봉 {bars}개 · 수급 {depths.get("investor", 0)}일', flush=True)
        except RuntimeError as exc:
            print(f'  {symbol}: 건너뜀 ({exc})', flush=True)
    print('심화 수집 완료.', flush=True)


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
    # 기존 이력을 보존한 채 병합만 합니다. 짧게 자르면 소급 수집분이 날아갑니다.
    ordered = [existing[k] for k in sorted(existing)][-DAILY_KEEP:]
    write_json(daily_path(symbol), {'symbol': symbol, 'updated_at': now().isoformat(),
                                    'candles': ordered})


def collect_index_daily(client, count=DAILY_KEEP):
    """상대강도 기준이 되는 지수 일봉. 종목과 다른 엔드포인트라 따로 받습니다.

    1회 응답이 200개로 제한되므로 before 페이지네이션으로 이어 받습니다.
    한 번만 받으면 200일치뿐이라 그보다 과거 구간의 상대강도를 계산할 수 없고,
    그러면 그 기간 전체가 검증에서 빠집니다.
    """
    for symbol in INDEX_SYMBOLS:
        path = STATE / 'daily' / f'_{symbol}.json'
        stored = read_json(path, {}) or {}
        merged = {row['timestamp']: row for row in stored.get('candles', [])}
        before = None
        for _ in range(4):
            params = {'interval': '1d', 'count': DAILY_CANDLE_COUNT}
            if before:
                params['before'] = before
            page = client.get_optional(f'/api/v1/market-indicators/{symbol}/candles', **params)
            rows = (page or {}).get('candles', [])
            if not rows:
                break
            for row in rows:
                merged[row['timestamp']] = row
            if len(merged) >= count or not (page or {}).get('nextBefore'):
                break
            before = page['nextBefore']
        ordered = [merged[key] for key in sorted(merged)][-count:]
        if ordered:
            write_json(path, {'symbol': symbol, 'updated_at': now().isoformat(),
                              'candles': ordered})
            print(f'  지수 {symbol}: 일봉 {len(ordered)}개 '
                  f'({ordered[0]["timestamp"][:10]} ~ {ordered[-1]["timestamp"][:10]})', flush=True)


def collect_reference(client):
    """일봉·수급·지수 일봉을 모읍니다. 휴장일에도 돌아갑니다.

    소급 수집(backfill)은 분봉만 받습니다. 특징 계산(ATR·거래량 추세·상대강도·수급)에는
    일봉과 수급 시계열이 필요하므로, 채점 전에 이 단계를 한 번 돌려야 합니다.
    """
    universe = read_json(universe_path(now().date().isoformat()))
    if not universe:
        latest = sorted((STATE / 'universe').glob('*.json'))
        universe = read_json(latest[-1], {}) if latest else None
    if not universe:
        universe = build_universe(client, now().date().isoformat())
    symbols = universe['symbols']
    print(f'기준 데이터 수집: 종목 {len(symbols)}개', flush=True)
    collect_index_daily(client)
    for entry in symbols:
        symbol = entry['symbol']
        try:
            collect_daily(client, symbol)
            collect_flows(client, symbol)
            print(f'  {symbol} {entry.get("name", "")}: 일봉·수급 완료', flush=True)
        except RuntimeError as exc:
            print(f'  {symbol}: 건너뜀 ({exc})', flush=True)
    print('기준 데이터 수집 완료.', flush=True)


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
            # 프리마켓은 소급하지 않습니다. 과거 호가 조회 API가 없어 스프레드를 알 수 없고,
            # 스프레드 없는 프리마켓 분봉만으로는 그 가설을 채점할 수 없기 때문입니다.
            # 호출 수를 절반 가까이 줄여 정규장 축적을 훨씬 빨리 끝내는 쪽을 택했습니다.
            day_filled += 1
            filled += 1
        print(f'  {date}: {day_filled}/{len(symbols)}종목 확보 (누적 {filled}건)', flush=True)
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
    # 예약 작업(daily_runner)과 같은 락을 씁니다. 같은 키로 토큰을 새로 발급하면
    # 상대 실행의 토큰이 무효화되므로, 동시에 돌지 않도록 막아야 합니다.
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'runner.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('다른 수집 실행이 진행 중입니다. 끝난 뒤 다시 실행하세요.')
            sys.exit(1)
        api = Client()
        command = sys.argv[1] if len(sys.argv) > 1 else 'run'
        try:
            if command == 'probe':
                depth_probe(api)
            elif command == 'backfill':
                backfill(api, max_days=int(sys.argv[2]) if len(sys.argv) > 2 else MAX_BACKFILL_DAYS)
            elif command == 'reference':
                collect_reference(api)
            elif command == 'deepen':
                deepen(api)
            else:
                run(api)
        finally:
            api.save_archive()
