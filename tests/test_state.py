"""state.py 단위 테스트. 임시 SQLite DB를 사용한다."""

from __future__ import annotations

import sqlite3
import time

import pytest

from kakao_mac_mcp import state


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "test_state.db"
    connection = state.init_db(db_path)
    yield connection
    connection.close()


def test_init_db_creates_tables(conn: sqlite3.Connection) -> None:
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    tables = {row["name"] for row in cursor.fetchall()}
    assert {"fingerprints", "baselines", "tickets", "events", "audit_log"} <= tables


def test_duplicate_detection(conn: sqlite3.Connection) -> None:
    fp = "deadbeef" * 8
    assert state.is_duplicate(conn, "room-a", fp) is False

    state.record_fingerprint(conn, "room-a", fp)
    assert state.is_duplicate(conn, "room-a", fp) is True

    # 다른 room에서는 별개로 취급된다.
    assert state.is_duplicate(conn, "room-b", fp) is False

    # 다른 fingerprint는 여전히 중복이 아니다.
    assert state.is_duplicate(conn, "room-a", "other" * 8) is False


def test_record_fingerprint_upsert_does_not_raise(conn: sqlite3.Connection) -> None:
    fp = "cafebabe" * 8
    state.record_fingerprint(conn, "room-a", fp)
    # 같은 fingerprint를 다시 기록해도 예외 없이 last_seen만 갱신된다.
    state.record_fingerprint(conn, "room-a", fp)
    assert state.is_duplicate(conn, "room-a", fp) is True


def test_baseline_set_and_get(conn: sqlite3.Connection) -> None:
    assert state.get_baseline(conn, "room-a") is None

    state.set_baseline(conn, "room-a", "fp-1")
    assert state.get_baseline(conn, "room-a") == "fp-1"

    # 갱신 시 덮어써야 한다.
    state.set_baseline(conn, "room-a", "fp-2")
    assert state.get_baseline(conn, "room-a") == "fp-2"

    # 다른 room의 baseline에는 영향 없다.
    assert state.get_baseline(conn, "room-b") is None


def test_ticket_lifecycle(conn: sqlite3.Connection) -> None:
    ticket_id = state.create_ticket(conn, "room-a", "안녕하세요", "fp-1")
    ticket = state.get_ticket(conn, ticket_id)

    assert ticket is not None
    assert ticket["room_id"] == "room-a"
    assert ticket["text"] == "안녕하세요"
    assert ticket["status"] == state.TICKET_STATUS_PREPARED

    state.update_ticket_status(conn, ticket_id, state.TICKET_STATUS_COMMITTED)
    updated = state.get_ticket(conn, ticket_id)
    assert updated is not None
    assert updated["status"] == state.TICKET_STATUS_COMMITTED


def test_get_ticket_missing_returns_none(conn: sqlite3.Connection) -> None:
    assert state.get_ticket(conn, "does-not-exist") is None


def test_expire_old_tickets(conn: sqlite3.Connection) -> None:
    # ttl_seconds=-1이면 생성 즉시 만료 시각이 과거가 된다.
    expired_ticket_id = state.create_ticket(conn, "room-a", "hi", "fp-1", ttl_seconds=-1)
    fresh_ticket_id = state.create_ticket(conn, "room-a", "hi2", "fp-2", ttl_seconds=300)

    changed = state.expire_old_tickets(conn)
    assert changed == 1

    expired = state.get_ticket(conn, expired_ticket_id)
    fresh = state.get_ticket(conn, fresh_ticket_id)

    assert expired is not None and expired["status"] == state.TICKET_STATUS_EXPIRED
    assert fresh is not None and fresh["status"] == state.TICKET_STATUS_PREPARED


def test_event_add_and_poll_drains_queue(conn: sqlite3.Connection) -> None:
    state.add_event(conn, "room-a", "fp-1", "new_message", {"len": 3})
    state.add_event(conn, "room-a", "fp-2", "new_message", "raw-payload")

    events = state.poll_events(conn, limit=50)
    assert len(events) == 2
    assert events[0]["fingerprint"] == "fp-1"
    assert events[0]["event_type"] == "new_message"
    assert events[1]["payload"] == "raw-payload"

    # poll은 소비형이므로 두 번째 poll은 비어 있어야 한다.
    assert state.poll_events(conn, limit=50) == []


def test_event_poll_respects_limit_and_order(conn: sqlite3.Connection) -> None:
    for i in range(5):
        state.add_event(conn, "room-a", f"fp-{i}", "new_message", None)

    first_batch = state.poll_events(conn, limit=2)
    assert [e["fingerprint"] for e in first_batch] == ["fp-0", "fp-1"]

    second_batch = state.poll_events(conn, limit=50)
    assert [e["fingerprint"] for e in second_batch] == ["fp-2", "fp-3", "fp-4"]


def test_audit_log_records_alias_not_real_title(conn: sqlite3.Connection) -> None:
    state.audit(conn, "send", "room-alias-1", detail="commit ok")

    cursor = conn.execute("SELECT * FROM audit_log")
    rows = [dict(row) for row in cursor.fetchall()]

    assert len(rows) == 1
    assert rows[0]["action"] == "send"
    assert rows[0]["room_alias"] == "room-alias-1"
    assert rows[0]["detail"] == "commit ok"
    assert rows[0]["created_at"]


def test_audit_log_multiple_entries_ordered(conn: sqlite3.Connection) -> None:
    state.audit(conn, "read", "room-a")
    time.sleep(0.01)
    state.audit(conn, "send", "room-a")

    cursor = conn.execute("SELECT action FROM audit_log ORDER BY id ASC")
    actions = [row["action"] for row in cursor.fetchall()]
    assert actions == ["read", "send"]
