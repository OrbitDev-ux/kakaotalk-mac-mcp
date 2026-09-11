"""macOS 접근성 API(AX API) 저수준 래퍼.

이 모듈은 PyObjC의 ApplicationServices 프레임워크를 감싸서
AXUIElement 관련 호출이 실패하거나 예외를 던지더라도 상위 로직이
죽지 않도록 방어적으로 처리한다.

macOS/카카오톡 버전이 바뀌면 AX 트리 구조(role, title 등)가 달라질 수
있으므로, 여기서는 항상 None/빈 값을 반환할 뿐 예외를 전파하지 않는다.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
    kAXChildrenAttribute,
    kAXRoleAttribute,
    kAXTitleAttribute,
)

# kAXErrorSuccess는 ApplicationServices에서 0으로 정의되어 있다.
_AX_ERROR_SUCCESS = 0


def is_trusted() -> bool:
    """현재 프로세스(또는 상위 터미널/앱)가 접근성 권한을 부여받았는지 확인한다.

    Returns:
        권한이 있으면 True. 확인 자체에 실패하면 안전 측면에서 False.
    """
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        return False


def get_attr(element: Any, attr_name: str) -> Any | None:
    """AXUIElement의 속성 값을 안전하게 읽는다.

    Args:
        element: AXUIElementRef.
        attr_name: 예) kAXTitleAttribute, kAXRoleAttribute 등.

    Returns:
        속성 값. 요소가 None이거나 읽기에 실패하면 None.
    """
    if element is None:
        return None
    try:
        error, value = AXUIElementCopyAttributeValue(element, attr_name, None)
    except Exception:
        return None
    if error != _AX_ERROR_SUCCESS:
        return None
    return value


def set_attr(element: Any, attr_name: str, value: Any) -> bool:
    """AXUIElement의 속성 값을 안전하게 설정한다.

    Args:
        element: AXUIElementRef.
        attr_name: 설정할 속성 이름.
        value: 설정할 값.

    Returns:
        성공 여부.
    """
    if element is None:
        return False
    try:
        error = AXUIElementSetAttributeValue(element, attr_name, value)
    except Exception:
        return False
    return error == _AX_ERROR_SUCCESS


def perform_action(element: Any, action: str) -> bool:
    """AXUIElement에 액션(버튼 클릭 등)을 수행한다.

    Args:
        element: AXUIElementRef.
        action: 예) kAXPressAction.

    Returns:
        성공 여부.
    """
    if element is None:
        return False
    try:
        error = AXUIElementPerformAction(element, action)
    except Exception:
        return False
    return error == _AX_ERROR_SUCCESS


def get_children(element: Any) -> list:
    """AXUIElement의 자식 목록을 안전하게 가져온다.

    Args:
        element: AXUIElementRef.

    Returns:
        자식 요소 리스트. 실패 시 빈 리스트.
    """
    children = get_attr(element, kAXChildrenAttribute)
    if children is None:
        return []
    try:
        return list(children)
    except Exception:
        return []


def find_by_role(element: Any, role: str, max_depth: int = 5) -> list:
    """특정 role을 가진 자손 요소를 BFS로 검색한다.

    Args:
        element: 검색을 시작할 루트 요소.
        role: 예) "AXWindow", "AXStaticText" 등 (kAXRoleAttribute 값).
        max_depth: 루트로부터 탐색할 최대 깊이.

    Returns:
        role이 일치하는 요소 리스트 (발견 순서 유지).
    """
    if element is None:
        return []

    # 방문 여부를 id(element)로 추적하지 않는다 — PyObjC가 매 호출마다
    # 새 래퍼 객체를 만들기 때문에 짧게 살다 GC된 래퍼의 메모리
    # 주소가 재사용되면 서로 다른 실제 AX 요소가 "이미 방문함"으로
    # 오판되어 하위 트리 탐색이 조기 중단되는 버그가 있었다. AX
    # 트리는 조상으로 되돌아가는 순환 참조가 없으므로 max_depth만으로
    # 무한 루프를 막기에 충분하다.
    results: list = []
    queue: deque[tuple[Any, int]] = deque([(element, 0)])

    while queue:
        current, depth = queue.popleft()
        if current is None:
            continue

        current_role = get_attr(current, kAXRoleAttribute)
        if current_role == role:
            results.append(current)

        if depth >= max_depth:
            continue

        for child in get_children(current):
            queue.append((child, depth + 1))

    return results


def find_by_title(element: Any, title_substring: str, max_depth: int = 5) -> list:
    """title 속성에 부분 문자열을 포함하는 자손 요소를 BFS로 검색한다.

    Args:
        element: 검색을 시작할 루트 요소.
        title_substring: 찾고자 하는 부분 문자열.
        max_depth: 루트로부터 탐색할 최대 깊이.

    Returns:
        title이 조건을 만족하는 요소 리스트 (발견 순서 유지).
    """
    if element is None or not title_substring:
        return []

    # find_by_role()과 동일한 이유로 id() 기반 방문 추적을 하지 않는다.
    results: list = []
    queue: deque[tuple[Any, int]] = deque([(element, 0)])

    while queue:
        current, depth = queue.popleft()
        if current is None:
            continue

        current_title = get_attr(current, kAXTitleAttribute)
        if isinstance(current_title, str) and title_substring in current_title:
            results.append(current)

        if depth >= max_depth:
            continue

        for child in get_children(current):
            queue.append((child, depth + 1))

    return results
