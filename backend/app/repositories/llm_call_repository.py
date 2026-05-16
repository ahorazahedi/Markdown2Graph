"""SQLite repository for LLM call audit logs.

Schema is created on first connection. Single file, no external service.
Thread-safe via per-connection short-lived sessions; concurrent writes are
serialized by sqlite's default WAL journal.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from ..config import get_settings

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve_db_path(raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _REPO_ROOT / p
    return p


_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,
    finished_at     TEXT,
    tag             TEXT    NOT NULL,
    model           TEXT,
    base_url        TEXT,
    provider        TEXT,
    status          TEXT    NOT NULL,            -- pending | success | error
    latency_ms      INTEGER,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    request_json    TEXT    NOT NULL,            -- serialized prompts/messages
    response_text   TEXT,
    response_json   TEXT,
    error           TEXT,
    extra_json      TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_tag        ON llm_calls(tag);
CREATE INDEX IF NOT EXISTS idx_llm_calls_status     ON llm_calls(status);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created_at ON llm_calls(created_at DESC);
"""


class LLMCallRepository:
    _lock = threading.Lock()
    _initialized: set[str] = set()

    def __init__(self, db_path: Optional[str] = None):
        s = get_settings()
        self.path = _resolve_db_path(db_path or s.llm_log_db_path)
        self.max_body = s.llm_log_max_body_chars
        self._ensure()

    # ---- bootstrap ----
    def _ensure(self) -> None:
        key = str(self.path)
        if key in self._initialized:
            return
        with self._lock:
            if key in self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as c:
                c.executescript(_SCHEMA)
                c.execute("PRAGMA journal_mode=WAL;")
            self._initialized.add(key)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ---- writes ----
    def insert_pending(
        self,
        *,
        created_at: str,
        tag: str,
        model: str | None,
        base_url: str | None,
        provider: str | None,
        request_json: dict | list,
        extra: dict | None = None,
    ) -> int:
        body = self._truncate(json.dumps(request_json, ensure_ascii=False, default=str))
        extras = json.dumps(extra or {}, ensure_ascii=False, default=str)
        with self._connect() as c:
            cur = c.execute(
                """
                INSERT INTO llm_calls
                  (created_at, tag, model, base_url, provider, status, request_json, extra_json)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (created_at, tag, model, base_url, provider, body, extras),
            )
            return int(cur.lastrowid)

    def mark_success(
        self,
        call_id: int,
        *,
        finished_at: str,
        latency_ms: int,
        response_text: str | None,
        response_json: Any | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
    ) -> None:
        rj = (
            self._truncate(json.dumps(response_json, ensure_ascii=False, default=str))
            if response_json is not None
            else None
        )
        rt = self._truncate(response_text) if response_text else None
        with self._connect() as c:
            c.execute(
                """
                UPDATE llm_calls SET
                    finished_at = ?, status = 'success', latency_ms = ?,
                    response_text = ?, response_json = ?,
                    prompt_tokens = ?, completion_tokens = ?, total_tokens = ?
                WHERE id = ?
                """,
                (finished_at, latency_ms, rt, rj, prompt_tokens, completion_tokens, total_tokens, call_id),
            )

    def mark_error(self, call_id: int, *, finished_at: str, latency_ms: int, error: str) -> None:
        with self._connect() as c:
            c.execute(
                """
                UPDATE llm_calls SET
                    finished_at = ?, status = 'error', latency_ms = ?, error = ?
                WHERE id = ?
                """,
                (finished_at, latency_ms, self._truncate(error), call_id),
            )

    # ---- reads ----
    def list(
        self,
        *,
        tag: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        clauses, params = [], []
        if tag:
            clauses.append("tag = ?")
            params.append(tag)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT id, created_at, finished_at, tag, model, status,
                   latency_ms, prompt_tokens, completion_tokens, total_tokens, error
            FROM llm_calls {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        with self._connect() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def count(self, *, tag: Optional[str] = None, status: Optional[str] = None) -> int:
        clauses, params = [], []
        if tag:
            clauses.append("tag = ?")
            params.append(tag)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as c:
            row = c.execute(f"SELECT COUNT(*) AS n FROM llm_calls {where}", params).fetchone()
            return int(row["n"])

    def get(self, call_id: int) -> dict | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            for k in ("request_json", "response_json", "extra_json"):
                if d.get(k):
                    try:
                        d[k] = json.loads(d[k])
                    except Exception:
                        pass
            return d

    def distinct_tags(self) -> list[str]:
        with self._connect() as c:
            return [r["tag"] for r in c.execute(
                "SELECT DISTINCT tag FROM llm_calls ORDER BY tag"
            ).fetchall()]

    def stats(self) -> dict:
        with self._connect() as c:
            row = c.execute(
                """
                SELECT
                  COUNT(*)                                   AS total,
                  SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,
                  SUM(CASE WHEN status='error'   THEN 1 ELSE 0 END) AS err,
                  SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                  COALESCE(SUM(total_tokens),0)              AS tokens,
                  COALESCE(AVG(latency_ms),0)                AS avg_latency_ms
                FROM llm_calls
                """
            ).fetchone()
            return {k: row[k] for k in row.keys()}

    def clear(self) -> int:
        with self._connect() as c:
            cur = c.execute("DELETE FROM llm_calls")
            return cur.rowcount or 0

    # ---- internal ----
    def _truncate(self, s: str | None) -> str | None:
        if s is None:
            return None
        if len(s) <= self.max_body:
            return s
        return s[: self.max_body] + f"\n…[truncated {len(s) - self.max_body} chars]"
