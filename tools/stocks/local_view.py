#!/usr/bin/env python3
"""맥북 내부 전용 화면 (http://127.0.0.1:8766).

루프백에서만 응답하고, 설정·토큰·원본 시세 아카이브는 절대 서빙하지 않습니다.
/api/reports는 사이트에 올리는 것과 똑같은 정제 형식을 돌려줍니다. 화면 코드가
하나뿐이어야 하고, 로컬 경로가 실수로 유출 통로가 되는 것도 막기 위해서입니다.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import publish
from store import STATE, read_json

HOST, PORT = '127.0.0.1', 8766
ALLOWED_HOSTS = (f'{HOST}:{PORT}', f'localhost:{PORT}')
ALLOWED_ORIGINS = tuple(f'http://{host}' for host in ALLOWED_HOSTS)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]          # 저장소 루트 (stocks.html이 있는 곳)
ASSETS = {'stocks.css', 'stocks.js', 'stocks-data.js', 'common.css'}
MAX_REPORTS = 120


def reports():
    """정제된 기록 목록. 사이트 업로드와 같은 형식입니다."""
    rows = []
    for path in sorted((STATE / 'reports').glob('*-forecast.json'), reverse=True)[:MAX_REPORTS]:
        forecast = read_json(path)
        if not forecast:
            continue
        date = forecast.get('trade_date')
        assessment = read_json(STATE / 'reports' / f'{date}-assessment.json')
        rows.append({
            'trade_date': date,
            'forecast': publish.sanitize_forecast(forecast),
            'assessment': publish.sanitize_assessment(assessment) if assessment else None,
        })
    return rows


def local_page():
    """사이트용 HTML을 로컬 모드로 바꿔 돌려줍니다."""
    text = (ROOT / 'stocks.html').read_text(encoding='utf-8')
    return (text
            .replace('style="display:none"', '')
            .replace('<script src="./nav.js?v=7"></script>',
                     '<nav class="wrap local-banner">'
                     '<a href="https://yunkwan.cloud/stocks.html">← BORAKWAN</a>'
                     ' · 이 맥북에서만 보는 개인 기록</nav>')
            .replace('<script src="./common.js"></script>', '')
            .replace('<script src="./auth-gate.js?v=2"></script>',
                     '<script>window.STOCK_LOCAL_MODE=true;'
                     'window.dispatchEvent(new Event("authReady"));</script>'))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reject_remote(self):
        if self.headers.get('Host') not in ALLOWED_HOSTS:
            self.send_error(403)
            return True
        origin = self.headers.get('Origin')
        if origin and origin not in ALLOWED_ORIGINS:
            self.send_error(403)
            return True
        return False

    def do_GET(self):
        if self._reject_remote():
            return
        path = self.path.split('?', 1)[0]
        if path == '/api/reports':
            body = json.dumps(reports(), ensure_ascii=False).encode()
            mime = 'application/json'
        elif path == '/':
            body = local_page().encode()
            mime = 'text/html; charset=utf-8'
        elif path.lstrip('/') in ASSETS:
            body = (ROOT / path.lstrip('/')).read_bytes()
            mime = 'text/css' if path.endswith('.css') else 'application/javascript'
        elif path == '/health':
            body, mime = b'{"service":"borakwan-local-stocks"}', 'application/json'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    print(f'개인 기록 화면: http://{HOST}:{PORT}/ (맥북 내부 전용)', flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
