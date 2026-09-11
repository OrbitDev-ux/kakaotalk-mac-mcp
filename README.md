# kakao-mac-mcp

## 개요

macOS 카카오톡 데스크톱 앱을 macOS 접근성 API(Accessibility API)로
제어하는 로컬 MCP(Model Context Protocol) 브리지다. 사설 프로토콜이나
카카오톡 DB에 직접 접근하지 않고, 화면에 보이는 AX(UI Automation)
트리만 읽고 조작한다. Claude Code 같은 MCP 클라이언트에 연결하면,
사용자가 명시적으로 허용한 채팅방만 읽고, 승인 절차를 거쳐서만
답장을 보낼 수 있다.

Windows용 [Bum-Boo/kakaotalk-local-mcp](https://github.com/Bum-Boo/kakaotalk-local-mcp)의
설계 철학(fail-closed, prepare/commit 승인, 불투명 room_id, baseline
기반 재생 금지, fingerprint 중복 방지)을 macOS로 이식한 프로젝트다.
코드는 이식하지 않고 설계 철학만 계승했다.

## 설치 방법

```bash
git clone <this-repo>
cd kakaotalk-mac-mcp
bash scripts/install-mac.sh   # uv 설치 확인 + uv sync + 스크립트 실행 권한 부여
uv run python -m kakao_mac_mcp.cli init-config   # config.example.json -> config.json
```

## 손쉬운 사용 권한 부여

이 프로젝트는 macOS 접근성 API로 카카오톡 창을 읽으므로, 이 명령을
실행하는 프로그램(터미널, Claude Code 등)에 손쉬운 사용 권한을
부여해야 한다.

1. **시스템 설정 → 개인정보 보호 및 보안 → 손쉬운 사용**으로 이동한다.
2. 터미널(또는 Claude Code가 실행 중인 앱)을 목록에서 찾아 켠다.
   찾지 못하면 `+` 버튼으로 직접 추가한다.

> _(스크린샷 자리: 시스템 설정 → 개인정보 보호 및 보안 → 손쉬운 사용
> 화면에서 터미널이 체크된 상태)_

권한이 제대로 부여됐는지는 다음으로 확인한다:

```bash
uv run python tests/test_accessibility.py
```

## config.json 작성법

```bash
uv run python -m kakao_mac_mcp.cli init-config
```

로 `config.example.json`을 `config.json`으로 복사한 뒤 편집한다.

```jsonc
{
  "adapter": "macos",
  "send_enabled": false,          // true로 바꿔야 실제 전송이 가능함 (기본 false)
  "auto_reply_enabled": false,    // 자동 답장 기능 (기본 false, 별도 상위 기능)
  "schedule_automation_enabled": false,
  "rooms": [
    { "room_id": "친구1", "display_hint": "실제 창 제목 일부", "enabled": true }
  ],
  "privacy": {
    "log_actual_room_titles": false,
    "log_message_content": false
  },
  "limits": {
    "max_messages_per_read": 50,
    "max_send_per_minute": 5,
    "fingerprint_retention_days": 30
  },
  "raise_window_on_send": false,   // 전송 시 카톡 창을 띄울지
  "raise_window_on_input": false,  // 입력 시 카톡 창을 띄울지
  "activation_timeout_ms": 100
}
```

- `room_id`는 **사용자가 직접 정하는 별칭**이다. MCP 도구를 쓰는
  쪽(예: Claude Code)에는 이 별칭만 노출되고, 실제 카카오톡 방
  제목은 로그나 `state.db`에 저장되지 않는다.
- `display_hint`는 실제 창 제목(또는 그 일부 문자열)이다. 창을
  찾는 데에만 쓰이며, `config.json`(사용자가 직접 관리하는 로컬
  파일) 안에만 존재한다 — 방을 등록할 때 이 값을 자동으로 채워주는
  `adopt-open-room` 명령도 있다:

  ```bash
  # 등록하려는 채팅방 하나만 열어둔 상태에서 실행
  uv run python -m kakao_mac_mcp.cli adopt-open-room --room-id 친구1
  ```

- `send_enabled: false`가 기본값이며, 이 상태에서는 어떤 방법으로도
  실제 전송이 일어나지 않는다.

## Claude Code 연결

```bash
claude mcp add kakaotalk-mac -- \
  bash $(pwd)/scripts/run-mcp.sh
```

등록 확인:

```bash
claude mcp list | grep kakao
```

## 제공 도구 (8개)

| 도구 | 설명 |
| --- | --- |
| `kakao_health` | 카카오톡 실행/접근성 권한/설정 상태 점검 (읽기 없음) |
| `kakao_allowed_rooms` | 허용된 room_id 목록 |
| `kakao_read_room` | 방의 최근 메시지 읽기 |
| `kakao_observe_room` | 방의 새 메시지 감지 (baseline 기반, 재생 금지) |
| `kakao_poll_events` | 이벤트 큐 폴링 (소비형) |
| `kakao_prepare_reply` | 답장 준비 (승인 대기 티켓 생성, 미전송) |
| `kakao_commit_reply` | 준비된 답장 실제 전송 |
| `kakao_operation_status` | 티켓 상태 조회 |

## 사용 예

Claude Code에 연결한 뒤 자연어로 요청하면 된다:

```
"kakao_health 호출해줘"
→ 카카오톡 실행 여부, 접근성 권한, send_enabled 등을 확인

"친구1 방 최근 메시지 10개 읽어줘"
→ kakao_read_room(room_id="친구1", limit=10)

"친구1 방에 '지금 회의 중이라 이따 연락할게' 보내줘"
→ kakao_prepare_reply로 티켓 생성 -> 사용자 승인 -> kakao_commit_reply로 전송
   (send_enabled=false이면 prepare 단계에서부터 거부됨)
```

## 안전 원칙 (fail-closed)

- `send_enabled`, `auto_reply_enabled`, `schedule_automation_enabled`는
  모두 기본값 `false`. 명시적으로 켜지 않으면 전송/자동화가 절대
  일어나지 않는다.
- 전송은 반드시 `kakao_prepare_reply`(승인 대기 티켓 생성) →
  `kakao_commit_reply`(실제 전송) 2단계를 거친다. 자동 재시도 없음.
  `commit` 시점에 방에 새 메시지가 도착해 맥락이 바뀌었으면 전송을
  거부한다.
- `room_id`(room_alias)는 사용자가 지정한 별칭이며, 실제 카카오톡
  창 제목과 발신자 실명은 `state.db`나 로그에 절대 저장하지 않는다
  (발신자는 방 범위 해시 별칭으로 마스킹, `config.json`의
  `display_hint`만 예외).
- 메시지는 SHA-256 fingerprint로 중복을 판정한다.
  `kakao_observe_room`은 baseline 이전 메시지를 이벤트로 재생하지
  않는다(최초 호출은 baseline만 저장).

## 제약사항: 카카오톡이 프론트(활성) 앱일 때만 읽기/조작 가능

이 카카오톡 빌드의 접근성 서버는 앱이 실제로 프론트(활성) 상태일
때만 창 목록(`kAXWindowsAttribute`)을 안정적으로 보고한다. 카카오톡이
백그라운드에 있으면 읽기 도구도 빈 창 목록을 받아 "채팅창을 찾을 수
없습니다" 오류를 반환한다 — 버그가 아니라 이 환경의 실제 동작이다.

- 운영 중에는 **카카오톡 창을 항상 화면에 띄워둘 것** (최소화 금지).
- 다른 작업과 병행하려면 macOS **Stage Manager**나 **별도의 Space**로
  카카오톡을 그 자리에 프론트 상태로 유지하는 방법을 권장한다.
- 반대로 입력/전송 경로(`set_input_text` / `send_current_input`)는
  기본적으로 창을 띄우지 않고 동작을 시도한다 — 읽기 제약과 전송 시
  화면 방해 문제는 서로 다른 이슈다.

## 문제 해결

| 증상 | 원인/조치 |
| --- | --- |
| `kakao_health`의 `accessibility_ok`가 `false` | 손쉬운 사용 권한 미부여. 시스템 설정에서 권한을 켠 뒤 터미널/Claude Code를 재시작 |
| `kakao_health`의 `kakao_running`이 `false` | 카카오톡이 실행 중이 아님. 카카오톡을 실행 |
| `"채팅창을 찾을 수 없습니다"` 오류 | 카카오톡이 프론트 앱이 아니거나, 해당 방이 열려 있지 않음. 방을 열고 카카오톡을 화면 앞으로 가져올 것 |
| `config.json 파일이 없습니다` | `uv run python -m kakao_mac_mcp.cli init-config` 실행 |
| `send_enabled=false` 때문에 전송이 안 됨 | 의도된 기본 동작. 진짜로 전송하려면 `config.json`에서 `send_enabled: true`로 변경 (신중히) |
| `kakao mcp list`에 안 보임 | `claude mcp add` 명령을 다시 실행했는지, 오타가 없는지 확인 |
| 종합 진단이 필요할 때 | `bash scripts/doctor.sh` 실행 (카톡 창을 건드리지 않음) |

## TODO (알려진 한계)

- `sender` / `sender_alias`가 카톡 버전에 따라 정확히 추출되지 않을
  수 있음 (예: 1:1 채팅에서 프로필 버튼의 접근성 설명이 실제 이름
  대신 "프로필" 같은 일반 레이블인 경우 — 현재는 의도적으로 `None`
  처리).
- 단체 채팅방에서 발신자 이름이 메시지와 별도의 헤더 행으로
  렌더링되는 경우, 그 헤더 행이 메시지 후보에 섞여 들어가 본문 대신
  이름이 읽힐 수 있음. 헤더 행 필터링 로직은 아직 없음.

## 개발

```bash
uv sync --extra dev
uv run pytest -v                       # 유닛/통합 테스트
uv run python tests/smoke_mcp.py       # 실제 stdio MCP 서버 스모크 테스트
```

카카오톡 창이 실제로 열려 있어야 하는 라이브 테스트는 창이 안 보이면
자동으로 스킵되며, 테스트 스위트 자체는 카카오톡을 화면 앞으로
띄우지 않는다.

수동 검증 스크립트(카카오톡 창을 미리 열어 둔 상태에서 직접 실행):

```bash
uv run python tests/test_accessibility.py   # 접근성 API 접근 가능 여부
uv run python tests/manual_read_chat.py     # 첫 번째 창의 최근 메시지 5개 출력
uv run python tests/manual_test_focus.py    # 창을 안 띄우고 입력/전송 준비가 되는지 확인
```

## 라이선스

MIT

## 참고

- [Bum-Boo/kakaotalk-local-mcp](https://github.com/Bum-Boo/kakaotalk-local-mcp) — Windows용 원본 프로젝트, 설계 철학의 출처 (코드는 이식하지 않음)
