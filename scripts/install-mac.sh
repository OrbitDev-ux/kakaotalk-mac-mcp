#!/usr/bin/env bash
# kakao-mac-mcp macOS 설치 스크립트.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "== kakao-mac-mcp 설치 =="

if ! command -v uv >/dev/null 2>&1; then
    echo "❌ uv가 설치되어 있지 않습니다."
    echo "   설치: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo "✅ uv 확인됨: $(uv --version)"

echo "-- 의존성 설치 (uv sync) --"
cd "${PROJECT_ROOT}"
uv sync

echo "-- scripts/*.sh 실행 권한 부여 --"
chmod +x "${SCRIPT_DIR}"/*.sh

echo ""
echo "== 다음 단계: 손쉬운 사용(접근성) 권한 부여 =="
echo "1. 시스템 설정 → 개인정보 보호 및 보안 → 손쉬운 사용"
echo "2. 이 명령을 실행하는 터미널(또는 Claude Code가 실행 중인 앱)을 허용 목록에 추가"
echo "3. 카카오톡 앱을 실행하고 채팅방을 하나 열어둔 뒤 다음을 실행해 확인:"
echo "     uv run python tests/test_accessibility.py"
echo ""
echo "설정 파일이 아직 없다면:"
echo "     uv run python -m kakao_mac_mcp.cli init-config"
echo ""
echo "✅ 설치 스크립트 완료."
