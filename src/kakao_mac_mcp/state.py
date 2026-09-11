"""SQLite 기반 상태 저장 계층.

fingerprint 기반 중복 방지, baseline(과거 메시지 재생 금지), 승인
티켓(prepare -> commit), 이벤트 큐, 감사 로그를 관리한다.

절대 규칙: 이 모듈의 어떤 테이블에도 실제 카카오톡 방 제목을
저장하지 않는다. room_id / room_alias 컬럼에는 항상 사용자 지정
별칭만 저장해야 하며, 호출하는 쪽(도구 계층)이 실제 창 제목을
여기로 넘기지 않도록 책임진다.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = "state.db"

TICKET_STATUS_PREPARED = "prepared"
TICKET_STATUS_COMMITTED = "committed"
TICKET_STATUS_EXPIRED = "expired"
TICKET_STATUS_CANCELLED = "cancelled"
TICKET_STATUS_FAILED = "failed"

DEFAULT_TICKET_TTL_SECONDS = 300

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fingerprints (
    hash TEXT PRIMARY KEY,
    room_id TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS baselines (
    room_id TEXT PRIMARY KEY,
    baseline_at TEXT NOT NULL,
    last_fingerprint TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    room_id TEXT NOT NULL,
    text TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL,
    fingerprint TEXT,
    event_type TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    room_alias TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def init_db(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """SQLite DB를 초기화하고 연결을 반환한다.

    테이블이 없으면 생성한다 (idempotent). 여러 스레드에서 공유될 수
    있으므로 check_same_thread=False로 연다 — 동시 쓰기 직렬화는
    호출하는 쪽(MCP 서버)의 책임이다.

    Args:
        path: DB 파일 경로. ":memory:"도 허용된다.

    Returns:
        초기화된 sqlite3.Connection.
    """
    db_path = Path(path)
    if str(db_path) != ":memory:" and db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def is_duplicate(conn: sqlite3.Connection, room_id: str, fingerprint: str) -> bool:
    """해당 room_id에서 fingerprint가 이미 기록되어 있는지 확인한다."""
    cursor = conn.execute(
        "SELECT 1 FROM fingerprints WHERE hash = ? AND room_id = ?",
        (fingerprint, room_id),
    )
    return cursor.fetchone() is not None


def record_fingerprint(conn: sqlite3.Connection, room_id: str, fingerprint: str) -> None:
    """fingerprint를 기록한다. 이미 있으면 last_seen만 갱신한다."""
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO fingerprints (hash, room_id, first_seen, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(hash) DO UPDATE SET last_seen = excluded.last_seen
        """,
        (fingerprint, room_id, now, now),
    )
    conn.commit()


def get_baseline(conn: sqlite3.Connection, room_id: str) -> str | None:
    """room_id의 baseline fingerprint를 조회한다. 없으면 None."""
    cursor = conn.execute(
        "SELECT last_fingerprint FROM baselines WHERE room_id = ?",
        (room_id,),
    )
    row = cursor.fetchone()
    return row["last_fingerprint"] if row is not None else None


def set_baseline(conn: sqlite3.Connection, room_id: str, fingerprint: str | None) -> None:
    """room_id의 baseline을 설정/갱신한다.

    baseline은 "이 시점 이전 메시지는 재생하지 않음"의 기준점이다.
    """
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO baselines (room_id, baseline_at, last_fingerprint)
        VALUES (?, ?, ?)
        ON CONFLICT(room_id) DO UPDATE SET
            baseline_at = excluded.baseline_at,
            last_fingerprint = excluded.last_fingerprint
        """,
        (room_id, now, fingerprint),
    )
    conn.commit()


def create_ticket(
    conn: sqlite3.Connection,
    room_id: str,
    text: str,
    fingerprint: str,
    ttl_seconds: int = DEFAULT_TICKET_TTL_SECONDS,
) -> str:
    """전송 승인 티켓을 생성한다 (prepare 단계).

    Args:
        conn: DB 연결.
        room_id: 대상 방 별칭.
        text: 전송할 텍스트.
        fingerprint: 이 전송 시도의 fingerprint.
        ttl_seconds: 티켓 유효 시간(초). 음수/0이면 즉시 만료된
            티켓을 만든다 (테스트용).

    Returns:
        생성된 ticket_id.
    """
    ticket_id = uuid.uuid4().hex
    now = _now()
    expires_at = now + timedelta(seconds=ttl_seconds)
    conn.execute(
        """
        INSERT INTO tickets
            (ticket_id, room_id, text, fingerprint, status, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticket_id,
            room_id,
            text,
            fingerprint,
            TICKET_STATUS_PREPARED,
            now.isoformat(),
            expires_at.isoformat(),
        ),
    )
    conn.commit()
    return ticket_id


def get_ticket(conn: sqlite3.Connection, ticket_id: str) -> dict[str, Any] | None:
    """ticket_id로 티켓 정보를 조회한다. 없으면 None."""
    cursor = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,))
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def update_ticket_status(conn: sqlite3.Connection, ticket_id: str, status: str) -> None:
    """티켓 상태를 갱신한다 (예: committed/cancelled/expired)."""
    conn.execute(
        "UPDATE tickets SET status = ? WHERE ticket_id = ?",
        (status, ticket_id),
    )
    conn.commit()


def expire_old_tickets(conn: sqlite3.Connection) -> int:
    """유효기간이 지난 prepared 티켓을 expired 상태로 전환한다.

    Returns:
        전환된 티켓 개수.
    """
    now = _now_iso()
    cursor = conn.execute(
        """
        UPDATE tickets
        SET status = ?
        WHERE status = ? AND expires_at < ?
        """,
        (TICKET_STATUS_EXPIRED, TICKET_STATUS_PREPARED, now),
    )
    conn.commit()
    return cursor.rowcount


def add_event(
    conn: sqlite3.Connection,
    room_id: str,
    fingerprint: str | None,
    event_type: str,
    payload: dict[str, Any] | str | None = None,
) -> int:
    """이벤트 큐에 이벤트를 추가한다.

    Args:
        payload: dict이면 JSON 문자열로 직렬화하고, 문자열이면 그대로,
            None이면 NULL로 저장한다.

    Returns:
        생성된 이벤트의 id.
    """
    payload_text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload

    cursor = conn.execute(
        """
        INSERT INTO events (room_id, fingerprint, event_type, payload, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (room_id, fingerprint, event_type, payload_text, _now_iso()),
    )
    conn.commit()
    return cursor.lastrowid


def poll_events(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """가장 오래된 이벤트부터 최대 limit개를 가져오고 큐에서 제거한다.

    poll은 소비형(drain) 동작이다 — 같은 이벤트를 두 번 poll할 수
    없으며, 반환 후 즉시 DB에서 삭제된다.

    Returns:
        이벤트 dict 리스트 (오래된 순).
    """
    cursor = conn.execute("SELECT * FROM events ORDER BY id ASC LIMIT ?", (limit,))
    rows = [dict(row) for row in cursor.fetchall()]

    if rows:
        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids)
        conn.commit()

    return rows


def audit(conn: sqlite3.Connection, action: str, room_alias: str | None, detail: str = "") -> None:
    """감사 로그를 남긴다.

    room_alias에는 반드시 사용자 지정 별칭만 전달해야 한다 — 실제
    카카오톡 방 제목을 여기 넘기지 않는다.
    """
    conn.execute(
        """
        INSERT INTO audit_log (action, room_alias, detail, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (action, room_alias, detail, _now_iso()),
    )
    conn.commit()
