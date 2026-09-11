"""카카오톡 앱 탐색 및 제어.

이 모듈은 실행 중인 카카오톡 프로세스를 찾고, 접근성 API를 통해
앱의 AX 루트 요소와 창(window) 목록에 접근하는 기능을 제공한다.

카카오톡이 실행 중이지 않은 경우, 접근성 권한이 없는 경우,
창이 하나도 없는 경우를 각각 구분해서 상위 계층(테스트, 도구)이
사용자에게 명확한 안내를 줄 수 있도록 None/빈 리스트를 반환한다.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any

from ApplicationServices import AXUIElementCreateApplication

from kakao_mac_mcp.macos.accessibility import get_attr
from ApplicationServices import kAXTitleAttribute, kAXWindowsAttribute

KAKAO_PROCESS_PATTERN = "KakaoTalk"

# 카카오톡의 AX 서버가 간헐적으로 kAXWindowsAttribute를 빈 배열로
# 반환하는 경우가 관찰됨 (창이 실제로 열려 있어도, 앱을 막
# activate한 직후에도 최대 1~2초가량 발생할 수 있음). 짧은 재시도로
# 이 순간적인 플레이키니스를 흡수한다.
_GET_WINDOWS_RETRIES = 10
_GET_WINDOWS_RETRY_DELAY_SECONDS = 0.2


def get_kakao_pid() -> int | None:
    """실행 중인 카카오톡 프로세스의 PID를 찾는다.

    `pgrep -f KakaoTalk`을 사용한다. 여러 프로세스가 매칭되면
    가장 먼저 나오는 PID를 사용한다 (보통 메인 앱 프로세스).

    Returns:
        PID. 카카오톡이 실행 중이 아니거나 조회에 실패하면 None.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", KAKAO_PROCESS_PATTERN],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            return int(line)
        except ValueError:
            continue

    return None


def is_kakao_running() -> bool:
    """카카오톡이 실행 중인지 확인한다.

    Returns:
        실행 중이면 True.
    """
    return get_kakao_pid() is not None


def get_app_root() -> Any | None:
    """카카오톡 앱의 AX 루트 요소를 가져온다.

    Returns:
        AXUIElementRef. 카카오톡이 실행 중이 아니면 None.
    """
    pid = get_kakao_pid()
    if pid is None:
        return None
    try:
        return AXUIElementCreateApplication(pid)
    except Exception:
        return None


def get_windows() -> list:
    """카카오톡 앱의 창 목록을 가져온다.

    접근성 권한이 없거나 앱 루트를 가져오지 못하면 빈 리스트를
    반환한다 (권한 없음과 창 0개는 상위에서 별도로 구분해야 함에
    주의 — 이 함수만으로는 두 경우를 구분할 수 없으므로,
    필요하면 get_app_root()의 반환값을 함께 확인한다).

    카카오톡의 AX 서버는 창이 실제로 열려 있어도 순간적으로 빈
    배열을 반환하는 경우가 있어(관찰된 버그성 동작), 빈 결과를
    얻으면 짧게 재시도한다.

    Returns:
        AXUIElement 창 리스트.
    """
    app_root = get_app_root()
    if app_root is None:
        return []

    for attempt in range(_GET_WINDOWS_RETRIES):
        windows = get_attr(app_root, kAXWindowsAttribute)
        if windows is not None:
            try:
                window_list = list(windows)
            except Exception:
                window_list = []
            if window_list:
                return window_list
        if attempt < _GET_WINDOWS_RETRIES - 1:
            time.sleep(_GET_WINDOWS_RETRY_DELAY_SECONDS)

    return []


def get_window_titles() -> list[str]:
    """카카오톡 창들의 제목 목록을 가져온다.

    Returns:
        창 제목 문자열 리스트. 제목을 읽지 못한 창은 빈 문자열로 채운다.
    """
    titles: list[str] = []
    for window in get_windows():
        title = get_attr(window, kAXTitleAttribute)
        titles.append(title if isinstance(title, str) else "")
    return titles


def find_chat_window(room_id_hint: str) -> Any | None:
    """제목에 힌트 문자열이 포함된 채팅 창을 찾는다.

    room_id는 사용자 지정 별칭이며 실제 방 제목이 아니다. 호출하는
    쪽에서 별칭 -> 실제 방 제목(또는 그 일부) 매핑을 관리한 뒤,
    여기에는 실제 창 제목에서 찾을 부분 문자열(hint)만 전달해야 한다.

    Args:
        room_id_hint: 창 제목에서 찾을 부분 문자열.

    Returns:
        가장 먼저 매칭된 AXUIElement 창. 없으면 None.
    """
    if not room_id_hint:
        return None

    for window in get_windows():
        title = get_attr(window, kAXTitleAttribute)
        if isinstance(title, str) and room_id_hint in title:
            return window

    return None
