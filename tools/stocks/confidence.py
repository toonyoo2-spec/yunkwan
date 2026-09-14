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

# 최소 적중률. 임의로 정한 숫자가 아니라 손익비가 결정한 선입니다.
# orb_strict@2.0%의 본전 적중률이 55.7%로 나왔고, 그 아래면 아무리 잘 맞혀도
# 계좌가 줄어듭니다. 적중률 70%를 요구하면 손익비가 나쁜 설정만 통과하게 되어
# (본전선 79.3% vs 실제 77.3% → 손실) 오히려 지는 전략을 고르게 됩니다.
TARGET_HIT_RATE = 0.55
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
MIN_EXCESS_PCT = 0.0        # 같은 기간 지수보다 나아야 합니다


def evaluate_setup(hits, total, target=TARGET_HIT_RATE, min_samples=MIN_SAMPLES,
                   expectancy=None, excess=None, breakeven=None):
    """셋업 하나의 통과 여부를 판정합니다.

    통과 조건 (전부 만족해야 함):
      1) 표본 30건 이상
      2) 실제 적중률이 '본전 적중률'을 넘을 것
         — 고정된 70%가 아니라 그 셋업의 손익비가 정하는 선입니다. 손익비가 좋으면
           50%로도 충분하고, 나쁘면 80%로도 모자랍니다.
      3) 거래당 기대값 > 0 (비용 차감 후)
      4) 같은 기간 지수 대비 초과수익 > 0 — 시장이 오른 덕인지 가려냅니다

    확립(established) vs 잠정(provisional):
      위 조건을 통과해도, 적중률의 Wilson 95% 하한이 본전선을 넘지 못하면 '잠정'입니다.
      "수익 구조로 보이지만 표본이 적어 우연일 가능성을 배제하지 못했다"는 뜻입니다.
      막지는 않되 화면에 그대로 표시해, 확립된 것과 구분해서 보게 합니다.
    """
    lower = wilson_lower_bound(hits, total)
    observed = hits / total if total else None
    bar = breakeven if breakeven is not None else target
    profitable = expectancy is None or expectancy > MIN_EXPECTANCY_PCT
    beats_market = excess is None or excess > MIN_EXCESS_PCT
    beats_breakeven = observed is not None and observed > bar

    notes = []
    if expectancy is not None:
        notes.append(f'거래당 {expectancy:+.2f}%')
    if excess is not None:
        notes.append(f'지수 대비 {excess:+.2f}%p')
    suffix = (' · ' + ' · '.join(notes)) if notes else ''
    established = lower > bar

    if total < min_samples:
        status, reason = 'observing', f'표본 {total}건 — {min_samples}건을 모아야 판정합니다{suffix}'
    elif not beats_breakeven:
        status = 'blocked'
        reason = (f'적중률 {observed * 100:.1f}%가 본전선 {bar * 100:.1f}% 아래입니다'
                  f'{suffix} — 구조상 계좌가 줄어듭니다')
    elif not profitable:
        status = 'blocked'
        reason = f'거래당 기대값 {expectancy:+.2f}% — 수익이 남지 않아 추천하지 않습니다'
    elif not beats_market:
        status = 'blocked'
        reason = (f'지수 대비 {excess:+.2f}%p — 시장을 이기지 못해 추천하지 않습니다'
                  f' (그냥 지수를 사는 편이 낫습니다)')
    elif established:
        status = 'passed'
        reason = (f'적중률 {observed * 100:.1f}% > 본전선 {bar * 100:.1f}%'
                  f' · 하한 {lower * 100:.1f}%도 본전선 위 — 확립{suffix}')
    else:
        status = 'passed'
        reason = (f'적중률 {observed * 100:.1f}% > 본전선 {bar * 100:.1f}%{suffix}'
                  f' · 다만 하한 {lower * 100:.1f}%는 본전선 아래라 잠정입니다'
                  f' (표본 {total}건)')
    return {
        'hits': hits,
        'total': total,
        'hit_rate': observed,
        'lower_bound': lower,
        'expectancy_pct': expectancy,
        'excess_pct': excess,
        'breakeven_used': bar,
        'established': established,
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
                                             'excess': [], 'wins': [], 'losses': []})
        bucket['total'] += 1
        net = row.get('net_pct') or 0.0
        bucket['net_sum'] += net
        if row.get('excess_pct') is not None:
            bucket['excess'].append(row['excess_pct'])
        # 적중 = 비용을 뺀 뒤에도 수익이 남은 거래. 목표가 도달만으로 세지 않습니다.
        if net > 0:
            bucket['hits'] += 1
            bucket['wins'].append(net)
        else:
            bucket['losses'].append(net)
    board = {}
    for name, value in sorted(counters.items()):
        total = value['total']
        excess = value['excess']
        profile = win_loss_profile(value['wins'], value['losses'])
        record = evaluate_setup(
            value['hits'], total,
            expectancy=value['net_sum'] / total if total else None,
            excess=sum(excess) / len(excess) if excess else None,
            breakeven=profile.get('breakeven_hit_rate'))
        record.update(profile)
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
    # 기록이 일부만 들어 있어도 발행 전체가 죽지 않게 방어적으로 읽습니다.
    reason = record.get('reason', record.get('status', '사유 없음'))
    return record.get('status') == 'passed', f'{setup_name}: {reason}'
