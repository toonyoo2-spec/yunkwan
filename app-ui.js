/* Progressive accessibility enhancements; no data or auth behavior changes. */
(function () {
  let queued = false;
  let activeDialog = null;
  let returnFocus = null;
  const focusables = dialog => [...dialog.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled):not([type=hidden]),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')].filter(el => el.getClientRects().length);
  function syncDialog() {
    const overlays = [...document.querySelectorAll('#authGate,.cal-modal-overlay.open,.modal-overlay.open')].filter(el => el.getClientRects().length);
    const overlay = overlays[overlays.length - 1];
    const dialog = overlay && (overlay.querySelector('.box,.cal-modal,.modal-card') || overlay);
    if (dialog === activeDialog) return;
    if (activeDialog && !dialog && returnFocus?.isConnected && returnFocus.getClientRects().length) returnFocus.focus();
    if (dialog) {
      if (!activeDialog) returnFocus = document.activeElement;
      dialog.setAttribute('role','dialog');
      dialog.setAttribute('aria-modal','true');
      const title = dialog.querySelector('h2,h3,.cal-modal-title,.display');
      dialog.setAttribute('aria-label',title?.textContent.trim() || '상세 보기');
      dialog.tabIndex = -1;
      if (!dialog.contains(document.activeElement)) (focusables(dialog)[0] || dialog).focus();
    }
    activeDialog = dialog || null;
  }
  function enhance() {
    queued = false;
    document.querySelectorAll('label:not([for])').forEach(label => {
      const field = label.parentElement.querySelector('input:not([type=hidden]), select, textarea');
      if (field && field.id && !label.contains(field)) label.htmlFor = field.id;
    });
    document.querySelectorAll('button[title]:not([aria-label])').forEach(button => {
      if (button.textContent.trim().length <= 2) button.setAttribute('aria-label', button.title);
    });
    document.querySelectorAll('.cal-nav-btn,.modal-close,.cal-modal-close').forEach(button => {
      if (!button.hasAttribute('aria-label')) button.setAttribute('aria-label', button.classList.contains('cal-nav-btn') ? (button.textContent.includes('‹') ? '이전 달' : '다음 달') : '닫기');
    });
    document.querySelectorAll('.tabs button,.owner-toggle button,.type-toggle button,.chip-group button,.cat-tabs button').forEach(button => {
      const pressed = String(button.classList.contains('active'));
      if (button.getAttribute('aria-pressed') !== pressed) button.setAttribute('aria-pressed', pressed);
    });
    document.querySelectorAll('.sum-card[onclick],.trip-card[onclick],.proj-card[onclick],.doc-card[onclick],.kpi-row-clickable[onclick]').forEach(card => {
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
        const close = activeDialog.querySelector('.modal-close,.cal-modal-close');
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
