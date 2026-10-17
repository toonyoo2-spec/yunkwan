/* BORAKWAN workspace navigation. Six focused modules, shared keyboard-accessible menu. */
(function(){
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

 const items=[
  ['index','home','일정','일정','월간 일정과 예약을 함께 관리해요.'],
  ['ledger','ledger','가계부','가계부','수입과 지출을 기록하고 흐름을 확인해요.'],
  ['review','review','내 체험단','내 체험단','예약·방문·작성할 체험단을 관리해요.'],
  ['campaigns','campaigns','모집 중','모집 중인 체험단','지역과 플랫폼으로 원하는 체험을 찾아요.'],
  ['docs','docs','보관함','보관함','문서와 계정 정보를 필요할 때 빠르게 찾아요.'],
  ['task','task','프로젝트','프로젝트','진행 중인 일과 결과물을 모아봐요.']
 ];
 const file=location.pathname.split('/').pop()||'index.html';
 const current=items.find(i=>i[0]+'.html'===file)||items[0];
 document.body.classList.add('bk-app');document.body.dataset.page=current[0];document.title=current[3]+' · BORAKWAN';
 const svg=name=>`<svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
 const link=i=>`<a class="bk-card${i===current?' active':''}" href="./${i[0]}.html" ${i===current?'aria-current="page"':''}>${svg(i[1])}<span>${i[2]}</span></a>`;
 const frequent=items.filter(i=>['index','ledger','review','docs'].includes(i[0]));
 document.body.insertAdjacentHTML('afterbegin',`
 <a class="bk-skip" href="#bkMain">본문으로 건너뛰기</a>
 <header id="bkNav"><a class="bk-brand" href="./index.html" aria-label="BORAKWAN 홈"><span class="bk-mark">b.</span><strong>BORAKWAN</strong></a><nav id="bkNavLinks" aria-label="주요 메뉴">${items.map(link).join('')}</nav><div class="bk-mobile-tools"><span id="bkMobileAction"></span><button class="bk-toggle bk-icon-button" type="button" aria-controls="bkMenu" aria-expanded="false" aria-label="전체 메뉴 열기">${svg('task')}</button></div><button class="bk-refresh bk-icon-button" type="button" aria-label="현재 페이지 새로고침">↻</button></header>
 <nav class="bk-bottomnav" aria-label="빠른 이동">${frequent.map(link).join('')}<button class="bk-more" type="button" aria-controls="bkMenu">${svg('task')}<span>전체</span></button></nav>
 <dialog id="bkMenu" aria-label="전체 메뉴"><header class="bk-dialog-head"><h2>메뉴</h2><button class="bk-icon-button bk-menu-close" type="button" aria-label="전체 메뉴 닫기">×</button></header><div class="bk-menu-body"><label class="bk-search"><span class="bk-sr-only">메뉴 검색</span><input id="bkMenuSearch" type="search" placeholder="메뉴 검색" autocomplete="off"></label><div class="bk-menu-grid">${items.map(link).join('')}</div><p class="bk-menu-empty" hidden>일치하는 메뉴가 없어요.</p></div></dialog>`);
 const menu=document.getElementById('bkMenu'),search=document.getElementById('bkMenuSearch'),toggle=document.querySelector('.bk-toggle');
 const open=()=>{if(menu.open)return;menu.showModal();toggle.setAttribute('aria-expanded','true');search.value='';search.dispatchEvent(new Event('input'));search.focus()};
 toggle.addEventListener('click',open);document.querySelector('.bk-more').addEventListener('click',open);document.querySelector('.bk-refresh').addEventListener('click',()=>location.reload());
 menu.querySelector('.bk-menu-close').addEventListener('click',()=>menu.close());menu.addEventListener('click',e=>{if(e.target===menu)menu.close()});menu.addEventListener('close',()=>toggle.setAttribute('aria-expanded','false'));menu.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();menu.close()}});
 search.addEventListener('input',()=>{let count=0;menu.querySelectorAll('.bk-card').forEach(a=>{a.hidden=!a.textContent.includes(search.value.trim());if(!a.hidden)count++});menu.querySelector('.bk-menu-empty').hidden=count>0});
 document.addEventListener('DOMContentLoaded',()=>{
  const content=document.querySelector('#appContent,#app');if(!content)return;
  const skip=document.createElement('div');skip.id='bkMain';skip.tabIndex=-1;content.before(skip);content.setAttribute('role','main');
  const heading=()=>{let h=content.querySelector('h1');if(!h){content.insertAdjacentHTML('afterbegin',`<header class="bk-page-heading"><h1>${current[3]}</h1><p>${current[4]}</p></header>`);h=content.querySelector('h1')}h.textContent=current[3];h.classList.add('bk-page-title');const head=h.closest('header')||h.parentElement;head.classList.add('bk-page-heading');};
  heading();if(current[0]==='ledger')new MutationObserver(heading).observe(content,{childList:true});
 });
})();
