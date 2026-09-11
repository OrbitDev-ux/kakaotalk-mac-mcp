"""kakao-mac-mcp CLI.

서브커맨드:
    adopt-open-room --room-id <alias>   현재 열린 카톡 창을 별칭으로 등록
    validate-config                     config.json 검증
    list-rooms                          허용된 room_id 목록 출력
    health                              카카오톡/설정 상태 점검
    init-config                         config.example.json을 config.json으로 복사

각 서브커맨드는 --config로 설정 파일 경로를 지정할 수 있다(기본
./config.json). 이 CLI는 카카오톡 창을 자동으로 활성화하지 않는다
(adopt-open-room도 현재 이미 보이는 창만 사용한다).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from kakao_mac_mcp.config import (
    DEFAULT_CONFIG_PATH,
    EXAMPLE_CONFIG_PATH,
    RoomConfig,
    load_config,
    save_config,
    validate_config,
)
from kakao_mac_mcp.macos.accessibility import get_attr
from kakao_mac_mcp.macos.kakao_app import get_windows, is_kakao_running
from kakao_mac_mcp.tools.health import kakao_health
from ApplicationServices import kAXTitleAttribute


def _add_config_option(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="config.json 경로 (기본: ./config.json)",
    )


def _cmd_init_config(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    example_path = Path(EXAMPLE_CONFIG_PATH)

    if config_path.exists():
        print(f"⚠️  {config_path}가 이미 존재합니다. 덮어쓰지 않습니다.")
        return 1

    if not example_path.exists():
        print(f"❌ {example_path}를 찾을 수 없습니다.")
        return 1

    shutil.copyfile(example_path, config_path)
    print(f"✅ {example_path} -> {config_path} 복사 완료. 필요한 값을 수정하세요.")
    return 0


def _cmd_validate_config(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"❌ {exc}")
        return 1

    warnings = validate_config(config)
    if not warnings:
        print("✅ 설정에 문제가 없습니다.")
        return 0

    print(f"⚠️  경고 {len(warnings)}건:")
    for warning in warnings:
        print(f"  - {warning}")
    return 1


def _cmd_list_rooms(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"❌ {exc}")
        return 1

    enabled_rooms = [room.room_id for room in config.rooms if room.enabled]
    if not enabled_rooms:
        print("등록된 방이 없습니다.")
        return 0

    print("허용된 방 (room_id):")
    for room_id in enabled_rooms:
        print(f"  - {room_id}")
    return 0


def _cmd_health(args: argparse.Namespace) -> int:
    result = kakao_health(config_path=args.config)

    print(f"카카오톡 실행 중: {'예' if result['kakao_running'] else '아니오'}")
    print(f"접근성 권한: {'있음' if result['accessibility_ok'] else '없음'}")
    print(f"send_enabled: {result['send_enabled']}")
    print(f"auto_reply_enabled: {result['auto_reply_enabled']}")
    print(f"허용된 방 개수: {len(result['allowed_rooms'])}")
    print(f"config 경로: {result['config_path']}")
    print(f"state.db 경로: {result['state_db_path']}")
    print()
    print("✅ 정상" if result["ok"] else "⚠️  일부 항목 확인이 필요합니다 (카톡 미실행/권한 없음/설정 오류 등).")
    return 0 if result["ok"] else 1


def _cmd_adopt_open_room(args: argparse.Namespace) -> int:
    config_path = Path(args.config)

    if not is_kakao_running():
        print("❌ 카카오톡이 실행 중이 아닙니다.")
        return 1

    windows = get_windows()
    if len(windows) == 0:
        print("❌ 열린 카카오톡 창이 없습니다. 등록할 방 하나만 열어두고 다시 시도하세요.")
        return 1
    if len(windows) > 1:
        print(f"❌ 열린 카카오톡 창이 {len(windows)}개입니다. 등록하려는 방 하나만 남기고 나머지는 닫은 뒤 다시 시도하세요.")
        return 1

    title = get_attr(windows[0], kAXTitleAttribute)
    if not isinstance(title, str) or not title.strip():
        print("❌ 창 제목을 읽지 못했습니다.")
        return 1

    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(f"❌ {config_path}가 없습니다. 먼저 'init-config'를 실행하세요.")
        return 1
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1

    if any(room.room_id == args.room_id for room in config.rooms):
        print(f"❌ 이미 등록된 room_id입니다: {args.room_id}")
        return 1

    # room_id는 사용자 지정 별칭이며 이후 로그/DB에는 이 별칭만 남는다.
    # display_hint에는 창을 다시 찾기 위해 실제 창 제목을 저장하는데,
    # 이는 사용자가 직접 관리하는 config.json 안에서만 쓰인다 —
    # state.db나 로그에는 절대 기록되지 않는다.
    config.rooms.append(RoomConfig(room_id=args.room_id, display_hint=title, enabled=True))
    save_config(config, config_path)

    print(f"✅ room_id '{args.room_id}' 등록 완료 (창 제목을 {config_path}의 display_hint로 저장함).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kakao-mac-mcp", description="카카오톡 macOS MCP 브리지 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    adopt = subparsers.add_parser("adopt-open-room", help="현재 열린 카톡 창을 room_id로 등록 (정확히 1개여야 함)")
    adopt.add_argument("--room-id", required=True, help="등록할 방 별칭")
    _add_config_option(adopt)
    adopt.set_defaults(func=_cmd_adopt_open_room)

    validate = subparsers.add_parser("validate-config", help="config.json 검증")
    _add_config_option(validate)
    validate.set_defaults(func=_cmd_validate_config)

    list_rooms = subparsers.add_parser("list-rooms", help="허용된 room_id 목록 출력")
    _add_config_option(list_rooms)
    list_rooms.set_defaults(func=_cmd_list_rooms)

    health = subparsers.add_parser("health", help="카카오톡/설정 상태 점검")
    _add_config_option(health)
    health.set_defaults(func=_cmd_health)

    init_config = subparsers.add_parser("init-config", help="config.example.json을 config.json으로 복사")
    _add_config_option(init_config)
    init_config.set_defaults(func=_cmd_init_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
