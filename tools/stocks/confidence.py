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


MIN_EXPECTANCY_PCT = 0.0    # 거래당 기대값이 이보다 커야 합니다 (비용 차감 후)


def evaluate_setup(hits, total, target=TARGET_HIT_RATE, min_samples=MIN_SAMPLES,
                   expectancy=None):
    """셋업 하나의 통과 여부를 판정합니다.

    적중률만으로는 판정할 수 없습니다. 목표 1%·손절 2.5%처럼 손익비가 나쁘면
    적중률 67%로도 돈을 잃습니다(67% × 0.8% − 33% × 2.0% = −0.1%). 실제로
    이 시스템의 첫 결과가 정확히 그랬습니다 — 적중률 67.7%, 거래당 −0.45%.

    그래서 두 조건을 모두 넘겨야 통과입니다.
      1) 적중률 하한(Wilson 95%)이 목표 이상
      2) 거래당 기대값이 0보다 큼 (수수료·거래세·슬리피지 차감 후)
    """
    lower = wilson_lower_bound(hits, total)
    profitable = expectancy is None or expectancy > MIN_EXPECTANCY_PCT
    expectancy_note = '' if expectancy is None else f' · 거래당 {expectancy:+.2f}%'

    if total < min_samples:
        status = 'observing'
        reason = f'표본 {total}건 — {min_samples}건을 모아야 판정합니다{expectancy_note}'
    elif lower >= target and profitable:
        status = 'passed'
        reason = f'적중률 하한 {lower * 100:.1f}% ≥ 목표 {target * 100:.0f}%{expectancy_note}'
    elif lower >= target and not profitable:
        status = 'blocked'
        reason = (f'적중률 하한 {lower * 100:.1f}%로 목표는 넘겼지만 거래당 기대값이 '
                  f'{expectancy:+.2f}%입니다 — 자주 맞히고 크게 잃는 구조라 추천하지 않습니다')
    else:
        status = 'blocked'
        reason = (f'적중률 하한 {lower * 100:.1f}% < 목표 {target * 100:.0f}%'
                  f'{expectancy_note} — 추천하지 않습니다')
    return {
        'hits': hits,
        'total': total,
        'hit_rate': hits / total if total else None,
        'lower_bound': lower,
        'expectancy_pct': expectancy,
        'target': target,
        'status': status,
        'reason': reason,
    }


def tally(outcomes):
    """청산 기록들을 셋업 이름별로 집계합니다. outcome은 evaluate.py가 만든 결과 행입니다.

    적중 횟수와 함께 거래당 기대값도 모읍니다. 적중률만으로는 '자주 맞히고 크게 잃는'
    전략을 걸러낼 수 없기 때문입니다.
    """
    counters = {}
    for row in outcomes:
        setup = row.get('setup')
        if not setup or row.get('result') not in ('target', 'stop', 'timeout'):
            continue
        bucket = counters.setdefault(setup, {'hits': 0, 'total': 0, 'net_sum': 0.0,
                                             'wins': [], 'losses': []})
        bucket['total'] += 1
        net = row.get('net_pct') or 0.0
        bucket['net_sum'] += net
        # 적중 = 비용을 뺀 뒤에도 수익이 남은 거래. 목표가 도달만으로 세지 않습니다.
        if net > 0:
            bucket['hits'] += 1
            bucket['wins'].append(net)
        else:
            bucket['losses'].append(net)
    board = {}
    for name, value in sorted(counters.items()):
        total = value['total']
        record = evaluate_setup(value['hits'], total,
                                expectancy=value['net_sum'] / total if total else None)
        record.update(win_loss_profile(value['wins'], value['losses']))
        board[name] = record
    return board


def win_loss_profile(wins, losses):
    """평균이익·평균손실과 손익비, 그리고 본전을 맞추는 데 필요한 적중률.

    적중률 목표를 몇 %로 잡아야 하는지는 취향이 아니라 손익비가 결정합니다.
    평균이익이 평균손실의 절반이면 본전만 맞추는 데도 적중률 67%가 필요합니다.
    이 숫자를 보여주지 않으면 '70%면 충분하다'는 잘못된 기대가 생깁니다.
    """
    average_win = sum(wins) / len(wins) if wins else None
    average_loss = abs(sum(losses) / len(losses)) if losses else None
    if not average_win or not average_loss:
        return {'average_win_pct': average_win, 'average_loss_pct': average_loss,
                'payoff_ratio': None, 'breakeven_hit_rate': None}
    payoff = average_win / average_loss
    return {
        'average_win_pct': average_win,
        'average_loss_pct': average_loss,
        'payoff_ratio': payoff,
        # 본전 적중률 = 평균손실 / (평균이익 + 평균손실)
        'breakeven_hit_rate': average_loss / (average_win + average_loss),
    }


def gate(setup_name, scoreboard):
    """추천을 내보내도 되는 셋업인지 확인합니다. 기록이 없으면 통과시키지 않습니다."""
    record = scoreboard.get(setup_name)
    if not record:
        return False, f'{setup_name}: 기록 없음 — 관찰만 합니다'
    return record['status'] == 'passed', f"{setup_name}: {record['reason']}"
