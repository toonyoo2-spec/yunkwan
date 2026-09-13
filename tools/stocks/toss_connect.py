#!/usr/bin/env python3
"""토스 공식 API 인증 및 시세 조회. Python 표준 라이브러리만 사용."""
import getpass
import json
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

BASE = 'https://openapi.tossinvest.com'

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def request(path, token=None, form=None):
    headers = {'Accept': 'application/json'}
    data = None
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if form is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        data = urlencode(form).encode()
    req = Request(BASE + path, data=data, headers=headers)
    try:
        with build_opener(NoRedirect()).open(req, timeout=20) as response:
            value = json.load(response)
    except HTTPError as exc:
        messages = {
            400: '요청 형식이 거절되었습니다. 공식 API 규격을 확인하세요.',
            401: '인증 실패: Client ID와 Secret 또는 키 활성 상태를 확인하세요.',
            403: '접근 거절: 토스 Open API 허용 IP와 이용 권한을 확인하세요.',
            429: '호출 한도에 도달했습니다. 잠시 후 다시 실행하세요.',
        }
        raise RuntimeError(messages.get(exc.code, 'API HTTP 오류: ' + str(exc.code))) from None
    except (URLError, TimeoutError):
        raise RuntimeError('연결 실패: 인터넷 연결 또는 인증서 설정을 확인하세요.') from None
    if not isinstance(value, dict) or value.get('error'):
        raise RuntimeError('API가 정상 응답을 반환하지 않았습니다.')
    return value

def main():
    if not sys.stdin.isatty():
        raise RuntimeError('비밀 입력을 위해 macOS 터미널에서 직접 실행하세요.')
    print('토스 시세 연결 확인 — 인증정보는 파일에 저장하지 않습니다.')
    print('새 토큰을 발급하면 같은 API 키의 기존 토큰은 무효화됩니다.')
    warnings.simplefilter('error', getpass.GetPassWarning)
    client_id = getpass.getpass('Client ID (입력 숨김): ').strip()
    secret = getpass.getpass('Client Secret (입력 숨김): ').strip()
    if not client_id or not secret:
        raise RuntimeError('두 값을 모두 입력해야 합니다.')
    auth = request('/oauth2/token', form={
        'grant_type': 'client_credentials', 'client_id': client_id,
        'client_secret': secret,
    })
    token = auth.get('access_token')
    if not isinstance(token, str) or not token:
        raise RuntimeError('인증 응답에서 토큰을 확인하지 못했습니다.')
    del secret, client_id, auth
    quotes = request('/api/v1/prices?symbols=005930,000660', token=token)
    rows = quotes.get('result')
    if not isinstance(rows, list) or not rows:
        raise RuntimeError('시세 데이터가 비어 있습니다. 추천에 사용할 수 없습니다.')
    for row in rows:
        if not isinstance(row, dict) or not all(k in row for k in ('symbol', 'lastPrice', 'timestamp')):
            raise RuntimeError('시세 응답 형식이 예상과 다릅니다.')
    # Save only explicitly selected market fields; never persist authentication responses.
    clean = [{k: row.get(k) for k in ('symbol', 'lastPrice', 'currency', 'timestamp')} for row in rows]
    output = Path(__file__).resolve().parent / 'toss_connection_result.json'
    result = {'received_at': datetime.now(timezone.utc).isoformat(),
              'source': BASE, 'status': 'connected',
              'purpose': 'connection_check_not_recommendation', 'prices': clean}
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
    print('인증 및 시세 조회 성공. 결과:', output)
    print('삼성전자·SK하이닉스는 연결 확인용이며 추천종목이 아닙니다.')
    print('휴장일·장외에는 이전 시세일 수 있습니다. 데이터 시각을 확인하세요.')

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, getpass.GetPassWarning) as exc:
        # Do not print response bodies, request headers or secret-bearing tracebacks.
        print('연결 확인 실패:', str(exc) if isinstance(exc, RuntimeError) else '입출력 또는 응답 처리 오류입니다.')
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print('\n취소했습니다.')
        sys.exit(1)
