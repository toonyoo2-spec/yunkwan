#!/usr/bin/env python3
"""셋업별 적중률 추적과 신뢰도 게이트.

목표 적중률을 "희망"이 아니라 "관문"으로 다룹니다.
어떤 셋업이 실제로 기록한 적중률의 Wilson 95% 하한이 목표를 넘을 때만 추천으로 내보내고,
넘지 못하면 추천을 만들지 않습니다. 하루 0개를 허용하는 것이 이 구조의 핵심입니다.

왜 단순 비율이 아니라 Wilson 하한인가:
  10건 중 8건 적중(80%)은 표본이 적어 우연일 수 있습니다. Wilson 하한은 표본이 적을수록
  값을 크게 깎기 때문에, 운 좋은 소수 표본이 관문을 통과하는 것을 막아줍니다.
  10건 중 8건 → 하한 약 49%. 100건 중 80건 → 하한 약 71%.
"""
import math

TARGET_HIT_RATE = 0.70      # 사용자가 요구한 최소 적중률
CONFIDENCE_Z = 1.96         # 95% 신뢰구간
MIN_SAMPLES = 30            # 이보다 표본이 적으면 통과시키지 않고 '관찰 중'으로 둡니다


def wilson_lower_bound(hits, total, z=CONFIDENCE_Z):
    """적중 비율의 Wilson 신뢰구간 하한. 표본이 적으면 보수적으로 낮아집니다."""
    if total <= 0:
        return 0.0
    phat = hits / total
    denominator = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denominator)


def evaluate_setup(hits, total, target=TARGET_HIT_RATE, min_samples=MIN_SAMPLES):
    """셋업 하나의 통과 여부를 판정합니다."""
    lower = wilson_lower_bound(hits, total)
    if total < min_samples:
        status = 'observing'
        reason = f'표본 {total}건 — {min_samples}건을 모아야 판정합니다'
    elif lower >= target:
        status = 'passed'
        reason = f'적중률 하한 {lower * 100:.1f}% ≥ 목표 {target * 100:.0f}%'
    else:
        status = 'blocked'
        reason = f'적중률 하한 {lower * 100:.1f}% < 목표 {target * 100:.0f}% — 추천하지 않습니다'
    return {
        'hits': hits,
        'total': total,
        'hit_rate': hits / total if total else None,
        'lower_bound': lower,
        'target': target,
        'status': status,
        'reason': reason,
    }


def tally(outcomes):
    """청산 기록들을 셋업 이름별로 집계합니다. outcome은 evaluate.py가 만든 결과 행입니다."""
    counters = {}
    for row in outcomes:
        setup = row.get('setup')
        if not setup or row.get('result') not in ('target', 'stop', 'timeout'):
            continue
        bucket = counters.setdefault(setup, {'hits': 0, 'total': 0})
        bucket['total'] += 1
        # 적중 = 비용을 뺀 뒤에도 수익이 남은 거래. 목표가 도달만으로 세지 않습니다.
        if row.get('net_pct', 0) > 0:
            bucket['hits'] += 1
    return {name: evaluate_setup(value['hits'], value['total'])
            for name, value in sorted(counters.items())}


def gate(setup_name, scoreboard):
    """추천을 내보내도 되는 셋업인지 확인합니다. 기록이 없으면 통과시키지 않습니다."""
    record = scoreboard.get(setup_name)
    if not record:
        return False, f'{setup_name}: 기록 없음 — 관찰만 합니다'
    return record['status'] == 'passed', f"{setup_name}: {record['reason']}"
