"""fail-closed 안전 검증 계층.

카카오톡으로의 메시지 전송은 여러 겹의 안전장치를 통과해야 한다:

1. 설정에서 send_enabled가 명시적으로 true일 것 (기본 false)
2. prepare(티켓 생성) -> 사람의 승인 -> commit 2단계 분리
3. 최근 1분간 전송 횟수가 rate limit 이내일 것
4. fingerprint 기반 중복 방지 (실제 저장/조회는 state.py 담당)

이 모듈의 모든 검증 함수는 "확실하지 않으면 차단(fail-closed)"
원칙을 따른다 — 예외적인 상황이나 애매한 판단은 항상 차단 쪽으로
기운다.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from kakao_mac_mcp.config import AppConfig, LimitsConfig
from kakao_mac_mcp.state import TICKET_STATUS_PREPARED, audit, get_ticket


class SafetyError(Exception):
    """안전 검증 실패를 나타내는 예외."""


MAX_REPLY_TEXT_LENGTH = 1000

# 창 활성화(raise)는 화면을 방해하는 부수 효과가 있으므로 초당 1회로
# 제한한다. 이 한도를 넘는 호출은 차단하지는 않되(활성화 자체는
# UI 조작일 뿐 전송이 아니므로) 감사 로그에 경고로 남긴다.
ACTIVATION_RATE_LIMIT_SECONDS = 1.0

# 제어 문자(탭/개행 제외)를 금지 패턴으로 취급한다.
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def can_send(config: AppConfig, is_auto_reply: bool = False) -> bool:
    """설정상 전송이 허용되는지 확인한다 (fail-closed).

    - send_enabled가 False이면 어떤 경우에도 차단한다.
    - is_auto_reply=True(자동 답장 경로)로 호출된 경우에는
      auto_reply_enabled도 True여야 허용한다. 사람이 승인하는
      prepare/commit 경로는 is_auto_reply를 지정하지 않으면
      send_enabled 여부만으로 판단한다.

    Args:
        config: 애플리케이션 설정.
        is_auto_reply: 자동 답장 경로에서 호출하는 경우 True.

    Returns:
        전송 허용 여부.
    """
    if not config.send_enabled:
        return False
    if is_auto_reply and not config.auto_reply_enabled:
        return False
    return True


def validate_reply_text(text: str | None, limits: LimitsConfig) -> tuple[bool, str]:
    """전송할 텍스트가 유효한지 검사한다.

    Args:
        text: 검사할 텍스트.
        limits: 길이 등 한도 설정 (향후 확장 대비로 전달받음).

    Returns:
        (유효 여부, 사유 문자열). 유효하면 (True, "").
    """
    if text is None or text.strip() == "":
        return False, "빈 문자열은 전송할 수 없습니다."

    if len(text) > MAX_REPLY_TEXT_LENGTH:
        return (
            False,
            f"텍스트가 너무 깁니다 (최대 {MAX_REPLY_TEXT_LENGTH}자, 현재 {len(text)}자).",
        )

    if _CONTROL_CHAR_PATTERN.search(text):
        return False, "허용되지 않는 제어 문자가 포함되어 있습니다."

    return True, ""


def compute_fingerprint(room_alias: str, sender_alias: str, text: str, ts: str) -> str:
    """room_alias + sender_alias + text + timestamp 기반 SHA-256 fingerprint.

    동일한 입력에 대해 항상 동일한 값을 반환한다(결정론적). 필드
    사이에 구분자를 넣어 값 경계가 섞이는 것을 방지한다.

    Args:
        room_alias: 방 별칭 (실제 방 제목 아님).
        sender_alias: 발신자 별칭.
        text: 메시지 텍스트.
        ts: 타임스탬프 문자열.

    Returns:
        64자 hex SHA-256 다이제스트.
    """
    payload = "\x1f".join(
        [room_alias or "", sender_alias or "", text or "", ts or ""]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def check_rate_limit(conn: sqlite3.Connection, room_alias: str, limits: LimitsConfig) -> bool:
    """최근 1분간 room_alias로의 전송 횟수가 한도 이내인지 확인한다.

    audit_log에서 action='send'인 행을 기준으로 센다. 이 함수는
    카운트만 확인할 뿐 자체적으로 로그를 남기지 않는다 — 실제 전송이
    성공한 뒤 audit()으로 기록하는 것은 호출하는 쪽의 책임이다.

    Args:
        conn: DB 연결.
        room_alias: 방 별칭.
        limits: max_send_per_minute을 포함한 한도 설정.

    Returns:
        한도 이내이면 True(전송 허용 가능), 초과하면 False(차단).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    cursor = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM audit_log
        WHERE action = 'send' AND room_alias = ? AND created_at >= ?
        """,
        (room_alias, cutoff),
    )
    row = cursor.fetchone()
    count = row["cnt"] if row is not None else 0
    return count < limits.max_send_per_minute


def require_approval(conn: sqlite3.Connection, ticket_id: str) -> bool:
    """티켓이 승인 대기(prepared) 상태이고 유효기간 내인지 확인한다.

    commit 단계 진입 전 반드시 호출해야 하는 최종 게이트다. 티켓이
    없거나, 상태가 prepared가 아니거나, 만료되었으면 False.

    Args:
        conn: DB 연결.
        ticket_id: 확인할 티켓 id.

    Returns:
        commit을 진행해도 되는지 여부.
    """
    ticket = get_ticket(conn, ticket_id)
    if ticket is None:
        return False
    if ticket["status"] != TICKET_STATUS_PREPARED:
        return False

    try:
        expires_at = datetime.fromisoformat(ticket["expires_at"])
    except (TypeError, ValueError):
        return False

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return datetime.now(timezone.utc) <= expires_at


def check_activation_rate_limit(conn: sqlite3.Connection) -> bool:
    """최근 1초 이내에 창 활성화(activate_window) 기록이 있는지 확인한다.

    창 활성화는 사용자 화면을 방해하는 부수 효과가 있으므로 초당
    1회로 제한한다. 이 함수는 카운트만 확인하며 자체적으로 로그를
    남기지 않는다 — 기록은 record_activation()의 책임이다.

    Returns:
        한도 이내이면 True(활성화해도 좋음), 이미 최근 1초 내 기록이
        있으면 False(경고 대상).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ACTIVATION_RATE_LIMIT_SECONDS)).isoformat()
    cursor = conn.execute(
        "SELECT COUNT(*) AS cnt FROM audit_log WHERE action = 'activate_window' AND created_at >= ?",
        (cutoff,),
    )
    row = cursor.fetchone()
    count = row["cnt"] if row is not None else 0
    return count < 1


def record_activation(conn: sqlite3.Connection, room_alias: str | None, detail: str = "") -> bool:
    """창 활성화를 감사 로그에 기록한다.

    초당 1회 제한을 초과한 경우에도 활성화 자체를 막지는 않지만
    detail에 경고 문구를 남긴다 — 호출하는 쪽(도구 계층)이 너무
    잦은 활성화를 감지해 상위 로직을 재검토할 수 있게 하기 위함이다.

    Returns:
        rate limit 이내였는지 여부.
    """
    within_limit = check_activation_rate_limit(conn)
    note = detail if within_limit else f"{detail} [경고: 초당 1회 초과]".strip()
    audit(conn, "activate_window", room_alias, detail=note)
    return within_limit
