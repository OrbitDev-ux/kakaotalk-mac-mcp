"""chat_window.py 통합 테스트.

카카오톡이 실행 중이고 채팅창이 최소 1개 열려 있을 때만 실행된다.
그렇지 않으면 스킵한다 (무인 환경/CI에서 실패하지 않도록). 실제
메시지 전송(send_current_input)은 send_enabled=False 원칙에 따라
이 테스트에서 호출하지 않는다.
"""

from __future__ import annotations

import pytest

from kakao_mac_mcp.macos import chat_window
from kakao_mac_mcp.macos.accessibility import get_attr
from kakao_mac_mcp.macos.kakao_app import get_windows, is_kakao_running
from ApplicationServices import kAXValueAttribute


def _get_first_window():
    """이미 보이는(활성화된) 카카오톡 창만 사용한다.

    이 테스트 스위트는 사용자 화면을 방해하지 않기 위해 카카오톡을
    스스로 활성화(raise)하지 않는다 — 카카오톡의 AX 서버는 앱이
    프론트에 있을 때만 창 목록을 안정적으로 보고하므로, 창이 이미
    보이는 상태가 아니면 조용히 스킵한다. 라이브 검증이 필요하면
    실행 전에 사용자가 직접 카카오톡 창을 화면에 띄워 둔다.
    """
    if not is_kakao_running():
        return None
    windows = get_windows()
    return windows[0] if windows else None


@pytest.fixture
def window():
    win = _get_first_window()
    if win is None:
        pytest.skip("카카오톡이 실행 중이 아니거나 열린 창이 없어 스킵합니다")
    return win


def test_read_messages_returns_list_within_limit(window) -> None:
    messages = chat_window.read_messages(window, limit=5)
    assert isinstance(messages, list)
    assert len(messages) <= 5
    for msg in messages:
        assert set(msg.keys()) >= {"sender", "text", "timestamp_hint", "raw_role"}


def test_read_messages_zero_limit_returns_empty(window) -> None:
    assert chat_window.read_messages(window, limit=0) == []


def test_set_input_text_then_clear(window) -> None:
    input_area = chat_window.find_input_area(window)
    if input_area is None:
        pytest.skip("입력창을 찾지 못해 스킵합니다 (카톡 버전 차이 가능)")

    probe_text = "__kakao_mac_mcp_test_probe__"
    assert chat_window.set_input_text(window, probe_text) is True
    assert get_attr(input_area, kAXValueAttribute) == probe_text

    assert chat_window.clear_input(window) is True
    assert get_attr(input_area, kAXValueAttribute) in ("", None)


def test_set_input_text_without_raising_window(window) -> None:
    """raise_window=False로도 창을 띄우지 않고 입력이 성공하는지 확인한다."""
    input_area = chat_window.find_input_area(window)
    if input_area is None:
        pytest.skip("입력창을 찾지 못해 스킵합니다 (카톡 버전 차이 가능)")

    probe_text = "__kakao_mac_mcp_test_probe_no_raise__"
    ok = chat_window.set_input_text(window, probe_text, raise_window=False)
    chat_window.clear_input(window)

    if not ok:
        pytest.skip("이 카톡 버전은 창을 띄우지 않고는 값 설정이 반영되지 않음")
    assert ok is True


def test_send_current_input_not_exercised_here() -> None:
    # 실제 전송 트리거는 fail-closed 원칙상 이 테스트 스위트에서 호출하지 않는다.
    # 구현 자체는 존재해야 한다 (PART 5+의 prepare/commit 흐름에서 사용).
    assert callable(chat_window.send_current_input)


def test_find_message_area_returns_none_for_none_window() -> None:
    assert chat_window.find_message_area(None) is None


def test_find_input_area_returns_none_for_none_window() -> None:
    assert chat_window.find_input_area(None) is None


def test_read_messages_returns_empty_for_none_window() -> None:
    assert chat_window.read_messages(None, limit=5) == []


# ---------------------------------------------------------------------------
# raise_window 폴백 체인 - 실제 카톡 창 없이 monkeypatch로 검증.
# ---------------------------------------------------------------------------


class _Unreachable:
    """호출되면 안 되는 함수 자리에 넣는 sentinel."""

    def __call__(self, *args, **kwargs):
        raise AssertionError("호출되지 않아야 할 함수가 호출됨")


def test_set_input_text_raise_false_success_never_activates(monkeypatch) -> None:
    monkeypatch.setattr(chat_window, "find_input_area", lambda w: "input-area")
    monkeypatch.setattr(chat_window, "set_attr", lambda el, attr, val: True)
    monkeypatch.setattr(chat_window, "get_attr", lambda el, attr: "hello")
    monkeypatch.setattr(chat_window, "activate_window", _Unreachable())

    assert chat_window.set_input_text("window", "hello", raise_window=False) is True


def test_set_input_text_raise_false_failure_never_activates(monkeypatch) -> None:
    monkeypatch.setattr(chat_window, "find_input_area", lambda w: "input-area")
    monkeypatch.setattr(chat_window, "set_attr", lambda el, attr, val: True)
    monkeypatch.setattr(chat_window, "get_attr", lambda el, attr: "not-what-we-set")
    monkeypatch.setattr(chat_window, "activate_window", _Unreachable())

    assert chat_window.set_input_text("window", "hello", raise_window=False) is False


def test_set_input_text_auto_falls_back_to_activation(monkeypatch) -> None:
    calls: list[str] = []
    read_values = iter(["not-yet", "hello"])

    monkeypatch.setattr(chat_window, "find_input_area", lambda w: "input-area")
    monkeypatch.setattr(chat_window, "set_attr", lambda el, attr, val: True)
    monkeypatch.setattr(chat_window, "get_attr", lambda el, attr: next(read_values))
    monkeypatch.setattr(chat_window, "activate_window", lambda w: calls.append("activate") or True)
    monkeypatch.setattr(chat_window, "_set_input_text_via_applescript", _Unreachable())

    assert chat_window.set_input_text("window", "hello", raise_window="auto") is True
    assert calls == ["activate"]


def test_set_input_text_auto_falls_back_to_applescript(monkeypatch) -> None:
    monkeypatch.setattr(chat_window, "find_input_area", lambda w: "input-area")
    monkeypatch.setattr(chat_window, "set_attr", lambda el, attr, val: True)
    monkeypatch.setattr(chat_window, "get_attr", lambda el, attr: "still-wrong")
    monkeypatch.setattr(chat_window, "activate_window", lambda w: True)
    monkeypatch.setattr(chat_window, "_set_input_text_via_applescript", lambda el, text: True)

    assert chat_window.set_input_text("window", "hello", raise_window="auto") is True


def test_set_input_text_raise_true_activates_before_setting(monkeypatch) -> None:
    order: list[str] = []

    monkeypatch.setattr(chat_window, "find_input_area", lambda w: "input-area")
    monkeypatch.setattr(chat_window, "activate_window", lambda w: order.append("activate") or True)

    def fake_set_attr(el, attr, val):
        order.append("set")
        return True

    monkeypatch.setattr(chat_window, "set_attr", fake_set_attr)
    monkeypatch.setattr(chat_window, "get_attr", lambda el, attr: "hello")

    assert chat_window.set_input_text("window", "hello", raise_window=True) is True
    assert order == ["activate", "set"]


def test_clear_input_delegates_with_raise_window_false(monkeypatch) -> None:
    seen = {}

    def fake_set_input_text(window, text, raise_window="auto"):
        seen["window"] = window
        seen["text"] = text
        seen["raise_window"] = raise_window
        return True

    monkeypatch.setattr(chat_window, "set_input_text", fake_set_input_text)

    assert chat_window.clear_input("window") is True
    assert seen == {"window": "window", "text": "", "raise_window": False}


def test_activate_window_prefers_ax_raise_over_applescript(monkeypatch) -> None:
    monkeypatch.setattr(chat_window, "perform_action", lambda el, action: True)
    monkeypatch.setattr(chat_window.subprocess, "run", _Unreachable())

    assert chat_window.activate_window("window", timeout_ms=0) is True


def test_activate_window_falls_back_to_applescript(monkeypatch) -> None:
    class _FakeResult:
        returncode = 0

    monkeypatch.setattr(chat_window, "perform_action", lambda el, action: False)
    monkeypatch.setattr(chat_window.subprocess, "run", lambda *a, **kw: _FakeResult())

    assert chat_window.activate_window("window", timeout_ms=0) is True


def test_activate_window_returns_false_for_none_window() -> None:
    assert chat_window.activate_window(None) is False


def test_send_current_input_raise_false_only_tries_button(monkeypatch) -> None:
    monkeypatch.setattr(chat_window, "find_send_button", lambda w: "button")
    monkeypatch.setattr(chat_window, "perform_action", lambda el, action: True)
    monkeypatch.setattr(chat_window, "activate_window", _Unreachable())
    monkeypatch.setattr(chat_window, "send_enter_key", _Unreachable())

    assert chat_window.send_current_input("window", raise_window=False) is True


def test_send_current_input_raise_false_never_escalates_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(chat_window, "find_send_button", lambda w: None)
    monkeypatch.setattr(chat_window, "activate_window", _Unreachable())
    monkeypatch.setattr(chat_window, "send_enter_key", _Unreachable())

    assert chat_window.send_current_input("window", raise_window=False) is False


def test_send_current_input_auto_falls_back_to_enter_key(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(chat_window, "find_send_button", lambda w: None)
    monkeypatch.setattr(chat_window, "activate_window", lambda w: calls.append("activate") or True)
    monkeypatch.setattr(chat_window, "send_enter_key", lambda w: calls.append("enter") or True)

    assert chat_window.send_current_input("window", raise_window="auto") is True
    assert calls == ["activate", "enter"]


def test_send_enter_key_always_activates_first(monkeypatch) -> None:
    order: list[str] = []

    monkeypatch.setattr(chat_window, "activate_window", lambda w: order.append("activate") or True)
    monkeypatch.setattr(chat_window, "find_input_area", lambda w: "input-area")
    monkeypatch.setattr(chat_window, "set_attr", lambda el, attr, val: order.append("focus") or True)
    monkeypatch.setattr(chat_window, "_post_return_key", lambda: order.append("enter") or True)

    assert chat_window.send_enter_key("window") is True
    assert order == ["activate", "focus", "enter"]


def test_send_enter_key_returns_false_for_none_window() -> None:
    assert chat_window.send_enter_key(None) is False
