"""도구: kakao_health.

카카오톡 연결 및 설정 상태를 점검한다. 메시지는 읽지 않는다
(진단 전용 — 사이드 이펙트 없음).
"""

from __future__ import annotations

from pathlib import Path

from kakao_mac_mcp.config import DEFAULT_CONFIG_PATH
from kakao_mac_mcp.macos.accessibility import is_trusted
from kakao_mac_mcp.macos.kakao_app import is_kakao_running
from kakao_mac_mcp.state import DEFAULT_DB_PATH
from kakao_mac_mcp.tools.common import try_load_config


def kakao_health(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """카카오톡 연결/설정 상태를 점검한다.

    메시지 읽기, 채팅창 탐색 등 부수 효과가 있는 동작은 수행하지
    않는다 — 프로세스 존재 여부, 접근성 권한, 설정 로드 가능 여부만
    확인한다.

    Args:
        config_path: config.json 경로 (MCP 도구로 호출될 때는 항상
            기본값을 쓴다 — CLI가 --config로 다른 경로를 검사할 때만
            override한다).

    Returns:
        {
            "ok": bool,
            "kakao_running": bool,
            "accessibility_ok": bool,
            "send_enabled": bool,
            "auto_reply_enabled": bool,
            "allowed_rooms": [room_id, ...],
            "config_path": str,
            "state_db_path": str,
        }
    """
    config = try_load_config(config_path)

    try:
        kakao_running = is_kakao_running()
    except Exception:
        kakao_running = False

    accessibility_ok = False
    if kakao_running:
        try:
            accessibility_ok = is_trusted()
        except Exception:
            accessibility_ok = False

    allowed_rooms: list[str] = []
    send_enabled = False
    auto_reply_enabled = False
    if config is not None:
        allowed_rooms = [room.room_id for room in config.rooms if room.enabled]
        send_enabled = config.send_enabled
        auto_reply_enabled = config.auto_reply_enabled

    ok = kakao_running and accessibility_ok and config is not None

    return {
        "ok": ok,
        "kakao_running": kakao_running,
        "accessibility_ok": accessibility_ok,
        "send_enabled": send_enabled,
        "auto_reply_enabled": auto_reply_enabled,
        "allowed_rooms": allowed_rooms,
        "config_path": str(Path(config_path).resolve()),
        "state_db_path": str(Path(DEFAULT_DB_PATH).resolve()),
    }
