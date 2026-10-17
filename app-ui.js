/* Shared product interactions. Original controls, values, IDs and data handlers are retained. */
(function () {
  let queued=false, activeDialog=null, returnFocus=null, activeSheet=null, entry=null;
  let requestedEntry=new URLSearchParams(location.search).get('action')==='new';
  const page=()=>document.body.dataset.page;
  const names={scope:'구분',date:'날짜',type:'대분류',main:'중분류',category:'소분류',cat:'소분류',note:'내용',amount:'금액',payment:'결제수단',pay:'결제수단',day:'매월 납부일'};
  const visible=el=>el && !!el.getClientRects().length;
  const focusables=dialog=>[...dialog.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled):not([type=hidden]),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')].filter(visible);
  function button(text,cls,handler){const b=document.createElement('button');b.type='button';b.className=cls;b.textContent=text;b.addEventListener('click',handler);return b;}
  function removeBackdrop(){document.getElementById('bkSheetBackdrop')?.remove();}
  function closeSheet(){
    const sheet=activeSheet;if(!sheet)return;
    activeSheet=null;removeBackdrop();
    if(sheet.dataset.bkEdit==='true'){
      const cancel=[...sheet.querySelectorAll('.edit-actions button')].find(b=>b.textContent.trim()==='취소');
      if(cancel)cancel.click();
    }else{
      sheet.classList.remove('bk-sheet');sheet.setAttribute('aria-modal','false');
      const details=sheet.closest('details');if(details)details.open=false;
    }
    syncDialog();
  }
  function openSheet(sheet){
    if(!sheet?.isConnected)return;
    if(activeSheet&&activeSheet!==sheet)closeSheet();
    returnFocus=document.activeElement;
    const details=sheet.closest('details');if(details)details.open=true;
    const recurring=sheet.closest('#recurringManagePanel');
    if(recurring&&!visible(recurring))document.getElementById('recurringToggleBtn')?.click();
    activeSheet=sheet;sheet.classList.add('bk-sheet');sheet.setAttribute('role','dialog');sheet.setAttribute('aria-modal','true');
    removeBackdrop();const backdrop=document.createElement('div');backdrop.id='bkSheetBackdrop';backdrop.className='bk-sheet-backdrop';backdrop.addEventListener('click',closeSheet);document.body.append(backdrop);
    syncDialog();sheet.querySelector('.bk-sheet-close')?.focus();
  }
  function prepareSheet(host,title,edit=false){
    if(host.dataset.bkEditor)return;
    host.dataset.bkEditor='true';host.dataset.bkEdit=String(edit);host.setAttribute('aria-label',title);
    const head=document.createElement('div');head.className='bk-dialog-head';
    const heading=document.createElement('h2');heading.textContent=title;
    const close=button('×','bk-icon-button bk-sheet-close',closeSheet);close.setAttribute('aria-label','닫기');head.append(heading,close);
    const body=document.createElement('div');body.className='bk-dialog-body';
    const footer=document.createElement('div');footer.className='bk-dialog-footer';
    const actions=host.querySelector('.edit-actions') || host.querySelector('.save-btn,.add-btn');
    if(actions)footer.append(actions);
    while(host.firstChild)body.append(host.firstChild);
    if(edit && body.querySelector('input[id^="rce_day_"]')){
      const endMonth=host.nextElementSibling;
      if(endMonth?.querySelector('input[id^="rce_endym_"]')){endMonth.classList.add('bk-recurring-end');body.append(endMonth);}
    }
    if(!edit){
      const cancel=button('취소','ghost-btn',closeSheet);
      footer.prepend(cancel);if(actions)actions.classList.add('bk-entry-submit');
    }
    if(page()==='ledger'){
      body.classList.add('bk-sheet-fields');
      const note=body.querySelector(':scope>.bk-form-field[data-field=note]');
      const amount=body.querySelector(':scope>.bk-form-field[data-field=amount]');
      if(note&&amount)note.before(amount);
    }
    host.append(head,body,footer);
    if(edit)openSheet(host);
  }
  function organizeLedger(){
    if(page()!=='ledger')return;
    document.querySelectorAll('.tx-form,.tx-edit-form').forEach(form=>{
      [...form.children].filter(el=>el.matches('input,select')).forEach(input=>{
        const key=[...input.classList].find(c=>c.startsWith('tx-edit-'))?.replace('tx-edit-','') || input.id.replace(/^(f_|rc_|rce_)/,'').split('_')[0];
        if(!names[key])return;
        const label=document.createElement('label');label.className='bk-form-field';label.dataset.field=key;
        const caption=document.createElement('span');caption.textContent=names[key];input.before(label);label.append(caption,input);
      });
      const add=form.querySelector('.add-btn');if(add?.textContent.trim()==='+')add.textContent='거래 추가';
      if(form.matches('.tx-edit-form'))prepareSheet(form,form.querySelector('input[id^="rce_day_"]')?'정기 자동이체 수정':'거래 수정',true);
    });
    const add=document.getElementById('txAddForm') || document.querySelector('.tx-form-recur');
    if(add){prepareSheet(add,add.id==='txAddForm'?'거래 기록':'정기 자동이체 추가');entry={host:add,label:add.id==='txAddForm'?'거래 기록':'정기 거래 추가'};}
    else entry=null;
  }
  function organizeEntry(){
    if(page()==='ledger'){organizeLedger();return;}
    if(page()==='review'){
      const host=document.getElementById('addFormBody');if(!host)return;
      document.getElementById('addPanel').dataset.bkEntry='true';
      prepareSheet(host,'체험단 등록');entry={host,label:'체험단 등록'};return;
    }
    const panel=document.querySelector('.bk-entry-panel');if(!panel)return;
    if(!panel.dataset.bkEntry){
      const title=panel.querySelector('h2');const text=title?.textContent || '새 기록';title?.remove();
      const body=document.createElement('div');body.className='bk-entry-body';
      while(panel.firstChild)body.append(panel.firstChild);panel.append(body);panel.dataset.bkEntry='true';
      if(page()==='task'){
        const photo=body.querySelector('.image-drop')?.closest('.field');
        if(photo){const label=photo.querySelector('label');const optional=document.createElement('details');optional.className='bk-optional';const summary=document.createElement('summary');summary.textContent=label?.textContent||'썸네일 이미지 (선택)';label?.remove();photo.before(optional);optional.append(summary,photo);}
      }
      prepareSheet(body,text);
    }
    const labels={docs:'문서 보관',task:'프로젝트 추가',trip:'여행 추가'};
    entry={host:panel.querySelector('.bk-entry-body'),label:labels[page()]||'새 기록'};
  }
  function primaryAction(){
    if(page()==='index')return{label:'일정 추가',run:()=>document.querySelector('.cal-add-couple-btn')?.click()};
    if(page()==='routine')return{label:'기록 저장',run:()=>document.getElementById('saveBtn')?.click(),disabled:!visible(document.querySelector('.save-row'))};
    if(entry?.host?.isConnected)return{label:entry.label,run:()=>openSheet(entry.host)};
    return null;
  }
  function routineProgress(){
    if(page()!=='routine')return;
    const checks=[...document.querySelectorAll('.check-item input[type=checkbox]')];
    let progress=document.getElementById('bkRoutineProgress');
    if(!progress){progress=document.createElement('div');progress.id='bkRoutineProgress';progress.className='bk-routine-progress';document.querySelector('.date-nav')?.after(progress);}
    progress.hidden=!checks.length;
    const done=checks.filter(c=>c.checked).length;
    const label=`선택한 날의 루틴 ${done} / ${checks.length}`;
    if(progress.dataset.label!==label){progress.dataset.label=label;progress.replaceChildren();const text=document.createElement('span');text.textContent=label;const bar=document.createElement('progress');bar.max=checks.length||1;bar.value=done;bar.setAttribute('aria-label',label);progress.append(text,bar);}
  }
  function mountActions(){
    const heading=document.querySelector('#appContent h1,#app h1');if(!heading)return;
    const header=heading.closest('header,.hero') || heading.parentElement;if(!header.classList.contains('bk-page-heading'))header.classList.add('bk-page-heading');
    const action=primaryAction();
    let desktop=header.querySelector('.bk-page-actions');
    if(!desktop){desktop=document.createElement('div');desktop.className='bk-page-actions';header.append(desktop);}
    const mobile=document.getElementById('bkMobileAction');
    for(const holder of [desktop,mobile].filter(Boolean)){
      if(!action){if(holder.children.length)holder.replaceChildren();continue;}
      let b=holder.querySelector('button');
      if(!b){b=button('', 'bk-primary',()=>primaryAction()?.run());holder.append(b);}
      const text=(page()==='routine'?'':'＋ ')+action.label;
      if(b.textContent!==text)b.textContent=text;
      if(b.disabled!==!!action.disabled)b.disabled=!!action.disabled;
    }
    if(requestedEntry&&entry?.host?.isConnected&&!visible(document.querySelector('#authGate,#authOverlay.open,#coupleLoginOverlay.open'))){requestedEntry=false;openSheet(entry.host);}
  }
  const records={review:'#tableSections tbody tr,#cardList .rv-card',docs:'.doc-row',task:'.project-card',trip:'.trip-card'};
  function applySearch(){
    const tools=document.getElementById('bkListTools');if(!tools)return;
    const query=tools.querySelector('input').value.trim().toLocaleLowerCase();
    const selector=records[page()];if(!selector)return;
    let count=0;
    document.querySelectorAll(selector).forEach(record=>{
      const text=record.dataset.search || (page()==='docs'?[...record.querySelectorAll('.title,.meta,.memo,.bk-record-title')].map(e=>e.textContent).join(' '):record.textContent);
      record.hidden=!!query&&!text.toLocaleLowerCase().includes(query);if(!record.hidden)count++;
    });
    if(page()==='docs'){document.querySelectorAll('#docListWrap .doc-list').forEach(list=>{const empty=!list.querySelector('.doc-row:not([hidden])');list.hidden=empty;if(list.previousElementSibling?.classList.contains('owner-section-title'))list.previousElementSibling.hidden=empty;});}
    if(page()==='review'){
      document.querySelectorAll('.status-section').forEach(group=>{group.hidden=!group.querySelector('tbody tr:not([hidden]),.rv-card:not([hidden])');});
      document.querySelectorAll('.bk-archive').forEach(archive=>{
        if(query){if(!archive.hasAttribute('data-search-open'))archive.dataset.searchOpen=String(archive.open);archive.open=true;}
        else if(archive.hasAttribute('data-search-open')){archive.open=archive.dataset.searchOpen==='true';archive.removeAttribute('data-search-open');}
      });
      count=Math.floor(count/2);
    }
    const counter=tools.querySelector('.bk-search-count');const message=query?`${count}건 검색됨`:'';if(counter.textContent!==message)counter.textContent=message;
    const empty=document.getElementById('bkSearchEmpty');empty.hidden=!query||count>0;
  }
  function mountSearch(){
    if(!records[page()])return;
    let tools=document.getElementById('bkListTools');
    if(!tools){
      const heading=document.querySelector('#appContent .bk-page-heading');if(!heading)return;
      tools=document.createElement('div');tools.id='bkListTools';tools.className='bk-list-tools';
      tools.innerHTML='<label class="bk-search"><span class="bk-sr-only">제목·내용 검색</span><input type="search" placeholder="제목·내용 검색" autocomplete="off"></label><span class="bk-search-count" role="status"></span>';
      heading.after(tools);tools.querySelector('input').addEventListener('input',applySearch);
      const empty=document.createElement('p');empty.id='bkSearchEmpty';empty.className='bk-search-empty';empty.textContent='검색 결과가 없어요. 다른 단어로 찾아보세요.';empty.hidden=true;tools.after(empty);
    }
    applySearch();
  }
  function organizeDialogs(){
    document.querySelectorAll('.cal-modal,.modal-card').forEach(card=>{
      if(card.closest('#authOverlay,#coupleLoginOverlay')||card.classList.contains('bk-form-dialog'))return;
      card.classList.add('bk-form-dialog');
      const close=card.querySelector('.modal-close,.cal-modal-close');
      const footer=card.querySelector(':scope > .modal-actions,:scope > .detail-actions');
      const directTitle=card.querySelector(':scope > h2,:scope > h3,:scope > .cal-modal-title');
      const head=document.createElement('div');head.className='bk-dialog-head';
      const title=directTitle || document.createElement('h3');if(!directTitle)title.textContent='상세 보기';title.classList.add('bk-chrome-title');
      head.append(title);if(close)head.append(close);
      const body=document.createElement('div');body.className='bk-dialog-body';
      while(card.firstChild)body.append(card.firstChild); // title/close are already detached
      if(footer){footer.remove();footer.classList.add('bk-dialog-footer');}
      card.append(head,body);if(footer)card.append(footer);
    });
    document.querySelectorAll('.bk-form-dialog .bk-chrome-title').forEach(title=>{
      const body=title.closest('.bk-form-dialog').querySelector('.bk-dialog-body');
      const source=body?.querySelector('#modalBody > h3,#modalBody > h2,.detail-title,.cal-modal-name,#calModalBody > .cal-modal-title');
      if(source){if(!source.classList.contains('bk-dialog-source-title'))source.classList.add('bk-dialog-source-title');if(title.textContent!==source.textContent)title.textContent=source.textContent;}
    });
  }
  function syncDialog(){
    if(activeSheet&&!activeSheet.isConnected){activeSheet=null;removeBackdrop();}
    const overlays=[...document.querySelectorAll('#authGate,.cal-modal-overlay.open,.modal-overlay.open,.preview-overlay.open')].filter(visible);
    const overlay=overlays[overlays.length-1];
    const dialog=activeSheet || (overlay&&(overlay.querySelector('.box,.cal-modal,.modal-card,.preview-box')||overlay)) || null;
    if(document.body.classList.contains('bk-modal-open')!==!!dialog)document.body.classList.toggle('bk-modal-open',!!dialog);
    if(dialog===activeDialog)return;
    if(dialog){
      if(!activeDialog&&!activeSheet)returnFocus=document.activeElement;
      dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.tabIndex=-1;
      const title=dialog.querySelector('.bk-dialog-head h2,.bk-dialog-head h3,h2,h3,.display,.preview-head .name');dialog.setAttribute('aria-label',title?.textContent.trim()||'상세 보기');
      if(!dialog.contains(document.activeElement))(focusables(dialog)[0]||dialog).focus();
    }else if(activeDialog){
      if(returnFocus?.isConnected&&visible(returnFocus))returnFocus.focus();
      else {const h=document.querySelector('h1');if(h){h.tabIndex=-1;h.focus({preventScroll:true});}}
    }
    activeDialog=dialog;
  }
  function enhance(){
    queued=false;organizeEntry();organizeDialogs();mountActions();mountSearch();routineProgress();
    document.querySelectorAll('label:not([for])').forEach(label=>{const field=label.parentElement.querySelector('input:not([type=hidden]),select,textarea');if(field?.id&&!label.contains(field))label.htmlFor=field.id;});
    document.querySelectorAll('.cal-nav-btn,.modal-close,.cal-modal-close,.preview-head .close-btn').forEach(b=>{if(!b.hasAttribute('aria-label'))b.setAttribute('aria-label',b.classList.contains('cal-nav-btn')?(b.textContent.includes('‹')?'이전 달':'다음 달'):'닫기');});
    document.querySelectorAll('.tabs button,.owner-toggle button,.type-toggle button,.chip-group button,.cat-tabs button').forEach(b=>{const value=String(b.classList.contains('active')||b.classList.contains('on'));if(b.getAttribute('aria-pressed')!==value)b.setAttribute('aria-pressed',value);});
    document.querySelectorAll('.sum-card[onclick],.trip-card[onclick],.proj-card[onclick],.doc-card[onclick],.rv-card[onclick],.kpi-row-clickable[onclick]').forEach(card=>{if(card.matches('a,button')||card.querySelector('button,a,input,select,textarea'))return;card.setAttribute('role','button');card.tabIndex=0;});
    document.querySelectorAll('.table-wrap,.table-card').forEach(region=>{if(!region.querySelector('table'))return;region.tabIndex=0;region.setAttribute('role','region');if(!region.hasAttribute('aria-label'))region.setAttribute('aria-label','데이터 표 · 좌우로 스크롤할 수 있어요');});
    document.querySelectorAll('.status-msg,.couple-status-msg,#authErr,#authMsg').forEach(status=>{status.setAttribute('role','status');status.setAttribute('aria-live','polite');});
    if(!document.body.classList.contains('bk-ui-ready'))document.body.classList.add('bk-ui-ready');
    syncDialog();
  }
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&activeSheet){event.preventDefault();event.stopImmediatePropagation();closeSheet();return;}
    if(activeDialog&&event.key==='Escape'){const close=activeDialog.querySelector('.modal-close,.cal-modal-close,.preview-head .close-btn');if(close){event.preventDefault();close.click();}}
    if(activeDialog&&event.key==='Tab'){
      const items=focusables(activeDialog),first=items[0],last=items[items.length-1];
      if(!first){event.preventDefault();activeDialog.focus();}
      else if(event.shiftKey&&(document.activeElement===first||!activeDialog.contains(document.activeElement))){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&(document.activeElement===last||!activeDialog.contains(document.activeElement))){event.preventDefault();first.focus();}
    }
    const card=event.target.closest('[role=button][onclick]');if(card&&event.target===card&&(event.key==='Enter'||event.key===' ')){event.preventDefault();card.click();}
  },true);
  window.BKUI={openEntry:()=>entry&&openSheet(entry.host),closeEntry:()=>{if(activeSheet?.dataset.bkEdit==='false')closeSheet();}};
  enhance();
  new MutationObserver(records=>{if(queued||!records.some(r=>r.type==='childList'||r.attributeName==='class'))return;queued=true;requestAnimationFrame(enhance);}).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['class']});
})();
