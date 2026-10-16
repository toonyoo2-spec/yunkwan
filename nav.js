/* Shared navigation for all BORAKWAN application pages. */
(function () {
  const icons = {
    home: '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z"/>',
    ledger: '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h8M8 12h3M8 16h8"/>',
    review: '<path d="M8 4H5a1 1 0 0 0-1 1v15h16V5a1 1 0 0 0-1-1h-3"/><rect x="8" y="2" width="8" height="5" rx="1"/><path d="m8 14 3 3 5-6"/>',
    campaigns: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
    docs: '<path d="M3 7V5a1 1 0 0 1 1-1h6l2 3h8a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z"/>',
    routine: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    stocks: '<path d="M4 3v17h17M8 15l4-5 4 2 5-7"/>',
    task: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v10"/>',
    trip: '<path d="m3 11 18-8-8 18-2-8Z"/>'
  };
  const items = [
    ['index', 'home', '홈', '함께 보는 일정', '일정과 체험단 예약을 한눈에 확인해요.', '일상'],
    ['ledger', 'ledger', '가계부', '우리 집 가계부', '수입과 지출을 기록하고 돈의 흐름을 살펴봐요.', '일상'],
    ['routine', 'routine', '루틴', '루틴', '', '일상'],
    ['review', 'review', '블로그', '블로그 체험단 관리', '예약부터 작성 완료까지, 체험단 진행 상황을 관리해요.', '기록'],
    ['campaigns', 'campaigns', '체험단 확인', '체험단 확인', '', '기록'],
    ['docs', 'docs', '보관함', '중요 문서함', '다시 찾을 문서와 링크를 한곳에 모아둬요.', '기록'],
    ['task', 'task', 'Task', 'Task · 포트폴리오', '', '계획'],
    ['trip', 'trip', '여행', '여행 계획표', '', '계획'],
    ['stocks', 'stocks', '주식', '주식 기록', '', '계획']
  ];
  const file = location.pathname.split('/').pop() || 'index.html';
  const current = items.find(item => `${item[0]}.html` === file) || items[0];
  document.body.classList.add('bk-app');
  document.body.dataset.page = current[0];
  document.title = `${current[3]} · BORAKWAN`;
  const svg = name => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
  let group = '';
  const links = items.map(item => {
    const heading = group !== item[5] ? `<div class="bk-nav-group">${item[5]}</div>` : '';
    group = item[5];
    return `${heading}<a class="bk-card${item === current ? ' active' : ''}" href="./${item[0]}.html" ${item === current ? 'aria-current="page"' : ''}><span class="bk-icon">${svg(item[1])}</span><span>${item[2]}</span>${item === current ? '<span class="bk-active-dot" aria-hidden="true"></span>' : ''}</a>`;
  }).join('');
  document.body.insertAdjacentHTML('afterbegin', `
    <a class="bk-skip" href="#bkMain">본문으로 건너뛰기</a>
    <header id="bkNav">
      <div class="bk-brand-row"><a class="bk-brand" href="./index.html" aria-label="BORAKWAN 홈"><span class="bk-mark" aria-hidden="true">b.</span><span>BORAKWAN<small>우리의 일상 기록</small></span></a><button class="bk-toggle" type="button" aria-expanded="false" aria-controls="bkNavLinks" aria-label="전체 메뉴 열기"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button></div>
      <nav id="bkNavLinks" aria-label="주요 메뉴">${links}</nav>
      <div class="bk-nav-footer"><span>함께 쌓아가는 하루</span><button class="bk-refresh" type="button" aria-label="현재 페이지 새로고침" title="새로고침">↻</button></div>
    </header>
    <div class="bk-topline"><span>우리의 공간 <span aria-hidden="true">/</span> <strong>${current[2]}</strong></span><span class="bk-date">${new Intl.DateTimeFormat('ko-KR', {month:'long',day:'numeric',weekday:'short'}).format(new Date())}</span></div>
  `);
  const nav = document.getElementById('bkNav');
  const toggle = nav.querySelector('.bk-toggle');
  toggle.addEventListener('click', () => {
    const expanded = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.setAttribute('aria-label', expanded ? '전체 메뉴 닫기' : '전체 메뉴 열기');
    nav.classList.toggle('is-open', expanded);
  });
  nav.addEventListener('keydown', event => {
    if (event.key === 'Escape' && nav.classList.contains('is-open')) { toggle.click(); toggle.focus(); }
  });
  nav.querySelector('.bk-refresh').addEventListener('click', () => location.reload());
  document.addEventListener('DOMContentLoaded', () => {
    const content = document.querySelector('#appContent, #app');
    if (!content) return;
    const main = document.createElement('div');
    main.id = 'bkMain';
    main.tabIndex = -1;
    content.before(main);
    if (content.tagName !== 'MAIN') content.setAttribute('role', 'main');
    const addHeading = () => {
      if (content.querySelector('h1')) return;
      content.insertAdjacentHTML('afterbegin', `<header class="bk-page-heading"><p class="bk-eyebrow">${current[5]}</p><h1>${current[3]}</h1><p>${current[4]}</p></header>`);
    };
    addHeading();
    // The ledger replaces its root when the month/year changes.
    if (current[0] === 'ledger') new MutationObserver(addHeading).observe(content, { childList: true });
    const heading = content.querySelector('h1');
    if (heading) {
      heading.classList.add('bk-page-title');
      const header = heading.closest('header');
      if (header) {
        header.classList.add('bk-page-heading');
        if (current[4] && !header.querySelector('p:not(.bk-eyebrow)')) header.insertAdjacentHTML('beforeend', `<p class="bk-page-description">${current[4]}</p>`);
      }
    }
  });
})();
