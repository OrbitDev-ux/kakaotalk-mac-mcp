"""수동 검증 스크립트: 창을 띄우지 않고 입력/전송 조작이 되는지 확인한다.

목표: 카카오톡 창이 화면 앞으로 튀어나오지 않으면서 입력창 조작이
가능한지 사람이 눈으로 확인하는 것.

주의(중요): 이 스크립트는 실제 메시지를 전송하지 않는다. "전송
테스트"는 전송 버튼을 실제로 누르는 대신, raise_window=False로
전송 버튼을 찾을 수 있는지(=창을 띄우지 않고도 전송을 시도할 수
있는 상태인지)만 확인하는 드라이런이다. send_enabled=False 기본
원칙과 "승인 없는 자동 전송 금지" 규칙을 이 수동 스크립트에서도
그대로 지킨다. 진짜 전송 동작은 이후 prepare/commit 승인 흐름이
구현된 뒤, 명시적 승인 하에서만 검증한다.

실행 전에 카카오톡 창을 다른 앱 뒤로 보내서(비활성 상태로 만들어)
"창을 띄우지 않고" 성공하는지가 의미 있게 검증되도록 한다.

실행:
    uv run python tests/manual_test_focus.py
"""

from __future__ import annotations

import subprocess
import sys
import time

from kakao_mac_mcp.macos import chat_window
from kakao_mac_mcp.macos.accessibility import get_attr
from kakao_mac_mcp.macos.kakao_app import get_windows, is_kakao_running
from ApplicationServices import kAXValueAttribute

PROBE_TEXT = "__kakao_mac_mcp_focus_test__"


def _send_kakao_to_background() -> None:
    """다른 앱(Finder)을 활성화해 카카오톡을 뒤로 보낸다 (최선 노력)."""
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Finder" to activate'],
            capture_output=True,
            timeout=3,
        )
        time.sleep(0.3)
    except Exception as exc:
        print(f"   (참고: 카톡을 뒤로 보내는 데 실패 — {exc!r}. 계속 진행)")


def main() -> int:
    if not is_kakao_running():
        print("❌ 카카오톡이 실행 중이 아님. 카카오톡을 실행하고 채팅방을 열어두세요")
        return 1

    windows = get_windows()
    if not windows:
        print("❌ 열린 카카오톡 창이 없음. 채팅방을 최소 1개 열어두고 다시 시도하세요")
        return 1

    window = windows[0]

    input_area = chat_window.find_input_area(window)
    if input_area is None:
        print("❌ 입력창을 찾지 못함 (카톡 버전 차이일 수 있음)")
        return 1

    print("1) 카카오톡을 화면 뒤로 보냅니다 (Finder를 활성화)...")
    _send_kakao_to_background()

    print("2) 창을 띄우지 않고(raise_window=False) 입력창에 텍스트 설정을 시도합니다...")
    input_ok = chat_window.set_input_text(window, PROBE_TEXT, raise_window=False)
    actual_value = get_attr(input_area, kAXValueAttribute)

    if input_ok and actual_value == PROBE_TEXT:
        print("   🎉 창 안 띄우고 입력 성공!")
    else:
        print("   ⚠️  창을 띄우지 않고는 입력이 반영되지 않음 (이 카톡 버전의 한계일 수 있음)")
        print("       사용자가 화면을 보고 카톡 창이 실제로 튀어나왔는지 확인해 주세요.")

    print("3) 입력창을 정리합니다 (raise_window=False)...")
    chat_window.clear_input(window)

    print("4) 창을 띄우지 않고(raise_window=False) 전송 버튼을 찾을 수 있는지 확인합니다")
    print("   (주의: 실제로 누르지 않습니다 — 이 단계는 읽기/조작 검증용이며 전송하지 않음)")
    send_button = chat_window.find_send_button(window)
    if send_button is not None:
        print("   🎉 전송 버튼을 창을 띄우지 않고도 찾음 (버튼을 누르지는 않았습니다).")
    else:
        print("   ⚠️  전송 버튼을 찾지 못함 — 실제 전송 시에는 활성화 폴백이 필요할 수 있음.")

    print(
        "\n5) 사용자 확인 필요: 이 스크립트를 실행하는 동안 카카오톡 창이 "
        "화면 앞으로 튀어나왔습니까?"
    )
    print("   튀어나오지 않았다면 성공, 튀어나왔다면 raise_window='auto' 폴백이 발동한 것입니다.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
