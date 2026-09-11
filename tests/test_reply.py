"""reply.py 단위 테스트.

카카오톡 창을 직접 열지 않고, chat_window/kakao_app 함수를
monkeypatch해서 prepare -> commit 2단계 승인 흐름과 안전 검증
로직만 검증한다. 실제 AX 호출은 전혀 일어나지 않는다.
"""

from __future__ import annotations

import pytest

from kakao_mac_mcp import state
from kakao_mac_mcp.config import AppConfig, RoomConfig
from kakao_mac_mcp.tools import reply

ROOM_ID = "test-room"
_SENTINEL_WINDOW = object()


@pytest.fixture
def conn(tmp_path):
    connection = state.init_db(tmp_path / "test_reply.db")
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _shared_conn(conn, monkeypatch):
    monkeypatch.setattr(reply, "get_conn", lambda: conn)
    return conn


@pytest.fixture
def enabled_config():
    return AppConfig(
        send_enabled=True,
        rooms=[RoomConfig(room_id=ROOM_ID, display_hint="힌트")],
    )


@pytest.fixture
def disabled_config():
    return AppConfig(
        send_enabled=False,
        rooms=[RoomConfig(room_id=ROOM_ID, display_hint="힌트")],
    )


def _mock_room(monkeypatch, config, messages=None, *, window=_SENTINEL_WINDOW):
    monkeypatch.setattr(reply, "try_load_config", lambda: config)
    monkeypatch.setattr(reply, "find_chat_window", lambda hint: window)
    monkeypatch.setattr(reply, "read_messages", lambda w, limit: messages or [])


# ---------------------------------------------------------------------------
# kakao_prepare_reply
# ---------------------------------------------------------------------------


def test_prepare_rejects_disallowed_room(monkeypatch, enabled_config) -> None:
    _mock_room(monkeypatch, enabled_config)
    result = reply.kakao_prepare_reply("not-allowed", "hi")
    assert "error" in result


def test_prepare_rejects_missing_config(monkeypatch) -> None:
    monkeypatch.setattr(reply, "try_load_config", lambda: None)
    result = reply.kakao_prepare_reply(ROOM_ID, "hi")
    assert "error" in result


def test_prepare_rejects_when_send_disabled(monkeypatch, disabled_config) -> None:
    _mock_room(monkeypatch, disabled_config)
    result = reply.kakao_prepare_reply(ROOM_ID, "hi")
    assert "error" in result
    assert "send_enabled" in result["error"]


def test_prepare_rejects_empty_text(monkeypatch, enabled_config) -> None:
    _mock_room(monkeypatch, enabled_config)
    result = reply.kakao_prepare_reply(ROOM_ID, "")
    assert "error" in result


def test_prepare_rejects_when_rate_limited(monkeypatch, enabled_config, conn) -> None:
    _mock_room(monkeypatch, enabled_config)
    for _ in range(enabled_config.limits.max_send_per_minute):
        state.audit(conn, "send", ROOM_ID)

    result = reply.kakao_prepare_reply(ROOM_ID, "hi")
    assert "error" in result


def test_prepare_rejects_when_window_missing(monkeypatch, enabled_config) -> None:
    _mock_room(monkeypatch, enabled_config, window=None)
    result = reply.kakao_prepare_reply(ROOM_ID, "hi")
    assert "error" in result


def test_prepare_succeeds_and_creates_prepared_ticket(monkeypatch, enabled_config, conn) -> None:
    _mock_room(
        monkeypatch,
        enabled_config,
        messages=[{"sender": "a", "text": "existing", "timestamp_hint": "오후 1:00"}],
    )

    result = reply.kakao_prepare_reply(ROOM_ID, "안녕하세요")

    assert "error" not in result
    assert result["expires_in_sec"] == reply.TICKET_TTL_SECONDS
    assert result["preview"] == "안녕하세요"
    assert isinstance(result["ticket_id"], str) and result["ticket_id"]

    ticket = state.get_ticket(conn, result["ticket_id"])
    assert ticket is not None
    assert ticket["status"] == state.TICKET_STATUS_PREPARED
    assert ticket["text"] == "안녕하세요"
    assert ticket["fingerprint"] == result["fingerprint"]


# ---------------------------------------------------------------------------
# kakao_commit_reply
# ---------------------------------------------------------------------------


def test_commit_rejects_nonexistent_ticket() -> None:
    result = reply.kakao_commit_reply("no-such-ticket")
    assert result["sent"] is False
    assert result["error"]


def test_commit_rejects_expired_ticket(monkeypatch, enabled_config, conn) -> None:
    _mock_room(monkeypatch, enabled_config, messages=[])
    ticket_id = state.create_ticket(conn, ROOM_ID, "hi", "fp-old", ttl_seconds=-1)

    result = reply.kakao_commit_reply(ticket_id)

    assert result["sent"] is False
    assert result["error"]
    ticket = state.get_ticket(conn, ticket_id)
    assert ticket["status"] == state.TICKET_STATUS_EXPIRED


def test_commit_rejects_when_room_changed_since_prepare(monkeypatch, enabled_config, conn) -> None:
    _mock_room(
        monkeypatch,
        enabled_config,
        messages=[{"sender": "a", "text": "before", "timestamp_hint": "오후 1:00"}],
    )
    prepared = reply.kakao_prepare_reply(ROOM_ID, "안녕하세요")
    ticket_id = prepared["ticket_id"]

    # 준비 이후 방에 새 메시지가 도착한 상황을 시뮬레이션.
    _mock_room(
        monkeypatch,
        enabled_config,
        messages=[{"sender": "b", "text": "new incoming message", "timestamp_hint": "오후 1:05"}],
    )

    result = reply.kakao_commit_reply(ticket_id)

    assert result["sent"] is False
    assert "새 메시지" in result["error"]
    ticket = state.get_ticket(conn, ticket_id)
    assert ticket["status"] == state.TICKET_STATUS_FAILED


def test_commit_rejects_when_send_disabled_at_commit_time(monkeypatch, enabled_config, disabled_config, conn) -> None:
    _mock_room(monkeypatch, enabled_config, messages=[])
    prepared = reply.kakao_prepare_reply(ROOM_ID, "hi")
    ticket_id = prepared["ticket_id"]

    # commit 시점에는 send_enabled가 꺼져 있는 상황.
    _mock_room(monkeypatch, disabled_config, messages=[])
    result = reply.kakao_commit_reply(ticket_id)

    assert result["sent"] is False
    assert "send_enabled" in result["error"]


def test_commit_fails_when_input_cannot_be_set(monkeypatch, enabled_config, conn) -> None:
    _mock_room(monkeypatch, enabled_config, messages=[])
    prepared = reply.kakao_prepare_reply(ROOM_ID, "hi")
    ticket_id = prepared["ticket_id"]

    monkeypatch.setattr(reply, "set_input_text", lambda w, text, raise_window: False)

    result = reply.kakao_commit_reply(ticket_id)

    assert result["sent"] is False
    ticket = state.get_ticket(conn, ticket_id)
    assert ticket["status"] == state.TICKET_STATUS_FAILED


def test_commit_fails_when_send_action_fails(monkeypatch, enabled_config, conn) -> None:
    _mock_room(monkeypatch, enabled_config, messages=[])
    prepared = reply.kakao_prepare_reply(ROOM_ID, "hi")
    ticket_id = prepared["ticket_id"]

    monkeypatch.setattr(reply, "set_input_text", lambda w, text, raise_window: True)
    monkeypatch.setattr(reply, "send_current_input", lambda w, raise_window: False)

    result = reply.kakao_commit_reply(ticket_id)

    assert result["sent"] is False
    ticket = state.get_ticket(conn, ticket_id)
    assert ticket["status"] == state.TICKET_STATUS_FAILED


def test_commit_succeeds_and_returns_readback(monkeypatch, enabled_config, conn) -> None:
    _mock_room(monkeypatch, enabled_config, messages=[])
    prepared = reply.kakao_prepare_reply(ROOM_ID, "hi")
    ticket_id = prepared["ticket_id"]

    monkeypatch.setattr(reply, "set_input_text", lambda w, text, raise_window: True)
    monkeypatch.setattr(reply, "send_current_input", lambda w, raise_window: True)
    # commit은 다시 _current_room_fingerprint 계산을 위해 read_messages를
    # 호출하므로, 여전히 "변경 없음" 상태(빈 방)를 유지한 채 readback도
    # 이 값으로 채워지도록 한다.
    monkeypatch.setattr(reply, "read_messages", lambda w, limit: [])

    result = reply.kakao_commit_reply(ticket_id)

    assert result["sent"] is True
    assert result["error"] is None
    ticket = state.get_ticket(conn, ticket_id)
    assert ticket["status"] == state.TICKET_STATUS_COMMITTED


def test_commit_no_automatic_retry_on_repeated_call(monkeypatch, enabled_config, conn) -> None:
    """commit이 실패한 뒤 같은 ticket_id로 다시 불러도 자동 재시도되지 않는다."""
    _mock_room(monkeypatch, enabled_config, messages=[])
    prepared = reply.kakao_prepare_reply(ROOM_ID, "hi")
    ticket_id = prepared["ticket_id"]

    monkeypatch.setattr(reply, "set_input_text", lambda w, text, raise_window: False)
    first = reply.kakao_commit_reply(ticket_id)
    second = reply.kakao_commit_reply(ticket_id)

    assert first["sent"] is False
    assert second["sent"] is False
    assert "승인 대기" in second["error"]


# ---------------------------------------------------------------------------
# kakao_operation_status
# ---------------------------------------------------------------------------


def test_operation_status_for_missing_ticket() -> None:
    result = reply.kakao_operation_status("no-such-ticket")
    assert "error" in result


def test_operation_status_reflects_ticket_state(monkeypatch, enabled_config) -> None:
    _mock_room(monkeypatch, enabled_config, messages=[])
    prepared = reply.kakao_prepare_reply(ROOM_ID, "hi")
    ticket_id = prepared["ticket_id"]

    status = reply.kakao_operation_status(ticket_id)

    assert status["status"] == state.TICKET_STATUS_PREPARED
    assert status["created_at"]
    assert status["expires_at"]
