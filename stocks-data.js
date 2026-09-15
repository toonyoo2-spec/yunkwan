/*
  주식 기록 데이터 검증
  ---------------------------------------------------------------
  화면에 들어오는 데이터는 두 경로에서 옵니다.
    1) 맥북 로컬 뷰어 (http://127.0.0.1:8766/api/reports)
    2) 사이트 Supabase (stock_reports 테이블)

  두 경로 모두 tools/stocks/publish.py의 화이트리스트를 통과한 같은 형식입니다.
  가격(원) 값은 애초에 들어오지 않습니다. 비율(%)과 판정만 다룹니다.

  값이 이상하면 조용히 넘기지 않고 예외를 던집니다. 잘못된 숫자를 그럴듯하게
  보여주는 것이 아무것도 안 보여주는 것보다 나쁘기 때문입니다.
*/
(function (root) {
  'use strict';

  const MAX_ROWS = 200;

  const text = (value, max = 300) => {
    if (typeof value !== 'string' || !value.length || value.length > max) {
      throw Error('텍스트 형식 오류');
    }
    return value;
  };

  // 빈 문자열은 오류가 아니라 '값 없음'입니다. 서버가 빈 detail을 보낼 수 있어
  // 여기서 null로 바꿔주지 않으면 카드 전체가 렌더링되지 않습니다.
  const optionalText = (value, max = 1000) =>
    value == null || value === '' ? null : text(value, max);

  const stamp = (value) => {
    text(value, 60);
    if (!Number.isFinite(Date.parse(value))) throw Error('시각 형식 오류');
    return value;
  };

  const day = (value) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) throw Error('날짜 형식 오류');
    return value;
  };

  const num = (value) => {
    if (value == null) return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) throw Error('숫자 형식 오류');
    return parsed;
  };

  const bool = (value) => (typeof value === 'boolean' ? value : null);

  const list = (value) => {
    if (value == null) return [];
    if (!Array.isArray(value) || value.length > MAX_ROWS) throw Error('목록 형식 오류');
    return value;
  };

  function strengthOf(value) {
    if (value == null) return null;
    const level = num(value.level);
    if (level == null || level < 1 || level > 10) throw Error('강도 형식 오류');
    return {
      level,
      rawScore: num(value.raw_score),
      note: optionalText(value.note, 300),
      components: list(value.components).map((part) => ({
        label: text(part.label, 40),
        points: num(part.points),
        detail: optionalText(part.detail, 220),
      })),
    };
  }

  function reportOf(value) {
    if (value == null) return null;
    return {
      numbers: list(value.numbers).map((row) => ({
        key: text(row.key, 40),
        label: text(row.label, 40),
        unit: optionalText(row.unit, 10),
        value: num(row.value),
        note: optionalText(row.note, 200),
      })),
      flags: list(value.flags).map((row) => ({
        key: text(row.key, 40),
        label: text(row.label, 40),
        value: bool(row.value),
        detail: optionalText(row.detail, 120),
      })),
      newsCount: num(value.news_count),
      positiveLabels: list(value.positive_labels).map((v) => text(v, 60)),
      negativeLabels: list(value.negative_labels).map((v) => text(v, 60)),
    };
  }

  function recommendation(row) {
    return {
      symbol: text(row.symbol, 6),
      name: optionalText(row.name, 100),
      market: optionalText(row.market, 20),
      setup: text(row.setup, 60),
      reason: optionalText(row.reason, 400),
      gate: optionalText(row.gate, 300),
      entryRule: optionalText(row.entry_rule, 200),
      stopPct: num(row.stop_pct),
      targetPct: num(row.target_pct),
      strength: strengthOf(row.strength),
      report: reportOf(row.report),
      setupRecord: row.setup_record ? score(row.setup_record) : null,
      blocks: list(row.blocks).map((v) => text(v, 200)),
      tradeable: bool(row.tradeable),
      entryDeadline: optionalText(row.entry_deadline, 10),
      exitTime: optionalText(row.exit_time, 10),
    };
  }

  function heldRow(row) {
    return {
      symbol: text(row.symbol, 6),
      name: optionalText(row.name, 100),
      market: optionalText(row.market, 20),
      setup: text(row.setup, 60),
      gate: optionalText(row.gate, 300),
    };
  }

  function score(row) {
    return {
      hits: num(row.hits),
      total: num(row.total),
      hitRate: num(row.hit_rate),
      lowerBound: num(row.lower_bound),
      expectancyPct: num(row.expectancy_pct),
      excessPct: num(row.excess_pct),
      averageWinPct: num(row.average_win_pct),
      averageLossPct: num(row.average_loss_pct),
      payoffRatio: num(row.payoff_ratio),
      breakevenHitRate: num(row.breakeven_hit_rate),
      established: bool(row.established),
      target: num(row.target),
      status: text(row.status, 20),
      reason: optionalText(row.reason, 300),
    };
  }

  function ladderEntry(row) {
    return {
      targetPct: num(row.target_pct),
      result: text(row.result, 30),
      netPct: num(row.net_pct),
      win: bool(row.win),
      ambiguous: bool(row.ambiguous_bar),
    };
  }

  function simulation(row) {
    return {
      symbol: text(row.symbol, 6),
      name: optionalText(row.name, 100),
      setup: text(row.setup, 60),
      result: text(row.result, 30),
      recommended: bool(row.recommended),
      stopPct: num(row.stop_pct),
      strengthLevel: num(row.strength_level),
      note: optionalText(row.note, 200),
      ladder: list(row.ladder).map(ladderEntry),
    };
  }

  function forecast(value) {
    if (!value || typeof value !== 'object') throw Error('예보 형식 오류');
    const board = value.scoreboard && typeof value.scoreboard === 'object' ? value.scoreboard : {};
    return {
      tradeDate: day(value.trade_date),
      publishedAt: stamp(value.published_at),
      strategyVersion: optionalText(value.strategy_version, 40),
      targetHitRate: num(value.target_hit_rate),
      methodNote: optionalText(value.method_note, 1000),
      regime: value.regime
        ? {
            status: optionalText(value.regime.status, 30),
            allowLong: bool(value.regime.allow_long),
            reason: optionalText(value.regime.reason, 300),
          }
        : null,
      goal: value.goal || null,
      recommendations: list(value.recommendations).map(recommendation),
      held: list(value.held).map(heldRow),
      scoreboard: Object.fromEntries(
        Object.entries(board)
          .slice(0, MAX_ROWS)
          .map(([key, row]) => [key, score(row)])
      ),
    };
  }

  function recommendationRow(row) {
    return {
      symbol: text(row.symbol, 6),
      name: optionalText(row.name, 100),
      status: text(row.status, 30),
      netPct: num(row.net_pct),
      win: bool(row.win),
    };
  }

  // 추천 10종목 자체의 +/-. 보유 종목 평가손익(goal)과는 분리된 값입니다.
  function recommendationSummary(value) {
    if (!value || typeof value !== 'object') return null;
    return {
      recommendedCount: num(value.recommended_count),
      enteredCount: num(value.entered_count),
      winCount: num(value.win_count),
      netSumPct: num(value.net_sum_pct),
      netAvgPct: num(value.net_avg_pct),
      rows: list(value.rows).map(recommendationRow),
      note: optionalText(value.note, 300),
    };
  }

  function assessment(value) {
    if (value == null) return null;
    const summary = value.summary || {};
    return {
      tradeDate: day(value.trade_date),
      assessedAt: stamp(value.assessed_at),
      complete: bool(value.complete),
      simulations: list(value.simulations).map(simulation),
      summary: {
        tradedCount: num(summary.traded_count),
        skippedCount: num(summary.skipped_count),
        perTarget: summary.per_target || {},
        note: optionalText(summary.note, 400),
      },
      recommendationSummary: recommendationSummary(value.recommendation_summary),
    };
  }

  function normalize(row) {
    if (!row || typeof row !== 'object') throw Error('기록 형식 오류');
    return {
      tradeDate: day(row.trade_date),
      forecast: forecast(row.forecast),
      assessment: assessment(row.assessment),
    };
  }

  function normalizeAll(rows) {
    return list(rows)
      .map(normalize)
      .sort((a, b) => (a.tradeDate < b.tradeDate ? 1 : -1));
  }

  function research(value) {
    if (!value || typeof value !== 'object') return null;
    const setups = value.setups && typeof value.setups === 'object' ? value.setups : {};
    return {
      updatedAt: value.updated_at || null,
      tradeDays: num(value.trade_days),
      tradeCount: num(value.trade_count),
      dateRange: Array.isArray(value.date_range) ? value.date_range.slice(0, 2).map(day) : null,
      targetHitRatePct: num(value.target_hit_rate_pct),
      setups: Object.fromEntries(
        Object.entries(setups)
          .slice(0, MAX_ROWS)
          .map(([key, row]) => [key, {
            hits: num(row.hits),
            total: num(row.total),
            hitRatePct: num(row.hit_rate_pct),
            lowerBoundPct: num(row.lower_bound_pct),
            status: optionalText(row.status, 20),
            chanceVerdict: optionalText(row.chance_verdict, 20),
            chanceReason: optionalText(row.chance_reason, 240),
            concentrationReason: optionalText(row.concentration_reason, 240),
            maxDrawdownPct: num(row.max_drawdown_pct),
            longestLosingStreak: num(row.longest_losing_streak),
            survivesCorrection: bool(row.survives_correction),
            testedCount: num(row.tested_count),
          }])
      ),
      regimes: value.regimes || null,
      strength: value.strength || null,
      horizon: list(value.horizon).map((row) => ({
        holdDays: num(row.hold_days),
        trades: num(row.trades),
        hitRatePct: num(row.hit_rate_pct),
        meanNetPct: num(row.mean_net_pct),
        totalReturnPct: num(row.total_return_pct),
        maxDrawdownPct: num(row.max_drawdown_pct),
        longestLosingStreak: num(row.longest_losing_streak),
        verdict: optionalText(row.verdict, 240),
      })),
      caveats: list(value.caveats).map((line) => text(line, 300)),
    };
  }

  const api = { normalize, normalizeAll, research };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.StockData = api;
})(typeof window !== 'undefined' ? window : globalThis);
