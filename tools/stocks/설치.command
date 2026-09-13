#!/bin/zsh
# 더블클릭 한 번으로: 인증정보 저장 → 연결 확인 → 자동 실행 일정 등록
cd "${0:A:h}"

echo "BORAKWAN 주식 기록 설치"
echo "------------------------------------"
echo "토스 Client ID / Secret을 입력받아 이 맥북의 사용자 전용 파일(권한 600)에 저장합니다."
echo "입력할 때 글자가 보이지 않는 것이 정상입니다. 키는 채팅이나 웹페이지에 절대 입력하지 마세요."
echo ""

python3 daily_runner.py setup || { read '?실패했습니다. Enter를 누르면 닫힙니다.'; exit 1; }

echo ""
echo "연결을 확인합니다..."
python3 daily_runner.py check || { read '?연결 확인에 실패했습니다. Enter를 누르면 닫힙니다.'; exit 1; }

echo ""
echo "자동 실행 일정을 등록합니다..."
python3 schedule.py install || { read '?일정 등록에 실패했습니다. Enter를 누르면 닫힙니다.'; exit 1; }

echo ""
echo "------------------------------------"
echo "설치가 끝났습니다. 다음 거래일 아침 08:30부터 기록이 쌓입니다."
echo "개인 기록 화면: http://127.0.0.1:8766/"
read '?Enter를 누르면 닫힙니다.'
