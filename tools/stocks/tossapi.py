#!/usr/bin/env python3
"""토스 Open API 공용 클라이언트.

조회 전용입니다. 주문 관련 엔드포인트는 이 파일에 존재하지 않습니다.
- 토큰은 캐시해서 재사용합니다. 같은 키로 새 토큰을 발급하면 기존 토큰이 즉시
  무효화되므로, 동시에 여러 실행이 붙지 않도록 daily_runner가 파일 락을 겁니다.
- 호출 한도(429)는 지수 백오프로 재시도합니다.
- 응답 원본은 호출자가 모아서 저장할 수 있게 archive에 쌓아둡니다.
"""
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from toss_connect import request
from store import STATE, read_json, write_json

KST = timezone(timedelta(hours=9))

CONFIG = STATE / 'config.json'
TOKEN_CACHE = STATE / 'toss_token.json'

MIN_INTERVAL_SEC = 1.1      # 호출 간 최소 간격. 한도 정책이 공개 수치가 아니라 보수적으로 잡습니다.
MAX_RETRY = 4
BACKOFF_BASE_SEC = 2.0
TOKEN_MARGIN_SEC = 120      # 만료 직전 토큰을 재사용하지 않기 위한 여유
MAX_CANDLE_COUNT = 200      # API 1회 응답 상한
MAX_PAGES = 40              # 무한 페이지네이션 방지


def now():
    return datetime.now(KST)


def parse_time(value):
    """ISO 8601 시각 문자열을 파싱합니다.

    macOS 기본 파이썬(3.9)의 fromisoformat은 'Z' 접미사와 일부 소수점 자리수를
    처리하지 못합니다. API 응답 형식이 바뀌었을 때 조용히 터지지 않도록 정규화합니다.
    """
    text = str(value).strip()
    if text.endswith(('Z', 'z')):
        text = text[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        # 소수점 자리수가 3·6자리가 아닌 경우를 잘라냅니다.
        head, _, tail = text.partition('.')
        offset = ''
        for marker in ('+', '-'):
            position = tail.find(marker)
            if position > 0:
                offset = tail[position:]
                break
        return datetime.fromisoformat(head + offset)


class TossError(RuntimeError):
    pass


class Client:
    """조회 전용 토스 API 클라이언트."""

    def __init__(self):
        config = read_json(CONFIG)
        if not config:
            raise TossError('초기 설정이 필요합니다. `python3 daily_runner.py setup`을 실행하세요.')
        self._config = config
        self._token = None
        self._last_call = 0.0
        self.archive = []

    # ---------- 인증 ----------

    def _access_token(self):
        if self._token:
            return self._token
        cached = read_json(TOKEN_CACHE)
        if cached and cached.get('expires_at', 0) > time.time() + TOKEN_MARGIN_SEC:
            self._token = cached['access_token']
            return self._token
        issued = request('/oauth2/token', form={
            'grant_type': 'client_credentials',
            'client_id': self._config['client_id'],
            'client_secret': self._config['client_secret'],
        })
        token = issued.get('access_token')
        if not isinstance(token, str) or not token:
            raise TossError('인증 응답에서 토큰을 확인하지 못했습니다.')
        self._token = token
        write_json(TOKEN_CACHE, {'access_token': token,
                                 'expires_at': time.time() + int(issued['expires_in'])})
        return token

    # ---------- 호출 ----------

    def _throttle(self):
        elapsed = time.monotonic() - self._last_call
        if elapsed < MIN_INTERVAL_SEC:
            time.sleep(MIN_INTERVAL_SEC - elapsed)
        self._last_call = time.monotonic()

    def get(self, path, **params):
        """조회 1건. 실패한 호출은 archive에 남기지 않습니다."""
        query = path + ('?' + urlencode(params) if params else '')
        for attempt in range(MAX_RETRY):
            self._throttle()
            try:
                response = request(query, token=self._access_token())
            except RuntimeError as exc:
                if '한도' not in str(exc) or attempt == MAX_RETRY - 1:
                    raise
                time.sleep(BACKOFF_BASE_SEC * (2 ** attempt))
                continue
            if 'result' not in response:
                raise TossError(f'{path} 응답에 result가 없습니다.')
            self.archive.append({'path': path, 'params': params,
                                 'received_at': now().isoformat(), 'result': response['result']})
            return response['result']
        raise TossError(f'{path} 호출이 한도 재시도 후에도 실패했습니다.')

    def get_optional(self, path, **params):
        """없어도 되는 보조 데이터용. 실패하면 None을 돌려주고 진행합니다."""
        try:
            return self.get(path, **params)
        except RuntimeError:
            return None

    # ---------- 시장 공통 ----------

    def calendar(self, date):
        return self.get('/api/v1/market-calendar/KR', date=date)['today']

    def regular_session(self, date):
        """해당 날짜의 정규장 세션. 휴장일이면 None."""
        calendar = self.get_cached_calendar(date)
        return (calendar.get('integrated') or {}).get('regularMarket')

    def pre_market_session(self, date):
        """NXT 프리마켓 세션(08:00~). 시각을 추측하지 않고 공식 캘린더 값을 씁니다."""
        calendar = self.get_cached_calendar(date)
        return (calendar.get('integrated') or {}).get('preMarket')

    def get_cached_calendar(self, date):
        cache = STATE / 'calendars' / f'{date}.json'
        cached = read_json(cache)
        if cached:
            return cached
        calendar = self.calendar(date)
        write_json(cache, calendar)
        return calendar

    def rankings(self, ranking_type, duration='1d', count=100):
        return self.get('/api/v1/rankings', type=ranking_type, marketCountry='KR',
                        duration=duration, excludeInvestmentCaution='true', count=count)

    def stocks(self, symbols):
        return self.get('/api/v1/stocks', symbols=','.join(symbols))

    # ---------- 종목 데이터 ----------

    def candles(self, symbol, interval='1m', count=MAX_CANDLE_COUNT, before=None):
        params = {'symbol': symbol, 'interval': interval, 'count': count, 'adjusted': 'false'}
        if before:
            params['before'] = before
        return self.get('/api/v1/candles', **params)

    def candles_between(self, symbol, interval, start, end):
        """[start, end] 구간의 봉을 오래된 순으로 모읍니다. 응답은 최신순이라 뒤집어 담습니다."""
        collected, before = {}, end
        for _ in range(MAX_PAGES):
            page = self.candles(symbol, interval=interval, before=before)
            rows = page.get('candles', [])
            if not rows:
                break
            for row in rows:
                collected[row['timestamp']] = row
            oldest = min(row['timestamp'] for row in rows)
            if oldest <= start or not page.get('nextBefore'):
                break
            before = page['nextBefore']
        return [collected[key] for key in sorted(collected) if start <= key <= end]

    def index_candles(self, symbol, interval='1m', count=MAX_CANDLE_COUNT, before=None):
        params = {'symbol': symbol, 'interval': interval, 'count': count}
        if before:
            params['before'] = before
        return self.get(f'/api/v1/market-indicators/{symbol}/candles', **params)

    def index_candles_between(self, symbol, interval, start, end):
        collected, before = {}, end
        for _ in range(MAX_PAGES):
            page = self.index_candles(symbol, interval=interval, before=before)
            rows = page.get('candles', [])
            if not rows:
                break
            for row in rows:
                collected[row['timestamp']] = row
            oldest = min(row['timestamp'] for row in rows)
            if oldest <= start or not page.get('nextBefore'):
                break
            before = page['nextBefore']
        return [collected[key] for key in sorted(collected) if start <= key <= end]

    def investor_trading(self, symbol, count=30, until=None):
        params = {'count': count}
        if until:
            params['until'] = until
        return self.get(f'/api/v1/stocks/{symbol}/investor-trading', **params)

    def short_selling(self, symbol, count=30, until=None):
        params = {'count': count}
        if until:
            params['until'] = until
        return self.get_optional(f'/api/v1/stocks/{symbol}/short-selling', **params)

    def program_trades(self, symbol, count=30, until=None):
        params = {'count': count}
        if until:
            params['until'] = until
        return self.get_optional(f'/api/v1/stocks/{symbol}/program-trades', **params)

    def securities_lending(self, symbol, count=30, until=None):
        """대차거래 동향. 기관이 주식을 빌린 잔고이며 공매도의 선행지표입니다."""
        params = {'count': count}
        if until:
            params['until'] = until
        return self.get_optional(f'/api/v1/stocks/{symbol}/securities-lending', **params)

    def credit_trades(self, symbol, count=30, until=None):
        """신용거래 동향. 신용융자 잔고가 크면 급락 시 반대매매로 하락이 증폭됩니다."""
        params = {'count': count}
        if until:
            params['until'] = until
        return self.get_optional(f'/api/v1/stocks/{symbol}/credit-trades', **params)

    def warnings(self, symbol):
        return self.get_optional(f'/api/v1/stocks/{symbol}/warnings') or []

    def orderbook(self, symbol):
        return self.get_optional('/api/v1/orderbook', symbol=symbol)

    def price_limits(self, symbol):
        return self.get_optional('/api/v1/price-limits', symbols=symbol)

    def commissions(self):
        return self.get_optional('/api/v1/commissions')

    # ---------- 마무리 ----------

    def save_archive(self):
        """이번 실행에서 받은 원본 응답을 맥북 안에만 남깁니다."""
        if not self.archive:
            return
        stamp = now().strftime('%Y%m%d_%H%M%S_%f')
        write_json(STATE / 'raw' / f'{stamp}.json', self.archive)
