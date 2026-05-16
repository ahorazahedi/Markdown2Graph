"""SQLite persistence for chat sessions, messages, citations, summaries.

Mirrors the schema documented in `docs/CHAT_RAG_REFERENCE.md` §11.2.
Stored separately from `text2graph.db` and `llm_calls.db` so chat history
can be wiped or exported without touching the rest of app state.

Multi-user readiness:
- `chat_sessions.user_id` is `NOT NULL DEFAULT 'default'`. Before auth
  ships, every session is created under that stub user. When middleware
  starts injecting real user ids the column already exists and is indexed.
- `role` is enforced at the API layer, not the row layer — same default-
  admin model. See `app/api/chat_api.py`.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional
from uuid import uuid4

from ..config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve(raw: str) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else _REPO_ROOT / p


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    title           TEXT NOT NULL DEFAULT 'New chat',
    mode            TEXT NOT NULL DEFAULT 'graph_vector_fulltext',
    model           TEXT,
    embedding_provider TEXT,
    embedding_model TEXT,
    document_names  TEXT NOT NULL DEFAULT '[]',
    pinned          INTEGER NOT NULL DEFAULT 0,
    archived        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_message_at TEXT,
    message_count   INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    meta_json       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated
    ON chat_sessions(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_archived
    ON chat_sessions(archived);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    mode            TEXT,
    model           TEXT,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    response_time_ms INTEGER,
    llm_call_id     INTEGER,
    error           TEXT,
    created_at      TEXT NOT NULL,
    sources_json    TEXT NOT NULL DEFAULT '[]',
    entities_json   TEXT NOT NULL DEFAULT '{}',
    nodedetails_json TEXT NOT NULL DEFAULT '{}',
    metric_json     TEXT NOT NULL DEFAULT '{}',
    meta_json       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, id);

CREATE TABLE IF NOT EXISTS chat_citations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    ref_id          TEXT NOT NULL,
    score           REAL,
    label           TEXT,
    extra_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chat_citations_message
    ON chat_citations(message_id);
CREATE INDEX IF NOT EXISTS idx_chat_citations_ref
    ON chat_citations(kind, ref_id);

CREATE TABLE IF NOT EXISTS chat_summaries (
    session_id      TEXT PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
    summary         TEXT NOT NULL,
    covers_up_to_message_id INTEGER NOT NULL,
    updated_at      TEXT NOT NULL,
    token_estimate  INTEGER
);

CREATE TABLE IF NOT EXISTS chat_feedback (
    message_id      INTEGER PRIMARY KEY REFERENCES chat_messages(id) ON DELETE CASCADE,
    rating          INTEGER NOT NULL,
    comment         TEXT,
    created_at      TEXT NOT NULL
);
"""


class ChatRepository:
    _lock = threading.Lock()
    _initialized: set[str] = set()

    def __init__(self, db_path: Optional[str] = None):
        s = get_settings()
        self.path = _resolve(db_path or s.chat_db_path)
        self._ensure()

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
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
        finally:
            conn.close()

    # ---------------- sessions ----------------
    def create_session(self, *, session_id: Optional[str] = None,
                       user_id: str = "default",
                       mode: str = "graph_vector_fulltext",
                       model: Optional[str] = None,
                       embedding_provider: Optional[str] = None,
                       embedding_model: Optional[str] = None,
                       document_names: Optional[list[str]] = None,
                       title: str = "New chat") -> dict:
        sid = session_id or uuid4().hex
        now = _now()
        with self._connect() as c:
            c.execute(
                """
                INSERT INTO chat_sessions (id, user_id, title, mode, model,
                    embedding_provider, embedding_model, document_names,
                    created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sid, user_id, title, mode, model,
                 embedding_provider, embedding_model,
                 json.dumps(document_names or [], ensure_ascii=False),
                 now, now),
            )
        return self.get_session(sid)  # type: ignore[return-value]

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, *, user_id: str = "default",
                      archived: bool = False,
                      limit: int = 50, offset: int = 0,
                      search: Optional[str] = None) -> list[dict]:
        sql = (
            "SELECT * FROM chat_sessions "
            "WHERE user_id = ? AND archived = ?"
        )
        params: list = [user_id, 1 if archived else 0]
        if search:
            sql += " AND title LIKE ?"
            params.append(f"%{search}%")
        sql += " ORDER BY pinned DESC, COALESCE(last_message_at, updated_at) DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as c:
            return [self._row_to_session(r) for r in c.execute(sql, params).fetchall()]

    def update_session(self, session_id: str, **fields) -> Optional[dict]:
        if not fields:
            return self.get_session(session_id)
        allowed = {"title", "mode", "model", "embedding_provider",
                   "embedding_model", "document_names", "pinned", "archived",
                   "meta_json"}
        sets = []
        vals = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in ("document_names", "meta_json") and not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            if k in ("pinned", "archived"):
                v = 1 if v else 0
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return self.get_session(session_id)
        sets.append("updated_at = ?")
        vals.append(_now())
        vals.append(session_id)
        with self._connect() as c:
            c.execute(
                f"UPDATE chat_sessions SET {', '.join(sets)} WHERE id = ?",
                vals,
            )
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        with self._connect() as c:
            cur = c.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
            return (cur.rowcount or 0) > 0

    # ---------------- messages ----------------
    def append_message(self, *, session_id: str, role: str, content: str,
                       mode: Optional[str] = None,
                       model: Optional[str] = None,
                       prompt_tokens: Optional[int] = None,
                       completion_tokens: Optional[int] = None,
                       total_tokens: Optional[int] = None,
                       response_time_ms: Optional[int] = None,
                       llm_call_id: Optional[int] = None,
                       sources: Optional[list] = None,
                       entities: Optional[dict] = None,
                       nodedetails: Optional[dict] = None,
                       metric: Optional[dict] = None,
                       error: Optional[str] = None) -> int:
        now = _now()
        with self._connect() as c:
            cur = c.execute(
                """
                INSERT INTO chat_messages
                  (session_id, role, content, mode, model,
                   prompt_tokens, completion_tokens, total_tokens,
                   response_time_ms, llm_call_id, error, created_at,
                   sources_json, entities_json, nodedetails_json, metric_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, role, content, mode, model,
                    prompt_tokens, completion_tokens, total_tokens,
                    response_time_ms, llm_call_id, error, now,
                    json.dumps(sources or [], ensure_ascii=False, default=str),
                    json.dumps(entities or {}, ensure_ascii=False, default=str),
                    json.dumps(nodedetails or {}, ensure_ascii=False, default=str),
                    json.dumps(metric or {}, ensure_ascii=False, default=str),
                ),
            )
            mid = int(cur.lastrowid)
            c.execute(
                """
                UPDATE chat_sessions
                SET message_count   = message_count + 1,
                    total_tokens    = total_tokens + COALESCE(?, 0),
                    last_message_at = ?,
                    updated_at      = ?
                WHERE id = ?
                """,
                (total_tokens or 0, now, now, session_id),
            )
        # auto-derive citation rows for assistant turns with structured info
        if role == "assistant" and (sources or nodedetails):
            self._auto_citations(mid, sources or [], nodedetails or {})
        return mid

    def list_messages(self, session_id: str, *, limit: Optional[int] = None,
                      after_id: int = 0) -> list[dict]:
        sql = (
            "SELECT * FROM chat_messages WHERE session_id = ? AND id > ? "
            "ORDER BY id ASC"
        )
        params: list = [session_id, after_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as c:
            return [self._row_to_message(r) for r in c.execute(sql, params).fetchall()]

    def get_message(self, message_id: int) -> Optional[dict]:
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM chat_messages WHERE id = ?", (message_id,)
            ).fetchone()
        return self._row_to_message(row) if row else None

    # ---------------- citations ----------------
    def list_citations(self, message_id: int) -> list[dict]:
        with self._connect() as c:
            return [
                {**dict(r), "extra": json.loads(r["extra_json"] or "{}")}
                for r in c.execute(
                    "SELECT * FROM chat_citations WHERE message_id = ? ORDER BY id ASC",
                    (message_id,),
                ).fetchall()
            ]

    def _auto_citations(self, message_id: int, sources: list,
                        nodedetails: dict) -> None:
        rows = []
        for s in sources:
            label = s.get("source_name") if isinstance(s, dict) else str(s)
            rows.append(("document", label or "", None, label or "", json.dumps(s, default=str)))
        for c in (nodedetails.get("chunkdetails") or []):
            rows.append(("chunk", str(c.get("id") or ""), c.get("score"),
                         None, json.dumps(c, default=str)))
        for e in (nodedetails.get("entitydetails") or []):
            rows.append(("entity", str(e.get("id") or ""), None,
                         e.get("label"), json.dumps(e, default=str)))
        for k in (nodedetails.get("communitydetails") or []):
            rows.append(("community", str(k.get("id") or ""), None,
                         k.get("label"), json.dumps(k, default=str)))
        if not rows:
            return
        with self._connect() as c:
            c.executemany(
                "INSERT INTO chat_citations (message_id, kind, ref_id, score, label, extra_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(message_id, k, ref, sc, lab, ex) for (k, ref, sc, lab, ex) in rows],
            )

    # ---------------- feedback ----------------
    def set_feedback(self, message_id: int, *, rating: int,
                     comment: Optional[str] = None) -> None:
        with self._connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO chat_feedback "
                "(message_id, rating, comment, created_at) VALUES (?, ?, ?, ?)",
                (message_id, int(rating), comment, _now()),
            )

    # ---------------- summary ----------------
    def upsert_summary(self, session_id: str, *, summary: str,
                       covers_up_to_message_id: int,
                       token_estimate: int) -> None:
        with self._connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO chat_summaries "
                "(session_id, summary, covers_up_to_message_id, updated_at, token_estimate) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, summary, int(covers_up_to_message_id), _now(), int(token_estimate)),
            )

    def get_summary(self, session_id: str) -> Optional[dict]:
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM chat_summaries WHERE session_id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    # ---------------- helpers ----------------
    @staticmethod
    def _row_to_session(row) -> dict:
        d = dict(row)
        d["pinned"] = bool(d.get("pinned"))
        d["archived"] = bool(d.get("archived"))
        d["document_names"] = json.loads(d.get("document_names") or "[]")
        d["meta"] = json.loads(d.pop("meta_json", "{}") or "{}")
        return d

    @staticmethod
    def _row_to_message(row) -> dict:
        d = dict(row)
        for k_json, k_out in (
            ("sources_json", "sources"),
            ("entities_json", "entities"),
            ("nodedetails_json", "nodedetails"),
            ("metric_json", "metric"),
            ("meta_json", "meta"),
        ):
            raw = d.pop(k_json, None) or ("{}" if k_out != "sources" else "[]")
            try:
                d[k_out] = json.loads(raw)
            except Exception:
                d[k_out] = None
        return d
