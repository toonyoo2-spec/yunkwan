(function(root){
  'use strict';
  const text = (v, max=200) => { if(typeof v !== 'string' || !v.length || v.length>max) throw Error('텍스트 형식 오류'); return v; };
  const stamp = v => { text(v,60); if(!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(v) || !Number.isFinite(Date.parse(v))) throw Error('시각 형식 오류'); return v; };
  const number = (v, positive=false) => { if(!['string','number'].includes(typeof v) || String(v).trim()==='' || !Number.isFinite(Number(v)) || (positive && Number(v)<=0)) throw Error('숫자 형식 오류'); return Number(v); };
  function normalize(value){
    if(!value || !['watchlist-only-v1','daily-v1'].includes(value.strategy_version) || !Array.isArray(value.watchlist) || value.watchlist.length>100) throw Error('수집기의 watchlist.json을 선택하세요.');
    const symbols=new Set();
    return {strategy_version:value.strategy_version,assessment:assessment(value.assessment),method_note:typeof value.method_note==='string'?text(value.method_note,1000):null,received_at:stamp(value.received_at),ranked_at:stamp(value.ranked_at),market_status:text(value.market_status),recommendations:[],watchlist:value.watchlist.map(row=>{
      const symbol=text(row.symbol,6); if(!/^\d{6}$/.test(symbol) || symbols.has(symbol)) throw Error('종목코드 오류'); symbols.add(symbol);
      return {symbol,name:text(row.name,100),price:number(row.price,true),change_pct:number(row.change_pct),trading_amount:number(row.trading_amount,true),reason:text(row.reason),expected_pct:row.expected_pct==null?null:number(row.expected_pct),sample_count:row.sample_count==null?0:number(row.sample_count),spread_pct:row.spread_pct==null?null:number(row.spread_pct)};
    })};
  }
  function assessment(a){
    if(a==null)return null;
    if(!Array.isArray(a.rows)||a.rows.length>100)throw Error('마감 분석 형식 오류');
    return {assessed_at:stamp(a.assessed_at),rows:a.rows.map(r=>({symbol:text(r.symbol,6),actual:r.actual==null?null:{actual_pct:number(r.actual.actual_pct),open:number(r.actual.open,true),close:number(r.actual.close,true)},expected_pct:r.expected_pct==null?null:number(r.expected_pct),error_pp:r.error_pp==null?null:number(r.error_pp),direction_hit:typeof r.direction_hit==='boolean'?r.direction_hit:null,status:text(r.status)})),mae_pp:a.mae_pp==null?null:number(a.mae_pp),direction_accuracy_pct:a.direction_accuracy_pct==null?null:number(a.direction_accuracy_pct),benchmark_zero_mae_pp:a.benchmark_zero_mae_pp==null?null:number(a.benchmark_zero_mae_pp)};
  }
  function fromServer(row){
    const f=row.forecast;
    return normalize({strategy_version:'daily-v1',received_at:f.published_at,ranked_at:f.ranked_at,market_status:'08:30 아침 예측 고정',method_note:f.method_note,watchlist:f.predictions,assessment:row.assessment});
  }
  function freshness(record, now=Date.now()){
    const age=now-Date.parse(record.ranked_at);
    if(age < -60000) return '기준 시각이 현재보다 미래입니다. 수집 환경을 확인하세요.';
    if(age > 300000) return '과거 집계 기록입니다. 현재 시세나 매매 신호로 사용하지 마세요.';
    return '최근 수집된 검토 후보입니다. 매수 신호는 아직 산출하지 않습니다.';
  }
  const api={normalize,freshness,fromServer};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  else root.StockData=api;
})(typeof window!=='undefined'?window:globalThis);
