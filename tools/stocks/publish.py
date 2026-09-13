#!/usr/bin/env python3
"""사이트 업로드용 정제와 전송.

토스 Open API 약관 제5조 ③은 '시세정보'의 제3자 제공·배포를 금지합니다.
우리가 계산한 분석 결과(적중률·손익률·판정)는 시세정보가 아니지만, 가격·거래량·호가는
시세정보 그 자체입니다. 그래서 다음 한 가지 기준으로 가릅니다.

    업로드한 내용만으로 토스의 시세를 복원할 수 있으면 올리지 않는다.

원(₩) 단위 값은 전부 제외하고 비율(%)과 판정만 올립니다. 기준가를 모르면 %로는
가격을 되돌릴 수 없습니다.

안전장치는 화이트리스트입니다. 허용 필드를 명시적으로 나열하고 나머지는 전부 버립니다.
블랙리스트(금지 목록)로 하면 나중에 필드를 추가할 때 빠뜨려서 새어나갑니다.
"""
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from store import STATE, read_json, write_json
from tossapi import now

SITE_CONFIG = STATE / 'site.json'
TABLE = 'stock_reports'
RESEARCH_TABLE = 'stock_research'
POSITIONS_TABLE = 'stock_positions'
TIMEOUT_SEC = 20

# 사이트가 브라우저에 그대로 노출하는 값입니다(supabase-config.js). 비밀이 아니므로
# 기본값으로 넣어두고, 설치할 때 Enter만 눌러 넘어갈 수 있게 합니다.
DEFAULT_URL = 'https://kblwddlquwlvumhwkirl.supabase.co'
DEFAULT_ANON_KEY = 'sb_publishable_X6OqS-mM1igLrGcc7g4CzQ_fbVXpblY'

# --- 화이트리스트: 여기 없는 필드는 올라가지 않습니다 ---

RECOMMENDATION_FIELDS = ('symbol', 'name', 'market', 'setup', 'entry_rule', 'stop_pct',
                         'target_pct', 'reason', 'gate', 'entry_deadline', 'exit_time')
HELD_FIELDS = ('symbol', 'name', 'market', 'setup', 'gate')
LADDER_FIELDS = ('target_pct', 'result', 'net_pct', 'win', 'ambiguous_bar')
SIMULATION_FIELDS = ('symbol', 'name', 'setup', 'result', 'recommended', 'stop_pct',
                     'note', 'strength_level')
STRENGTH_FIELDS = ('level', 'raw_score', 'components', 'note')
STRENGTH_COMPONENT_FIELDS = ('label', 'points', 'detail')
SCORE_FIELDS = ('hits', 'total', 'hit_rate', 'lower_bound', 'target', 'status', 'reason')
REGIME_FIELDS = ('status', 'allow_long', 'reason')

# 원 단위 값이 들어 있는 키. 방어적으로 한 번 더 확인합니다.
PRICE_LIKE = ('price', 'close', 'open', 'high', 'low', 'volume', 'amount', 'net', 'rate_krw')


def pick(source, fields):
    """허용 필드만 남깁니다. 값이 없는 필드는 아예 넣지 않습니다."""
    if not isinstance(source, dict):
        return {}
    return {key: source[key] for key in fields if source.get(key) is not None}


def assert_no_prices(payload):
    """업로드 직전 마지막 점검. 원 단위로 보이는 큰 정수가 남아 있으면 중단합니다."""
    def walk(node, path=''):
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = key.lower()
                if any(token in lowered for token in PRICE_LIKE) and isinstance(value, (int, float)):
                    # 비율 필드(%)는 허용합니다. 그 외 숫자는 가격일 수 있으므로 막습니다.
                    if not lowered.endswith('_pct') and not lowered.endswith('rate'):
                        raise ValueError(f'가격으로 의심되는 값이 남아 있습니다: {path}{key}')
                walk(value, f'{path}{key}.')
        elif isinstance(node, list):
            for item in node:
                walk(item, path)
    walk(payload)


def sanitize_strength(rating):
    """강도는 비율·판정만 담고 있어 올려도 됩니다. 그래도 필드는 화이트리스트로 거릅니다."""
    if not isinstance(rating, dict):
        return None
    picked = pick(rating, STRENGTH_FIELDS)
    picked['components'] = [pick(part, STRENGTH_COMPONENT_FIELDS)
                            for part in (rating.get('components') or [])][:20]
    return picked


GOAL_FIELDS = ('date', 'target_pct', 'account_return_pct', 'exposure_pct',
               'position_count', 'achieved', 'shortfall_note')
GOAL_SUMMARY_FIELDS = ('days', 'achieved_days', 'achievement_rate_pct',
                       'actual_multiple', 'target_multiple', 'mean_daily_pct', 'note')


def sanitize_goal(state):
    """하루 목표 현황. 계좌 금액은 올리지 않고 비율과 건수만 올립니다."""
    if not isinstance(state, dict):
        return None
    picked = pick(state, GOAL_FIELDS)
    picked['summary'] = pick(state.get('summary') or {}, GOAL_SUMMARY_FIELDS)
    return picked


def sanitize_forecast(forecast):
    """08:30 고정본에서 업로드 가능한 부분만 뽑습니다."""
    return {
        'trade_date': forecast['trade_date'],
        'published_at': forecast['published_at'],
        'strategy_version': forecast.get('strategy_version'),
        'target_hit_rate': forecast.get('target_hit_rate'),
        'method_note': forecast.get('method_note'),
        'regime': pick(forecast.get('regime', {}), REGIME_FIELDS),
        'goal': sanitize_goal(forecast.get('goal')),
        'recommendations': [{**pick(row, RECOMMENDATION_FIELDS),
                             'strength': sanitize_strength(row.get('strength'))}
                            for row in forecast.get('recommendations', [])],
        'held': [pick(row, HELD_FIELDS) for row in forecast.get('held', [])][:20],
        'scoreboard': {key: pick(value, SCORE_FIELDS)
                       for key, value in (forecast.get('scoreboard') or {}).items()},
    }


def sanitize_assessment(assessment):
    """마감 평가에서 업로드 가능한 부분만 뽑습니다. 가격은 전부 제외합니다."""
    simulations = []
    for run in assessment.get('simulations', []):
        row = pick(run, SIMULATION_FIELDS)
        ladder = run.get('ladder') or {}
        row['ladder'] = [pick(entry, LADDER_FIELDS) for entry in ladder.values()]
        simulations.append(row)
    summary = assessment.get('summary', {})
    return {
        'trade_date': assessment['trade_date'],
        'assessed_at': assessment['assessed_at'],
        'complete': assessment.get('complete'),
        'simulations': simulations,
        'summary': {
            'traded_count': summary.get('traded_count'),
            'skipped_count': summary.get('skipped_count'),
            'per_target': summary.get('per_target'),
            'note': summary.get('note'),
        },
    }


# --- 전송 ---------------------------------------------------------------

def load_site_config():
    config = read_json(SITE_CONFIG)
    if not config:
        raise RuntimeError('사이트 설정이 없습니다. `python3 publish.py setup`을 먼저 실행하세요.')
    return config


def post_json(url, payload, headers):
    request = Request(url, data=json.dumps(payload).encode(), headers=headers, method='POST')
    try:
        with urlopen(request, timeout=TIMEOUT_SEC) as response:
            body = response.read().decode()
            return json.loads(body) if body.strip() else {}
    except HTTPError as exc:
        raise RuntimeError(f'업로드 실패 (HTTP {exc.code}). 사이트 설정과 테이블 권한을 확인하세요.') from None
    except (URLError, TimeoutError):
        raise RuntimeError('업로드 실패: 네트워크를 확인하세요.') from None


def sign_in(config):
    """사이트 계정으로 로그인해 사용자 토큰을 받습니다. RLS가 이 사용자 기준으로 적용됩니다."""
    url = f"{config['url']}/auth/v1/token?{urlencode({'grant_type': 'password'})}"
    result = post_json(url, {'email': config['email'], 'password': config['password']},
                       {'apikey': config['anon_key'], 'Content-Type': 'application/json'})
    token = result.get('access_token')
    if not token:
        raise RuntimeError('사이트 로그인에 실패했습니다. 아이디와 비밀번호를 확인하세요.')
    return token


def upsert(config, token, row):
    url = f"{config['url']}/rest/v1/{TABLE}?on_conflict=trade_date"
    post_json(url, row, {
        'apikey': config['anon_key'],
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Prefer': 'resolution=merge-duplicates,return=minimal',
    })


# 분석 요약에 허용되는 필드. 전부 비율(%)·건수·판정 문구이며 가격은 없습니다.
RESEARCH_SETUP_FIELDS = ('hits', 'total', 'hit_rate_pct', 'lower_bound_pct', 'status',
                         'gate_reason', 'chance_verdict', 'chance_reason',
                         'concentration_reason', 'max_drawdown_pct',
                         'longest_losing_streak', 'total_return_pct',
                         'survives_correction', 'tested_count')
RESEARCH_HORIZON_FIELDS = ('hold_days', 'trades', 'hit_rate_pct', 'mean_net_pct',
                           'total_return_pct', 'max_drawdown_pct',
                           'longest_losing_streak', 'verdict')


def sanitize_research(summary):
    """분석 요약에서 올려도 되는 부분만 뽑습니다."""
    return {
        'updated_at': summary.get('updated_at'),
        'trade_days': summary.get('trade_days'),
        'date_range': summary.get('date_range'),
        'trade_count': summary.get('trade_count'),
        'target_hit_rate_pct': summary.get('target_hit_rate_pct'),
        'setups': {key: pick(value, RESEARCH_SETUP_FIELDS)
                   for key, value in (summary.get('setups') or {}).items()},
        'regimes': summary.get('regimes'),
        'strength': summary.get('strength'),
        'horizon': [pick(row, RESEARCH_HORIZON_FIELDS)
                    for row in (summary.get('horizon') or [])],
        'walkforward': summary.get('walkforward'),
        'caveats': summary.get('caveats'),
    }


def publish_research(summary):
    """분석 요약 1행을 사이트에 올립니다. 계정마다 한 행만 유지합니다."""
    payload = sanitize_research(summary)
    assert_no_prices(payload)
    config = load_site_config()
    token = sign_in(config)
    url = f"{config['url']}/rest/v1/{RESEARCH_TABLE}?on_conflict=owner_id"
    post_json(url, {'payload': payload, 'updated_at': now().isoformat()}, {
        'apikey': config['anon_key'],
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Prefer': 'resolution=merge-duplicates,return=minimal',
    })
    print('분석 요약 업로드 완료 — 비율과 판정만 올렸습니다.')


# 보유 종목 판정에서 사이트로 올려도 되는 필드.
# 손익률(%)과 판정만 올립니다. 현재가는 올리지 않습니다.
VERDICT_FIELDS = ('verdict', 'trigger', 'reason', 'held_days', 'net_pnl_pct',
                  'current_strength', 'switch_to', 'best_alternative', 'decided_at')


def get_json(url, config, token):
    request = Request(url, headers={'apikey': config['anon_key'],
                                    'Authorization': f'Bearer {token}',
                                    'Accept': 'application/json'})
    try:
        with urlopen(request, timeout=TIMEOUT_SEC) as response:
            return json.loads(response.read().decode() or '[]')
    except HTTPError as exc:
        raise RuntimeError(f'조회 실패 (HTTP {exc.code}).') from None
    except (URLError, TimeoutError):
        raise RuntimeError('조회 실패: 네트워크를 확인하세요.') from None


def patch_json(url, payload, config, token):
    body = json.dumps(payload).encode()
    request = Request(url, data=body, method='PATCH', headers={
        'apikey': config['anon_key'],
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal',
    })
    try:
        with urlopen(request, timeout=TIMEOUT_SEC) as response:
            response.read()
    except HTTPError as exc:
        raise RuntimeError(f'수정 실패 (HTTP {exc.code}).') from None
    except (URLError, TimeoutError):
        raise RuntimeError('수정 실패: 네트워크를 확인하세요.') from None


def fetch_open_positions():
    """사이트에 기록된 보유 중인 종목을 읽습니다."""
    config = load_site_config()
    token = sign_in(config)
    url = (f"{config['url']}/rest/v1/{POSITIONS_TABLE}"
           "?status=eq.open&select=id,symbol,name,market,entry_date,entry_price,"
           "quantity,target_pct,stop_pct&order=entry_date.asc")
    return get_json(url, config, token)


def push_position_verdicts(verdicts):
    """판정 결과를 각 보유 종목 행에 써 넣습니다."""
    if not verdicts:
        return
    config = load_site_config()
    token = sign_in(config)
    for row in verdicts:
        if not row.get('id'):
            continue
        payload = {'verdict': pick(row, VERDICT_FIELDS), 'updated_at': now().isoformat()}
        assert_no_prices(payload)
        url = f"{config['url']}/rest/v1/{POSITIONS_TABLE}?id=eq.{int(row['id'])}"
        patch_json(url, payload, config, token)
    print(f'보유 종목 판정 {len(verdicts)}건 업로드 완료.')


def publish(date):
    """해당 날짜의 고정본과 평가를 정제해 사이트로 올립니다."""
    forecast = read_json(STATE / 'reports' / f'{date}-forecast.json')
    if not forecast:
        print('올릴 기록이 없습니다:', date)
        return
    assessment = read_json(STATE / 'reports' / f'{date}-assessment.json')
    payload = {
        'trade_date': date,
        'forecast': sanitize_forecast(forecast),
        'assessment': sanitize_assessment(assessment) if assessment else None,
        'uploaded_at': now().isoformat(),
    }
    assert_no_prices(payload)
    config = load_site_config()
    upsert(config, sign_in(config), payload)
    print(f'{date} 업로드 완료 — 가격·거래량은 제외하고 분석 결과만 올렸습니다.')


def publish_recent(limit=10):
    paths = sorted((STATE / 'reports').glob('*-forecast.json'), reverse=True)[:limit]
    for path in paths:
        publish(path.name.split('-forecast')[0])


def setup():
    """사이트 업로드 설정. 비밀번호는 이 맥북의 권한 600 파일에만 저장됩니다."""
    import getpass
    if not sys.stdin.isatty():
        raise RuntimeError('터미널에서 직접 실행하세요.')
    print('사이트(yunkwan.cloud) 업로드 설정입니다. 토스 키가 아니라 사이트 로그인 정보입니다.')
    print('URL과 anon key는 Enter만 누르면 기본값이 들어갑니다 (사이트에 공개된 값이라 비밀이 아닙니다).')
    url = input(f'Supabase URL [{DEFAULT_URL}]: ').strip() or DEFAULT_URL
    anon = input(f'Supabase anon key [{DEFAULT_ANON_KEY}]: ').strip() or DEFAULT_ANON_KEY
    login = input('사이트 아이디: ').strip()
    password = getpass.getpass('사이트 비밀번호 (입력 숨김): ').strip()
    if not all((anon, login, password)):
        raise RuntimeError('모든 값을 입력하세요.')
    write_json(SITE_CONFIG, {'url': url, 'anon_key': anon,
                             'email': f'{login}@internal.local', 'password': password})
    print('사이트 설정 저장 완료.')


if __name__ == '__main__':
    try:
        command = sys.argv[1] if len(sys.argv) > 1 else 'recent'
        if command == 'setup':
            setup()
        elif command == 'recent':
            publish_recent()
        else:
            publish(command)
    except (KeyboardInterrupt, EOFError):
        print('취소했습니다.')
        sys.exit(1)
    except (RuntimeError, ValueError) as exc:
        print('실패:', exc)
        sys.exit(1)
