/* =========================================================
   BORAKWAN 공용 고정 메뉴바 (nav.js)
   - 사용법: 각 페이지 <body> 여는 태그 바로 다음 줄에
     <script src="./nav.js"></script> 한 줄만 넣으면
     제목(BORAKWAN) + 이동 버튼이 상단에 자동으로 삽입됩니다.
   - 메뉴 항목을 추가/삭제/수정하려면 아래 NAV_ITEMS만 고치면
     모든 페이지에 한 번에 반영됩니다.
   - 스타일은 common.css의 색상 변수(--bg, --ink, --line, --card,
     --accent)를 그대로 따르되, 혹시 그 변수가 없는 페이지에서도
     깨지지 않도록 각 변수에 기본값(fallback)을 넣어뒀습니다.
   ========================================================= */
(function(){
  const NAV_ITEMS = [
    { href: './review.html', icon: '📋', label: 'Blog' },
    { href: './ledger.html', icon: '💰', label: '가계부' },
    { href: './task.html',   icon: '✅', label: 'Task' },
    { href: './trip.html',   icon: '✈️', label: '여행' },
    { href: './docs.html',   icon: '📁', label: '문서함' },
  ];

  const currentFile = (location.pathname.split('/').pop() || 'index.html');

  const style = document.createElement('style');
  style.textContent = `
    #bkNav{
      position:sticky;
      top:0;
      z-index:500;
      background:var(--bg, #f6f5f1);
      display:flex;
      align-items:center;
      justify-content:space-between;
      flex-wrap:wrap;
      gap:16px;
      padding:16px 0 20px;
      border-bottom:1px solid var(--line, #e1dfd6);
      margin-bottom:22px;
    }
    #bkNav .bk-title{
      font-size:44px;
      font-weight:800;
      letter-spacing:-0.02em;
      margin:0;
      color:var(--ink, #22221f);
      text-decoration:none;
      flex-shrink:0;
    }
    #bkNav .bk-grid{
      display:flex;
      flex-wrap:wrap;
      gap:10px;
    }
    #bkNav .bk-card{
      background:var(--card, #ffffff);
      border:1px solid var(--line, #e1dfd6);
      border-radius:12px;
      padding:12px 20px;
      text-decoration:none;
      color:var(--ink, #22221f);
      display:flex;
      align-items:center;
      gap:10px;
      flex:0 0 auto;
      transition:transform .12s ease, box-shadow .12s ease;
    }
    #bkNav .bk-card:hover{
      transform:translateY(-1px);
      box-shadow:0 4px 12px rgba(34,34,31,0.07);
      border-color:var(--accent, #3a5a52);
    }
    #bkNav .bk-card.active{
      border-color:var(--accent, #3a5a52);
      box-shadow:0 0 0 1px var(--accent, #3a5a52) inset;
    }
    #bkNav .bk-icon{
      font-size:19px;
      width:34px;height:34px;
      display:flex;align-items:center;justify-content:center;
      background:#eef0ec;
      border-radius:9px;
      flex-shrink:0;
    }
    #bkNav .bk-label{font-size:15.5px;font-weight:700;white-space:nowrap;}

    @media (max-width:640px){
      #bkNav{
        padding:12px 0 16px;
        margin-bottom:18px;
        gap:10px;
      }
      #bkNav .bk-title{font-size:30px;}
      #bkNav .bk-grid{
        width:100%;
        display:grid;
        grid-template-columns:repeat(3, 1fr);
        gap:8px;
      }
      #bkNav .bk-card{
        padding:12px 8px;
        flex-direction:column;
        justify-content:center;
        text-align:center;
        gap:5px;
        border-radius:10px;
      }
      #bkNav .bk-icon{width:36px;height:36px;font-size:19px;border-radius:9px;}
      #bkNav .bk-label{font-size:13.5px;white-space:normal;}
    }
  `;
  document.head.appendChild(style);

  const cardsHtml = NAV_ITEMS.map(item => {
    const fileName = item.href.replace('./', '');
    const isActive = fileName === currentFile;
    return `<a class="bk-card${isActive ? ' active' : ''}" href="${item.href}">
      <span class="bk-icon">${item.icon}</span>
      <span class="bk-label">${item.label}</span>
    </a>`;
  }).join('');

  const navHtml = `
    <header id="bkNav">
      <a class="bk-title" href="./index.html">BORAKWAN</a>
      <div class="bk-grid">${cardsHtml}</div>
    </header>
  `;

  document.body.insertAdjacentHTML('afterbegin', navHtml);
})();
