#!/usr/bin/env python3
"""One-shot KR market watchlist collector; never creates trade recommendations."""
import getpass
import html
import json
import os
import sys
import time
import warnings
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode
from toss_connect import request

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))

def eligible(stock):
    detail = stock.get('koreanMarketDetail') or {}
    return (stock.get('market') in ('KOSPI', 'KOSDAQ')
            and stock.get('securityType') == 'STOCK'
            and stock.get('isCommonShare') is True
            and stock.get('status') == 'ACTIVE'
            and detail.get('liquidationTrading') is False
            and detail.get('krxTradingSuspended') is False
            and detail.get('nxtTradingSuspended') is not True)

def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('non-finite value')
    return result

def select(rankings, stocks):
    by_symbol = {s['symbol']: s for s in stocks}
    selected, excluded = [], []
    for row in rankings:
        symbol = row.get('symbol')
        stock = by_symbol.get(symbol, {})
        if not eligible(stock):
            excluded.append({'symbol': symbol, 'reason': '보통주·거래상태 조건 미충족 또는 정보 부족'})
            continue
        try:
            price = number(row['price']['lastPrice'])
            amount = number(row['tradingAmount'])
            rate = number(row['price']['changeRate']) * 100
            if price <= 0 or amount <= 0:
                raise ValueError('invalid price or amount')
        except (KeyError, InvalidOperation, ValueError, TypeError):
            excluded.append({'symbol': symbol, 'reason': '가격·거래대금·등락률 데이터 부족'})
            continue
        selected.append({'symbol': symbol, 'name': stock['name'],
                         'price': str(price), 'change_pct': str(rate),
                         'trading_amount': str(amount),
                         'reason': '시장 전체 1일 거래대금 순위 — 추가 검증용'})
    selected.sort(key=lambda r: number(r['trading_amount']), reverse=True)
    return selected[:20], excluded

def render(report):
    esc = lambda v: html.escape(str(v))
    cards = ''.join('<tr><td>' + esc(r['name']) + '<br><small>' + esc(r['symbol']) +
                    '</small></td><td>' + esc(r['price']) + '</td><td>' +
                    f"{number(r['change_pct']):+.2f}%" + '</td><td>' +
                    f"{number(r['trading_amount']) / Decimal(100000000):,.1f}억" + '</td></tr>'
                    for r in report['watchlist'])
    return '''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>시장 검토 후보</title>
<style>body{font:16px -apple-system,sans-serif;background:#f7f7f5;color:#17181a;max-width:850px;margin:40px auto;padding:20px}table{width:100%;border-collapse:collapse;background:white}td,th{padding:14px;text-align:right;border-bottom:1px solid #ddd}td:first-child,th:first-child{text-align:left}small,p{color:#62666d;line-height:1.7}.notice{background:#fff2cf;padding:16px;border-radius:12px}</style>
<h1>시장 검토 후보</h1><p class="notice">매수 추천 전 단계입니다. 거래대금 순위는 상승 확률이 아닙니다. 매매 신호는 생성하지 않았습니다.</p>''' + \
        '<p>수집 시각: ' + esc(report['received_at']) + '<br>순위 기준 시각: ' + esc(report['ranked_at']) + \
        '<br>시장 상태: ' + esc(report['market_status']) + '</p>' + \
        '<table><thead><tr><th>종목</th><th>가격(원)</th><th>전일 대비</th><th>1일 거래대금</th></tr></thead><tbody>' + cards + \
        '</tbody></table><p>시장 전체 거래대금 상위 100개에서 확인 가능한 국내 보통주를 선별했습니다. 순위 기준 시각이 오래되면 현재 매매 판단에 사용하지 마세요. 공시·뉴스·호가·손익 검증은 아직 반영되지 않았습니다.</p></html>'

def main():
    if not sys.stdin.isatty():
        raise RuntimeError('macOS 터미널에서 직접 실행하세요.')
    warnings.simplefilter('error', getpass.GetPassWarning)
    print('시장 후보 1회 수집. 새 인증으로 동일 키의 이전 토큰이 무효화됩니다.')
    client_id = getpass.getpass('Client ID (입력 숨김): ').strip()
    secret = getpass.getpass('Client Secret (입력 숨김): ').strip()
    if not client_id or not secret:
        raise RuntimeError('인증정보를 모두 입력하세요.')
    auth = request('/oauth2/token', form={'grant_type': 'client_credentials',
                   'client_id': client_id, 'client_secret': secret})
    token = auth.get('access_token')
    del client_id, secret, auth
    if not isinstance(token, str) or not token:
        raise RuntimeError('인증 응답 확인 실패')
    archive = []
    def get(path, **params):
        time.sleep(1.1)
        response = request(path + ('?' + urlencode(params) if params else ''), token=token)
        if 'result' not in response:
            raise RuntimeError('API 응답에 데이터가 없습니다.')
        archive.append({'path': path, 'params': params, 'received_at': datetime.now(KST).isoformat(),
                        'result': response['result']})
        return response['result']
    now = datetime.now(KST)
    calendar = get('/api/v1/market-calendar/KR', date=now.date().isoformat())
    ranking = get('/api/v1/rankings', type='MARKET_TRADING_AMOUNT', marketCountry='KR',
                  duration='1d', excludeInvestmentCaution='true', count=100)
    rows = ranking.get('rankings', [])
    if not rows or not ranking.get('rankedAt'):
        raise RuntimeError('집계된 순위가 없습니다. 후보를 생성하지 않았습니다.')
    stocks = get('/api/v1/stocks', symbols=','.join(r['symbol'] for r in rows))
    watchlist, excluded = select(rows, stocks)
    today = calendar.get('today', {})
    integrated = today.get('integrated')
    market_status = '휴장 또는 세션 정보 없음'
    if integrated:
        market_status = '거래일 / 현재 세션 밖'
        for name, session in integrated.items():
            if session and datetime.fromisoformat(session['startTime']) <= now < datetime.fromisoformat(session['endTime']):
                market_status = name
    report = {'received_at': datetime.now(KST).isoformat(), 'ranked_at': ranking['rankedAt'],
              'market_status': market_status, 'strategy_version': 'watchlist-only-v1',
              'recommendations': [], 'watchlist': watchlist, 'excluded': excluded}
    # Separate timestamped runs retain every collected snapshot.
    run = ROOT / 'market_runs' / datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f')
    run.mkdir(parents=True, mode=0o700)
    (run / 'raw_market_data.json').write_text(json.dumps(archive, ensure_ascii=False, indent=2))
    (run / 'watchlist.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (ROOT / '시장후보.html').write_text(render(report), encoding='utf-8')
    print('수집 완료:', run)
    print('후보 화면:', ROOT / '시장후보.html')
    print('검토 후보', len(watchlist), '개 / 매매 신호 0개. 아직 전략 검증 전입니다.')

if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\n취소했습니다.')
    except Exception as exc:
        print('수집 실패:', str(exc) if isinstance(exc, RuntimeError) else '데이터 또는 연결 처리 오류. 비밀키 없이 이 메시지만 알려주세요.')
        sys.exit(1)
