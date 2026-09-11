"""config.json 로더 및 검증 (pydantic v2).

이 모듈은 설정 파일을 읽어 pydantic 모델로 검증하고, `${VAR}` 형태의
환경변수 참조를 치환하며, 위험한 설정 조합(예: 자동 전송 활성화)을
경고로 잡아내는 역할을 한다.

fail-closed 철학에 따라 send_enabled / auto_reply_enabled /
schedule_automation_enabled는 모두 기본값 False다.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = "config.json"
EXAMPLE_CONFIG_PATH = "config.example.json"

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class RoomConfig(BaseModel):
    """허용된 방 하나에 대한 설정.

    room_id는 사용자 지정 별칭이며 실제 카카오톡 방 제목이 아니다.
    """

    room_id: str
    display_hint: str | None = None
    enabled: bool = True


class PrivacyConfig(BaseModel):
    """개인정보/로그 관련 설정."""

    log_actual_room_titles: bool = False
    log_message_content: bool = False


class LimitsConfig(BaseModel):
    """읽기/전송 한도 관련 설정."""

    max_messages_per_read: int = 50
    max_send_per_minute: int = 5
    fingerprint_retention_days: int = 30


class AppConfig(BaseModel):
    """전체 애플리케이션 설정.

    send_enabled / auto_reply_enabled / schedule_automation_enabled는
    모두 기본값 False (fail-closed).
    """

    adapter: str = "macos"
    send_enabled: bool = False
    auto_reply_enabled: bool = False
    schedule_automation_enabled: bool = False
    rooms: list[RoomConfig] = Field(default_factory=list)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    # 입력/전송 시 카카오톡 창을 화면 앞으로 띄울지 여부. 기본값은
    # 둘 다 False — 창을 띄우지 않는 방식(AX 값 직접 설정)을 우선
    # 시도하고, 실패했을 때만 상위 계층이 명시적으로 True를 넘겨
    # 활성화하도록 유도한다.
    raise_window_on_send: bool = False
    raise_window_on_input: bool = False
    activation_timeout_ms: int = 100


def resolve_env_vars(value: Any) -> Any:
    """`${VAR}` 형태의 환경변수 참조를 치환한다.

    문자열이 아닌 값은 그대로 반환한다. 환경변수가 정의되어 있지
    않으면 원본 `${VAR}` 문자열을 그대로 남긴다 (조용히 빈 문자열로
    바꾸지 않음 — 설정 오류를 숨기지 않기 위함).

    Args:
        value: 치환 대상 값 (문자열이 아니면 그대로 반환).

    Returns:
        치환된 문자열, 또는 원본 값.
    """
    if not isinstance(value, str):
        return value

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _resolve_env_vars_recursive(data: Any) -> Any:
    """중첩된 dict/list 구조를 순회하며 문자열 값에 환경변수 치환을 적용한다."""
    if isinstance(data, dict):
        return {key: _resolve_env_vars_recursive(val) for key, val in data.items()}
    if isinstance(data, list):
        return [_resolve_env_vars_recursive(item) for item in data]
    return resolve_env_vars(data)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """config.json을 읽어 검증된 AppConfig를 반환한다.

    파일이 없으면 config.example.json을 복사해서 사용하라는 안내와
    함께 FileNotFoundError를 발생시킨다 (기본값으로 조용히 대체하지
    않음 — 사용자가 명시적으로 설정을 확인하도록 유도).

    Args:
        path: config.json 경로.

    Returns:
        검증된 AppConfig 인스턴스.

    Raises:
        FileNotFoundError: 설정 파일이 없을 때.
        ValueError: JSON 파싱 또는 설정 검증에 실패했을 때.
    """
    config_path = Path(path)
    if not config_path.exists():
        example_path = config_path.parent / EXAMPLE_CONFIG_PATH
        raise FileNotFoundError(
            f"{config_path} 파일이 없습니다.\n"
            f"  cp {example_path} {config_path}\n"
            "명령으로 예제 설정을 복사한 뒤 필요한 값을 수정하세요."
        )

    try:
        raw_data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{config_path} 파싱 실패: {exc}") from exc

    resolved = _resolve_env_vars_recursive(raw_data)

    try:
        return AppConfig.model_validate(resolved)
    except Exception as exc:
        raise ValueError(f"{config_path} 검증 실패: {exc}") from exc


def save_config(config: AppConfig, path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    """AppConfig를 JSON 파일로 저장한다.

    Args:
        config: 저장할 설정.
        path: 저장할 경로.
    """
    config_path = Path(path)
    if config_path.parent and not config_path.parent.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)

    text = json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2)
    config_path.write_text(text + "\n", encoding="utf-8")


def validate_config(config: AppConfig) -> list[str]:
    """설정의 위험한 조합/오류를 검사해 경고 메시지 목록을 반환한다.

    빈 리스트는 문제 없음을 의미한다. 예외를 던지지 않고 항상
    사람이 읽을 수 있는 한국어 메시지 리스트를 반환한다.

    Args:
        config: 검사할 설정.

    Returns:
        경고/오류 메시지 리스트.
    """
    warnings: list[str] = []

    if config.send_enabled and config.auto_reply_enabled:
        warnings.append(
            "send_enabled와 auto_reply_enabled가 동시에 true입니다 — "
            "승인 없는 자동 전송이 발생할 수 있으니 신중히 검토하세요."
        )

    if config.limits.max_send_per_minute <= 0:
        warnings.append("limits.max_send_per_minute은 1 이상이어야 합니다.")

    if config.limits.max_messages_per_read <= 0:
        warnings.append("limits.max_messages_per_read은 1 이상이어야 합니다.")

    if config.limits.fingerprint_retention_days <= 0:
        warnings.append("limits.fingerprint_retention_days은 1 이상이어야 합니다.")

    room_ids = [room.room_id for room in config.rooms]
    if len(room_ids) != len(set(room_ids)):
        warnings.append("rooms에 중복된 room_id가 있습니다.")

    if any(not room.room_id.strip() for room in config.rooms):
        warnings.append("room_id가 비어 있는 항목이 있습니다.")

    if config.privacy.log_actual_room_titles:
        warnings.append(
            "privacy.log_actual_room_titles=true — 실제 방 제목이 로그에 남을 수 있어 "
            "프로젝트 규칙(실제 방 제목 로그/DB 저장 금지)에 위배됩니다."
        )

    if not config.rooms:
        warnings.append("허용된 방(rooms)이 비어 있습니다 — 모든 방 접근이 거부됩니다.")

    return warnings
