"""도구: kakao_observe_room, kakao_poll_events.

새 메시지 감지(관찰)와 이벤트 큐 폴링을 담당한다. baseline 이전
메시지는 "과거"로 간주해 이벤트를 만들지 않는다(재생 금지) — 방마다
최초 관찰 시점의 메시지들은 baseline으로만 기록되고, 그 이후 호출
부터는 이전에 본 적 없는 fingerprint를 가진 메시지만 이벤트가 되어
큐에 쌓인다.

TODO: 단체 채팅방에서 발신자 이름이 메시지와 별도의 헤더 행으로
렌더링되는 경우, 그 헤더 행이 메시지 후보에 섞여 preview에 사람
이름이 노출될 수 있음(chat_window.read_messages의 알려진 한계).
"""

from __future__ import annotations

import json

from kakao_mac_mcp.macos.chat_window import read_messages
from kakao_mac_mcp.macos.kakao_app import find_chat_window
from kakao_mac_mcp.safety import compute_fingerprint
from kakao_mac_mcp.state import (
    add_event,
    get_baseline,
    is_duplicate,
    poll_events as _drain_events,
    record_fingerprint,
    set_baseline,
)
from kakao_mac_mcp.tools.common import (
    MAX_READ_LIMIT,
    find_room,
    get_conn,
    mask_sender_alias,
    try_load_config,
)

PREVIEW_LENGTH = 30
DEFAULT_POLL_LIMIT = 50
MAX_POLL_LIMIT = 200


def _message_fingerprint(room_id: str, message: dict) -> str:
    """chat_window.read_messages()가 반환한 메시지 dict의 fingerprint를 계산한다."""
    sender_alias = mask_sender_alias(room_id, message.get("sender"))
    return compute_fingerprint(
        room_alias=room_id,
        sender_alias=sender_alias or "",
        text=message.get("text") or "",
        ts=message.get("timestamp_hint") or "",
    )


def kakao_observe_room(room_id: str) -> dict:
    """방의 새 메시지를 감지한다.

    최초 호출: 현재 보이는 메시지들을 baseline으로만 기록하고
    new_events=[]를 반환한다 — 과거 메시지를 이벤트로 재생하지
    않는다. 이후 호출: 이전에 기록된 적 없는 fingerprint를 가진
    메시지만 이벤트 큐에 추가하고 new_events로 함께 반환한다.

    Args:
        room_id: config.json에 정의된 방 별칭.

    Returns:
        성공: {"baseline_created": bool, "new_events": [...]}
        실패: {"error": "..."}
    """
    config = try_load_config()
    if config is None:
        return {"error": "설정을 불러올 수 없습니다. config.json을 확인하세요."}

    room = find_room(config, room_id)
    if room is None:
        return {"error": f"허용되지 않은 room_id입니다: {room_id}"}

    hint = room.display_hint or room.room_id
    try:
        window = find_chat_window(hint)
    except Exception:
        window = None
    if window is None:
        return {"error": "채팅창을 찾을 수 없습니다. 카카오톡에서 해당 방을 열어두세요."}

    read_limit = max(1, min(config.limits.max_messages_per_read, MAX_READ_LIMIT))
    try:
        messages = read_messages(window, limit=read_limit)
    except Exception:
        return {"error": "메시지를 읽는 중 오류가 발생했습니다."}

    conn = get_conn()
    baseline_created = get_baseline(conn, room_id) is None

    new_events: list[dict] = []
    # 방이 비어 있어도(메시지 0개) baseline은 반드시 세워야 한다 —
    # 그렇지 않으면 다음 호출도 계속 "최초 관찰"로 취급되어 baseline이
    # 영영 확정되지 않는다. 빈 상태는 빈 fingerprint("")로 표시한다.
    latest_fingerprint: str = ""

    for message in messages:
        fingerprint = _message_fingerprint(room_id, message)
        latest_fingerprint = fingerprint

        if baseline_created:
            # 최초 관찰 - 현재 보이는 메시지는 전부 "과거"로 취급해
            # baseline으로만 기록하고 이벤트를 만들지 않는다.
            record_fingerprint(conn, room_id, fingerprint)
            continue

        if is_duplicate(conn, room_id, fingerprint):
            continue

        record_fingerprint(conn, room_id, fingerprint)
        preview = (message.get("text") or "")[:PREVIEW_LENGTH]
        event_id = add_event(conn, room_id, fingerprint, "new_message", {"preview": preview})
        new_events.append(
            {
                "event_id": event_id,
                "room_id": room_id,
                "fingerprint": fingerprint,
                "preview": preview,
            }
        )

    set_baseline(conn, room_id, latest_fingerprint)

    return {"baseline_created": baseline_created, "new_events": new_events}


def kakao_poll_events(limit: int = DEFAULT_POLL_LIMIT) -> list[dict]:
    """이벤트 큐에서 미처리 이벤트를 꺼낸다.

    소비형(drain) 동작이다 — 반환된 이벤트는 큐에서 즉시 삭제되어
    다시 poll할 수 없다.

    Args:
        limit: 가져올 최대 이벤트 개수 (기본 50, 최대 200).

    Returns:
        {"event_id", "room_id", "fingerprint", "preview", "event_type"} 리스트.
    """
    conn = get_conn()
    safe_limit = max(1, min(limit, MAX_POLL_LIMIT))
    rows = _drain_events(conn, limit=safe_limit)

    events: list[dict] = []
    for row in rows:
        preview = None
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                preview = parsed.get("preview")

        events.append(
            {
                "event_id": row["id"],
                "room_id": row["room_id"],
                "fingerprint": row["fingerprint"],
                "preview": preview,
                "event_type": row["event_type"],
            }
        )

    return events
