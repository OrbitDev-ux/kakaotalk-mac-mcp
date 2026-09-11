"""수동 검증 스크립트: 열린 카카오톡 창의 최근 메시지를 콘솔에 출력한다.

이 스크립트는 사람이 직접 실행해서 결과를 눈으로 확인하기 위한
것이다. 어떤 파일/DB/로그에도 쓰지 않고 표준 출력(콘솔)에만
출력한다 — 실제 방 제목과 메시지 내용이 여기 출력되지만, 이는
로그로 저장되지 않는다.

실행:
    uv run python tests/manual_read_chat.py
"""

from __future__ import annotations

import sys

from kakao_mac_mcp.macos import chat_window
from kakao_mac_mcp.macos.kakao_app import get_window_titles, get_windows, is_kakao_running


def main() -> int:
    if not is_kakao_running():
        print("❌ 카카오톡이 실행 중이 아님. 카카오톡을 실행하고 채팅방을 열어두세요")
        return 1

    windows = get_windows()
    titles = get_window_titles()

    if not windows:
        print("❌ 열린 카카오톡 창이 없음. 채팅방을 최소 1개 열어두고 다시 시도하세요")
        return 1

    print(f"열린 카카오톡 창 {len(windows)}개:")
    for index, title in enumerate(titles):
        print(f"   [{index}] {title}")

    window = windows[0]
    print("\n첫 번째 창의 최근 메시지 5개:")

    try:
        messages = chat_window.read_messages(window, limit=5)
    except Exception as exc:
        print(f"❌ 메시지 읽기 중 예외 발생: {exc!r}")
        return 1

    if not messages:
        print("   (읽은 메시지가 없음 — 카톡 버전에 따라 AX 트리 구조가 다를 수 있음)")
        return 1

    for index, msg in enumerate(messages):
        print(
            f"   [{index}] sender={msg['sender']!r} "
            f"text={msg['text']!r} timestamp_hint={msg['timestamp_hint']!r} "
            f"raw_role={msg['raw_role']!r}"
        )

    print("\n🎉 카카오톡 메시지 읽기 성공.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
