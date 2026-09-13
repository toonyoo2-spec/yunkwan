#!/bin/zsh
# 더블클릭 한 번으로 설치합니다.
#   1) 토스 인증정보 저장  2) 연결 확인  3) 공시·뉴스 키(선택)  4) 사이트 업로드(선택)
#   5) 자동 실행 일정 등록  6) 과거 분봉 소급 수집(선택)
cd "${0:A:h}"

echo "BORAKWAN 주식 기록 설치"
echo "------------------------------------"
echo "입력할 때 글자가 보이지 않는 것이 정상입니다."
echo "키는 채팅이나 웹페이지에 절대 입력하지 마세요."
echo ""

python3 daily_runner.py setup || { read '?실패했습니다. Enter를 누르면 닫힙니다.'; exit 1; }

echo ""
echo "연결을 확인합니다..."
python3 daily_runner.py check || { read '?연결 확인에 실패했습니다. Enter를 누르면 닫힙니다.'; exit 1; }

echo ""
echo "------------------------------------"
echo "[선택] 공시·뉴스 수집 키를 등록하시겠습니까?"
echo "  금감원 OpenDART와 네이버 검색 API 모두 무료입니다."
echo "  등록하지 않으면 공시·뉴스 없이 나머지만 동작합니다."
read "reply?등록하려면 y, 건너뛰려면 Enter: "
if [[ "$reply" == "y" ]]; then
  python3 news.py || echo "공시·뉴스 키 등록을 건너뜁니다."
fi

echo ""
echo "------------------------------------"
echo "[선택] 폰에서도 보려면 사이트 업로드를 설정하세요."
echo "  가격은 올라가지 않습니다. 비율(%)과 판정만 올립니다."
echo "  먼저 Supabase에서 stocks_schema.sql을 실행해두셔야 합니다."
read "reply?설정하려면 y, 건너뛰려면 Enter: "
if [[ "$reply" == "y" ]]; then
  python3 publish.py setup || echo "사이트 업로드 설정을 건너뜁니다."
fi

echo ""
echo "자동 실행 일정을 등록합니다..."
python3 schedule.py install || { read '?일정 등록에 실패했습니다. Enter를 누르면 닫힙니다.'; exit 1; }

echo ""
echo "------------------------------------"
echo "[선택] 과거 분봉을 지금 끌어올까요?"
echo "  토스 보관 한계까지 거슬러 올라가며 받습니다. 오래 걸리지만(수십 분~수 시간)"
echo "  중단했다가 다시 실행하면 이어서 진행됩니다."
echo "  성공하면 적중률 판정에 필요한 표본이 몇 주 대신 며칠 만에 모입니다."
read "reply?지금 받으려면 y, 나중에 하려면 Enter: "
if [[ "$reply" == "y" ]]; then
  python3 history.py backfill
fi

echo ""
echo "------------------------------------"
echo "설치가 끝났습니다."
echo "개인 기록 화면: http://127.0.0.1:8766/"
echo "다음 거래일 08:30부터 기록이 쌓입니다."
echo ""
echo "[중요] 맥북이 잠자기에 들어가면 08:30을 놓칠 수 있습니다."
echo "  터미널을 켜둘 필요도, 화면이 켜져 있을 필요도 없지만,"
echo "  로그인된 상태로 전원이 들어와 있어야 합니다."
echo ""
echo "  평일 07:50에 자동으로 깨우려면 아래를 한 번 실행하세요 (비밀번호 필요):"
echo ""
echo "    sudo pmset repeat wakeorpoweron MTWRF 07:50:00"
echo ""
echo "  전원 어댑터를 연결해두는 것도 권장합니다."
echo "  현재 예약 확인: pmset -g sched   /   예약 해제: sudo pmset repeat cancel"
read '?Enter를 누르면 닫힙니다.'
