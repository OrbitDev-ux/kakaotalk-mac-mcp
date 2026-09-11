"""observe.py 단위 테스트.

카카오톡 창을 직접 열지 않고, chat_window.read_messages와
kakao_app.find_chat_window를 monkeypatch해서 순수 로직(baseline
생성, 신규 이벤트 판정, 큐 폴링)만 검증한다.
"""

from __future__ import annotations

import pytest

from kakao_mac_mcp import state
from kakao_mac_mcp.config import AppConfig, RoomConfig
from kakao_mac_mcp.tools import observe


ROOM_ID = "test-room"
_SENTINEL_WINDOW = object()


@pytest.fixture
def conn(tmp_path):
    connection = state.init_db(tmp_path / "test_observe.db")
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _shared_conn(conn, monkeypatch):
    """observe.py의 모듈 전역 커넥션을 테스트용 임시 DB로 고정한다."""
    monkeypatch.setattr(observe, "get_conn", lambda: conn)
    return conn


@pytest.fixture
def config_with_room():
    return AppConfig(rooms=[RoomConfig(room_id=ROOM_ID, display_hint="힌트")])


def _patch_messages(monkeypatch, messages: list[dict]) -> None:
    monkeypatch.setattr(observe, "find_chat_window", lambda hint: _SENTINEL_WINDOW)
    monkeypatch.setattr(observe, "read_messages", lambda window, limit: messages)


def test_observe_room_rejects_disallowed_room(monkeypatch, config_with_room) -> None:
    monkeypatch.setattr(observe, "try_load_config", lambda: config_with_room)
    result = observe.kakao_observe_room("not-allowed")
    assert "error" in result


def test_observe_room_missing_config_returns_error(monkeypatch) -> None:
    monkeypatch.setattr(observe, "try_load_config", lambda: None)
    result = observe.kakao_observe_room(ROOM_ID)
    assert "error" in result


def test_observe_room_missing_window_returns_error(monkeypatch, config_with_room) -> None:
    monkeypatch.setattr(observe, "try_load_config", lambda: config_with_room)
    monkeypatch.setattr(observe, "find_chat_window", lambda hint: None)
    result = observe.kakao_observe_room(ROOM_ID)
    assert "error" in result


def test_first_call_creates_baseline_without_events(monkeypatch, config_with_room) -> None:
    monkeypatch.setattr(observe, "try_load_config", lambda: config_with_room)
    _patch_messages(
        monkeypatch,
        [
            {"sender": "a", "text": "hello", "timestamp_hint": "오후 1:00"},
            {"sender": "b", "text": "world", "timestamp_hint": "오후 1:01"},
        ],
    )

    result = observe.kakao_observe_room(ROOM_ID)

    assert result == {"baseline_created": True, "new_events": []}


def test_first_call_records_baseline_fingerprint(monkeypatch, config_with_room, conn) -> None:
    monkeypatch.setattr(observe, "try_load_config", lambda: config_with_room)
    _patch_messages(monkeypatch, [{"sender": "a", "text": "hello", "timestamp_hint": "오후 1:00"}])

    observe.kakao_observe_room(ROOM_ID)

    assert state.get_baseline(conn, ROOM_ID) is not None


def test_second_call_with_no_new_messages_returns_no_events(monkeypatch, config_with_room) -> None:
    monkeypatch.setattr(observe, "try_load_config", lambda: config_with_room)
    messages = [{"sender": "a", "text": "hello", "timestamp_hint": "오후 1:00"}]
    _patch_messages(monkeypatch, messages)

    observe.kakao_observe_room(ROOM_ID)  # baseline 생성
    result = observe.kakao_observe_room(ROOM_ID)  # 동일 메시지 - 신규 없음

    assert result["baseline_created"] is False
    assert result["new_events"] == []


def test_new_message_after_baseline_produces_event(monkeypatch, config_with_room) -> None:
    monkeypatch.setattr(observe, "try_load_config", lambda: config_with_room)
    initial = [{"sender": "a", "text": "hello", "timestamp_hint": "오후 1:00"}]
    _patch_messages(monkeypatch, initial)
    observe.kakao_observe_room(ROOM_ID)  # baseline 생성

    updated = initial + [{"sender": "b", "text": "new message here", "timestamp_hint": "오후 1:05"}]
    _patch_messages(monkeypatch, updated)
    result = observe.kakao_observe_room(ROOM_ID)

    assert result["baseline_created"] is False
    assert len(result["new_events"]) == 1
    event = result["new_events"][0]
    assert event["room_id"] == ROOM_ID
    assert event["preview"] == "new message here"
    assert "fingerprint" in event and "event_id" in event


def test_preview_truncated_to_30_chars(monkeypatch, config_with_room) -> None:
    monkeypatch.setattr(observe, "try_load_config", lambda: config_with_room)
    _patch_messages(monkeypatch, [])
    observe.kakao_observe_room(ROOM_ID)  # baseline 생성 (빈 방)

    long_text = "가" * 50
    _patch_messages(monkeypatch, [{"sender": "a", "text": long_text, "timestamp_hint": "오후 2:00"}])
    result = observe.kakao_observe_room(ROOM_ID)

    assert len(result["new_events"]) == 1
    assert result["new_events"][0]["preview"] == long_text[:30]
    assert len(result["new_events"][0]["preview"]) == 30


def test_events_are_queryable_via_poll(monkeypatch, config_with_room) -> None:
    monkeypatch.setattr(observe, "try_load_config", lambda: config_with_room)
    _patch_messages(monkeypatch, [])
    observe.kakao_observe_room(ROOM_ID)

    _patch_messages(monkeypatch, [{"sender": "a", "text": "queued event", "timestamp_hint": None}])
    observe.kakao_observe_room(ROOM_ID)

    polled = observe.kakao_poll_events()
    assert len(polled) == 1
    assert polled[0]["room_id"] == ROOM_ID
    assert polled[0]["preview"] == "queued event"
    assert polled[0]["event_type"] == "new_message"


def test_poll_events_drains_queue(monkeypatch, config_with_room) -> None:
    monkeypatch.setattr(observe, "try_load_config", lambda: config_with_room)
    _patch_messages(monkeypatch, [])
    observe.kakao_observe_room(ROOM_ID)
    _patch_messages(monkeypatch, [{"sender": "a", "text": "one-shot", "timestamp_hint": None}])
    observe.kakao_observe_room(ROOM_ID)

    first = observe.kakao_poll_events()
    second = observe.kakao_poll_events()

    assert len(first) == 1
    assert second == []


def test_poll_events_respects_limit(monkeypatch, config_with_room) -> None:
    monkeypatch.setattr(observe, "try_load_config", lambda: config_with_room)
    _patch_messages(monkeypatch, [])
    observe.kakao_observe_room(ROOM_ID)

    for i in range(3):
        _patch_messages(monkeypatch, [{"sender": "a", "text": f"msg-{i}", "timestamp_hint": None}])
        observe.kakao_observe_room(ROOM_ID)

    first_batch = observe.kakao_poll_events(limit=2)
    second_batch = observe.kakao_poll_events(limit=2)

    assert len(first_batch) == 2
    assert len(second_batch) == 1


def test_poll_events_empty_queue_returns_empty_list() -> None:
    assert observe.kakao_poll_events() == []
