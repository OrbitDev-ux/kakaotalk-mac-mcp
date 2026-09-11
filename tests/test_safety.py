"""safety.py 단위 테스트."""

from __future__ import annotations

import sqlite3

import pytest

from kakao_mac_mcp import state
from kakao_mac_mcp.config import AppConfig, LimitsConfig
from kakao_mac_mcp.safety import (
    can_send,
    check_activation_rate_limit,
    check_rate_limit,
    compute_fingerprint,
    record_activation,
    require_approval,
    validate_reply_text,
)


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    connection = state.init_db(tmp_path / "test_safety.db")
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# can_send
# ---------------------------------------------------------------------------


def test_can_send_false_when_send_disabled() -> None:
    config = AppConfig(send_enabled=False)
    assert can_send(config) is False


def test_can_send_true_for_manual_flow_when_enabled() -> None:
    config = AppConfig(send_enabled=True)
    assert can_send(config) is True


def test_can_send_auto_reply_requires_both_flags() -> None:
    config = AppConfig(send_enabled=True, auto_reply_enabled=False)
    assert can_send(config, is_auto_reply=True) is False

    config2 = AppConfig(send_enabled=True, auto_reply_enabled=True)
    assert can_send(config2, is_auto_reply=True) is True


def test_can_send_false_when_send_disabled_even_with_auto_reply_enabled() -> None:
    config = AppConfig(send_enabled=False, auto_reply_enabled=True)
    assert can_send(config, is_auto_reply=True) is False
    assert can_send(config) is False


# ---------------------------------------------------------------------------
# validate_reply_text
# ---------------------------------------------------------------------------


def test_validate_reply_text_rejects_empty() -> None:
    limits = LimitsConfig()
    ok, reason = validate_reply_text("", limits)
    assert ok is False
    assert reason

    ok, reason = validate_reply_text("   ", limits)
    assert ok is False


def test_validate_reply_text_rejects_none() -> None:
    ok, _ = validate_reply_text(None, LimitsConfig())
    assert ok is False


def test_validate_reply_text_rejects_too_long() -> None:
    limits = LimitsConfig()
    too_long = "가" * 2000
    ok, reason = validate_reply_text(too_long, limits)
    assert ok is False
    assert "너무 깁니다" in reason


def test_validate_reply_text_rejects_control_chars() -> None:
    limits = LimitsConfig()
    ok, reason = validate_reply_text("안녕\x00하세요", limits)
    assert ok is False
    assert reason


def test_validate_reply_text_accepts_normal_text() -> None:
    limits = LimitsConfig()
    ok, reason = validate_reply_text("안녕하세요, 잘 지내시나요?", limits)
    assert ok is True
    assert reason == ""


# ---------------------------------------------------------------------------
# compute_fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_is_deterministic() -> None:
    fp1 = compute_fingerprint("room-a", "sender-1", "hello", "2026-01-01T00:00:00Z")
    fp2 = compute_fingerprint("room-a", "sender-1", "hello", "2026-01-01T00:00:00Z")
    assert fp1 == fp2
    assert len(fp1) == 64


def test_fingerprint_changes_with_any_field() -> None:
    base = compute_fingerprint("room-a", "sender-1", "hello", "ts-1")

    assert compute_fingerprint("room-b", "sender-1", "hello", "ts-1") != base
    assert compute_fingerprint("room-a", "sender-2", "hello", "ts-1") != base
    assert compute_fingerprint("room-a", "sender-1", "world", "ts-1") != base
    assert compute_fingerprint("room-a", "sender-1", "hello", "ts-2") != base


# ---------------------------------------------------------------------------
# check_rate_limit
# ---------------------------------------------------------------------------


def test_check_rate_limit_allows_under_limit(conn: sqlite3.Connection) -> None:
    limits = LimitsConfig(max_send_per_minute=3)
    for _ in range(2):
        state.audit(conn, "send", "room-a")

    assert check_rate_limit(conn, "room-a", limits) is True


def test_check_rate_limit_blocks_over_limit(conn: sqlite3.Connection) -> None:
    limits = LimitsConfig(max_send_per_minute=3)
    for _ in range(3):
        state.audit(conn, "send", "room-a")

    assert check_rate_limit(conn, "room-a", limits) is False


def test_check_rate_limit_is_per_room(conn: sqlite3.Connection) -> None:
    limits = LimitsConfig(max_send_per_minute=1)
    state.audit(conn, "send", "room-a")

    assert check_rate_limit(conn, "room-a", limits) is False
    assert check_rate_limit(conn, "room-b", limits) is True


def test_check_rate_limit_ignores_non_send_actions(conn: sqlite3.Connection) -> None:
    limits = LimitsConfig(max_send_per_minute=1)
    state.audit(conn, "read", "room-a")
    state.audit(conn, "read", "room-a")

    assert check_rate_limit(conn, "room-a", limits) is True


# ---------------------------------------------------------------------------
# require_approval
# ---------------------------------------------------------------------------


def test_require_approval_true_for_fresh_prepared_ticket(conn: sqlite3.Connection) -> None:
    ticket_id = state.create_ticket(conn, "room-a", "hi", "fp-1", ttl_seconds=300)
    assert require_approval(conn, ticket_id) is True


def test_require_approval_false_for_missing_ticket(conn: sqlite3.Connection) -> None:
    assert require_approval(conn, "no-such-ticket") is False


def test_require_approval_false_for_expired_ticket(conn: sqlite3.Connection) -> None:
    ticket_id = state.create_ticket(conn, "room-a", "hi", "fp-1", ttl_seconds=-1)
    assert require_approval(conn, ticket_id) is False


def test_require_approval_false_for_already_committed_ticket(conn: sqlite3.Connection) -> None:
    ticket_id = state.create_ticket(conn, "room-a", "hi", "fp-1", ttl_seconds=300)
    state.update_ticket_status(conn, ticket_id, state.TICKET_STATUS_COMMITTED)
    assert require_approval(conn, ticket_id) is False


# ---------------------------------------------------------------------------
# check_activation_rate_limit / record_activation
# ---------------------------------------------------------------------------


def test_check_activation_rate_limit_allows_first_call(conn: sqlite3.Connection) -> None:
    assert check_activation_rate_limit(conn) is True


def test_check_activation_rate_limit_blocks_within_one_second(conn: sqlite3.Connection) -> None:
    state.audit(conn, "activate_window", "room-a")
    assert check_activation_rate_limit(conn) is False


def test_record_activation_logs_and_returns_within_limit(conn: sqlite3.Connection) -> None:
    within_limit = record_activation(conn, "room-a", detail="input fallback")

    assert within_limit is True
    cursor = conn.execute("SELECT * FROM audit_log WHERE action = 'activate_window'")
    rows = [dict(row) for row in cursor.fetchall()]
    assert len(rows) == 1
    assert rows[0]["room_alias"] == "room-a"
    assert rows[0]["detail"] == "input fallback"


def test_record_activation_notes_warning_when_over_limit(conn: sqlite3.Connection) -> None:
    record_activation(conn, "room-a")
    within_limit = record_activation(conn, "room-a")

    assert within_limit is False
    cursor = conn.execute(
        "SELECT detail FROM audit_log WHERE action = 'activate_window' ORDER BY id ASC"
    )
    details = [row["detail"] for row in cursor.fetchall()]
    assert "경고" in details[-1]
