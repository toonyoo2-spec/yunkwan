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

  const STATUS_LABEL = {
    passed: '관문 통과',
    blocked: '관문 미달',
    observing: '관찰 중',
  };

  let records = [];
  let index = 0;

  function element(tag, className, textContent) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (textContent != null) node.textContent = textContent;
    return node;
  }

  function renderRegime(forecast) {
    const box = $('regime');
    box.replaceChildren();
    if (!forecast.regime) {
      box.append(element('p', null, '새벽 해외시장 정보를 받지 못했습니다.'));
      return;
    }
    const allowed = forecast.regime.allowLong;
    box.append(
      element('strong', null, allowed ? '오늘은 신호를 낼 수 있는 환경입니다' : '오늘은 신호를 만들지 않습니다'),
      element('p', null, forecast.regime.reason || '')
    );
    box.classList.toggle('risk-off', !allowed);
  }

  function recommendationCard(row, assessment) {
    const card = element('article', 'stock-card');
    const top = element('div', 'stock-top');
    const left = element('div');
    left.append(element('h3', null, row.name || row.symbol), element('small', null, row.symbol));
    const right = element('div', 'plan');
    right.append(
      element('div', 'target', `목표 ${plain(row.targetPct)}`),
      element('div', 'stop', `손절 ${plain(row.stopPct)}`)
    );
    top.append(left, right);
    card.append(top);

    if (row.entryRule) card.append(element('p', 'entry-rule', row.entryRule));
    if (row.reason) card.append(element('p', 'reason', row.reason));
    if (row.gate) card.append(element('p', 'gate', row.gate));

    const outcome = (assessment?.simulations || []).find(
      (s) => s.symbol === row.symbol && s.setup === row.setup
    );
    if (outcome) card.append(outcomeBlock(outcome, row.targetPct));
    return card;
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

    $('empty').hidden = !!record;
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

  let loading = false;
  async function load() {
    if (loading) return;
    loading = true;
    try {
      const raw = window.STOCK_LOCAL_MODE ? await loadLocal() : await loadSite();
      records = window.StockData.normalizeAll(raw);
      index = Math.min(index, Math.max(0, records.length - 1));
      render();
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
    await load();
    setInterval(load, REFRESH_MS);
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
