/* Progressive accessibility enhancements; no data or auth behavior changes. */
(function () {
  let queued = false;
  let activeDialog = null;
  let returnFocus = null;
  const focusables = dialog => [...dialog.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled):not([type=hidden]),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')].filter(el => el.getClientRects().length);
  function syncDialog() {
    const overlays = [...document.querySelectorAll('#authGate,.cal-modal-overlay.open,.modal-overlay.open,.preview-overlay.open')].filter(el => el.getClientRects().length);
    const overlay = overlays[overlays.length - 1];
    const dialog = overlay && (overlay.querySelector('.box,.cal-modal,.modal-card,.preview-box') || overlay);
    if (dialog === activeDialog) return;
    if (activeDialog && !dialog && returnFocus?.isConnected && returnFocus.getClientRects().length) returnFocus.focus();
    if (dialog) {
      if (!activeDialog) returnFocus = document.activeElement;
      dialog.setAttribute('role','dialog');
      dialog.setAttribute('aria-modal','true');
      const title = dialog.querySelector('h2,h3,.cal-modal-title,.display,.preview-head .name');
      dialog.setAttribute('aria-label',title?.textContent.trim() || '상세 보기');
      dialog.tabIndex = -1;
      if (!dialog.contains(document.activeElement)) (focusables(dialog)[0] || dialog).focus();
    }
    activeDialog = dialog || null;
  }
  function organizeForms() {
    // Keep the original input nodes and handlers; label only the ledger's compact forms.
    if (document.body.dataset.page === 'ledger') {
      const names = {scope:'구분',date:'날짜',type:'대분류',main:'중분류',category:'소분류',cat:'소분류',note:'내용',amount:'금액',payment:'결제수단',pay:'결제수단',day:'매월 납부일'};
      document.querySelectorAll('.tx-form,.tx-edit-form').forEach(form => {
        [...form.children].filter(el => el.matches('input,select')).forEach(input => {
          let key = [...input.classList].find(c => c.startsWith('tx-edit-'))?.replace('tx-edit-','') || input.id.replace(/^(f_|rc_|rce_)/,'').split('_')[0];
          if (!names[key]) return;
          const label = document.createElement('label'); label.className = 'bk-form-field'; label.dataset.field = key;
          const caption = document.createElement('span'); caption.textContent = names[key];
          input.before(label); label.append(caption,input);
        });
        const add = form.querySelector('.add-btn');
        if (add && add.textContent.trim() === '+') add.textContent = '추가';
      });
      const addForm = document.getElementById('txAddForm');
      if (addForm && !addForm.closest('details')) {
        const details = document.createElement('details'); details.className = 'bk-entry-details bk-tx-entry';
        details.open = window.matchMedia('(min-width: 1101px)').matches;
        details.innerHTML = '<summary>거래 추가<span class="bk-entry-hint">내용과 금액을 입력해요</span></summary>';
        addForm.before(details); details.append(addForm);
      }
    }
    document.querySelectorAll('.bk-entry-panel:not([data-organized])').forEach(panel => {
      panel.dataset.organized = 'true';
      const title = panel.querySelector('h2'); if (!title) return;
      const details = document.createElement('details'); details.className = 'bk-entry-details';
      const summary = document.createElement('summary'); summary.textContent = title.textContent;
      const body = document.createElement('div'); body.className = 'bk-entry-body';
      title.remove(); while (panel.firstChild) body.append(panel.firstChild);
      details.append(summary,body); panel.append(details);
    });
  }
  function enhance() {
    queued = false;
    organizeForms();
    document.querySelectorAll('label:not([for])').forEach(label => {
      const field = label.parentElement.querySelector('input:not([type=hidden]), select, textarea');
      if (field && field.id && !label.contains(field)) label.htmlFor = field.id;
    });
    document.querySelectorAll('button[title]:not([aria-label])').forEach(button => {
      if (button.textContent.trim().length <= 2) button.setAttribute('aria-label', button.title);
    });
    document.querySelectorAll('.cal-nav-btn,.modal-close,.cal-modal-close,.preview-head .close-btn').forEach(button => {
      if (!button.hasAttribute('aria-label')) button.setAttribute('aria-label', button.classList.contains('cal-nav-btn') ? (button.textContent.includes('‹') ? '이전 달' : '다음 달') : '닫기');
    });
    document.querySelectorAll('.tabs button,.owner-toggle button,.type-toggle button,.chip-group button,.cat-tabs button').forEach(button => {
      const pressed = String((button.classList.contains('active') || button.classList.contains('on')));
      if (button.getAttribute('aria-pressed') !== pressed) button.setAttribute('aria-pressed', pressed);
    });
    document.querySelectorAll('.sum-card[onclick],.trip-card[onclick],.proj-card[onclick],.doc-card[onclick],.rv-card[onclick],.kpi-row-clickable[onclick]').forEach(card => {
      if (card.matches('a,button') || card.querySelector('button,a,input,select,textarea')) return;
      card.setAttribute('role','button');
      card.tabIndex = 0;
    });
    document.querySelectorAll('.table-wrap,.table-card').forEach(region => {
      if (!region.querySelector('table')) return;
      region.tabIndex = 0;
      region.setAttribute('role','region');
      if (!region.hasAttribute('aria-label')) region.setAttribute('aria-label','데이터 표 · 좌우로 스크롤할 수 있어요');
    });
    document.querySelectorAll('.status-msg,.couple-status-msg,#authErr,#authMsg').forEach(status => {
      status.setAttribute('role','status');
      status.setAttribute('aria-live','polite');
    });
    syncDialog();
  }
  document.addEventListener('keydown', event => {
    if (activeDialog) {
      if (event.key === 'Escape') {
        const close = activeDialog.querySelector('.modal-close,.cal-modal-close,.preview-head .close-btn');
        if (close) { event.preventDefault(); close.click(); }
      }
      if (event.key === 'Tab') {
        const items = focusables(activeDialog);
        const first = items[0], last = items[items.length - 1];
        if (!first) { event.preventDefault(); activeDialog.focus(); }
        else if (event.shiftKey && (document.activeElement === first || !activeDialog.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !activeDialog.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
      }
    }
    const card = event.target.closest('[role=button][onclick]');
    if (card && event.target === card && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault(); card.click();
    }
  });
  enhance();
  new MutationObserver(records => {
    if (queued || !records.some(record => record.type === 'childList' || record.attributeName === 'class')) return;
    queued = true; requestAnimationFrame(enhance);
  }).observe(document.body, { childList:true, subtree:true, attributes:true, attributeFilter:['class'] });
})();
