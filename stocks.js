(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const fmt=n=>Number(n).toLocaleString('ko-KR',{maximumFractionDigits:2});
  const date=s=>new Date(s).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});
  let records=[], index=0, storageKey=null;
  function render(){
    const select=$('recordSelect'); select.replaceChildren();
    records.forEach((r,i)=>{const o=document.createElement('option');o.value=i;o.textContent=date(r.received_at)+' 수집';select.append(o);});
    select.disabled=!records.length; select.value=String(index);
    $('prev').disabled=index>=records.length-1; $('next').disabled=index<=0;
    $('exportButton').disabled=!records.length;
    const r=records[index]; $('stockList').replaceChildren();
    $('candidateCount').textContent=r?r.watchlist.length+'개':'—';
    $('forecastCount').textContent=r?r.watchlist.filter(x=>x.expected_pct!=null).length+'개':'—';
    $('mae').textContent=r?.assessment?.mae_pp!=null?fmt(r.assessment.mae_pp)+'%p':'—';
    $('methodNote').textContent=r?.method_note||'';
    const show=$('showAnalysis').checked;
    $('analysisSummary').hidden=!show;
    $('analysisSummary').textContent=!r?.assessment?'장 마감 평가 대기 중입니다. 종가가 확인되지 않은 경우 임의 가격으로 채우지 않습니다.':r.assessment.mae_pp==null?'종가는 수집했지만 예상값이 부족해 오차를 계산할 수 없습니다.':'평균 절대오차 '+fmt(r.assessment.mae_pp)+'%p · 방향 적중률 '+fmt(r.assessment.direction_accuracy_pct)+'% · 0% 예상 기준 오차 '+fmt(r.assessment.benchmark_zero_mae_pp)+'%p. 오차는 작을수록 좋습니다.';
    $('timestamps').textContent=r?'순위 기준 '+date(r.ranked_at)+' · 수집 '+date(r.received_at)+' · '+r.market_status:'';
    $('freshness').textContent=r?StockData.freshness(r):'';
    $('empty').hidden=!!(r && r.watchlist.length);
    if(!r)return;
    r.watchlist.forEach(row=>{
      const card=document.createElement('article');card.className='stock-card';
      const top=document.createElement('div');top.className='stock-top';
      const left=document.createElement('div'),name=document.createElement('h2'),code=document.createElement('small');
      name.textContent=row.name;code.textContent=row.symbol;left.append(name,code);
      const right=document.createElement('div');right.className='price';right.textContent=fmt(row.price)+'원';
      const change=document.createElement('div');change.className='change '+(row.change_pct>0?'up':row.change_pct<0?'down':'');change.textContent=(row.change_pct>0?'+':'')+row.change_pct.toFixed(2)+'% · 전일 대비';right.append(change);
      top.append(left,right);
      const forecast=document.createElement('p');forecast.className='forecast';forecast.textContent=row.expected_pct==null?'예상 상승률: 자료 부족 / 산출 보류':'예상 상승률 '+(row.expected_pct>0?'+':'')+row.expected_pct.toFixed(2)+'% · 과거 '+row.sample_count+'일';
      const detail=document.createElement('div');detail.className='assessment';detail.hidden=!show;
      const a=r.assessment?.rows.find(x=>x.symbol===row.symbol);
      if(!a?.actual) detail.textContent='정규장 시가·종가 확인 대기';
      else detail.textContent='실제 '+(a.actual.actual_pct>0?'+':'')+a.actual.actual_pct.toFixed(2)+'% · 시가 '+fmt(a.actual.open)+'원 → 종가 '+fmt(a.actual.close)+'원'+(a.error_pp==null?' · 예상값 부족으로 비교 보류':' · 오차 '+(a.error_pp>0?'+':'')+a.error_pp.toFixed(2)+'%p · 방향 '+(a.direction_hit?'일치':'불일치'));
      const reason=document.createElement('p');reason.className='reason';reason.textContent='1일 거래대금 '+fmt(row.trading_amount/1e8)+'억원 · '+row.reason;card.append(top,forecast,detail,reason);$('stockList').append(card);
    });
  }
  window.addEventListener('authReady',async()=>{
    const {data:{session}}=window.STOCK_LOCAL_MODE?{data:{session:{user:{id:'local-mac'}}}}:await window.sb.auth.getSession();
    if(!session)return;
    const key='borakwan-stock-records-v1:'+session.user.id;
    if(storageKey===key)return;
    storageKey=key;records=[];index=0;
    try{const saved=JSON.parse(localStorage.getItem(key)||'[]');if(!Array.isArray(saved)||saved.length>100)throw Error();records=saved.map(StockData.normalize).sort((a,b)=>Date.parse(b.received_at)-Date.parse(a.received_at));}
    catch{$('message').textContent='저장 기록을 읽지 못했습니다. 수집 JSON을 다시 불러오세요.';}
    render();await loadServer();
  });
  $('importFile').addEventListener('change',async e=>{
    try{
      if(!storageKey)throw Error('로그인 후 불러오세요.');
      const files=[...e.target.files];if(files.length>100)throw Error('한 번에 최대 100개 파일을 불러올 수 있습니다.');
      const incoming=[];
      for(const f of files){if(f.size>1000000)throw Error('파일 크기는 1MB 이하여야 합니다.');const json=JSON.parse(await f.text());incoming.push(...(Array.isArray(json)?json:[json]).map(StockData.normalize));}
      const all=new Map(records.map(r=>[r.received_at,r]));incoming.forEach(r=>all.set(r.received_at,r));
      const next=[...all.values()].sort((a,b)=>Date.parse(b.received_at)-Date.parse(a.received_at));
      if(next.length>100)throw Error('보관 한도는 100회입니다. 기존 기록을 내보내 보관하세요.');
      localStorage.setItem(storageKey,JSON.stringify(next.filter(r=>r.strategy_version!=='daily-v1')));records=next;index=0;render();$('message').textContent='기록을 불러왔습니다. 이 브라우저에만 저장됩니다.';
    }catch(error){$('message').textContent=error instanceof SyntaxError?'JSON 파일을 읽을 수 없습니다.':error.name==='QuotaExceededError'?'브라우저 저장 공간이 부족합니다.':error.message;}
    e.target.value='';
  });
  let loading=false;
  async function loadServer(){
    if(!storageKey||loading||!window.STOCK_LOCAL_MODE)return;loading=true;
    try{
      const response=await fetch('/api/reports',{cache:'no-store'});
      if(!response.ok)throw Error('맥북 자동 기록을 읽지 못했습니다. 로컬 뷰어를 확인하세요.');
      const data=await response.json();
      const current=records[index]?.received_at;
      const map=new Map(records.map(r=>[r.received_at,r]));
      data.map(StockData.fromServer).forEach(r=>map.set(r.received_at,r));records=[...map.values()].sort((a,b)=>Date.parse(b.received_at)-Date.parse(a.received_at));
      index=Math.max(0,records.findIndex(r=>r.received_at===current));render();
      $('message').textContent=data.length?'맥북에 저장된 자동 기록을 불러왔습니다.':'자동 수집 첫 실행을 기다리고 있습니다.';
    }catch(e){$('message').textContent=e.message;}finally{loading=false;}
  }
  $('showAnalysis').addEventListener('change',render);
  setInterval(loadServer,60000);
  $('recordSelect').addEventListener('change',e=>{index=Number(e.target.value);render();});
  $('prev').addEventListener('click',()=>{if(index<records.length-1){index++;render();}});
  $('next').addEventListener('click',()=>{if(index>0){index--;render();}});
  $('exportButton').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(records,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='stock-records.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  setInterval(()=>{if(records[index])$('freshness').textContent=StockData.freshness(records[index]);},30000);
})();
