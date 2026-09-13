#!/usr/bin/env python3
"""맥북 내부 저장소. 토스 시세는 약관상 본인 매매 목적 전용이라 이 경로 밖으로 나가지 않습니다."""
import json
import os
from pathlib import Path

STATE = Path.home() / 'Library/Application Support/BorakwanStocks'

DIR_MODE = 0o700
FILE_MODE = 0o600


def write_json(path, value):
    """소유자만 읽을 수 있는 권한으로 원자적으로 씁니다."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    tmp = path.with_suffix(path.suffix + '.tmp')
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(handle, 'w', encoding='utf-8') as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json(path, default=None):
    """없거나 깨진 파일은 조용히 기본값으로 넘깁니다. 부분 저장된 파일 때문에 파이프라인이 멈추지 않게."""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def list_json(directory, pattern='*.json'):
    """디렉터리 안의 JSON을 파일명 오름차순으로 돌려줍니다."""
    if not directory.exists():
        return []
    return [path for path in sorted(directory.glob(pattern))]
