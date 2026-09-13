#!/usr/bin/env python3
"""Loopback-only viewer. Never serves configuration, tokens or raw market archives."""
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from daily_runner import STATE

HERE=Path(__file__).resolve().parent
ROOT=HERE/'viewer' if (HERE/'viewer').exists() else HERE.parents[1]
ASSETS={'stocks.css','stocks.js','stocks-data.js','common.css'}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.headers.get('Host') not in ('127.0.0.1:8766','localhost:8766'):
            self.send_error(403);return
        origin=self.headers.get('Origin')
        if origin and origin not in ('http://127.0.0.1:8766','http://localhost:8766'):
            self.send_error(403);return
        path=self.path.split('?',1)[0]
        if path=='/api/reports':
            data=[]
            for file in sorted((STATE/'reports').glob('*-forecast.json'),reverse=True)[:100]:
                forecast=json.loads(file.read_text());day=forecast['trade_date'];assessment=STATE/'reports'/f'{day}-assessment.json'
                data.append({'forecast':forecast,'assessment':json.loads(assessment.read_text()) if assessment.exists() else None})
            content=json.dumps(data,ensure_ascii=False).encode();mime='application/json'
        elif path=='/':
            text=(ROOT/'stocks.html').read_text().replace('style="display:none"','')
            text=text.replace('<script src="./nav.js?v=7"></script>','<nav><a href="https://yunkwan.cloud/stocks.html">← BORAKWAN</a> · 이 맥북에서만 보는 개인 기록</nav>')
            text=text.replace('<script src="./common.js"></script>','')
            text=text.replace('<script src="./auth-gate.js?v=2"></script>','<script>window.STOCK_LOCAL_MODE=true;window.dispatchEvent(new Event("authReady"));</script>')
            content=text.encode();mime='text/html; charset=utf-8'
        elif path.lstrip('/') in ASSETS:
            content=(ROOT/path.lstrip('/')).read_bytes();mime='text/css' if path.endswith('.css') else 'application/javascript'
        elif path=='/health':content=b'{"service":"borakwan-local-stocks"}';mime='application/json'
        else:self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"frame-ancestors 'none'");self.end_headers();self.wfile.write(content)

if __name__=='__main__':
    print('개인 기록 화면: http://127.0.0.1:8766/ (맥북 내부 전용)',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
