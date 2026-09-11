# kakaotalk-mac-mcp

macOS 카카오톡 앱을 MCP로 제어하는 로컬 브리지.
Windows용 Bum-Boo/kakaotalk-local-mcp의 설계 철학을 macOS에 이식한 프로젝트.

## 참고 프로젝트
https://github.com/Bum-Boo/kakaotalk-local-mcp
- 코드 복사 금지 (Windows 전용)
- 설계 철학만 계승:
  * fail-closed (기본 전송/자동답장 비활성)
  * 불투명 room_id (실제 방 제목 노출 금지)
  * fingerprint 기반 중복 방지
  * prepare → 승인 → commit → readback
  * 허용 목록 기반 접근
  * baseline 저장 (과거 메시지 재생 금지)
  * 유휴 시 AI 호출 금지

## 기술 스택
- Python 3.11+
- macOS 접근성 API (pyobjc-framework-ApplicationServices)
- MCP SDK (mcp)
- SQLite (상태 저장)
- uv (패키지 관리)
- pydantic v2 (설정 검증)

## 절대 규칙
1. 접근성 API만 사용. 사설 프로토콜/DB 직접 접근/OCR/인증정보 추출 금지.
2. send_enabled: false 기본. true여도 prepare/commit 분리 필수.
3. room_id는 사용자 지정 별칭. 실제 방 제목은 로그/DB에 저장 금지.
4. fingerprint로 중복 차단.
5. 새 도구는 src/kakao_mac_mcp/tools/ 에 파일 추가.
6. macOS 버전/카톡 버전 변경 시 AX 트리 구조가 바뀔 수 있음 → 방어적 코딩.
7. 개인정보를 로그에 남기지 않음.

## 도구 시그니처 (참고 프로젝트와 동일)
- kakao_health() -> {ok, send_enabled, allowed_rooms}
- kakao_allowed_rooms() -> [room_id, ...]
- kakao_read_room(room_id, limit=20) -> {messages: [{fingerprint, text, ...}]}
- kakao_observe_room(room_id) -> {baseline: bool, new_events: [...]}
- kakao_poll_events() -> [event, ...]
- kakao_prepare_reply(room_id, text) -> {ticket_id, fingerprint}
- kakao_commit_reply(ticket_id) -> {sent, readback}
- kakao_operation_status(ticket_id) -> {status}

## macOS 접근성 API 핵심
- AXUIElementCreateApplication(pid) → 앱 루트
- AXUIElementCopyAttributeValue(el, attr, None) → 속성 읽기
- AXUIElementSetAttributeValue(el, attr, value) → 값 쓰기
- AXUIElementPerformAction(el, action) → 버튼 클릭 등
- 필요 권한: 손쉬운 사용 (System Settings → Privacy & Security → Accessibility)

## 개발 순서
1. [검증] tests/test_accessibility.py — 카톡 AX 접근 가능한지
2. [기반] config.py, state.py, safety.py
3. [macos] accessibility.py, kakao_app.py, chat_window.py
4. [도구] health → rooms → observe → reply
5. [CLI] adopt-open-room, validate-config
6. [MCP] server.py
7. [테스트] pytest

## 하지 말 것
- 사설 프로토콜 구현 (로코 등)
- 카톡 DB 직접 파싱
- 세션/비밀번호 추출
- 승인 없는 자동 전송
- 실제 방 제목 로그 저장
