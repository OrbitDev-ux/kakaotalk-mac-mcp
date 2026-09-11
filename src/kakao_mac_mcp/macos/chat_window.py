"""카카오톡 채팅창 파싱 및 조작.

macOS 카카오톡의 AX 트리는 대략 다음과 같은 구조를 가진다 (버전에
따라 달라질 수 있음, 아래는 참고용 근사치):

    AXWindow (채팅방 창)
    └── AXSplitGroup
        ├── AXScrollArea (메시지 목록 영역)
        │   └── AXGroup (메시지 버블) / AXStaticText (발신자/본문/시간)
        ├── AXTextArea 또는 AXTextField (입력창)
        └── AXButton ("전송" 버튼)

이 모듈은 role/title 기반의 유연한 탐색만 사용하며, 특정 인덱스나
고정된 트리 경로에 의존하지 않는다. macOS/카카오톡 버전이 바뀌면
이 구조가 달라질 수 있으므로, 모든 함수는 요소를 찾지 못하면
예외를 던지지 않고 None/빈 값을 반환한다 (방어적 코딩).

## 창 활성화(raise) 정책

set_input_text / send_current_input은 카카오톡 창을 화면 앞으로
띄우지 않고 조작하는 것을 우선시한다 (raise_window="auto"). 다음
3단계 폴백을 따른다:

    1순위: 창을 띄우지 않고 AX 값 설정을 시도한다 (가장 안정적).
    2순위: 1순위가 실패하면 activate_window()로 창을 최소 시간만
           활성화한 뒤 재시도한다.
    3순위: 그래도 실패하면 AppleScript(System Events keystroke) 등
           최후 수단을 시도한다.

raise_window=False를 지정하면 1순위만 시도하고 절대 창을 띄우지
않는다(실패 시 그대로 False). raise_window=True는 1순위를 건너뛰고
바로 활성화 후 시도한다. 전역 키 이벤트(Return 키)는 포커스가 없는
동안 보내면 엉뚱한 앱에 입력될 위험이 있으므로, 항상 활성화 이후에만
전송한다.
"""

from __future__ import annotations

import re
import subprocess
import time

from ApplicationServices import (
    kAXButtonRole,
    kAXDescriptionAttribute,
    kAXFocusedAttribute,
    kAXGroupRole,
    kAXPressAction,
    kAXRaiseAction,
    kAXRoleAttribute,
    kAXRowRole,
    kAXScrollAreaRole,
    kAXStaticTextRole,
    kAXTextAreaRole,
    kAXTextFieldRole,
    kAXTitleAttribute,
    kAXValueAttribute,
)

from kakao_mac_mcp.macos.accessibility import (
    find_by_role,
    get_attr,
    get_children,
    perform_action,
    set_attr,
)

# macOS 키코드 (kVK_Return). 카카오톡 버전에 따라 전송 버튼을 찾지
# 못하는 경우의 최후 수단으로만 사용한다.
_RETURN_KEY_CODE = 36

_TIMESTAMP_PATTERN = re.compile(r"(오전|오후)?\s*\d{1,2}:\d{2}")

# 프로필 사진 버튼의 접근성 설명이 실제 발신자 이름이 아니라
# "프로필"처럼 모든 메시지에 공통인 일반 레이블인 카톡 버전이 있음
# (특히 1:1 채팅). 이런 값은 sender로 쓰면 오해를 유발하므로 None
# 처리한다 (거짓 정보보다 "모름"이 낫다는 fail-closed 원칙).
_GENERIC_SENDER_LABELS = {"프로필", "프로필 사진", "profile", "profile photo"}

_MAX_SEARCH_DEPTH = 8

# activate_window()가 창을 활성화한 뒤 값을 반영할 시간을 벌기 위해
# 대기하는 기본 시간(ms). 최대한 짧게 유지한다.
DEFAULT_ACTIVATION_TIMEOUT_MS = 100

KAKAO_APP_NAME = "KakaoTalk"


def _count_descendants(element, max_depth: int = 6) -> int:
    """진단/휴리스틱용 - 요소 이하의 자손 개수를 얕게 센다."""
    if element is None or max_depth <= 0:
        return 0
    total = 0
    for child in get_children(element):
        total += 1 + _count_descendants(child, max_depth - 1)
    return total


def find_message_area(window) -> object | None:
    """채팅창에서 메시지 목록이 들어 있는 스크롤 영역을 찾는다.

    스크롤 영역이 여러 개 발견되면(예: 입력창 자체도 스크롤 가능한
    경우), 자손 요소가 가장 많은 것을 메시지 영역으로 추정한다.
    카톡 버전에 따라 실패할 수 있다 — 이 경우 None을 반환한다.
    """
    if window is None:
        return None

    scroll_areas = find_by_role(window, kAXScrollAreaRole, max_depth=_MAX_SEARCH_DEPTH)
    if not scroll_areas:
        return None
    if len(scroll_areas) == 1:
        return scroll_areas[0]

    return max(scroll_areas, key=lambda el: _count_descendants(el))


def _collect_static_texts(element, max_depth: int = 3) -> list[str]:
    """요소 이하의 AXStaticText 값들을 순서대로 수집한다 (중복 제거)."""
    texts: list[str] = []
    seen: set[str] = set()
    for node in find_by_role(element, kAXStaticTextRole, max_depth=max_depth):
        value = get_attr(node, kAXValueAttribute)
        if isinstance(value, str) and value.strip() and value not in seen:
            texts.append(value)
            seen.add(value)
    return texts


def _extract_timestamp_hint(texts: list[str]) -> str | None:
    """텍스트 후보들 중 시간 형식(예: '오후 3:21')처럼 보이는 것을 찾는다."""
    for text in texts:
        match = _TIMESTAMP_PATTERN.search(text)
        if match:
            return match.group(0)
    return None


def _extract_message_fields(element) -> dict:
    """메시지 한 건에 해당하는 요소에서 발신자/본문/시간을 추출한다.

    관찰된 카카오톡 버전의 메시지 셀 구조:
        AXStaticText (짧음, 시간 - 예: "오후 3:46")
        AXButton (description = 발신자 표시 이름)
        AXImage (프로필 사진, 텍스트 없음)
        AXTextArea (실제 메시지 본문)

    본문이 AXStaticText가 아니라 AXTextArea 안에 있다는 점이 핵심 —
    AXStaticText만 훑으면 시간/이름만 잡히고 본문을 놓친다. 이 구조와
    다른 카톡 버전을 위해, 위 방식으로 아무것도 못 찾으면 텍스트
    전용 휴리스틱으로 대체한다.
    """
    text: str | None = None
    for text_area in find_by_role(element, kAXTextAreaRole, max_depth=3):
        value = get_attr(text_area, kAXValueAttribute)
        if isinstance(value, str) and value.strip():
            text = value
            break

    sender: str | None = None
    for button in find_by_role(element, kAXButtonRole, max_depth=3):
        description = get_attr(button, kAXDescriptionAttribute)
        if isinstance(description, str) and description.strip():
            if description.strip().lower() in _GENERIC_SENDER_LABELS:
                continue
            sender = description
            break

    static_texts = _collect_static_texts(element, max_depth=3)
    timestamp_hint = _extract_timestamp_hint(static_texts)

    if text is None and static_texts:
        # AXTextArea 구조가 없는 카톡 버전을 위한 대체 경로.
        text = static_texts[-1]
        if sender is None and len(static_texts) > 1:
            sender = static_texts[0]

    return {"sender": sender, "text": text, "timestamp_hint": timestamp_hint}


_ROW_SEARCH_RETRIES = 5
_ROW_SEARCH_RETRY_DELAY_SECONDS = 0.15


def _find_message_candidates(area) -> list:
    """메시지 영역에서 개별 메시지에 해당하는 요소들을 찾는다.

    카카오톡의 AX 서버는 컨테이너의 자손을 순간적으로 빈 배열로
    보고하는 경우가 있어(get_windows()에서도 관찰된 것과 동일한
    현상), 메시지 영역은 찾았는데 후보가 0개인 경우 짧게 재시도한다.
    """
    role_order = (kAXRowRole, kAXGroupRole, kAXStaticTextRole)

    for attempt in range(_ROW_SEARCH_RETRIES):
        for role in role_order:
            candidates = find_by_role(area, role, max_depth=4)
            if candidates:
                return candidates
        if attempt < _ROW_SEARCH_RETRIES - 1:
            time.sleep(_ROW_SEARCH_RETRY_DELAY_SECONDS)

    return []


def read_messages(window, limit: int = 20) -> list[dict]:
    """채팅창에서 최근 메시지를 읽는다.

    각 메시지는 {"sender", "text", "timestamp_hint", "raw_role"}
    형태의 dict이며, 추출에 실패한 필드는 None이다. 메시지 영역을
    찾지 못하거나 카톡 버전 차이로 파싱이 실패하면 빈 리스트를
    반환한다 (예외를 던지지 않음).

    Args:
        window: 채팅창 AXUIElement.
        limit: 반환할 최대 메시지 개수.

    Returns:
        메시지 dict 리스트 (오래된 순 -> 최근 순).
    """
    if window is None or limit <= 0:
        return []

    area = find_message_area(window)
    if area is None:
        return []

    # 관찰된 카카오톡 버전은 AXTable > AXRow 구조로 메시지를 표현한다.
    # 다른 버전은 AXGroup으로 묶거나, 텍스트가 그룹 없이 바로 노출될
    # 수 있어 순서대로 대체 경로를 시도한다.
    candidates = find_by_role(area, kAXRowRole, max_depth=4)
    if not candidates:
        candidates = find_by_role(area, kAXGroupRole, max_depth=4)
    if not candidates:
        candidates = find_by_role(area, kAXStaticTextRole, max_depth=4)
    if not candidates:
        return []

    recent = candidates[-limit:]

    messages: list[dict] = []
    for element in recent:
        role = get_attr(element, kAXRoleAttribute)
        fields = _extract_message_fields(element)
        messages.append({**fields, "raw_role": role})

    return messages


def find_input_area(window) -> object | None:
    """채팅창의 메시지 입력창을 찾는다.

    카톡 버전에 따라 AXTextArea 또는 AXTextField로 구현되어 있을 수
    있어 둘 다 시도한다.
    """
    if window is None:
        return None

    for role in (kAXTextAreaRole, kAXTextFieldRole):
        found = find_by_role(window, role, max_depth=_MAX_SEARCH_DEPTH)
        if found:
            return found[0]

    return None


def activate_window(window, timeout_ms: int = DEFAULT_ACTIVATION_TIMEOUT_MS) -> bool:
    """창을 최소한으로 활성화(앞으로 raise)한다.

    다른 조작(값 설정 등)이 창이 떠 있지 않은 상태에서 실패했을
    때에만 호출해야 하는 최후 수단이다. 가능한 한 앱 전체가 아니라
    해당 창만 raise하는 kAXRaiseAction을 먼저 시도하고, 실패하면
    AppleScript로 앱 전체를 활성화한다(더 침습적이므로 후순위).

    Args:
        window: 활성화할 채팅창 AXUIElement.
        timeout_ms: 활성화 직후 값 반영을 기다리는 시간(ms).

    Returns:
        활성화를 시도했고 성공했다고 판단되면 True.
    """
    if window is None:
        return False

    activated = perform_action(window, kAXRaiseAction)

    if not activated:
        try:
            script = f'tell application "{KAKAO_APP_NAME}" to activate'
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                timeout=3,
            )
            activated = result.returncode == 0
        except Exception:
            activated = False

    if activated and timeout_ms > 0:
        time.sleep(timeout_ms / 1000)

    return activated


def _set_input_text_via_applescript(input_area, text: str) -> bool:
    """최후 수단: 입력창에 포커스를 준 뒤 System Events로 키 입력을 보낸다.

    창이 이미 활성화되어 있어야 한다(호출 전 activate_window 필요).
    System Events 자동화 권한이 없으면 실패하며, 이 경우에도 예외를
    던지지 않고 False를 반환한다.
    """
    try:
        set_attr(input_area, kAXFocusedAttribute, True)
        time.sleep(0.05)
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{escaped}"'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=3)
        if result.returncode != 0:
            return False
        return get_attr(input_area, kAXValueAttribute) == text
    except Exception:
        return False


def set_input_text(window, text: str, raise_window: bool | str = "auto") -> bool:
    """입력창에 텍스트를 설정한다.

    Args:
        window: 채팅창 AXUIElement.
        text: 설정할 텍스트.
        raise_window:
            - "auto"(기본): 창을 띄우지 않고 시도 -> 실패 시 활성화 후
              재시도 -> 그래도 실패하면 AppleScript로 최후 시도.
            - False: 창을 절대 띄우지 않는다. 실패하면 그대로 False.
            - True: 곧바로 창을 활성화한 뒤 시도한다.

    Returns:
        성공 여부. 입력창을 찾지 못하면 False. 성공 판정은 값을 설정한
        뒤 실제로 반영되었는지 읽어서 확인한다(설정 API가 오류 없이
        반환해도 웹 기반 UI에서는 반영되지 않을 수 있기 때문).
    """
    input_area = find_input_area(window)
    if input_area is None:
        return False

    def _try_set_without_raise() -> bool:
        return set_attr(input_area, kAXValueAttribute, text) and get_attr(input_area, kAXValueAttribute) == text

    if raise_window is False:
        return _try_set_without_raise()

    if raise_window is True:
        activate_window(window)
        return _try_set_without_raise()

    # "auto": 3단계 폴백.
    if _try_set_without_raise():
        return True

    activate_window(window)
    if _try_set_without_raise():
        return True

    return _set_input_text_via_applescript(input_area, text)


def find_send_button(window) -> object | None:
    """채팅창의 전송 버튼을 찾는다.

    title 또는 description에 "전송"/"Send"가 포함된 AXButton을
    찾는다. 카톡 버전에 따라 버튼에 접근 가능한 이름이 없을 수 있어
    실패할 수 있다.
    """
    if window is None:
        return None

    buttons = find_by_role(window, kAXButtonRole, max_depth=_MAX_SEARCH_DEPTH)
    for button in buttons:
        title = get_attr(button, kAXTitleAttribute)
        description = get_attr(button, kAXDescriptionAttribute)
        for label in (title, description):
            if isinstance(label, str) and ("전송" in label or "Send" in label):
                return button

    return None


def _post_return_key() -> bool:
    """Return 키 입력을 시스템에 전달한다.

    전역(system-wide) 키 이벤트이므로, 대상 창에 실제로 키보드
    포커스가 있는 상태에서만 호출해야 한다 — 그렇지 않으면 엉뚱한
    앱에 Return 키가 전달될 수 있다. 이 함수 자체는 포커스를
    보장하지 않으므로 send_enter_key()를 통해서만 호출한다.

    Quartz CGEvent를 사용한다. 실패해도 예외를 던지지 않고 False를
    반환한다.
    """
    try:
        import Quartz

        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        key_down = Quartz.CGEventCreateKeyboardEvent(source, _RETURN_KEY_CODE, True)
        key_up = Quartz.CGEventCreateKeyboardEvent(source, _RETURN_KEY_CODE, False)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_up)
        return True
    except Exception:
        return False


def send_enter_key(window) -> bool:
    """채팅창의 입력창에 포커스를 준 뒤 Return 키 입력을 시뮬레이션한다.

    Return 키는 전역 키 이벤트이므로, 안전을 위해 이 함수는 항상
    먼저 창을 활성화(activate_window)한다 — 창이 활성화되지 않은
    채로 키 이벤트를 보내면 다른 앱에 입력될 위험이 있기 때문이다.

    Returns:
        성공 여부.
    """
    if window is None:
        return False

    activate_window(window)

    input_area = find_input_area(window)
    if input_area is not None:
        set_attr(input_area, kAXFocusedAttribute, True)

    return _post_return_key()


def send_current_input(window, raise_window: bool | str = "auto") -> bool:
    """입력창에 현재 들어 있는 텍스트를 전송한다.

    전송 버튼(AX 액션, 특정 요소만 대상으로 하므로 창을 띄우지 않고도
    시도 가능)을 우선 시도하고, 찾지 못하면 창을 활성화한 뒤 Return
    키를 시뮬레이션한다. 이 함수는 fail-closed 정책의 적용 대상이
    아니다 — 호출 여부(prepare/commit 승인 등)는 상위 도구
    계층(safety.py)의 책임이다.

    Args:
        window: 채팅창 AXUIElement.
        raise_window:
            - "auto"(기본): 버튼 클릭(창 안 띄움) -> 실패 시 활성화 후
              버튼 재시도 -> 그래도 실패하면 Return 키(항상 활성화됨).
            - False: 버튼 클릭만 시도한다. 실패해도 절대 창을 띄우거나
              전역 키 이벤트를 보내지 않는다.
            - True: 곧바로 창을 활성화한 뒤 버튼 -> Return 키 순으로
              시도한다.

    Returns:
        성공 여부.
    """
    if window is None:
        return False

    def _try_button() -> bool:
        send_button = find_send_button(window)
        return send_button is not None and perform_action(send_button, kAXPressAction)

    if raise_window is False:
        return _try_button()

    if raise_window is True:
        activate_window(window)
        if _try_button():
            return True
        return send_enter_key(window)

    # "auto": 3단계 폴백.
    if _try_button():
        return True

    activate_window(window)
    if _try_button():
        return True

    return send_enter_key(window)


def clear_input(window) -> bool:
    """입력창을 빈 문자열로 비운다. 창을 절대 띄우지 않는다."""
    return set_input_text(window, "", raise_window=False)
