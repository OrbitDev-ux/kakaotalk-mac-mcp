"""도구: kakao_allowed_rooms, kakao_read_room.

room_id는 config.json에서 사용자가 지정한 별칭이다. 실제 카카오톡
창 제목(room.display_hint)은 채팅창을 찾는 데에만 잠깐 쓰이고,
DB나 응답 어디에도 그대로 저장/반환되지 않는다.

발신자 이름(카톡에 표시되는 실제 닉네임)도 개인정보로 취급한다.
kakao_read_room의 응답 필드 이름이 "sender"가 아니라
"sender_alias"인 이유가 이것이다 — 실제 표시 이름 대신 room_id와
묶은 해시 기반의 안정적인 별칭을 반환한다. 같은 발신자는 같은 방
안에서 항상 같은 sender_alias를 받는다(결정론적).
"""

from __future__ import annotations

from kakao_mac_mcp.macos.chat_window import read_messages
from kakao_mac_mcp.macos.kakao_app import find_chat_window
from kakao_mac_mcp.safety import compute_fingerprint
from kakao_mac_mcp.state import audit, record_fingerprint
from kakao_mac_mcp.tools.common import (
    MAX_READ_LIMIT,
    DEFAULT_READ_LIMIT,
    find_room,
    get_conn,
    mask_sender_alias,
    try_load_config,
)


def kakao_allowed_rooms() -> list[str]:
    """설정에서 허용된(enabled=True) room_id 목록을 반환한다.

    Returns:
        room_id 문자열 리스트.
    """
    config = try_load_config()
    if config is None:
        return []
    return [room.room_id for room in config.rooms if room.enabled]


def kakao_read_room(room_id: str, limit: int = DEFAULT_READ_LIMIT) -> dict:
    """허용된 방의 최근 메시지를 읽는다.

    room_id가 허용 목록에 없으면 {"error": "..."}를 반환한다. 성공
    시에도 실제 카카오톡 방 제목은 절대 포함하지 않는다. 읽은 각
    메시지의 fingerprint는 DB에 기록된다(향후 baseline/중복 판정에
    사용하기 위함이며, 이 도구 자체는 중복을 걸러내지 않는다).

    Args:
        room_id: config.json에 정의된 방 별칭.
        limit: 반환할 최대 메시지 개수 (1~50, 기본 20).

    Returns:
        성공: {"room_id", "messages": [...], "count"}
        실패: {"error": "..."}
    """
    config = try_load_config()
    if config is None:
        return {"error": "설정을 불러올 수 없습니다. config.json을 확인하세요."}

    room = find_room(config, room_id)
    if room is None:
        return {"error": f"허용되지 않은 room_id입니다: {room_id}"}

    safe_limit = max(1, min(limit, MAX_READ_LIMIT))

    hint = room.display_hint or room.room_id
    try:
        window = find_chat_window(hint)
    except Exception:
        window = None

    if window is None:
        return {"error": "채팅창을 찾을 수 없습니다. 카카오톡에서 해당 방을 열어두세요."}

    try:
        raw_messages = read_messages(window, limit=safe_limit)
    except Exception:
        return {"error": "메시지를 읽는 중 오류가 발생했습니다."}

    conn = get_conn()
    messages: list[dict] = []

    for raw in raw_messages:
        sender_alias = mask_sender_alias(room_id, raw.get("sender"))
        text = raw.get("text")
        timestamp_hint = raw.get("timestamp_hint")

        fingerprint = compute_fingerprint(
            room_alias=room_id,
            sender_alias=sender_alias or "",
            text=text or "",
            ts=timestamp_hint or "",
        )

        try:
            record_fingerprint(conn, room_id, fingerprint)
        except Exception:
            pass

        messages.append(
            {
                "fingerprint": fingerprint,
                "sender_alias": sender_alias,
                "text": text,
                "timestamp_hint": timestamp_hint,
            }
        )

    detail = f"read {len(messages)} messages"
    if config.privacy.log_message_content and messages:
        last_text = messages[-1]["text"] or ""
        detail += f", last_snippet={last_text[:20]!r}"

    try:
        audit(conn, "read", room_id, detail=detail)
    except Exception:
        pass

    return {
        "room_id": room_id,
        "messages": messages,
        "count": len(messages),
    }
