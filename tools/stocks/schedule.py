#!/usr/bin/env python3
"""맥북 자동 실행 일정(launchd) 등록·해제·확인.

등록되는 작업 (한국시간 기준, 맥북의 시스템 시간대를 따릅니다)
  08:00 prepare  — 유니버스·특징·공시·뉴스·새벽 해외시장 수집
  08:30 morning  — 그날의 추천 고정
  5분마다 intraday — 장중 실제 돌파 시점 감시. 장 시간이 아니면 즉시 종료합니다
  15:40 close    — 분봉 축적 + 계획 재현 평가
  16:10 close    — 15:40에 종가가 덜 채워졌을 때를 위한 재시도
  상시  viewer   — http://127.0.0.1:8766 개인 기록 화면 (맥북 내부 전용)

토스 인증정보는 여기서 다루지 않습니다. `daily_runner.py setup`이 사용자 전용
파일(권한 600)에 저장한 값을 그대로 씁니다.
"""
import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENTS = Path.home() / 'Library/LaunchAgents'
LOGS = Path.home() / 'Library/Logs/BorakwanStocks'
PREFIX = 'cloud.yunkwan.stocks.'
WEEKDAYS = (1, 2, 3, 4, 5)  # launchd: 1=월 … 5=금. 휴장일은 실행 후 시장 캘린더로 걸러집니다.

INTRADAY_INTERVAL_SEC = 300     # 5분. 장 시간이 아니면 스크립트가 스스로 즉시 끝냅니다.

JOBS = {
    'prepare': {'args': ['daily_runner.py', 'prepare'], 'times': [(8, 0)]},
    'morning': {'args': ['daily_runner.py', 'morning'], 'times': [(8, 30)]},
    'intraday': {'args': ['intraday.py'], 'interval': INTRADAY_INTERVAL_SEC},
    'close': {'args': ['daily_runner.py', 'close'], 'times': [(15, 40), (16, 10)]},
    'viewer': {'args': ['local_view.py'], 'keep_alive': True},
}


def label(name):
    return PREFIX + name


def plist_path(name):
    return AGENTS / (label(name) + '.plist')


def calendar_entries(times):
    return [{'Weekday': day, 'Hour': hour, 'Minute': minute}
            for (hour, minute) in times for day in WEEKDAYS]


def build_plist(name, job):
    plist = {
        'Label': label(name),
        'ProgramArguments': [sys.executable, *[str(HERE / a) if a.endswith('.py') else a
                                               for a in job['args']]],
        'WorkingDirectory': str(HERE),
        'StandardOutPath': str(LOGS / f'{name}.log'),
        'StandardErrorPath': str(LOGS / f'{name}.log'),
        'ProcessType': 'Background',
    }
    if job.get('keep_alive'):
        plist['RunAtLoad'] = True
        plist['KeepAlive'] = True
    elif job.get('interval'):
        plist['StartInterval'] = job['interval']
    else:
        plist['StartCalendarInterval'] = calendar_entries(job['times'])
    return plist


def launchctl(*args):
    return subprocess.run(['launchctl', *args], capture_output=True, text=True)


def bootout(name):
    launchctl('bootout', f'gui/{os_uid()}/{label(name)}')


def bootstrap(name):
    result = launchctl('bootstrap', f'gui/{os_uid()}', str(plist_path(name)))
    if result.returncode != 0:
        raise RuntimeError(f'{name} 등록 실패: {result.stderr.strip() or result.stdout.strip()}')


def os_uid():
    import os
    return os.getuid()


def install():
    AGENTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    for name, job in JOBS.items():
        path = plist_path(name)
        path.write_bytes(plistlib.dumps(build_plist(name, job)))
        bootout(name)
        bootstrap(name)
        print(f'등록 완료: {name}')
    print()
    print('자동 실행 일정이 등록되었습니다.')
    print('개인 기록 화면: http://127.0.0.1:8766/')
    print('실행 기록: ' + str(LOGS))
    print('맥북이 자거나 인터넷이 끊겨 있으면 그날 기록은 비어 있게 됩니다.')


def uninstall():
    for name in JOBS:
        bootout(name)
        plist_path(name).unlink(missing_ok=True)
        print(f'해제 완료: {name}')


def status():
    result = launchctl('list')
    lines = [line for line in result.stdout.splitlines() if PREFIX in line]
    if not lines:
        print('등록된 자동 실행 일정이 없습니다. `python3 schedule.py install`을 실행하세요.')
        return
    print('PID\t종료코드\t작업')
    for line in lines:
        print(line)
    print()
    print('실행 기록: ' + str(LOGS))


def main():
    parser = argparse.ArgumentParser(description='주식 기록 자동 실행 일정 관리')
    parser.add_argument('command', choices=['install', 'uninstall', 'status'])
    globals()[parser.parse_args().command]()


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        print('실패:', exc)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print('\n취소했습니다.')
        sys.exit(1)
