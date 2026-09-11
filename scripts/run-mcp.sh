#!/usr/bin/env bash
# kakao-mac-mcp MCP 서버를 stdio 트랜스포트로 실행한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

exec uv run python -m kakao_mac_mcp.server
