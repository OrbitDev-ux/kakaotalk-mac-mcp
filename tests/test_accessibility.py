"""카카오톡 접근성 API(AX API) 접근 가능 여부를 검증하는 스크립트.

PART 0/1 단계 검증용 스크립트로, pytest로도 실행 가능하지만
`python tests/test_accessibility.py`로 직접 실행해 사람이 읽을 수 있는
한국어 진단 메시지를 받는 것을 주 목적으로 한다.

절대 예외를 조용히 삼키지 않는다 — 예상치 못한 예외는 traceback과
함께 그대로 출력한다.
"""

from __future__ import annotations

import sys
import traceback

from kakao_mac_mcp.macos.accessibility import is_trusted
from kakao_mac_mcp.macos.kakao_app import (
    get_kakao_pid,
    get_windows,
    get_window_titles,
)


def _check_accessibility_trusted() -> bool:
    """접근성 권한 여부를 확인하고, 실패 시 원인을 진단 출력한다."""
    try:
        return is_trusted()
    except Exception as exc:  # pragma: no cover - is_trusted()는 내부에서 이미 삼킴
        print(f"❌ 접근성 권한 확인 중 예외 발생: {exc!r}")
        traceback.print_exc()
        return False


def run_accessibility_check() -> int:
    """카카오톡 접근성 API 접근 가능 여부를 단계별로 검증한다.

    Returns:
        성공 시 0, 실패 시 1.
    """
    try:
        pid = get_kakao_pid()
    except Exception as exc:
        print(f"❌ 카카오톡 PID 조회 중 예외 발생: {exc!r}")
        traceback.print_exc()
        return 1

    if pid is None:
        print("❌ 카카오톡이 실행 중이 아님. 카카오톡 앱을 실행한 뒤 다시 시도하세요")
        return 1

    if not _check_accessibility_trusted():
        print(
            "❌ 접근성 권한 없음.\n"
            "   시스템 설정 → 개인정보 보호 및 보안 → 손쉬운 사용에서\n"
            "   터미널(또는 Claude Code가 실행 중인 앱)을 허용하세요"
        )
        return 1

    try:
        windows = get_windows()
        titles = get_window_titles()
    except Exception as exc:
        print(f"❌ 카카오톡 창 목록 조회 중 예외 발생: {exc!r}")
        traceback.print_exc()
        return 1

    if len(windows) == 0:
        print("❌ 카카오톡 창이 0개. 창을 최소화하지 말고 띄워두세요")
        return 1

    print(f"✅ 카카오톡 PID: {pid}")
    print(f"✅ 창 {len(windows)}개 접근 성공")
    for index, title in enumerate(titles):
        print(f"   [{index}] {title}")
    print("🎉 접근성 API 사용 가능. 다음 단계 진행 가능.")
    return 0


if __name__ == "__main__":
    sys.exit(run_accessibility_check())
