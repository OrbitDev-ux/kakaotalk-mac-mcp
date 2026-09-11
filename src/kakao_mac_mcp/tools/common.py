"""도구 모듈들이 공유하는 내부 헬퍼.

이 파일 자체는 MCP 도구가 아니다 — health/rooms/observe/reply 등
여러 도구 모듈이 똑같이 필요로 하는 설정 로드, DB 연결, 허용 방
조회, 발신자 마스킹 로직을 한 곳에 모아 둔다. 특히 발신자 마스킹은
rooms.py/observe.py/reply.py가 같은 메시지에 대해 항상 같은
fingerprint를 계산해야 하므로(그렇지 않으면 중복 판정이 어긋난다)
반드시 이 구현 하나만 사용해야 한다.
"""

from __future__ import annotations

import hashlib
import sqlite3

from kakao_mac_mcp.config import DEFAULT_CONFIG_PATH, AppConfig, RoomConfig, load_config
from kakao_mac_mcp.state import DEFAULT_DB_PATH, init_db

MAX_READ_LIMIT = 50
DEFAULT_READ_LIMIT = 20

_conn: sqlite3.Connection | None = None


def try_load_config(config_path: str = DEFAULT_CONFIG_PATH) -> AppConfig | None:
    """config.json 로드를 시도한다. 실패하면 None (예외를 밖으로 던지지 않음)."""
    try:
        return load_config(config_path)
    except (FileNotFoundError, ValueError):
        return None


def get_conn() -> sqlite3.Connection:
    """상태 DB 연결을 lazy하게 초기화해서 도구 모듈 간에 재사용한다."""
    global _conn
    if _conn is None:
        _conn = init_db(DEFAULT_DB_PATH)
    return _conn


def close_conn() -> None:
    """전역 DB 연결을 닫고 다음 get_conn() 호출 시 새로 열리도록 초기화한다."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def find_room(config: AppConfig, room_id: str) -> RoomConfig | None:
    """허용 목록에서 enabled=True인 room_id를 찾는다."""
    return next(
        (room for room in config.rooms if room.room_id == room_id and room.enabled),
        None,
    )


def mask_sender_alias(room_id: str, raw_sender: str | None) -> str | None:
    """실제 발신자 이름을 room_id 범위의 안정적인 익명 별칭으로 바꾼다."""
    if raw_sender is None:
        return None
    digest = hashlib.sha256(f"{room_id}\x1f{raw_sender}".encode("utf-8")).hexdigest()
    return f"sender-{digest[:8]}"
