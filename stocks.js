/*
  주식 기록 화면
  ---------------------------------------------------------------
  두 곳에서 데이터를 읽습니다.
    - 맥북 로컬 뷰어: http://127.0.0.1:8766/api/reports
    - 사이트: Supabase stock_reports (본인 계정 행만 RLS로 내려옵니다)

  가격(원)은 애초에 내려오지 않습니다. 토스 약관 제5조 ③에 따라 시세는 맥북 밖으로
  내보내지 않고, 우리가 계산한 비율(%)과 판정만 올립니다.
*/
(function () {
  'use strict';

  // 수집 계정이 아니면 기록 화면 대신 안내만 보여줍니다. 비워두면 계정 구분 없이
  // 각자 자기 기록만 보게 됩니다. 예: ['yk']
  const OWNER_LOGIN_IDS = [];
  const MAX_RECORDS = 120;
  const REFRESH_MS = 60000;

  const $ = (id) => document.getElementById(id);
  const loginId = (session) => String(session.user.email || '').split('@')[0].toLowerCase();
  const isOwner = (session) =>
    !OWNER_LOGIN_IDS.length || window.STOCK_LOCAL_MODE || OWNER_LOGIN_IDS.includes(loginId(session));

  const pct = (value, digits = 2) =>
    value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`;
  const plain = (value, digits = 1) => (value == null ? '—' : `${value.toFixed(digits)}%`);
  const when = (value) =>
    value ? new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '—';

  const RESULT_LABEL = {
    target: '목표 도달',
    stop: '손절',
    timeout: '시간 청산',
    no_entry: '진입 조건 미발생',
    no_range: '레인지 계산 불가',
    no_data: '분봉 미확보',
    not_recommended: '추천 아님',
  };

  // 강도 구간별 등급. 8 이상이 근거가 가장 많이 모인 구간입니다.
  const TIERS = [
    { min: 8, key: 'top', label: '1순위' },
    { min: 6, key: 'mid', label: '2순위' },
    { min: 1, key: 'low', label: '참고' },
  ];
  const tierOf = (level) => TIERS.find((tier) => level >= tier.min) || TIERS[TIERS.length - 1];

  const STATUS_LABEL = {
    passed: '관문 통과',
    blocked: '관문 미달',
    observing: '관찰 중',
  };

  let records = [];
  let research = null;
  let positions = [];
  let index = 0;

  function element(tag, className, textContent) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (textContent != null) node.textContent = textContent;
    return node;
  }

  // 새벽 해외시장 판정을 아이콘 한 줄로. 이유는 title(길게 누르면/호버) 로만.
  function renderRegime(forecast) {
    const wrap = $('regimeSection');
    const box = $('regime');
    box.replaceChildren();
    if (!forecast.regime) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    const allowed = forecast.regime.allowLong;
    const pill = element('span', 'pill', allowed ? '🟢 오늘 매수 가능' : '🔴 오늘 신호 보류');
    pill.title = forecast.regime.reason || '';
    box.append(pill);
    wrap.classList.toggle('risk-off', !allowed);
  }

  function recommendationCard(row, assessment) {
    const card = element('article', 'stock-card');
    if (row.strength) card.classList.add(`tier-${tierOf(row.strength.level).key}`);
    const top = element('div', 'stock-top');
    const left = element('div');
    const code = element('small', null, row.market ? `${row.symbol} · ${row.market}` : row.symbol);
    left.append(element('h3', null, row.name || row.symbol), code);
    const right = element('div', 'plan');
    right.append(
      element('div', 'target', `목표 ${plain(row.targetPct)}`),
      element('div', 'stop', `손절 ${plain(row.stopPct)}`)
    );
    top.append(left, right);
    card.append(top);

    if (row.strength) card.append(strengthBlock(row.strength));

    // 근거는 문단으로 늘어놓지 않고 카드를 눌러야 보이는 상세 리포트로 뺍니다.
    const hint = element('p', 'card-hint', '탭해서 근거 보기 →');
    card.append(hint);

    const outcome = (assessment?.simulations || []).find(
      (s) => s.symbol === row.symbol && s.setup === row.setup
    );
    if (outcome) card.append(outcomeBlock(outcome, row.targetPct));

    const open = element('button', 'report-open', '근거 보기');
    open.addEventListener('click', () => openReport(row, outcome));
    card.append(open);
    card.addEventListener('click', (event) => {
      if (event.target.closest('button, details, summary, a')) return;
      openReport(row, outcome);
    });
    return card;
  }

  // ---- 상세 리포트 -------------------------------------------------------

  const fmtValue = (row) => {
    if (row.value == null) return '—';
    if (row.unit === '배') return `${row.value.toFixed(2)}배`;
    if (row.unit === '비율') return row.value.toFixed(3);
    if (row.unit === '일') return `${row.value}일`;
    if (row.unit === '%p') return `${row.value > 0 ? '+' : ''}${row.value.toFixed(2)}%p`;
    return `${row.value > 0 ? '+' : ''}${row.value.toFixed(2)}%`;
  };

  function section(title) {
    const box = element('section', 'report-section');
    box.append(element('h4', null, title));
    return box;
  }

  function keyValueTable(rows) {
    const table = element('table', 'report-table');
    rows.forEach(({ label, value, note, tone }) => {
      const tr = element('tr', tone || null);
      tr.append(element('th', null, label), element('td', null, value));
      table.append(tr);
      if (note) {
        const hint = element('tr', 'note-row');
        const cell = element('td', null, note);
        cell.colSpan = 2;
        hint.append(cell);
        table.append(hint);
      }
    });
    return table;
  }

  function openReport(row, outcome) {
    const dialog = $('reportDialog');
    const body = $('reportBody');
    body.replaceChildren();

    $('reportTitle').textContent = row.name || row.symbol;
    $('reportSubtitle').textContent =
      `${row.symbol} · ${row.market || ''} · ${row.setup}`;

    // 1. 판정
    const verdict = section('판정');
    const tier = row.strength ? tierOf(row.strength.level) : null;
    verdict.append(keyValueTable([
      { label: '추천 강도', value: row.strength ? `${row.strength.level} / 10 (${tier.label})` : '—' },
      { label: '목표', value: plain(row.targetPct), tone: 'good' },
      { label: '손절', value: plain(row.stopPct), tone: 'bad' },
      { label: '진입 조건', value: row.entryRule || '—' },
      { label: '진입 마감', value: row.entryDeadline || '—',
        note: '이 시각까지 조건이 안 나오면 그날은 진입하지 않습니다.' },
      { label: '청산 시각', value: row.exitTime || '—',
        note: '목표·손절 미도달 시 이 시각에 정리합니다.' },
    ]));
    body.append(verdict);

    // 2. 이 셋업의 과거 성적 — 왜 믿을 수 있는가
    if (row.setupRecord) {
      const s = row.setupRecord;
      const past = section('이 셋업의 과거 성적');
      past.append(keyValueTable([
        { label: '표본', value: `${s.total ?? 0}건 중 ${s.hits ?? 0}건 적중` },
        { label: '적중률', value: plain((s.hitRate ?? 0) * 100),
          tone: (s.hitRate ?? 0) > (s.breakevenHitRate ?? 1) ? 'good' : 'bad' },
        { label: '본전 적중률', value: plain((s.breakevenHitRate ?? 0) * 100),
          note: '손익비가 정하는 선입니다. 실제 적중률이 이보다 높아야 계좌가 늘어납니다.' },
        { label: '평균이익 / 평균손실',
          value: `${pct(s.averageWinPct)} / ${pct(s.averageLossPct)}` },
        { label: '손익비', value: s.payoffRatio == null ? '—' : s.payoffRatio.toFixed(2) },
        { label: '거래당 기대값', value: pct(s.expectancyPct),
          tone: (s.expectancyPct ?? 0) > 0 ? 'good' : 'bad' },
        { label: '지수 대비', value: s.excessPct == null ? '—' : `${pct(s.excessPct)}p`,
          tone: (s.excessPct ?? 0) > 0 ? 'good' : 'bad',
          note: '같은 기간 지수보다 나았는지. 이게 0 이하면 그냥 지수를 사는 편이 낫습니다.' },
        { label: '신뢰 수준', value: s.established ? '확립' : '잠정',
          tone: s.established ? 'good' : 'warn',
          note: s.established
            ? '적중률 95% 하한도 본전선 위입니다.'
            : '수익 구조로 보이지만 표본이 적어 우연일 가능성을 배제하지 못했습니다.' },
      ]));
      if (s.reason) past.append(element('p', 'report-note', s.reason));
      body.append(past);
    }

    // 3. 강도 점수 내역
    if (row.strength?.components?.length) {
      const score = section('강도 점수 내역');
      score.append(keyValueTable(
        row.strength.components
          .slice()
          .sort((a, b) => (b.points ?? 0) - (a.points ?? 0))
          .map((part) => ({
            label: part.label,
            value: `${part.points > 0 ? '+' : ''}${part.points ?? 0}`,
            note: part.detail || null,
            tone: (part.points ?? 0) < 0 ? 'bad' : (part.points ?? 0) > 0 ? 'good' : null,
          }))
      ));
      score.append(element('p', 'report-note',
        `합계 ${row.strength.rawScore ?? '—'} / 100 → 강도 ${row.strength.level}`));
      body.append(score);
    }

    // 4. 선정 근거 수치
    if (row.report?.numbers?.length) {
      const data = section('선정 근거 수치');
      data.append(keyValueTable(row.report.numbers.map((n) => ({
        label: n.label, value: fmtValue(n), note: n.note,
      }))));
      body.append(data);
    }

    // 5. 수급·공시
    if (row.report?.flags?.length || row.report?.newsCount != null) {
      const flow = section('수급 · 공시 · 뉴스');
      const rows = row.report.flags.map((f) => ({
        label: f.label,
        value: f.detail || (f.value ? '있음' : '없음'),
        tone: f.key.startsWith('has_negative') ? (f.value ? 'bad' : null)
          : f.value ? 'good' : null,
      }));
      if (row.report.newsCount != null) {
        rows.push({ label: '관련 기사', value: `${row.report.newsCount}건` });
      }
      flow.append(keyValueTable(rows));
      body.append(flow);
    }

    // 6. 결과 (마감 후)
    if (outcome) {
      const result = section('마감 결과');
      result.append(outcomeBlock(outcome, row.targetPct));
      body.append(result);
    }

    // 7. 걸린 조건
    if (row.blocks?.length) {
      const blocked = section('걸린 조건');
      const ul = element('ul', 'block-list');
      row.blocks.forEach((b) => ul.append(element('li', null, b)));
      blocked.append(ul);
      body.append(blocked);
    }

    body.append(element('p', 'report-caution',
      '이 리포트는 근거를 정리한 것이지 수익을 약속하지 않습니다. 가격은 표시하지 않습니다 — '
      + '토스 시세는 맥북 밖으로 나가지 않습니다.'));

    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }

  // 카드에는 강도 막대만 짧게 보여줍니다. 항목별 점수 내역은 '근거 보기'를
  // 눌렀을 때 열리는 리포트에서만 보여줍니다(중복 방지 + 카드 텍스트 최소화).
  function strengthBlock(rating) {
    const box = element('div', 'strength');
    const tier = tierOf(rating.level);
    box.classList.add(tier.key);
    const bar = element('div', 'strength-bar');
    for (let step = 1; step <= 10; step += 1) {
      bar.append(element('span', step <= rating.level ? 'on' : 'off'));
    }
    box.append(element('span', 'tier', tier.label), element('strong', null, `${rating.level}/10`), bar);
    return box;
  }

  function outcomeBlock(simulation, targetPct) {
    const box = element('div', 'assessment');
    if (simulation.result !== 'traded') {
      box.append(element('p', null, RESULT_LABEL[simulation.result] || simulation.result));
      if (simulation.note) box.append(element('p', 'sub', simulation.note));
      return box;
    }
    const chosen =
      simulation.ladder.find((entry) => entry.targetPct === targetPct) || simulation.ladder[0];
    if (!chosen) {
      box.append(element('p', null, '결과 기록이 없습니다.'));
      return box;
    }
    const line = element('p', chosen.win ? 'win' : 'loss');
    line.textContent = `${RESULT_LABEL[chosen.result] || chosen.result} · 비용 차감 ${pct(chosen.netPct)}`;
    box.append(line);
    if (chosen.ambiguous) {
      box.append(
        element('p', 'sub', '같은 봉에서 목표와 손절을 모두 건드려 손절로 처리했습니다 (보수적 판정)')
      );
    }
    return box;
  }

  function renderScoreboard(forecast) {
    const wrap = $('scoreboard');
    wrap.replaceChildren();
    const entries = Object.entries(forecast.scoreboard || {});
    if (!entries.length) {
      wrap.append(
        element('p', 'sub', '아직 채점된 거래가 없습니다. 기록이 쌓이면 셋업별 적중률이 여기 나옵니다.')
      );
      return;
    }
    const table = element('table', 'score-table');
    const head = element('tr');
    ['셋업 @ 목표', '표본', '적중', '적중률', '하한(95%)', '판정'].forEach((label) =>
      head.append(element('th', null, label))
    );
    table.append(head);
    entries
      .sort((a, b) => (b[1].lowerBound ?? 0) - (a[1].lowerBound ?? 0))
      .forEach(([key, row]) => {
        const tr = element('tr', row.status);
        tr.append(
          element('td', null, key),
          element('td', null, `${row.total ?? 0}건`),
          element('td', null, `${row.hits ?? 0}건`),
          element('td', null, row.hitRate == null ? '—' : plain(row.hitRate * 100)),
          element('td', null, row.lowerBound == null ? '—' : plain(row.lowerBound * 100)),
          element('td', null, STATUS_LABEL[row.status] || row.status)
        );
        table.append(tr);
      });
    wrap.append(table);
  }

  const VERDICT_LABEL = { hold: '보유', sell: '매도', switch: '교체' };

  async function loadPositions() {
    if (window.STOCK_LOCAL_MODE) return [];
    const { data, error } = await window.sb
      .from('stock_positions')
      .select('id,symbol,name,market,entry_date,entry_price,quantity,target_pct,stop_pct,strategy,verdict')
      .eq('status', 'open')
      .order('entry_date', { ascending: true });
    if (error) return [];
    return data || [];
  }

  function renderPositions() {
    const list = $('positionList');
    list.replaceChildren();
    const goal = records[0]?.forecast?.goal;
    if (goal) {
      const line = $('goalLine');
      line.className = `sub ${goal.achieved ? 'goal-hit' : 'goal-miss'}`;
      line.textContent = goal.shortfall_note || '';
    }
    if (!positions.length) {
      list.append(element('p', 'sub',
        '기록된 보유 종목이 없습니다. 실제로 매수하셨다면 아래에 적어두세요. 다음 날 아침 08:30에 계속 들고 갈지 판정해 드립니다.'));
      return;
    }
    positions.forEach((row) => {
      const verdict = row.verdict || {};
      const card = element('article', 'position');
      const top = element('div', 'position-top');
      const left = element('div');
      const kind = row.strategy === 'long' ? '장기' : '스윙';
      left.append(
        element('h3', null, row.name || row.symbol),
        element('small', null,
          `${row.symbol} · ${row.market || ''} · ${kind} · ${row.entry_date} 매수 · ${row.quantity}주`)
      );
      const pnl = verdict.net_pnl_pct;
      const right = element('div', `pnl ${pnl > 0 ? 'up' : pnl < 0 ? 'down' : ''}`,
        pnl == null ? '—' : pct(pnl));
      top.append(left, right);
      card.append(top);

      if (verdict.verdict) {
        card.append(element('span', `verdict ${verdict.verdict}`,
          VERDICT_LABEL[verdict.verdict] || verdict.verdict));
      }
      if (verdict.reason) card.append(element('p', 'why', verdict.reason));
      if (verdict.held_days != null) {
        card.append(element('p', 'why', `보유 ${verdict.held_days}거래일` +
          (row.target_pct ? ` · 목표 ${row.target_pct}%` : '') +
          (row.stop_pct ? ` · 손절 ${row.stop_pct}%` : '')));
      }

      const close = element('button', 'close-btn', '매도 완료로 기록');
      close.addEventListener('click', () => closePosition(row));
      card.append(close);
      list.append(card);
    });
  }

  async function closePosition(row) {
    const raw = window.prompt(`${row.name || row.symbol} 매도가를 입력하세요 (원)`);
    if (raw == null) return;
    const price = Number(raw);
    if (!Number.isFinite(price) || price <= 0) {
      setMessage('매도가가 올바르지 않습니다.');
      return;
    }
    const { error } = await window.sb
      .from('stock_positions')
      .update({ status: 'closed', exit_price: price,
                exit_date: new Date().toISOString().slice(0, 10),
                updated_at: new Date().toISOString() })
      .eq('id', row.id);
    setMessage(error ? '기록에 실패했습니다.' : '매도로 기록했습니다.');
    if (!error) {
      positions = await loadPositions();
      renderPositions();
    }
  }

  function bindPositionForm() {
    const form = $('positionForm');
    if (!form) return;
    form.elements.entry_date.value = new Date().toISOString().slice(0, 10);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(form).entries());
      const payload = {
        symbol: String(data.symbol).trim(),
        name: String(data.name || '').trim() || null,
        market: data.market,
        entry_date: data.entry_date,
        entry_price: Number(data.entry_price),
        quantity: Number(data.quantity),
        target_pct: data.target_pct ? Number(data.target_pct) : null,
        stop_pct: data.stop_pct ? Number(data.stop_pct) : null,
        strategy: data.strategy || 'swing',
      };
      if (!/^[A-Za-z0-9]{6}$/.test(payload.symbol)) {
        setMessage('종목코드는 6자리입니다.');
        return;
      }
      const { error } = await window.sb.from('stock_positions').insert(payload);
      if (error) {
        setMessage('저장에 실패했습니다. 다시 시도해주세요.');
        return;
      }
      form.reset();
      form.elements.entry_date.value = new Date().toISOString().slice(0, 10);
      setMessage('매수 기록을 저장했습니다. 다음 아침 08:30에 판정이 붙습니다.');
      positions = await loadPositions();
      renderPositions();
    });
  }

  function renderResearch() {
    const section = $('researchSection');
    if (!research) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    const range = research.dateRange ? `${research.dateRange[0]} ~ ${research.dateRange[1]}` : '—';
    $('researchMeta').textContent =
      `${range} · ${research.tradeDays ?? 0}거래일 · 채점 ${research.tradeCount ?? 0}건`;

    const body = $('researchBody');
    body.replaceChildren();

    // 셋업별: 적중률만이 아니라 우연 판정까지 함께 봅니다.
    const setups = Object.entries(research.setups || {});
    if (setups.length) {
      body.append(element('h3', 'research-h', '셋업별 성적'));
      const table = element('table', 'score-table');
      const head = element('tr');
      ['셋업 @ 목표', '표본', '적중률', '하한', '우연 배제', '최대낙폭'].forEach((label) =>
        head.append(element('th', null, label))
      );
      table.append(head);
      setups
        .sort((a, b) => (b[1].lowerBoundPct ?? 0) - (a[1].lowerBoundPct ?? 0))
        .forEach(([key, row]) => {
          const tr = element('tr', row.survivesCorrection ? 'passed' : 'blocked');
          tr.append(
            element('td', null, key),
            element('td', null, `${row.total ?? 0}건`),
            element('td', null, plain(row.hitRatePct)),
            element('td', null, plain(row.lowerBoundPct)),
            element('td', null, row.survivesCorrection ? '통과' : '미통과'),
            element('td', null, plain(row.maxDrawdownPct))
          );
          table.append(tr);
        });
      body.append(table);
      body.append(element('p', 'sub', '통과 = 우연이 아니라는 근거가 확인된 조합이에요.'));
    }

    // 강도가 실제로 작동하는지
    const verdict = research.strength?.verdict;
    if (verdict) {
      body.append(element('h3', 'research-h', '강도 검증'));
      body.append(element('p', verdict.works ? 'verdict-good' : 'verdict-bad', verdict.reason));
    }

    // 보유 기간 비교
    if (research.horizon?.length) {
      body.append(element('h3', 'research-h', '보유 기간 비교'));
      const table = element('table', 'score-table');
      const head = element('tr');
      ['보유', '거래', '적중률', '거래당', '최대낙폭', '연속손실'].forEach((label) =>
        head.append(element('th', null, label))
      );
      table.append(head);
      const best = research.horizon.reduce((a, b) =>
        (b.meanNetPct ?? -99) > (a.meanNetPct ?? -99) ? b : a);
      research.horizon.forEach((row) => {
        const tr = element('tr', row === best ? 'passed' : null);
        tr.append(
          element('td', null, `${row.holdDays}일`),
          element('td', null, `${row.trades ?? 0}건`),
          element('td', null, plain(row.hitRatePct)),
          element('td', null, pct(row.meanNetPct)),
          element('td', null, plain(row.maxDrawdownPct)),
          element('td', null, `${row.longestLosingStreak ?? 0}회`)
        );
        table.append(tr);
      });
      body.append(table);
      body.append(element('p', 'sub', `가장 효율적이었던 구간: ${best.holdDays}일 보유`));
    }

    // 시장 레짐
    const regimes = research.regimes;
    if (regimes?.by_regime) {
      body.append(element('h3', 'research-h', '시장 상황별'));
      const line = Object.entries(regimes.by_regime)
        .map(([name, value]) => `${name} ${plain(value.hit_rate_pct)} (${value.total}건)`)
        .join(' · ');
      body.append(element('p', null, line));
      if (regimes.single_regime_warning) {
        body.append(element('p', 'verdict-bad', regimes.single_regime_warning));
      }
    }

    if (research.caveats?.length) {
      const details = element('details', 'research-caveats');
      details.append(element('summary', null, '이 숫자를 읽을 때 주의할 점'));
      const list = element('ul');
      research.caveats.forEach((line) => list.append(element('li', null, line)));
      details.append(list);
      body.append(details);
    }
  }

  function render() {
    const record = records[index];
    const select = $('recordSelect');
    select.replaceChildren();
    records.forEach((row, position) => {
      const option = document.createElement('option');
      option.value = String(position);
      option.textContent = `${row.tradeDate} 기록`;
      select.append(option);
    });
    select.disabled = !records.length;
    if (records.length) select.value = String(index);
    $('prev').disabled = index >= records.length - 1;
    $('next').disabled = index <= 0;

    $('empty').hidden = !!record || !!research;
    ['regimeSection', 'summary', 'records', 'scoreboardSection'].forEach((id) => {
      const node = $(id);
      if (node) node.hidden = !record;
    });
    if (!record) return;

    const { forecast, assessment } = record;
    renderRegime(forecast);
    renderScoreboard(forecast);

    $('candidateCount').textContent = `${forecast.recommendations.length}개`;
    $('targetRate').textContent =
      forecast.targetHitRate == null ? '—' : plain(forecast.targetHitRate * 100, 0);
    const perTarget = assessment?.summary?.perTarget || {};
    const best = Object.values(perTarget)
      .filter((row) => row && row.hit_rate_pct != null)
      .sort((a, b) => b.hit_rate_pct - a.hit_rate_pct)[0];
    $('dayHitRate').textContent = best ? plain(best.hit_rate_pct) : '—';

    $('timestamps').textContent = `발행 ${when(forecast.publishedAt)}${
      assessment ? ` · 마감 평가 ${when(assessment.assessedAt)}` : ' · 마감 평가 대기'
    }`;
    $('methodNote').textContent = forecast.methodNote || '';

    const list = $('stockList');
    list.replaceChildren();
    if (!forecast.recommendations.length) {
      const note = element('div', 'empty-day');
      note.append(
        element('h3', null, '이 날은 추천이 0건입니다'),
        element(
          'p',
          null,
          '관문을 통과한 셋업이 없었습니다. 오류가 아니라 설계된 동작입니다 — 조건이 안 맞는 날 억지로 5개를 채우지 않습니다.'
        )
      );
      list.append(note);
    } else {
      forecast.recommendations.forEach((row) => list.append(recommendationCard(row, assessment)));
    }

    const heldBox = $('heldList');
    heldBox.replaceChildren();
    forecast.held.slice(0, 20).forEach((row) => {
      const item = element('li');
      item.append(
        element('span', 'held-name', `${row.name || row.symbol} · ${row.setup}`),
        element('span', 'held-gate', row.gate || '')
      );
      heldBox.append(item);
    });
    $('heldSection').hidden = !forecast.held.length;
  }

  function setMessage(text) {
    $('message').textContent = text || '';
  }

  async function loadLocal() {
    const response = await fetch('/api/reports', { cache: 'no-store' });
    if (!response.ok) throw Error('맥북 자동 기록을 읽지 못했습니다.');
    return response.json();
  }

  async function loadSite() {
    const { data, error } = await window.sb
      .from('stock_reports')
      .select('trade_date,forecast,assessment')
      .order('trade_date', { ascending: false })
      .limit(MAX_RECORDS);
    if (error) throw Error('기록을 불러오지 못했습니다. 잠시 후 다시 시도해주세요.');
    return data || [];
  }

  async function loadResearch() {
    try {
      if (window.STOCK_LOCAL_MODE) {
        const response = await fetch('/api/research', { cache: 'no-store' });
        return response.ok ? window.StockData.research(await response.json()) : null;
      }
      const { data } = await window.sb.from('stock_research').select('payload').limit(1);
      return data?.length ? window.StockData.research(data[0].payload) : null;
    } catch {
      return null;   // 분석 요약이 없어도 나머지 화면은 그대로 보여줍니다.
    }
  }

  let loading = false;
  async function load() {
    if (loading) return;
    loading = true;
    try {
      const raw = window.STOCK_LOCAL_MODE ? await loadLocal() : await loadSite();
      records = window.StockData.normalizeAll(raw);
      research = await loadResearch();
      positions = await loadPositions();
      index = Math.min(index, Math.max(0, records.length - 1));
      render();
      renderResearch();
      renderPositions();
      setMessage(
        records.length
          ? ''
          : '아직 기록이 없습니다. 맥북에서 수집을 한 번 돌리면 이 화면이 채워집니다.'
      );
    } catch (error) {
      setMessage(error.message || '기록을 불러오지 못했습니다.');
    } finally {
      loading = false;
    }
  }

  window.addEventListener('authReady', async () => {
    if (window.STOCK_LOCAL_MODE) {
      const section = $('positionsSection');
      if (section) section.hidden = true;
    }
    if (!window.STOCK_LOCAL_MODE) {
      const {
        data: { session },
      } = await window.sb.auth.getSession();
      if (!session) return;
      if (!isOwner(session)) {
        $('ownerNotice').hidden = false;
        $('ownerGated').hidden = true;
        return;
      }
    }
    bindPositionForm();
    await load();
    setInterval(load, REFRESH_MS);
  });

  const guideLink = document.getElementById('openGuide');
  if (guideLink) {
    guideLink.addEventListener('click', (event) => {
      event.preventDefault();
      const guide = document.querySelector('.guide');
      if (guide) {
        guide.open = true;
        guide.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  }

  $('reportClose').addEventListener('click', () => {
    const dialog = $('reportDialog');
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  });

  $('recordSelect').addEventListener('change', (event) => {
    index = Number(event.target.value);
    render();
  });
  $('prev').addEventListener('click', () => {
    if (index < records.length - 1) {
      index += 1;
      render();
    }
  });
  $('next').addEventListener('click', () => {
    if (index > 0) {
      index -= 1;
      render();
    }
  });
})();
