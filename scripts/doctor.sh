#!/usr/bin/env bash
# kakao-mac-mcp 진단 스크립트. 카카오톡 창을 활성화하지 않는다.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

echo "== kakao-mac-mcp 진단 =="

overall_ok=1

# 1) 카카오톡 실행 여부
if pgrep -f KakaoTalk >/dev/null 2>&1; then
    echo "✅ 카카오톡 실행 중"
else
    echo "❌ 카카오톡이 실행 중이 아님 - 카카오톡을 실행하세요"
    overall_ok=0
fi

# 2) 접근성 권한 (창을 건드리지 않고 AXIsProcessTrusted()만 확인)
trusted="$(uv run python -c "from kakao_mac_mcp.macos.accessibility import is_trusted; print(is_trusted())" 2>/dev/null)"
if [ "${trusted}" = "True" ]; then
    echo "✅ 접근성 권한 있음"
else
    echo "❌ 접근성 권한 없음 - 시스템 설정 → 개인정보 보호 및 보안 → 손쉬운 사용에서 허용하세요"
    overall_ok=0
fi

# 3) config.json 존재 여부
if [ -f "${PROJECT_ROOT}/config.json" ]; then
    echo "✅ config.json 존재함"
else
    echo "❌ config.json 없음 - 'uv run python -m kakao_mac_mcp.cli init-config' 실행하세요"
    overall_ok=0
fi

# 4) state.db 접근 가능 여부
db_ok="$(uv run python -c "
from kakao_mac_mcp.state import init_db
try:
    conn = init_db('state.db')
    conn.execute('SELECT 1')
    conn.close()
    print('True')
except Exception:
    print('False')
" 2>/dev/null)"
if [ "${db_ok}" = "True" ]; then
    echo "✅ state.db 접근 가능"
else
    echo "❌ state.db 접근 불가"
    overall_ok=0
fi

echo ""
if [ "${overall_ok}" = "1" ]; then
    echo "🎉 모든 진단 항목 통과."
    exit 0
else
    echo "⚠️  위 항목을 확인한 뒤 다시 시도하세요."
    exit 1
fi
