/*
  BORAKWAN 패널형 페이지 공통 스크립트 (docs.html / review.html / task.html / trip.html 공용)
  - 4개 페이지가 똑같이 갖고 있던 escapeHtml 함수를 여기 한 곳으로 모았습니다.
*/
function escapeHtml(str){
  return (str||"").replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}
