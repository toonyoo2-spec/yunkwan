/*
  BORAKWAN 공통 스크립트 (모든 페이지 공용)
  - HTML에 안전하게 텍스트를 넣을 때 쓰는 escapeHtml 하나만 여기서 관리합니다.
  - 예전에는 index.html(calEscapeHtml), ledger.html(escapeAttr), docs/review/task/trip.html(escapeHtml)이
    전부 같은 내용을 이름만 다르게 따로 갖고 있었는데, 이제 여기 하나만 있습니다.
*/
function escapeHtml(str){
  return (str||"").replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}
