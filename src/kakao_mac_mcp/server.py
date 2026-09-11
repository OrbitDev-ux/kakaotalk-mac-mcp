"""MCP 서버 진입점 및 도구 등록.

각 도구는 예외를 밖으로 던지지 않고 항상 JSON 직렬화 가능한 값을
반환한다. 실패 상황은 {"error": "..."} 형태로 표현한다 (허용 목록
위반, 채팅창 없음, 설정 로드 실패 등).

주의: stdio 트랜스포트에서는 stdout이 JSON-RPC 채널이므로, 이
파일의 모든 사람이 읽는 로그는 반드시 stderr로 출력한다.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from kakao_mac_mcp.config import DEFAULT_CONFIG_PATH, EXAMPLE_CONFIG_PATH, load_config
from kakao_mac_mcp.state import expire_old_tickets
from kakao_mac_mcp.tools.common import DEFAULT_READ_LIMIT, close_conn, get_conn
from kakao_mac_mcp.tools.health import kakao_health as _kakao_health
from kakao_mac_mcp.tools.observe import (
    DEFAULT_POLL_LIMIT,
    kakao_observe_room as _kakao_observe_room,
    kakao_poll_events as _kakao_poll_events,
)
from kakao_mac_mcp.tools.reply import (
    kakao_commit_reply as _kakao_commit_reply,
    kakao_operation_status as _kakao_operation_status,
    kakao_prepare_reply as _kakao_prepare_reply,
)
from kakao_mac_mcp.tools.rooms import (
    kakao_allowed_rooms as _kakao_allowed_rooms,
    kakao_read_room as _kakao_read_room,
)


@asynccontextmanager
async def _lifespan(app: MCPServer) -> AsyncIterator[None]:
    """서버 시작/종료 시 상태 DB를 초기화/정리한다.

    시작: state.db를 초기화한다(테이블이 없으면 생성 - idempotent).
    종료: 만료된 티켓을 정리하고 DB 연결을 닫는다.
    """
    get_conn()  # state.db 초기화
    try:
        yield None
    finally:
        expired = expire_old_tickets(get_conn())
        if expired:
            print(f"만료된 티켓 {expired}개 정리함.", file=sys.stderr)
        close_conn()


server = MCPServer(name="kakao-mac-mcp", lifespan=_lifespan)


@server.tool(name="kakao_health")
def kakao_health_tool() -> dict:
    """카카오톡 연결/설정 상태를 점검한다. 메시지는 읽지 않는다."""
    return _kakao_health()


@server.tool(name="kakao_allowed_rooms")
def kakao_allowed_rooms_tool() -> list[str]:
    """설정에서 허용된(enabled=True) room_id 목록을 반환한다."""
    return _kakao_allowed_rooms()


@server.tool(name="kakao_read_room")
def kakao_read_room_tool(room_id: str, limit: int = DEFAULT_READ_LIMIT) -> dict:
    """허용된 방의 최근 메시지를 읽는다. 실제 방 제목은 포함하지 않는다."""
    return _kakao_read_room(room_id, limit)


@server.tool(name="kakao_observe_room")
def kakao_observe_room_tool(room_id: str) -> dict:
    """방의 새 메시지를 감지한다. 최초 호출은 baseline만 저장한다."""
    return _kakao_observe_room(room_id)


@server.tool(name="kakao_poll_events")
def kakao_poll_events_tool(limit: int = DEFAULT_POLL_LIMIT) -> list[dict]:
    """이벤트 큐에서 미처리 이벤트를 꺼낸다 (소비형)."""
    return _kakao_poll_events(limit)


@server.tool(name="kakao_prepare_reply")
def kakao_prepare_reply_tool(room_id: str, text: str) -> dict:
    """답장을 준비한다 (승인 대기 티켓 생성). 실제로 전송하지 않는다."""
    return _kakao_prepare_reply(room_id, text)


@server.tool(name="kakao_commit_reply")
def kakao_commit_reply_tool(ticket_id: str) -> dict:
    """준비된 답장을 실제로 전송한다."""
    return _kakao_commit_reply(ticket_id)


@server.tool(name="kakao_operation_status")
def kakao_operation_status_tool(ticket_id: str) -> dict:
    """티켓 상태를 조회한다."""
    return _kakao_operation_status(ticket_id)


def main() -> None:
    """설정을 검증한 뒤 stdio 트랜스포트로 MCP 서버를 실행한다.

    config.json이 없거나 검증에 실패하면 사람이 읽을 수 있는 안내와
    함께 종료 코드 1로 종료한다 (fail-closed — 설정 없이 조용히
    기본값으로 뜨지 않는다).
    """
    config_path = Path(DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        example_path = config_path.parent / EXAMPLE_CONFIG_PATH
        print(
            f"❌ {config_path} 파일이 없습니다.\n"
            f"  cp {example_path} {config_path}\n"
            "명령으로 예제 설정을 복사한 뒤 필요한 값을 수정하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        config = load_config(config_path)
    except ValueError as exc:
        print(f"❌ 설정 오류: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"kakaotalk-mac-mcp 시작. send_enabled={config.send_enabled}", file=sys.stderr)
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
