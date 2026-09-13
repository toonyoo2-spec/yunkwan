#!/usr/bin/env python3
"""공시·뉴스 수집.

출처 (둘 다 공식 무료 API, 크롤링 아님):
  - 금융감독원 OpenDART — 공시 목록과 원문 링크. https://opendart.fss.or.kr
  - 네이버 뉴스 검색 API — 기사 제목·링크·요약. https://developers.naver.com

검증에서 가장 중요한 규칙:
  판단 시각 이전에 '게시된' 것만 씁니다. 게시 시각(published_at)과 우리가 받은 시각
  (collected_at)을 따로 저장하는 이유입니다. 이걸 섞으면 08:30에 알 수 없었던 뉴스로
  08:30 판단을 평가하게 되어 결과가 통째로 거짓이 됩니다.

왜 AI 감성분석을 쓰지 않는가:
  같은 기사에 같은 점수가 다시 나온다는 보장이 없으면 과거 검증이 재현되지 않습니다.
  분류는 키워드 규칙으로 하고, AI는 사람이 읽을 요약에만 씁니다.

키가 없으면 이 모듈은 조용히 비활성화되고 나머지 파이프라인은 그대로 돌아갑니다.
"""
import io
import json
import re
import zipfile
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from store import STATE, read_json, write_json
from tossapi import KST, now

KEYS = STATE / 'news_keys.json'
CORP_MAP = STATE / 'dart_corp_codes.json'
NEWS_DIR = STATE / 'news'
TIMEOUT_SEC = 20
CORP_MAP_MAX_AGE_DAYS = 30
NAVER_DISPLAY = 20

DART_LIST = 'https://opendart.fss.or.kr/api/list.json'
DART_CORP = 'https://opendart.fss.or.kr/api/corpCode.xml'
NAVER_NEWS = 'https://openapi.naver.com/v1/search/news.json'

# 단타 관점에서 방향이 분명한 공시만 분류합니다. 애매한 것은 'other'로 둡니다.
POSITIVE_PATTERNS = (
    (r'단일판매|공급계약', 'supply_contract'),
    (r'자기주식.*취득|자사주.*취득', 'buyback'),
    (r'무상증자', 'bonus_issue'),
    (r'영업(잠정)?실적.*(상향|흑자)', 'earnings_up'),
)
NEGATIVE_PATTERNS = (
    (r'유상증자', 'rights_issue'),
    (r'전환사채|신주인수권부사채|교환사채', 'convertible_bond'),
    (r'감자', 'capital_reduction'),
    (r'횡령|배임', 'embezzlement'),
    (r'감사의견.*거절|상장폐지', 'delisting_risk'),
    (r'자기주식.*처분', 'treasury_disposal'),
)


def load_keys():
    return read_json(KEYS, {}) or {}


def enabled(source):
    return bool(load_keys().get(source))


def fetch_json(url, headers=None):
    request = Request(url, headers=headers or {})
    try:
        with urlopen(request, timeout=TIMEOUT_SEC) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


# ---------- OpenDART ----------

def refresh_corp_codes(api_key):
    """종목코드 → DART 고유번호 매핑. 월 1회만 새로 받습니다."""
    cached = read_json(CORP_MAP)
    if cached:
        age = now() - datetime.fromisoformat(cached['updated_at'])
        if age < timedelta(days=CORP_MAP_MAX_AGE_DAYS):
            return cached['map']
    request = Request(f'{DART_CORP}?{urlencode({"crtfc_key": api_key})}')
    try:
        with urlopen(request, timeout=60) as response:
            payload = response.read()
    except (HTTPError, URLError, TimeoutError):
        return (cached or {}).get('map', {})
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        root = ElementTree.fromstring(archive.read(archive.namelist()[0]))
    except (zipfile.BadZipFile, ElementTree.ParseError, IndexError):
        return (cached or {}).get('map', {})
    mapping = {}
    for item in root.iter('list'):
        stock_code = (item.findtext('stock_code') or '').strip()
        corp_code = (item.findtext('corp_code') or '').strip()
        if len(stock_code) == 6 and corp_code:
            mapping[stock_code] = corp_code
    if mapping:
        write_json(CORP_MAP, {'updated_at': now().isoformat(), 'map': mapping})
    return mapping or (cached or {}).get('map', {})


def classify(title):
    for pattern, label in NEGATIVE_PATTERNS:
        if re.search(pattern, title):
            return 'negative', label
    for pattern, label in POSITIVE_PATTERNS:
        if re.search(pattern, title):
            return 'positive', label
    return 'neutral', 'other'


def disclosures(symbol, start_date, end_date):
    """기간 내 공시 목록. 키가 없으면 빈 목록을 돌려줍니다."""
    api_key = load_keys().get('dart')
    if not api_key:
        return []
    corp_code = refresh_corp_codes(api_key).get(symbol)
    if not corp_code:
        return []
    url = DART_LIST + '?' + urlencode({
        'crtfc_key': api_key, 'corp_code': corp_code,
        'bgn_de': start_date.replace('-', ''), 'end_de': end_date.replace('-', ''),
        'page_count': 100,
    })
    payload = fetch_json(url)
    if not payload or payload.get('status') != '000':
        return []
    rows = []
    for item in payload.get('list', []):
        title = item.get('report_nm', '')
        direction, label = classify(title)
        rows.append({
            'title': title,
            'direction': direction,
            'label': label,
            # rcept_dt는 접수일자(YYYYMMDD)입니다. 시각까지는 제공되지 않아 하루 단위로만 씁니다.
            'published_date': item.get('rcept_dt'),
            'url': f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item.get('rcept_no')}",
            'collected_at': now().isoformat(),
        })
    return rows


# ---------- 네이버 뉴스 ----------

def parse_rfc822(value):
    try:
        return datetime.strptime(value, '%a, %d %b %Y %H:%M:%S %z').astimezone(KST)
    except (ValueError, TypeError):
        return None


def strip_tags(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()


def news(query, cutoff=None):
    """최근 뉴스. cutoff 이후에 게시된 기사는 버립니다 (미래 정보 차단)."""
    keys = load_keys()
    client_id, client_secret = keys.get('naver_id'), keys.get('naver_secret')
    if not (client_id and client_secret):
        return []
    url = f'{NAVER_NEWS}?{urlencode({"query": query, "display": NAVER_DISPLAY, "sort": "date"})}'
    payload = fetch_json(url, headers={'X-Naver-Client-Id': client_id,
                                       'X-Naver-Client-Secret': client_secret})
    if not payload:
        return []
    rows = []
    for item in payload.get('items', []):
        published = parse_rfc822(item.get('pubDate'))
        if cutoff and published and published > cutoff:
            continue
        rows.append({
            'title': strip_tags(item.get('title')),
            'summary': strip_tags(item.get('description'))[:300],
            'url': item.get('originallink') or item.get('link'),
            'published_at': published.isoformat() if published else None,
            'collected_at': now().isoformat(),
        })
    return rows


# ---------- 특징으로 변환 ----------

def collect(symbol, name, date, cutoff=None):
    """한 종목의 공시·뉴스를 모아 저장하고 특징을 돌려줍니다."""
    path = NEWS_DIR / symbol / f'{date}.json'
    cached = read_json(path)
    if cached:
        return cached['features']
    yesterday = (datetime.fromisoformat(date) - timedelta(days=5)).date().isoformat()
    filings = disclosures(symbol, yesterday, date)
    articles = news(name, cutoff=cutoff) if name else []
    record = {
        'symbol': symbol, 'date': date, 'collected_at': now().isoformat(),
        'disclosures': filings, 'news': articles,
        'features': build_features(filings, articles, date),
    }
    write_json(path, record)
    return record['features']


def build_features(filings, articles, date):
    """숫자 특징만 추립니다. 조건 판정은 signals.py가 합니다."""
    recent = [f for f in filings if (f.get('published_date') or '') >= date.replace('-', '')]
    return {
        'disclosure_count_5d': len(filings),
        'disclosure_count_today': len(recent),
        'has_negative_disclosure': any(f['direction'] == 'negative' for f in filings),
        'has_positive_disclosure': any(f['direction'] == 'positive' for f in filings),
        'negative_labels': sorted({f['label'] for f in filings if f['direction'] == 'negative'}),
        'positive_labels': sorted({f['label'] for f in filings if f['direction'] == 'positive'}),
        'news_count': len(articles),
        'sources_available': {'dart': enabled('dart'),
                              'naver': enabled('naver_id') and enabled('naver_secret')},
    }


def setup():
    """공시·뉴스 API 키 저장. 없으면 해당 소스만 꺼진 채로 돌아갑니다."""
    import getpass
    import sys
    if not sys.stdin.isatty():
        raise RuntimeError('터미널에서 직접 실행하세요.')
    print('공시·뉴스 수집용 키를 저장합니다. 없으면 그냥 Enter로 건너뛰세요.')
    print('  OpenDART 무료 발급: https://opendart.fss.or.kr/  (인증키 신청)')
    print('  네이버 검색 API 무료 발급: https://developers.naver.com/apps/')
    current = load_keys()
    values = {
        'dart': getpass.getpass('OpenDART 인증키 (입력 숨김): ').strip() or current.get('dart'),
        'naver_id': input('네이버 Client ID: ').strip() or current.get('naver_id'),
        'naver_secret': getpass.getpass('네이버 Client Secret (입력 숨김): ').strip()
        or current.get('naver_secret'),
    }
    write_json(KEYS, {key: value for key, value in values.items() if value})
    print('저장 완료. 저장소나 화면 어디에도 올라가지 않습니다.')


if __name__ == '__main__':
    setup()
