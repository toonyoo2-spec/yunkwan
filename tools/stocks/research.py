#!/usr/bin/env python3
"""분석 요약을 한 곳에 모읍니다.

과거 채점·보유 기간 비교·강도 보정·우연 배제 판정은 지금까지 터미널에만 남았습니다.
이 파일이 그 결과를 하나의 요약으로 모아 저장하고, publish.py가 사이트로 올려
폰에서도 볼 수 있게 합니다.

올라가는 것은 비율(%)과 판정뿐입니다. 가격·거래량은 들어가지 않습니다.
"""
import json
import sys
from statistics import fmean

import confidence
import evaluate
import robustness
import strength
from store import STATE, read_json, write_json
from tossapi import now

RESEARCH_FILE = STATE / 'research.json'
MAX_SETUPS = 40


def collect_rows():
    """저장된 모든 채점 기록을 날짜·레짐과 함께 폅니다."""
    rows, all_rows, dates, regimes = [], [], [], {}
    for path in sorted((STATE / 'reports').glob('*-assessment.json')):
        date = path.name.split('-assessment')[0]
        record = read_json(path, {}) or {}
        simulations = record.get('simulations', [])
        if not simulations:
            continue
        dates.append(date)
        label = (record.get('market_regime') or {}).get('label', 'unknown')
        regimes[date] = label
        for target, bucket in ((rows, evaluate.flatten_for_scoreboard(simulations)),
                               (all_rows, evaluate.flatten_for_scoreboard(
                                   simulations, conditions_only=False))):
            target.extend({**row, 'date': date, 'regime': label} for row in bucket)
    return rows, all_rows, sorted(dates), regimes


def setup_section(rows):
    """셋업별 성적 + 우연 배제 판정 + 다중비교 보정."""
    board = confidence.tally(rows)
    entries, p_values = {}, {}
    for key, record in list(board.items())[:MAX_SETUPS]:
        subset = [row for row in rows if row['setup'] == key]
        verdict = robustness.assess(subset)
        p_values[key] = verdict['chance']['p_value']
        entries[key] = {
            'hits': record['hits'],
            'total': record['total'],
            'hit_rate_pct': (record['hit_rate'] or 0) * 100,
            'lower_bound_pct': (record['lower_bound'] or 0) * 100,
            'status': record['status'],
            'gate_reason': record['reason'],
            'chance_verdict': verdict['chance']['verdict'],
            'chance_reason': verdict['chance']['reason'],
            'concentration_reason': verdict['concentration']['reason'],
            'max_drawdown_pct': verdict['risk']['max_drawdown_pct'],
            'longest_losing_streak': verdict['risk']['longest_losing_streak'],
            'total_return_pct': verdict['risk']['total_return_pct'],
        }
    corrected = robustness.benjamini_hochberg(p_values)
    for key, value in corrected.items():
        entries[key]['survives_correction'] = value['survives']
        entries[key]['tested_count'] = value['tested']
    return entries


def regime_section(rows, regimes):
    """레짐별 성적. 표본이 한 레짐뿐이면 그 사실을 명시합니다."""
    buckets = {}
    for row in rows:
        bucket = buckets.setdefault(row.get('regime', 'unknown'), {'total': 0, 'wins': 0})
        bucket['total'] += 1
        bucket['wins'] += 1 if row.get('win') else 0
    result = {name: {**value,
                     'hit_rate_pct': 100 * value['wins'] / value['total'] if value['total'] else None}
              for name, value in sorted(buckets.items())}
    return {
        'by_regime': result,
        'days_by_regime': {label: sum(1 for value in regimes.values() if value == label)
                           for label in sorted(set(regimes.values()))},
        'single_regime_warning': (
            f'표본이 전부 "{next(iter(result))}" 레짐입니다. 다른 시장 상황에서도 통한다는 '
            '근거가 없습니다.') if len(result) == 1 else None,
    }


def strength_section(all_rows):
    """강도별 실제 적중률. 강도가 의미 있는 값인지 판정합니다."""
    buckets = strength.calibration(all_rows)
    levels = [(level, value['hit_rate_pct']) for level, value in sorted(buckets.items())
              if value['hit_rate_pct'] is not None]
    verdict = None
    if len(levels) >= 3:
        high = fmean(rate for level, rate in levels if level >= 7) if any(
            level >= 7 for level, _ in levels) else None
        low = fmean(rate for level, rate in levels if level <= 4) if any(
            level <= 4 for level, _ in levels) else None
        if high is not None and low is not None:
            gap = high - low
            verdict = {
                'high_avg_pct': high, 'low_avg_pct': low, 'gap_pct': gap,
                'works': gap > 5,
                'reason': (f'강도 7~10 평균 {high:.1f}% vs 강도 1~4 평균 {low:.1f}% '
                           f'({gap:+.1f}%p) — '
                           + ('강도가 작동합니다' if gap > 5
                              else '차이가 거의 없어 강도 계산을 고쳐야 합니다')),
            }
    return {'by_level': {str(level): value for level, value in sorted(buckets.items())},
            'verdict': verdict}


def build(horizon_result=None, walkforward_result=None):
    """전체 요약을 만들어 저장합니다."""
    rows, all_rows, dates, regimes = collect_rows()
    summary = {
        'updated_at': now().isoformat(),
        'trade_days': len(dates),
        'date_range': [dates[0], dates[-1]] if dates else None,
        'trade_count': len(rows),
        'target_hit_rate_pct': confidence.TARGET_HIT_RATE * 100,
        'setups': setup_section(rows),
        'regimes': regime_section(rows, regimes),
        'strength': strength_section(all_rows),
        'horizon': horizon_result,
        'walkforward': walkforward_result,
        'caveats': [
            '과거 채점은 오늘 거래대금 상위 종목으로 과거를 재현한 것이라 유니버스·생존 편향이 있습니다.',
            'VI·투자경고 등 거래 유의 지정은 과거 이력 조회가 없어 반영되지 않았습니다.',
            '프리마켓 호가 스프레드는 과거 조회가 불가능해 슬리피지를 1틱으로 고정했습니다.',
            '손익은 수수료·거래세·슬리피지를 뺀 거래당 수익률이며 계좌 수익률이 아닙니다.',
        ],
    }
    write_json(RESEARCH_FILE, summary)
    return summary


def describe(summary):
    """터미널용 한눈 요약."""
    lines = [f"분석 요약 — {summary['trade_days']}거래일 · 거래 {summary['trade_count']}건"]
    if summary.get('date_range'):
        lines.append(f"  기간: {summary['date_range'][0]} ~ {summary['date_range'][1]}")
    survivors = [key for key, value in summary['setups'].items()
                 if value.get('survives_correction')]
    lines.append(f"  다중비교까지 통과한 셋업: {', '.join(survivors) if survivors else '없음'}")
    verdict = summary['strength'].get('verdict')
    if verdict:
        lines.append(f"  강도 검증: {verdict['reason']}")
    warning = summary['regimes'].get('single_regime_warning')
    if warning:
        lines.append(f"  ⚠ {warning}")
    return '\n'.join(lines)


if __name__ == '__main__':
    horizon_result = read_json(STATE / 'horizon.json')
    result = build(horizon_result=horizon_result)
    print(describe(result))
    if '--publish' in sys.argv:
        import publish
        try:
            publish.publish_research(result)
        except (RuntimeError, ValueError) as exc:
            print('사이트 업로드 건너뜀:', exc)
