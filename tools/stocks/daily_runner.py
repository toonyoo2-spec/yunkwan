#!/usr/bin/env python3
"""하루 실행 파이프라인.

  08:00 prepare  — 유니버스·특징·공시·뉴스·새벽 해외시장을 모아 후보 계획을 만듭니다
  08:30 morning  — 신뢰도 관문을 통과한 계획만 그날의 추천으로 고정합니다
  장중  intraday — 5분마다 실제 돌파 시점을 감시합니다 (intraday.py)
  15:40 close    — 분봉을 축적하고, 고정했던 계획을 그대로 재현해 결과를 기록합니다

진입은 정규장에서만 합니다. 프리마켓(NXT 08:00~)은 호가 스프레드만 매일 재서
기록을 쌓고 있고, 그 기록이 관문을 통과하기 전까지 추천에 넣지 않습니다.

조회 전용입니다. 주문은 하지 않습니다. 모든 저장은 맥북 내부에만 이뤄집니다.
추천이 0개인 날이 정상입니다. 관문을 통과하지 못하면 숫자를 만들지 않습니다.
"""
import argparse
import fcntl
import getpass
import sys
import warnings
from datetime import datetime, timedelta

import confidence
import evaluate
import global_context
import history
import news
import publish
import signals
from features import build as build_features
from store import STATE, read_json, write_json
from tossapi import Client, TossError, now

STRATEGY_VERSION = 'plan-v2'
PUBLISH_HOUR = 8                # 정규장 개장(09:00)에 가까울수록 데이터가 신선합니다
PUBLISH_MINUTES = (30, 40)      # 08:30~08:39 안에서만 발행합니다
CLOSE_GRACE_MINUTES = 5
MAX_RECOMMENDATIONS = 5
SCOREBOARD = STATE / 'scoreboard.json'
KOSPI_DAILY = STATE / 'daily' / '_KOSPI.json'


def publish_soft(date):
    """사이트 업로드. 설정이 없거나 실패해도 수집·평가는 멈추지 않습니다."""
    try:
        publish.publish(date)
    except (RuntimeError, ValueError) as exc:
        print('사이트 업로드 건너뜀:', exc)


def forecast_path(date):
    return STATE / 'reports' / f'{date}-forecast.json'


def assessment_path(date):
    return STATE / 'reports' / f'{date}-assessment.json'


def prepared_path(date):
    return STATE / 'prepared' / f'{date}.json'


# ---------- 설정 ----------

def setup():
    if not sys.stdin.isatty():
        raise TossError('터미널에서 직접 실행하세요.')
    warnings.simplefilter('error', getpass.GetPassWarning)
    print('자동 실행용 인증정보를 이 맥북의 사용자 전용 파일(권한 600)에 저장합니다.')
    config = {key: getpass.getpass(label).strip() for key, label in
              [('client_id', '토스 Client ID: '), ('client_secret', '토스 Client Secret: ')]}
    if not all(config.values()):
        raise TossError('두 값을 모두 입력하세요.')
    write_json(STATE / 'config.json', config)
    print('설정 저장 완료. 이 값은 저장소나 화면 어디에도 올라가지 않습니다.')


def check(client):
    client.get('/api/v1/prices', symbols='005930')
    print('토스 연결 성공 — 데이터는 맥북 내부에만 저장됩니다.')
    if client.commissions():
        print('수수료 조회도 성공. 손익 계산에 실제 요율을 쓸 수 있습니다.')


# ---------- 08:00 준비 ----------

def index_daily_candles(client):
    """상대강도 기준이 되는 코스피 일봉. 지수는 별도 엔드포인트라 따로 받습니다."""
    stored = read_json(KOSPI_DAILY, {}) or {}
    page = client.get_optional('/api/v1/market-indicators/KOSPI/candles',
                               interval='1d', count=120)
    merged = {row['timestamp']: row for row in stored.get('candles', [])}
    for row in (page or {}).get('candles', []):
        merged[row['timestamp']] = row
    ordered = [merged[key] for key in sorted(merged)][-120:]
    write_json(KOSPI_DAILY, {'symbol': 'KOSPI', 'updated_at': now().isoformat(),
                             'candles': ordered})
    return ordered


def prepare(client):
    """유니버스·특징·새벽 해외시장을 모아 계획 후보를 만듭니다."""
    date = now().date().isoformat()
    if not client.regular_session(date):
        print('휴장일: 준비 생략', date)
        return
    context = global_context.collect(client, date)
    universe = history.build_universe(client, date)
    index_candles = index_daily_candles(client)
    candidates, rejected = [], []
    for entry in universe['symbols']:
        symbol = entry['symbol']
        history.collect_daily(client, symbol)
        history.collect_flows(client, symbol)
        daily = read_json(history.daily_path(symbol), {}) or {}
        flows = read_json(history.flow_path(symbol), {}) or {}
        row = build_features(symbol, date, daily.get('candles', []), index_candles, flows)
        # 공시·뉴스는 '지금 시각 이전에 게시된 것'만 받습니다. 미래 정보 차단.
        row.update(news.collect(symbol, entry['name'], date, cutoff=now()))
        warning_list = client.warnings(symbol)
        for setup_name in signals.SETUPS:
            built = signals.plan(row, warning_list, setup_name)
            built.update({'name': entry['name'], 'features': row,
                          'reason': signals.describe(row)})
            (candidates if built['tradeable'] else rejected).append(built)
    prepared = {
        'trade_date': date,
        'prepared_at': now().isoformat(),
        'ranked_at': universe.get('ranked_at'),
        'global_context': context,
        'candidates': candidates,
        'rejected': rejected[:50],
        'strategy_version': STRATEGY_VERSION,
    }
    write_json(prepared_path(date), prepared)
    print(f'아침 준비 완료: {date} — 조건 통과 {len(candidates)}건 / 탈락 {len(rejected)}건')
    print('새벽 해외시장:', global_context.describe(context))


# ---------- 08:30 발행 ----------

def gated_recommendations(candidates, scoreboard, allow_long):
    """신뢰도 관문을 통과한 계획만 추천으로 내보냅니다."""
    passed, held = [], []
    for candidate in candidates:
        if not allow_long:
            held.append({**candidate, 'gate': '새벽 시장 상황으로 당일 신호 보류'})
            continue
        cleared = [(target, reason) for target in signals.TARGET_LADDER
                   for ok, reason in [confidence.gate(f"{candidate['setup']}@{target}%", scoreboard)]
                   if ok]
        if cleared:
            target_pct, reason = max(cleared)
            passed.append({**candidate, 'target_pct': target_pct, 'gate': reason})
        else:
            record = scoreboard.get(f"{candidate['setup']}@{signals.TARGET_LADDER[0]}%")
            held.append({**candidate, 'gate': record['reason'] if record
                         else '기록이 쌓이지 않아 관찰만 합니다'})
    passed.sort(key=lambda row: row['target_pct'], reverse=True)
    return passed[:MAX_RECOMMENDATIONS], held


def morning(client):
    date = now().date().isoformat()
    if read_json(forecast_path(date)):
        print('당일 아침 기록이 이미 고정되어 있습니다.')
        return
    session = client.regular_session(date)
    if not session:
        print('휴장일: 발행 생략')
        return
    current = now()
    low, high = PUBLISH_MINUTES
    if not (current.hour == PUBLISH_HOUR and low <= current.minute < high):
        raise TossError(f'{PUBLISH_HOUR:02d}:{low}~{PUBLISH_HOUR:02d}:{high - 1} KST '
                        '발행 시간 밖입니다. 뒤늦은 예측을 만들지 않습니다.')
    prepared = read_json(prepared_path(date))
    if not prepared:
        raise TossError('아침 준비 데이터가 없습니다. prepare 단계를 확인하세요.')
    if datetime.fromisoformat(prepared['prepared_at']) > current:
        raise TossError('미래 시각 데이터가 감지되었습니다.')
    scoreboard = read_json(SCOREBOARD, {}) or {}
    regime = prepared['global_context'].get('regime', {})
    recommendations, held = gated_recommendations(
        prepared['candidates'], scoreboard, regime.get('allow_long', False))
    forecast = {
        **prepared,
        'published_at': current.isoformat(),
        'scheduled_at': f'{date}T{PUBLISH_HOUR:02d}:{low}:00+09:00',
        'close_at': session['endTime'],
        'pre_market': client.pre_market_session(date),
        'regime': regime,
        'scoreboard': scoreboard,
        'recommendations': recommendations,
        'held': held[:50],
        'target_hit_rate': confidence.TARGET_HIT_RATE,
        'method_note': (
            f'적중률 {confidence.TARGET_HIT_RATE * 100:.0f}% 관문(Wilson 95% 하한)을 통과한 '
            '셋업만 추천합니다. 적중은 수수료·거래세·슬리피지를 뺀 뒤에도 수익이 남은 거래를 '
            '뜻합니다. 관문을 넘긴 셋업이 없으면 추천은 0개이며, 오류가 아니라 설계된 동작입니다.'),
    }
    write_json(forecast_path(date), forecast)
    print(f'아침 고정 완료: {date} — 추천 {len(recommendations)}건 / 관찰 {len(held)}건')
    if not recommendations:
        print('관문을 통과한 셋업이 없어 오늘은 추천하지 않습니다.')
    publish_soft(date)


# ---------- 마감 평가 ----------

def assess_day(date):
    """그날 고정했던 계획을 분봉으로 재현합니다. 보류한 계획도 함께 재현해야 기록이 쌓입니다."""
    forecast = read_json(forecast_path(date))
    if not forecast:
        return None
    recommended = {(p['symbol'], p['setup']) for p in forecast.get('recommendations', [])}
    simulations = []
    for plan in forecast.get('recommendations', []) + forecast.get('held', []):
        if not plan.get('tradeable'):
            continue
        stored = read_json(history.history_path(plan['symbol'], date), {}) or {}
        result = evaluate.simulate(plan, stored.get('candles', []))
        result['name'] = plan.get('name')
        result['recommended'] = (plan['symbol'], plan['setup']) in recommended
        simulations.append(result)
    return {
        'trade_date': date,
        'assessed_at': now().isoformat(),
        'simulations': simulations,
        'summary': evaluate.summarize(simulations),
        'complete': all(s.get('result') != 'no_data' for s in simulations),
    }


def rebuild_scoreboard():
    """전체 평가 기록으로 셋업별 적중률을 다시 계산합니다."""
    rows = []
    for path in sorted((STATE / 'reports').glob('*-assessment.json')):
        record = read_json(path, {}) or {}
        rows.extend(evaluate.flatten_for_scoreboard(record.get('simulations', [])))
    scoreboard = confidence.tally(rows)
    write_json(SCOREBOARD, scoreboard)
    return scoreboard


def close(client):
    """분봉을 축적한 뒤, 아직 평가하지 않은 날들을 재현합니다."""
    history.run(client)
    done = 0
    for path in sorted((STATE / 'reports').glob('*-forecast.json'), reverse=True)[:30]:
        forecast = read_json(path, {}) or {}
        date = forecast.get('trade_date')
        if not date:
            continue
        if now() < datetime.fromisoformat(forecast['close_at']) + timedelta(minutes=CLOSE_GRACE_MINUTES):
            continue
        existing = read_json(assessment_path(date))
        if existing and existing.get('complete'):
            continue
        result = assess_day(date)
        if result:
            write_json(assessment_path(date), result)
            publish_soft(date)
            done += 1
    scoreboard = rebuild_scoreboard()
    passed = [key for key, value in scoreboard.items() if value['status'] == 'passed']
    print(f'마감 평가 {done}일 기록. 관문 통과 셋업 {len(passed)}개.')
    for key, record in sorted(scoreboard.items()):
        print(f'  {key}: {record["reason"]}')


# ---------- 진입점 ----------

COMMANDS = {'check': check, 'prepare': prepare, 'morning': morning, 'close': close}


def main():
    parser = argparse.ArgumentParser(description='주식 기록 하루 실행')
    parser.add_argument('command', choices=['setup', *COMMANDS])
    command = parser.parse_args().command
    if command == 'setup':
        setup()
        return
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'runner.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TossError('다른 수집 실행이 진행 중입니다.') from None
        client = Client()
        try:
            COMMANDS[command](client)
        finally:
            client.save_archive()


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('취소했습니다.')
        sys.exit(1)
    except Exception as exc:
        print('실행 실패:', str(exc) if isinstance(exc, RuntimeError)
              else '데이터 또는 연결 처리 오류입니다.')
        sys.exit(1)
