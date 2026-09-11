"""도구: kakao_prepare_reply, kakao_commit_reply, kakao_operation_status.

전송은 반드시 prepare(승인 대기 티켓 생성) -> commit(실제 전송) 2단계를
거친다 — 카톡bot 프로젝트 전반의 절대 규칙(승인 없는 자동 전송 금지)을
구현하는 지점이다.

티켓의 fingerprint 필드는 "이 전송 시도 자체"의 fingerprint가 아니라
prepare 시점에 방에 있던 가장 최근 메시지의 fingerprint다. commit
시점에 방의 최신 fingerprint를 다시 계산해 이 값과 비교함으로써,
prepare와 commit 사이에 방에 새 메시지가 도착했는지(즉 맥락이
바뀌었는지) 감지하고, 바뀌었다면 전송을 거부한다. 자동 재시도는
하지 않는다 — commit이 실패하면 호출자가 명시적으로 다시
prepare/commit해야 한다.
"""

from __future__ import annotations

from kakao_mac_mcp.macos.chat_window import read_messages, send_current_input, set_input_text
from kakao_mac_mcp.macos.kakao_app import find_chat_window
from kakao_mac_mcp.safety import (
    can_send,
    check_rate_limit,
    compute_fingerprint,
    require_approval,
    validate_reply_text,
)
from kakao_mac_mcp.state import (
    TICKET_STATUS_COMMITTED,
    TICKET_STATUS_EXPIRED,
    TICKET_STATUS_FAILED,
    TICKET_STATUS_PREPARED,
    audit,
    create_ticket,
    get_ticket,
    record_fingerprint,
    update_ticket_status,
)
from kakao_mac_mcp.tools.common import find_room, get_conn, mask_sender_alias, try_load_config

TICKET_TTL_SECONDS = 300
PREVIEW_LENGTH = 30


def _current_room_fingerprint(room_id: str, window) -> str:
    """방의 가장 최근 메시지를 기준으로 fingerprint를 계산한다.

    메시지가 없거나 읽기에 실패한 방은 "빈 상태"를 나타내는 고정
    fingerprint를 사용한다(빈 문자열 필드로 계산).
    """
    try:
        messages = read_messages(window, limit=1)
    except Exception:
        messages = []

    if not messages:
        return compute_fingerprint(room_id, "", "", "")

    latest = messages[-1]
    sender_alias = mask_sender_alias(room_id, latest.get("sender"))
    return compute_fingerprint(
        room_alias=room_id,
        sender_alias=sender_alias or "",
        text=latest.get("text") or "",
        ts=latest.get("timestamp_hint") or "",
    )


def _resolve_window(config, room_id: str):
    """room_id에 해당하는 채팅창을 찾는다. 실패하면 (None, error_dict)를 반환."""
    room = find_room(config, room_id)
    if room is None:
        return None, {"error": f"허용되지 않은 room_id입니다: {room_id}"}

    hint = room.display_hint or room.room_id
    try:
        window = find_chat_window(hint)
    except Exception:
        window = None

    if window is None:
        return None, {"error": "채팅창을 찾을 수 없습니다. 카카오톡에서 해당 방을 열어두세요."}

    return window, None


def kakao_prepare_reply(room_id: str, text: str) -> dict:
    """답장을 준비한다 (승인 대기 티켓 생성). 실제로 전송하지 않는다.

    검증 순서: 허용 목록 -> send_enabled -> 텍스트 유효성 -> rate
    limit -> 채팅창 존재 여부. 하나라도 실패하면 즉시 {"error": ...}.

    Args:
        room_id: config.json에 정의된 방 별칭.
        text: 전송할 텍스트.

    Returns:
        성공: {"ticket_id", "fingerprint", "expires_in_sec", "preview"}
        실패: {"error": "..."}
    """
    config = try_load_config()
    if config is None:
        return {"error": "설정을 불러올 수 없습니다. config.json을 확인하세요."}

    room = find_room(config, room_id)
    if room is None:
        return {"error": f"허용되지 않은 room_id입니다: {room_id}"}

    if not can_send(config):
        return {"error": "전송이 비활성화되어 있습니다 (send_enabled=false)."}

    is_valid, reason = validate_reply_text(text, config.limits)
    if not is_valid:
        return {"error": reason}

    conn = get_conn()
    if not check_rate_limit(conn, room_id, config.limits):
        return {"error": "최근 1분간 전송 한도를 초과했습니다."}

    window, error = _resolve_window(config, room_id)
    if error is not None:
        return error

    latest_fingerprint = _current_room_fingerprint(room_id, window)
    ticket_id = create_ticket(conn, room_id, text, latest_fingerprint, ttl_seconds=TICKET_TTL_SECONDS)
    audit(conn, "prepare", room_id, detail=f"ticket={ticket_id}")

    return {
        "ticket_id": ticket_id,
        "fingerprint": latest_fingerprint,
        "expires_in_sec": TICKET_TTL_SECONDS,
        "preview": text[:PREVIEW_LENGTH],
    }


def kakao_commit_reply(ticket_id: str) -> dict:
    """준비된 답장을 실제로 전송한다.

    commit 시점에 방의 최신 fingerprint가 prepare 당시와 다르면
    (그 사이 새 메시지가 도착했으면) 전송을 거부한다. 실패해도
    자동으로 재시도하지 않는다.

    Args:
        ticket_id: kakao_prepare_reply가 발급한 티켓 id.

    Returns:
        {"sent": bool, "readback": {...} | None, "error": str | None}
    """
    conn = get_conn()

    if not require_approval(conn, ticket_id):
        ticket = get_ticket(conn, ticket_id)
        if ticket is not None and ticket["status"] == TICKET_STATUS_PREPARED:
            update_ticket_status(conn, ticket_id, TICKET_STATUS_EXPIRED)
        return {
            "sent": False,
            "readback": None,
            "error": "티켓이 없거나 승인 대기 상태가 아니거나 만료되었습니다.",
        }

    ticket = get_ticket(conn, ticket_id)
    room_id = ticket["room_id"]

    config = try_load_config()
    if config is None:
        update_ticket_status(conn, ticket_id, TICKET_STATUS_FAILED)
        return {"sent": False, "readback": None, "error": "설정을 불러올 수 없습니다."}

    if not can_send(config):
        update_ticket_status(conn, ticket_id, TICKET_STATUS_FAILED)
        return {"sent": False, "readback": None, "error": "전송이 비활성화되어 있습니다 (send_enabled=false)."}

    window, error = _resolve_window(config, room_id)
    if error is not None:
        update_ticket_status(conn, ticket_id, TICKET_STATUS_FAILED)
        return {"sent": False, "readback": None, "error": error["error"]}

    current_fingerprint = _current_room_fingerprint(room_id, window)
    if current_fingerprint != ticket["fingerprint"]:
        update_ticket_status(conn, ticket_id, TICKET_STATUS_FAILED)
        audit(conn, "commit_rejected", room_id, detail="room changed since prepare")
        return {
            "sent": False,
            "readback": None,
            "error": "준비 이후 방에 새 메시지가 도착해 전송을 거부했습니다. 다시 prepare하세요.",
        }

    text = ticket["text"]

    if not set_input_text(window, text, raise_window=config.raise_window_on_input):
        update_ticket_status(conn, ticket_id, TICKET_STATUS_FAILED)
        return {"sent": False, "readback": None, "error": "입력창에 텍스트를 설정하지 못했습니다."}

    if not send_current_input(window, raise_window=config.raise_window_on_send):
        update_ticket_status(conn, ticket_id, TICKET_STATUS_FAILED)
        return {"sent": False, "readback": None, "error": "전송에 실패했습니다."}

    try:
        readback_messages = read_messages(window, limit=1)
    except Exception:
        readback_messages = []
    readback = readback_messages[-1] if readback_messages else None

    update_ticket_status(conn, ticket_id, TICKET_STATUS_COMMITTED)
    # 방금 보낸 메시지의 fingerprint를 기록해, 이후 kakao_observe_room이
    # 우리 자신의 발화를 "새 메시지 이벤트"로 잘못 인식하지 않게 한다.
    sent_fingerprint = _current_room_fingerprint(room_id, window)
    record_fingerprint(conn, room_id, sent_fingerprint)

    audit(conn, "send", room_id, detail=f"ticket={ticket_id}")

    return {"sent": True, "readback": readback, "error": None}


def kakao_operation_status(ticket_id: str) -> dict:
    """티켓 상태를 조회한다.

    Args:
        ticket_id: 조회할 티켓 id.

    Returns:
        성공: {"status", "created_at", "expires_at"}
        실패: {"error": "..."}
    """
    conn = get_conn()
    ticket = get_ticket(conn, ticket_id)
    if ticket is None:
        return {"error": f"존재하지 않는 ticket_id입니다: {ticket_id}"}

    return {
        "status": ticket["status"],
        "created_at": ticket["created_at"],
        "expires_at": ticket["expires_at"],
    }
