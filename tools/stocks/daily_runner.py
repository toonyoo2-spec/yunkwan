#!/usr/bin/env python3
"""하루 실행 파이프라인.

  08:00 prepare  — 유니버스·특징·공시·뉴스·새벽 해외시장을 모아 후보 계획을 만듭니다
  08:30 morning  — 강도 순으로 코스피 7 · 코스닥 3 종목을 그날의 추천으로 고정합니다
  장중  intraday — 5분마다 실제 돌파 시점을 감시합니다 (intraday.py)
  15:40 close    — 분봉을 축적하고, 고정했던 계획을 그대로 재현해 결과를 기록합니다

진입은 정규장에서만 합니다. 프리마켓(NXT 08:00~)은 호가 스프레드만 매일 재서
기록을 쌓고 있고, 그 기록이 관문을 통과하기 전까지 추천에 넣지 않습니다.

조회 전용입니다. 주문은 하지 않습니다. 모든 저장은 맥북 내부에만 이뤄집니다.

매일 정해진 수만큼 추천하되, 강도(1~10)로 차등을 둡니다. 조건이 나쁜 날 추천을
감추는 대신 강도가 낮은 종목이 나갑니다. 강도에는 셋업의 과거 적중률이 가장 큰
비중으로 들어가며, 강도 자체가 맞는 값인지는 strength.calibration으로 검증합니다.
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
import goal
import history
import news
import positions
import publish
import signals
import strength
from features import build as build_features
from store import STATE, read_json, write_json
from tossapi import Client, TossError, now, parse_time

STRATEGY_VERSION = 'plan-v2'
PUBLISH_HOUR = 8                # 정규장 개장(09:00)에 가까울수록 데이터가 신선합니다
PUBLISH_MINUTES = (30, 40)      # 08:30~08:39 안에서만 발행합니다
CLOSE_GRACE_MINUTES = 5
# 시장별 추천 정원. 매일 이 수만큼 내보내되, 강도(1~10)로 차등을 둡니다.
# 조건이 나쁜 날은 추천을 감추는 대신 강도가 낮은 종목이 나갑니다.
MARKET_QUOTA = {'KOSPI': 7, 'KOSDAQ': 3}
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
                               interval='1d', count=200)
    merged = {row['timestamp']: row for row in stored.get('candles', [])}
    for row in (page or {}).get('candles', []):
        merged[row['timestamp']] = row
    # 소급 수집해둔 과거를 매일 아침 잘라내지 않도록 보관 길이를 넉넉히 둡니다.
    ordered = [merged[key] for key in sorted(merged)][-history.DAILY_KEEP:]
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
    candidates = []
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
            built.update({'name': entry['name'], 'market': entry.get('market'),
                          'features': row, 'reason': signals.describe(row)})
            candidates.append(built)
    prepared = {
        'trade_date': date,
        'prepared_at': now().isoformat(),
        'ranked_at': universe.get('ranked_at'),
        'global_context': context,
        'candidates': candidates,
        'strategy_version': STRATEGY_VERSION,
    }
    write_json(prepared_path(date), prepared)
    passed = sum(1 for row in candidates if row.get('tradeable'))
    print(f'아침 준비 완료: {date} — 후보 {len(candidates)}건 '
          f'(조건 통과 {passed}건 / 미충족 {len(candidates) - passed}건)')
    print('새벽 해외시장:', global_context.describe(context))


# ---------- 08:30 발행 ----------

def best_target(setup_name, scoreboard):
    """이 셋업에 쓸 목표를 고릅니다.

    관문을 통과한 목표가 있으면 그중 가장 높은 것을 씁니다.
    통과한 것이 없으면 '가장 낮은 목표'로 떨어뜨리면 안 됩니다. 목표를 낮추면
    이겨도 조금 벌고 지면 손절 폭만큼 잃어 손익비가 무너지기 때문입니다.
    (실제로 목표 1.0% · 손절 2.5%는 손익비 0.4라 본전 적중률이 71%까지 올라갑니다.)
    그래서 기록이 있으면 거래당 기대값이 가장 높았던 목표를 쓰고, 기록이 전혀
    없을 때만 사다리 중간값을 씁니다.
    """
    cleared = [target for target in signals.TARGET_LADDER
               if confidence.gate(f'{setup_name}@{target}%', scoreboard)[0]]
    if cleared:
        return max(cleared), True

    scored = [(record.get('expectancy_pct'), target)
              for target in signals.TARGET_LADDER
              for record in [scoreboard.get(f'{setup_name}@{target}%')]
              if record and record.get('expectancy_pct') is not None]
    if scored:
        return max(scored)[1], False
    middle = signals.TARGET_LADDER[len(signals.TARGET_LADDER) // 2]
    return middle, False


def rank_candidates(candidates, scoreboard, regime_status):
    """모든 후보에 강도를 매겨 순위를 만듭니다. 조건 미충족 종목도 포함합니다."""
    ranked = []
    for candidate in candidates:
        setup_name = candidate['setup']
        target_pct, gate_passed = best_target(setup_name, scoreboard)
        record = scoreboard.get(f'{setup_name}@{target_pct}%')
        rating = strength.score(candidate.get('features', {}), record, regime_status)
        # 조건을 통과하지 못한 종목은 강도를 한 단계 더 낮춰, 같은 점수라도 뒤에 서게 합니다.
        if not candidate.get('tradeable'):
            rating['level'] = max(1, rating['level'] - 1)
            rating['components'].append({'label': '조건 미충족', 'points': 0,
                                         'detail': ', '.join(candidate.get('blocks', []))[:200]})
        ranked.append({**candidate, 'target_pct': target_pct, 'strength': rating,
                       'gate': (record or {}).get('reason', '셋업 기록 없음 — 관찰 중'),
                       'gate_passed': gate_passed,
                       'setup_record': record,
                       'report': signals.report_features(candidate.get('features', {}))})
    ranked.sort(key=lambda row: (row['strength']['level'], row['strength']['raw_score']),
                reverse=True)
    return ranked


def select(ranked):
    """시장별 정원만큼 고릅니다. 같은 종목이 두 셋업으로 중복 추천되지 않게 합니다."""
    chosen, rest = [], []
    remaining = dict(MARKET_QUOTA)
    seen = set()
    for candidate in ranked:
        market = candidate.get('market')
        if candidate['symbol'] in seen:
            rest.append({**candidate, 'gate': '같은 종목의 다른 셋업이 이미 선정됨'})
            continue
        if remaining.get(market, 0) > 0:
            remaining[market] -= 1
            seen.add(candidate['symbol'])
            chosen.append(candidate)
        else:
            rest.append(candidate)
    return chosen, rest


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
    if parse_time(prepared['prepared_at']) > current:
        raise TossError('미래 시각 데이터가 감지되었습니다.')
    scoreboard = read_json(SCOREBOARD, {}) or {}
    regime = prepared['global_context'].get('regime', {})
    ranked = rank_candidates(prepared['candidates'], scoreboard, regime.get('status'))
    recommendations, held = select(ranked)
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
        'market_quota': MARKET_QUOTA,
        'method_note': (
            f'매일 코스피 {MARKET_QUOTA["KOSPI"]}종목 · 코스닥 {MARKET_QUOTA["KOSDAQ"]}종목을 '
            '강도 순으로 내보냅니다. 강도(1~10)는 근거가 얼마나 모였는지를 나타내며 상승 확률이 '
            f'아닙니다. 강도 계산에는 셋업의 과거 적중률(목표 {confidence.TARGET_HIT_RATE * 100:.0f}% '
            '관문, Wilson 95% 하한)이 가장 큰 비중으로 들어갑니다. 적중은 수수료·거래세·슬리피지를 '
            '뺀 뒤에도 수익이 남은 거래를 뜻합니다. 조건이 나쁜 날은 강도가 낮은 종목이 나갑니다.'),
    }
    # 보유 종목 판정 — 오늘 후보와 비교해 계속 들고 갈지 결정합니다.
    try:
        forecast['positions'] = positions.run(client, prepared['candidates'])
    except (RuntimeError, ValueError) as exc:
        print('보유 종목 판정 건너뜀:', exc)
        forecast['positions'] = []

    # 하루 목표(3%) 현황을 함께 실어 사이트에서 바로 보이게 합니다.
    goal_state = read_json(goal.GOAL_FILE, {}) or {}
    today_row = next((row for row in goal_state.get('days', [])
                      if row.get('date') == date), None)
    forecast['goal'] = {**(today_row or {}), 'summary': goal_state.get('summary'),
                        'target_pct': goal.DAILY_TARGET_PCT}

    write_json(forecast_path(date), forecast)
    levels = [row['strength']['level'] for row in recommendations]
    print(f'아침 고정 완료: {date} — 추천 {len(recommendations)}건 / 관찰 {len(held)}건')
    if levels:
        print(f'  강도 분포: 최고 {max(levels)} · 최저 {min(levels)}')
        for row in recommendations:
            print(f"  [{row['strength']['level']:2d}] {row.get('market', '?'):6s} "
                  f"{row['symbol']} {row.get('name', '')} · {row['setup']} "
                  f"· 목표 {row['target_pct']}% / 손절 {row['stop_pct']:.1f}%")
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
        # 조건을 통과하지 못한 계획도 채점합니다. 매일 정원만큼 강도 순으로 내보내는
        # 구조라 그런 종목도 실제 추천에 들어가는데, 채점에서 빼면 화면에 '결과 대기'로
        # 영원히 남고 강도가 맞는지 검증할 표본도 모이지 않습니다.
        if not plan.get('stop_pct'):
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
        # 위 summary는 강도 보정을 위한 목표사다리 전체 집계입니다(관찰 종목 포함).
        # 아래는 그날 실제로 '이걸 사라'고 내보낸 10종목만, 고시했던 목표 기준으로
        # 채점한 결과입니다. 계좌 화면의 '목표 달성'(보유 종목 평가손익)과는 다른,
        # 추천 자체의 +/-입니다.
        'recommendation_summary': evaluate.summarize_recommendations(
            simulations, forecast.get('recommendations', [])),
        'complete': all(s.get('result') != 'no_data' for s in simulations),
    }


def rebuild_scoreboard():
    """전체 평가 기록으로 셋업별 적중률을 다시 계산합니다."""
    rows, all_rows = [], []
    for path in sorted((STATE / 'reports').glob('*-assessment.json')):
        record = read_json(path, {}) or {}
        simulations = record.get('simulations', [])
        rows.extend(evaluate.flatten_for_scoreboard(simulations))
        all_rows.extend(evaluate.flatten_for_scoreboard(simulations, conditions_only=False))
    scoreboard = confidence.tally(rows)
    write_json(SCOREBOARD, scoreboard)
    return scoreboard, all_rows


def close(client):
    """분봉을 축적한 뒤, 아직 평가하지 않은 날들을 재현합니다."""
    history.run(client)
    done = 0
    for path in sorted((STATE / 'reports').glob('*-forecast.json'), reverse=True)[:30]:
        forecast = read_json(path, {}) or {}
        date = forecast.get('trade_date')
        if not date:
            continue
        if now() < parse_time(forecast['close_at']) + timedelta(minutes=CLOSE_GRACE_MINUTES):
            continue
        existing = read_json(assessment_path(date))
        if existing and existing.get('complete'):
            continue
        result = assess_day(date)
        if result:
            write_json(assessment_path(date), result)
            publish_soft(date)
            done += 1
    scoreboard, rows = rebuild_scoreboard()
    buckets = strength.calibration(rows)
    if buckets:
        print('강도별 실제 적중률:')
        for level, value in sorted(buckets.items(), reverse=True):
            rate = value['hit_rate_pct']
            if rate is not None:
                print(f'  강도 {level:2d}: {value["wins"]}/{value["total"]}건 = {rate:.1f}%')
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
