#!/usr/bin/env python3
"""Read-only market collection, frozen morning estimates, after-close assessment.
No trading endpoints. Secrets never included in stdout or market archives.
"""
import argparse
import fcntl
import getpass
import json
import math
import os
import statistics
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from toss_connect import NoRedirect, request
from toss_collect import KST, select

STATE = Path.home() / 'Library/Application Support/BorakwanStocks'
MODEL = 'regular-session-mean-shrink-v1'

def now(): return datetime.now(KST)
def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def setup():
    if not sys.stdin.isatty(): raise RuntimeError('터미널에서 setup을 실행하세요.')
    warnings.simplefilter('error', getpass.GetPassWarning)
    print('자동 실행용 인증정보를 내 Mac의 사용자 전용 파일(권한 600)에 저장합니다.')
    cfg = {k:getpass.getpass(label).strip() for k,label in [
        ('client_id','토스 Client ID: '),('client_secret','토스 Client Secret: ')]}
    if not all(cfg.values()): raise RuntimeError('모든 값을 입력하세요.')
    write(STATE/'config.json',cfg)
    print('설정 저장 완료. 비밀정보는 GitHub에 업로드하지 않습니다.')

class Client:
    def __init__(self):
        if not (STATE/'config.json').exists(): raise RuntimeError('초기 설정 필요: daily_runner.py setup 실행')
        self.cfg=json.loads((STATE/'config.json').read_text())
        self.token=None; self.raw=[]
    def get(self,path,**params):
        if not self.token:
            # Reuse cached token to avoid revoking a concurrent run's token.
            cache=STATE/'toss_token.json'
            if cache.exists():
                saved=json.loads(cache.read_text())
                if saved.get('expires_at',0)>time.time()+120: self.token=saved['access_token']
            if not self.token:
                a=request('/oauth2/token',form={'grant_type':'client_credentials','client_id':self.cfg['client_id'],'client_secret':self.cfg['client_secret']})
                self.token=a['access_token'];write(cache,{'access_token':self.token,'expires_at':time.time()+int(a['expires_in'])})
        time.sleep(1.1)
        response=request(path+('?' + urlencode(params) if params else ''),token=self.token)
        if 'result' not in response: raise RuntimeError('시장 데이터 응답 누락')
        self.raw.append({'path':path,'params':params,'received_at':now().isoformat(),'result':response['result']})
        return response['result']
    def calendar(self,date): return self.get('/api/v1/market-calendar/KR',date=date)['today']

def regular_pair(client,symbol,day):
    """Require exact opening/closing minute; never substitute previous/after-hours bar."""
    cache=STATE/'bars'/symbol/(day+'.json')
    if cache.exists(): return json.loads(cache.read_text())
    calcache=STATE/'calendars'/(day+'.json')
    if calcache.exists(): cal=json.loads(calcache.read_text())
    else: cal=client.calendar(day);write(calcache,cal)
    session=(cal.get('integrated') or {}).get('regularMarket')
    if not session:return None
    pair=[]
    for field in ('startTime','endTime'):
        instant=session[field]
        result=client.get('/api/v1/candles',symbol=symbol,interval='1m',count=1,before=instant,adjusted='false')
        rows=result.get('candles',[])
        if not rows or datetime.fromisoformat(rows[0]['timestamp'])!=datetime.fromisoformat(instant):return None
        pair.append(rows[0])
    op=float(pair[0]['openPrice']);cl=float(pair[1]['closePrice'])
    if not all(math.isfinite(v) and v>0 for v in (op,cl)):return None
    result={'date':day,'open':op,'close':cl,'actual_pct':(cl/op-1)*100,
            'open_at':pair[0]['timestamp'],'close_at':pair[1]['timestamp']}
    write(cache,result);return result

def estimate(samples):
    # Historical unconditional mean, shrunk toward zero. Not a calibrated ML forecast.
    values=[x['actual_pct'] for x in samples]
    if len(values)<20:return {'expected_pct':None,'sample_count':len(values),'spread_pct':None}
    mean=statistics.mean(values)
    return {'expected_pct':mean*len(values)/(len(values)+20),'sample_count':len(values),
            'spread_pct':statistics.stdev(values)}

def prepare(client):
    today=now().date().isoformat();cal=client.calendar(today)
    if not (cal.get('integrated') or {}).get('regularMarket'):
        print('휴장일: 준비 생략');return
    rank=client.get('/api/v1/rankings',type='MARKET_TRADING_AMOUNT',marketCountry='KR',duration='1d',excludeInvestmentCaution='true',count=100)
    rows=rank.get('rankings',[])
    if not rows or not rank.get('rankedAt'):raise RuntimeError('순위 데이터가 없어 준비하지 못했습니다.')
    stocks=client.get('/api/v1/stocks',symbols=','.join(r['symbol'] for r in rows))
    candidates,_=select(rows,stocks);candidates=candidates[:5]
    predictions=[]
    for row in candidates:
        dates=client.get('/api/v1/candles',symbol=row['symbol'],interval='1d',count=35,adjusted='false')
        days=sorted({x['timestamp'][:10] for x in dates.get('candles',[]) if x['timestamp'][:10]<today},reverse=True)[:25]
        samples=[]
        for day in days:
            pair=regular_pair(client,row['symbol'],day)
            if pair:samples.append(pair)
            if len(samples)>=20:break
        predictions.append({**row,**estimate(samples),'training_through':max((x['date'] for x in samples),default=None),
            'model':MODEL,'reason':'시장 거래대금 상위 보통주 · 과거 정규장 수익률 평균을 0% 방향으로 보정한 초기 추정'})
    prepared={'trade_date':today,'prepared_at':now().isoformat(),'ranked_at':rank['rankedAt'],'calendar':cal,'predictions':predictions}
    write(STATE/'prepared'/f'{today}.json',prepared)
    print('아침 준비 완료:',today,'후보',len(predictions),'개')

def morning(client):
    day=now().date().isoformat()
    if (STATE/'reports'/f'{day}-forecast.json').exists():print('당일 아침 기록이 이미 고정되어 있습니다.');return
    cal=client.calendar(day);session=(cal.get('integrated') or {}).get('regularMarket')
    if not session:print('휴장일: 발행 생략');return
    current=now()
    if not (current.hour==8 and 30<=current.minute<40):raise RuntimeError('08:30~08:39 KST 발행 시간 밖입니다. 뒤늦은 예측을 생성하지 않습니다.')
    path=STATE/'prepared'/f'{day}.json'
    if not path.exists():raise RuntimeError('아침 준비 데이터가 없습니다. prepare 단계 확인 필요')
    prepared=json.loads(path.read_text())
    if datetime.fromisoformat(prepared['prepared_at'])>current or datetime.fromisoformat(prepared['ranked_at'])>current:raise RuntimeError('미래 시각 데이터 감지')
    forecast={**prepared,'published_at':current.isoformat(),'scheduled_at':day+'T08:30:00+09:00',
              'model':MODEL,'target':'regular_open_to_close','close_at':session['endTime'],
              'method_note':'과거 정규장 20일 시가→종가 평균의 50%를 초기 추정값으로 사용. 상승 확률이나 검증된 투자전략이 아닙니다. 표본 표준편차는 과거 변동 폭이며 예측구간이 아닙니다.'}
    write(STATE/'reports'/f'{day}-forecast.json',forecast)
    print('아침 예측 고정 및 맥북 저장 완료:',day)

def assess(forecast, pairs):
    rows=[]
    for p in forecast['predictions']:
        actual=pairs.get(p['symbol']);expected=p.get('expected_pct')
        error=actual['actual_pct']-expected if actual and expected is not None else None
        rows.append({'symbol':p['symbol'],'actual':actual,'expected_pct':expected,'error_pp':error,
                     'absolute_error_pp':abs(error) if error is not None else None,
                     'direction_hit':((actual['actual_pct']>0)-(actual['actual_pct']<0)==(expected>0)-(expected<0)) if error is not None else None,
                     'status':'complete' if actual else 'close_data_pending'})
    valid=[x for x in rows if x['error_pp'] is not None]
    return {'assessed_at':now().isoformat(),'rows':rows,'evaluated_count':len(valid),
            'mae_pp':statistics.mean(x['absolute_error_pp'] for x in valid) if valid else None,
            'direction_accuracy_pct':100*sum(x['direction_hit'] for x in valid)/len(valid) if valid else None,
            'benchmark_zero_mae_pp':statistics.mean(abs(x['actual']['actual_pct']) for x in valid) if valid else None,
            'note':'실제 수익률은 시가 대비 종가 변동률입니다. 개인 체결·투입 비중·세금·수수료는 반영하지 않습니다.'}

def close(client):
    files=sorted((STATE/'reports').glob('*-forecast.json'),reverse=True)[:30];done=0
    for file in files:
        forecast=json.loads(file.read_text());day=forecast['trade_date']
        if now()<datetime.fromisoformat(forecast['close_at'])+timedelta(minutes=5):continue
        target=STATE/'reports'/f'{day}-assessment.json'
        existing=json.loads(target.read_text()) if target.exists() else None
        if existing and all(x['status']=='complete' for x in existing['rows']):continue
        pairs={p['symbol']:regular_pair(client,p['symbol'],day) for p in forecast['predictions']}
        result=assess(forecast,pairs)
        write(target,result);done+=1
    print('마감 평가 맥북 저장:',done,'일. 종가 분봉 미확인 종목은 대기로 유지합니다.')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['setup','check','prepare','morning','close']);args=parser.parse_args()
    if args.command=='setup':setup();return
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (STATE/'runner.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('다른 수집 실행이 진행 중입니다.')
        client=Client()
        try:
            if args.command=='check':client.get('/api/v1/prices',symbols='005930');print('토스 연결 성공 — 데이터는 맥북 내부에만 저장됩니다.')
            else:globals()[args.command](client)
        finally:
            if client.raw:write(STATE/'raw'/f'{now().strftime("%Y%m%d_%H%M%S_%f")}.json',client.raw)

if __name__=='__main__':
    try:main()
    except (KeyboardInterrupt,EOFError):print('취소했습니다.');sys.exit(1)
    except Exception as e:
        print('실행 실패:',str(e) if isinstance(e,RuntimeError) else '데이터 또는 연결 처리 오류. 인증정보는 출력하지 않습니다.');sys.exit(1)
