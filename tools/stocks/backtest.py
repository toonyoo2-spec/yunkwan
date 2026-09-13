#!/usr/bin/env python3
"""과거 데이터로 셋업을 채점합니다.

daily_runner의 close는 08:30에 '고정해둔' 계획만 채점합니다. 과거 날짜에는 그 계획이
없으므로, 여기서 그 날짜 시점의 데이터만으로 계획을 소급 생성한 뒤 그날 분봉으로
시뮬레이션합니다. 결과는 실시간 기록과 같은 형식으로 저장되어 같은 관문에 들어갑니다.

미래 정보 차단:
  특징은 전부 기준일 '이전' 데이터만 씁니다(features.bars_before, net_flow의 date 필터).
  분봉은 기준일 당일 것만 쓰고, 진입·청산 판정은 시간 순서대로만 진행합니다.

정직하게 밝혀두는 한계 — 이 결과를 실시간 성과와 동일하게 취급하면 안 됩니다:
  1. 유니버스 편향: 오늘 거래대금 상위 종목으로 과거를 채점합니다. 그날 실제로
     상위였던 종목과 다르고, 지금까지 살아남은 종목만 봅니다(생존 편향).
  2. VI·투자경고 등 거래 유의 지정은 과거 이력 조회가 없어 현재 상태만 반영됩니다.
  3. 공시·뉴스는 게시일 기준으로만 거르며, 장중 몇 시에 나왔는지는 알 수 없습니다.
  4. 호가 스프레드가 없어 슬리피지는 1틱 가정으로 고정합니다.
  그래서 이 점수는 '가능성 탐색'이지 '검증 완료'가 아닙니다. 실시간 기록이 쌓이면
  그쪽 숫자가 진짜입니다.
"""
import fcntl
import sys
from datetime import datetime

import confidence
import evaluate
import history
import robustness
import signals
import strength
from features import build as build_features, number
from store import STATE, read_json, write_json
from tossapi import Client, now

BACKTEST_MARK = 'backtest'
ETF_CACHE = STATE / 'global' / '_etf_daily.json'
ETF_COUNT = 200
KOSPI_DAILY = STATE / 'daily' / '_KOSPI.json'


def stored_dates():
    """분봉을 확보한 날짜들. 종목 폴더를 훑어 모읍니다."""
    root = STATE / 'history'
    if not root.exists():
        return []
    dates = set()
    for symbol_dir in root.iterdir():
        if not symbol_dir.is_dir() or symbol_dir.name.startswith('_'):
            continue
        for path in symbol_dir.glob('*.json'):
            dates.add(path.stem)
    return sorted(dates)


def etf_history(client):
    """새벽 해외시장 판정을 과거 날짜에도 하기 위해 ETF 일봉을 모아둡니다."""
    cached = read_json(ETF_CACHE)
    if cached:
        return cached['series']
    import global_context
    series = {}
    for symbol in global_context.PROXIES:
        page = client.get_optional('/api/v1/candles', symbol=symbol, interval='1d',
                                   count=ETF_COUNT, adjusted='false')
        bars = (page or {}).get('candles', [])
        # 최신순 응답을 날짜 오름차순으로 되돌려 전일 대비 등락률을 만듭니다.
        ordered = sorted(bars, key=lambda row: row['timestamp'])
        changes = {}
        for previous, current in zip(ordered, ordered[1:]):
            before, after = number(previous.get('closePrice')), number(current.get('closePrice'))
            if before and after:
                changes[current['timestamp'][:10]] = (after / before - 1) * 100
        series[symbol] = changes
    write_json(ETF_CACHE, {'updated_at': now().isoformat(), 'series': series})
    return series


def regime_for(series, date):
    """그 날짜의 새벽 해외시장 판정.

    미국장은 한국시간 새벽에 끝나므로, 한국의 date 아침에 알 수 있는 것은
    미국 날짜로 date 직전 거래일의 종가입니다. ETF 일봉에서 date보다 앞선
    가장 최근 날짜를 씁니다.
    """
    import global_context
    markets = {}
    for symbol, changes in series.items():
        usable = [day for day in changes if day < date]
        if usable:
            markets[symbol] = {'change_pct': changes[max(usable)], 'as_of': max(usable)}
    return global_context.regime(markets), markets


def plans_for(date, symbols, index_candles, series, scoreboard):
    """그 날짜 시점의 특징으로 계획을 만듭니다."""
    regime, markets = regime_for(series, date)
    built = []
    for symbol in symbols:
        daily = read_json(history.daily_path(symbol), {}) or {}
        flows = read_json(history.flow_path(symbol), {}) or {}
        row = build_features(symbol, date, daily.get('candles', []), index_candles, flows)
        news_row = read_json(STATE / 'news' / symbol / f'{date}.json', {}) or {}
        row.update(news_row.get('features', {}))
        for setup_name in signals.SETUPS:
            # 과거 유의 지정 이력이 없어 경고 목록은 비워둡니다 (한계 3번).
            plan = signals.plan(row, [], setup_name)
            plan['name'] = symbol
            plan['features'] = row
            # 강도도 그 시점 기준으로 매겨둡니다. 나중에 '강도가 높을수록 실제로 잘 맞았는가'를
            # 검증하려면 거래마다 강도가 함께 기록돼 있어야 합니다.
            plan['strength'] = strength.score(row, scoreboard.get(f'{setup_name}@1.0%'),
                                              regime.get('status'))
            built.append(plan)
    return built, regime, markets


def run(client, limit=None):
    dates = stored_dates()
    if not dates:
        print('분봉이 없습니다. 먼저 `python3 history.py backfill`을 실행하세요.')
        return
    if limit:
        dates = dates[-limit:]
    series = etf_history(client)
    index_candles = (read_json(KOSPI_DAILY, {}) or {}).get('candles', [])
    if not index_candles:
        print('코스피 일봉이 없어 상대강도를 계산할 수 없습니다. prepare를 한 번 실행하세요.')
        return
    universe = read_json(history.universe_path(max(dates)), {}) or {}
    symbols = [entry['symbol'] for entry in universe.get('symbols', [])]
    if not symbols:
        latest = sorted((STATE / 'universe').glob('*.json'))[-1:]
        symbols = [entry['symbol']
                   for entry in (read_json(latest[0], {}) or {}).get('symbols', [])] if latest else []
    if not symbols:
        print('유니버스 기록이 없습니다. prepare 또는 backfill을 먼저 실행하세요.')
        return
    scoreboard = read_json(STATE / 'scoreboard.json', {}) or {}
    print(f'{len(dates)}일 × 종목 {len(symbols)}개 × 셋업 {len(signals.SETUPS)}개 채점 시작')
    scored = 0
    for date in dates:
        plans, regime, markets = plans_for(date, symbols, index_candles, series, scoreboard)
        # 조건 미충족 계획도 채점합니다 — 실제 운영에서도 강도 순으로 추천에 들어갑니다.
        tradeable = [p for p in plans if p.get('tradeable')]
        simulations = []
        index_minutes = (read_json(history.index_path('KOSPI', date), {}) or {}).get('candles', [])
        for plan in plans:
            stored = read_json(history.history_path(plan['symbol'], date), {}) or {}
            result = evaluate.simulate(plan, stored.get('candles', []), index_minutes)
            result['name'] = plan['symbol']
            result['recommended'] = False       # 소급 채점은 실제 추천이 아니었습니다.
            simulations.append(result)
        traded = sum(1 for s in simulations if s.get('result') == 'traded')
        write_json(STATE / 'reports' / f'{date}-assessment.json', {
            'trade_date': date,
            'assessed_at': now().isoformat(),
            'source': BACKTEST_MARK,
            'regime': regime,
            # 시장 레짐을 함께 남깁니다. 한 레짐의 성적을 다른 레짐에 적용하면 안 되므로,
            # 나중에 레짐별로 따로 채점할 수 있어야 합니다.
            'market_regime': robustness.market_regime(index_candles, date),
            'overnight': {k: v['change_pct'] for k, v in markets.items()},
            'simulations': simulations,
            'summary': evaluate.summarize(simulations),
            'complete': True,
        })
        scored += traded
        print(f'  {date}: 조건 통과 {len(tradeable)}건 → 진입 발생 {traded}건'
              f' · 새벽 {regime.get("status")}')
    rows, all_rows = [], []
    for path in sorted((STATE / 'reports').glob('*-assessment.json')):
        record = read_json(path, {}) or {}
        simulations = record.get('simulations', [])
        rows.extend(evaluate.flatten_for_scoreboard(simulations))
        all_rows.extend(evaluate.flatten_for_scoreboard(simulations, conditions_only=False))
    scoreboard = confidence.tally(rows)
    write_json(STATE / 'scoreboard.json', scoreboard)
    print(f'\n총 진입 {scored}건. 셋업별 판정:')
    if not scoreboard:
        print('  채점된 거래가 없습니다. 조건이 너무 엄격하거나 분봉이 부족합니다.')
    for key, record in sorted(scoreboard.items(),
                              key=lambda item: -(item[1]['lower_bound'] or 0)):
        print(f'  {key}: {record["reason"]} (표본 {record["total"]}건, 적중 {record["hits"]}건)')
    buckets = strength.calibration(all_rows)
    if buckets:
        print('\n강도별 실제 적중률 (강도가 의미 있는 값인지 확인):')
        for level, value in sorted(buckets.items(), reverse=True):
            rate = value['hit_rate_pct']
            print(f'  강도 {level:2d}: {value["wins"]:4d}/{value["total"]:4d}건'
                  f' = {rate:.1f}%' if rate is not None else f'  강도 {level:2d}: 표본 없음')
        print('  강도가 높을수록 적중률이 높아지지 않으면 강도 계산이 틀린 것입니다.')

    print('\n이 점수는 소급 채점입니다. 유니버스·생존 편향이 있어 실시간 성과와 같지 않습니다.')


def collect_rows(dates):
    """해당 날짜들의 채점 기록을 모읍니다."""
    wanted = set(dates)
    rows, all_rows = [], []
    for path in sorted((STATE / 'reports').glob('*-assessment.json')):
        date = path.name.split('-assessment')[0]
        if date not in wanted:
            continue
        record = read_json(path, {}) or {}
        simulations = record.get('simulations', [])
        regime_label = (record.get('market_regime') or {}).get('label', 'unknown')
        for target, bucket in ((rows, evaluate.flatten_for_scoreboard(simulations)),
                               (all_rows, evaluate.flatten_for_scoreboard(
                                   simulations, conditions_only=False))):
            target.extend({**row, 'date': date, 'regime': regime_label} for row in bucket)
    return rows, all_rows


def group_by(rows, key):
    buckets = {}
    for row in rows:
        buckets.setdefault(row.get(key), []).append(row)
    return buckets


def report(dates, label, baseline_rates=None):
    """한 구간의 성적표.

    적중률 숫자 하나만 보면 속습니다. 그 숫자가 우연인지, 한 곳에서 몰아 나왔는지,
    견딜 수 있는 손실인지, 특정 시장 상황에서만 되는 건지를 함께 봅니다.
    """
    rows, all_rows = collect_rows(dates)
    board = confidence.tally(rows)
    print(f'\n[{label}] {len(dates)}거래일 · 거래 {len(rows)}건')
    if not board:
        print('  채점된 거래가 없습니다.')
        return board, {}

    # 셋업별: 적중률 + 우연일 확률 + 집중도 + 견딜 수 있는 손실
    p_values = {}
    for key in sorted(board, key=lambda name: -(board[name]['lower_bound'] or 0)):
        record = board[key]
        subset = [row for row in rows if row['setup'] == key]
        baseline = (baseline_rates or {}).get(key, robustness.DEFAULT_BASELINE)
        verdict = robustness.assess(subset, baseline)
        p_values[key] = verdict['chance']['p_value']
        rate = record['hit_rate']
        nets = [row['net_pct'] for row in subset if row.get('net_pct') is not None]
        excess = [row['excess_pct'] for row in subset if row.get('excess_pct') is not None]
        mean_net = sum(nets) / len(nets) if nets else 0.0
        mean_excess = sum(excess) / len(excess) if excess else None
        line = (f'  {key}: {record["hits"]}/{record["total"]}건 = {rate * 100:.1f}%'
                f' (하한 {record["lower_bound"] * 100:.1f}%) · 거래당 {mean_net:+.2f}%')
        if mean_excess is not None:
            line += f' · 지수 대비 {mean_excess:+.2f}%p'
        print(line)
        print(f'      {robustness.summarize(verdict)}')

    # 여러 조합을 동시에 시험한 대가를 보정합니다.
    corrected = robustness.benjamini_hochberg(p_values)
    survivors = [key for key, value in corrected.items() if value['survives']]
    if corrected:
        tested = next(iter(corrected.values()))['tested']
        print(f'  다중비교 보정({tested}개 조합 동시 검정): '
              + (', '.join(survivors) + ' 만 살아남음' if survivors
                 else '살아남는 조합 없음 — 통과처럼 보인 것은 우연일 수 있습니다'))

    # 강도가 실제로 작동하는지
    buckets = strength.calibration(all_rows)
    if buckets:
        print('  강도별 적중률:', ' · '.join(
            f'{level}→{value["hit_rate_pct"]:.0f}%({value["total"]}건)'
            for level, value in sorted(buckets.items(), reverse=True)
            if value['hit_rate_pct'] is not None))

    # 시장 레짐별로 나눠 봅니다. 한 레짐에만 통하는 전략인지 확인하는 용도입니다.
    by_regime = group_by(rows, 'regime')
    if len(by_regime) > 1:
        print('  레짐별 적중률:', ' · '.join(
            f'{name}→{100 * sum(1 for r in subset if r.get("win")) / len(subset):.0f}%'
            f'({len(subset)}건)'
            for name, subset in sorted(by_regime.items()) if subset))
    elif by_regime:
        only = next(iter(by_regime))
        print(f'  주의: 표본이 전부 "{only}" 레짐입니다. 다른 시장 상황에서도 통한다는'
              f' 근거가 없습니다.')
    return board, buckets


def walkforward(split_date=None):
    """앞 구간으로 규칙을 보고, 뒤 구간으로 검증합니다.

    과거에 잘 맞는 규칙은 언제든 찾을 수 있습니다. 조건을 바꿔가며 맞을 때까지 돌리면
    되니까요. 그게 과적합이고, 그렇게 만든 규칙은 실전에서 무너집니다.

    유일한 방어는 '규칙을 만드는 데 쓰지 않은 구간'에서 확인하는 것입니다. 앞 구간을
    보고 조건을 고쳤다면, 뒤 구간 성적만이 의미 있는 숫자입니다. 뒤 구간을 보고 또
    고치는 순간 그 구간도 학습 데이터가 되어 버립니다.
    """
    dates = stored_dates()
    if len(dates) < 4:
        print('구간을 나눌 만큼 날짜가 없습니다.')
        return
    split_date = split_date or dates[len(dates) // 2]
    train = [d for d in dates if d < split_date]
    test = [d for d in dates if d >= split_date]
    if not train or not test:
        print('분할 기준일이 범위를 벗어났습니다:', split_date)
        return
    print(f'분할 기준일: {split_date}')
    print(f'  학습 구간 {train[0]} ~ {train[-1]} ({len(train)}일)')
    print(f'  검증 구간 {test[0]} ~ {test[-1]} ({len(test)}일)')
    train_board, _ = report(train, '학습 구간 — 조건을 보고 고쳐도 되는 구간')
    test_board, _ = report(test, '검증 구간 — 여기 숫자만 의미가 있습니다')

    print('\n[비교] 같은 셋업의 두 구간 적중률')
    keys = sorted(set(train_board) | set(test_board))
    if not keys:
        print('  비교할 셋업이 없습니다.')
    for key in keys:
        before = (train_board.get(key) or {}).get('hit_rate')
        after = (test_board.get(key) or {}).get('hit_rate')
        if before is None or after is None:
            print(f'  {key}: 한쪽 구간에 표본이 없어 비교 불가')
            continue
        gap = (after - before) * 100
        verdict = '유지' if gap > -10 else '급락 — 과적합 의심'
        print(f'  {key}: 학습 {before * 100:.1f}% → 검증 {after * 100:.1f}%'
              f' ({gap:+.1f}%p) {verdict}')
    print('\n검증 구간을 보고 조건을 또 고치면 그 구간도 학습 데이터가 됩니다.')
    print('고친 뒤에는 아직 쓰지 않은 새 구간에서 다시 확인해야 합니다.')


if __name__ == '__main__':
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'runner.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('다른 수집 실행이 진행 중입니다. 끝난 뒤 다시 실행하세요.')
            sys.exit(1)
        command = sys.argv[1] if len(sys.argv) > 1 else 'run'
        if command == 'walkforward':
            walkforward(sys.argv[2] if len(sys.argv) > 2 else None)
            sys.exit(0)
        api = Client()
        try:
            run(api, limit=int(command) if command.isdigit() else None)
        finally:
            api.save_archive()
