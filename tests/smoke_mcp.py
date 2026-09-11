"""MCP 서버 stdio 통합 스모크 테스트.

실제 kakao_mac_mcp.server를 서브프로세스로 띄우고 MCP 클라이언트로
접속해, 핵심 도구를 순서대로 호출하며 각 단계의 결과를 사람이 읽을
수 있는 한국어로 출력한다. 실패하면 어느 단계에서 실패했는지
명확히 표시한다.

이 스크립트는 카카오톡 창을 자동으로 활성화하지 않는다 —
kakao_read_room 단계는 허용된 방이 없거나 카톡 창이 프론트가 아니면
"스킵"으로 처리하고 실패로 취급하지 않는다.

실행:
    uv run python tests/smoke_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_WINDOW_NOT_FOUND_MARKER = "채팅창을 찾을 수 없습니다"


def _extract_result(result: types.CallToolResult) -> Any:
    """CallToolResult에서 실제 파이썬 값(dict/list/str)을 뽑아낸다.

    구조화 출력(structured_content)이 있으면 그대로 쓰고, 없으면
    텍스트 블록을 모아 JSON으로 파싱을 시도한다(도구가 dict/list를
    반환해도 텍스트 콘텐츠로만 직렬화되는 경우가 있음). list를
    반환하는 도구는 JSON 스키마 상 객체가 아니어서 MCP SDK가
    {"result": [...]}로 감싸는 경우가 있어, 그 형태면 벗겨낸다.
    """
    if result.structured_content is not None:
        value = result.structured_content
    else:
        texts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
        joined = "\n".join(texts)
        try:
            value = json.loads(joined)
        except (json.JSONDecodeError, TypeError):
            return joined

    if isinstance(value, dict) and set(value.keys()) == {"result"}:
        return value["result"]
    return value


async def run_smoke_test() -> int:
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "kakao_mac_mcp.server"],
        cwd=str(PROJECT_ROOT),
    )

    step = "서버 연결"
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("✅ [서버 연결] MCP 서버 stdio 연결 및 초기화 성공")

                # 1) kakao_health
                step = "kakao_health"
                result = await session.call_tool("kakao_health", {})
                health = _extract_result(result)
                if not isinstance(health, dict):
                    print(f"❌ [{step}] 예상치 못한 응답 형식: {health!r}")
                    return 1
                print(
                    f"✅ [{step}] ok={health.get('ok')} "
                    f"kakao_running={health.get('kakao_running')} "
                    f"accessibility_ok={health.get('accessibility_ok')} "
                    f"send_enabled={health.get('send_enabled')}"
                )

                # 2) kakao_allowed_rooms
                step = "kakao_allowed_rooms"
                result = await session.call_tool("kakao_allowed_rooms", {})
                rooms = _extract_result(result)
                if not isinstance(rooms, list):
                    print(f"❌ [{step}] 예상치 못한 응답 형식: {rooms!r}")
                    return 1
                print(f"✅ [{step}] 허용된 방 {len(rooms)}개")

                # 3) kakao_read_room - 허용된 방이 있을 때만 시도. 카톡 창이
                # 프론트가 아니어서 못 찾는 경우도 실패가 아니라 스킵이다.
                step = "kakao_read_room"
                if not rooms:
                    print(f"⏭️  [{step}] 허용된 방이 없어 스킵 (config.json에 room을 등록하면 실행됨)")
                else:
                    room_id = rooms[0]
                    result = await session.call_tool("kakao_read_room", {"room_id": room_id, "limit": 5})
                    read_result = _extract_result(result)
                    if not isinstance(read_result, dict):
                        print(f"❌ [{step}] 예상치 못한 응답 형식: {read_result!r}")
                        return 1
                    if "error" in read_result:
                        error_msg = read_result["error"]
                        if _WINDOW_NOT_FOUND_MARKER in error_msg:
                            print(f"⏭️  [{step}] 카톡 창이 프론트가 아니거나 방을 못 찾아 스킵: {error_msg}")
                        else:
                            print(f"❌ [{step}] 오류: {error_msg}")
                            return 1
                    else:
                        print(f"✅ [{step}] 메시지 {read_result.get('count')}개 읽음")

                # 4) kakao_prepare_reply - send_enabled=False면 거부되어야 함
                step = "kakao_prepare_reply"
                target_room = rooms[0] if rooms else "smoke-test-room"
                result = await session.call_tool(
                    "kakao_prepare_reply",
                    {"room_id": target_room, "text": "스모크 테스트 메시지"},
                )
                prepare_result = _extract_result(result)
                if not isinstance(prepare_result, dict):
                    print(f"❌ [{step}] 예상치 못한 응답 형식: {prepare_result!r}")
                    return 1

                if not health.get("send_enabled"):
                    if "error" in prepare_result:
                        print(f"✅ [{step}] send_enabled=false 상태에서 올바르게 거부됨: {prepare_result['error']}")
                    else:
                        print(f"❌ [{step}] send_enabled=false인데도 거부되지 않았습니다: {prepare_result}")
                        return 1
                else:
                    print(
                        f"⚠️  [{step}] send_enabled=true 상태라 이 스크립트로는 거부 여부를 "
                        f"확인할 수 없습니다 (결과: {prepare_result})"
                    )

    except Exception as exc:
        print(f"❌ [{step}] 예외 발생: {exc!r}")
        return 1

    print("\n🎉 스모크 테스트 완료 (모든 단계 통과 또는 정상 스킵).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run_smoke_test()))
