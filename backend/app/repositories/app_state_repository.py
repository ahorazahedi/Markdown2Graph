"""Persistent app state: schemas + documents.

Separate SQLite file from the LLM call audit log so the two can be backed
up / cleared independently.

Tables
------
schemas           - latest active schema (single row, id=1)
schema_versions   - immutable history of every save (audit + rollback)
documents         - registry of every uploaded markdown file, status, counts
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from ..config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve(raw: str) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else _REPO_ROOT / p


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schemas (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    node_labels     TEXT    NOT NULL DEFAULT '[]',
    triplets        TEXT    NOT NULL DEFAULT '[]',
    extra           TEXT    NOT NULL DEFAULT '',
    updated_at      TEXT    NOT NULL,
    updated_by      TEXT
);

CREATE TABLE IF NOT EXISTS schema_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,
    node_labels     TEXT    NOT NULL,
    triplets        TEXT    NOT NULL,
    extra           TEXT    NOT NULL DEFAULT '',
    source          TEXT    NOT NULL DEFAULT 'manual'  -- 'manual' | 'discovered' | 'imported'
);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name       TEXT    NOT NULL UNIQUE,
    title           TEXT,
    sha1            TEXT    NOT NULL,
    source_path     TEXT    NOT NULL,
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'pending', -- pending|processing|completed|failed
    error           TEXT,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    entity_count    INTEGER NOT NULL DEFAULT 0,
    relationship_count INTEGER NOT NULL DEFAULT 0,
    last_job_id     TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    processed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_status     ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC);

CREATE TABLE IF NOT EXISTS prompts (
    key             TEXT PRIMARY KEY,
    template        TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    variables       TEXT NOT NULL DEFAULT '[]',  -- JSON list of {name, description}
    is_custom       INTEGER NOT NULL DEFAULT 0,  -- 1 if user-edited from default
    default_hash    TEXT,                        -- sha1 of original disk template
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL DEFAULT 'ingest',
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued|running|succeeded|failed
    progress        REAL NOT NULL DEFAULT 0,
    stage           TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT '',
    error           TEXT,
    scope_json      TEXT NOT NULL DEFAULT '{}',      -- inputs (doc_ids, reextract, ...)
    result_json     TEXT,                            -- final totals
    started_at      TEXT,
    ended_at        TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingest_runs_status     ON ingest_runs(status);
CREATE INDEX IF NOT EXISTS idx_ingest_runs_created_at ON ingest_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS ingest_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    ts              TEXT NOT NULL,
    stage           TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT '',
    progress        REAL NOT NULL DEFAULT 0,
    file_name       TEXT,
    level           TEXT NOT NULL DEFAULT 'info',    -- info|warn|error
    extra_json      TEXT,
    FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ingest_events_run_id  ON ingest_events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_ingest_events_level   ON ingest_events(level);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,           -- JSON-encoded
    updated_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AppStateRepository:
    _lock = threading.Lock()
    _initialized: set[str] = set()

    def __init__(self, db_path: Optional[str] = None):
        s = get_settings()
        self.path = _resolve(db_path or s.app_state_db_path)
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
                # seed the single-row schemas table
                row = c.execute("SELECT 1 FROM schemas WHERE id = 1").fetchone()
                if not row:
                    c.execute(
                        "INSERT INTO schemas (id, node_labels, triplets, extra, updated_at) "
                        "VALUES (1, '[]', '[]', '', ?)",
                        (_now(),),
                    )
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

    # ---------------- schemas ----------------
    def get_schema(self) -> dict:
        with self._connect() as c:
            row = c.execute(
                "SELECT node_labels, triplets, extra, updated_at, updated_by FROM schemas WHERE id = 1"
            ).fetchone()
        if not row:
            return {"node_labels": [], "triplets": [], "extra": "", "updated_at": None, "updated_by": None}
        return {
            "node_labels": json.loads(row["node_labels"]),
            "triplets": json.loads(row["triplets"]),
            "extra": row["extra"] or "",
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }

    def save_schema(
        self,
        *,
        node_labels: list[str],
        triplets: list[list[str]],
        extra: str = "",
        source: str = "manual",
        updated_by: str | None = None,
    ) -> dict:
        nl = json.dumps(node_labels, ensure_ascii=False)
        tp = json.dumps(triplets, ensure_ascii=False)
        now = _now()
        with self._connect() as c:
            c.execute(
                "UPDATE schemas SET node_labels = ?, triplets = ?, extra = ?, "
                "updated_at = ?, updated_by = ? WHERE id = 1",
                (nl, tp, extra, now, updated_by),
            )
            c.execute(
                "INSERT INTO schema_versions (created_at, node_labels, triplets, extra, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, nl, tp, extra, source),
            )
        return self.get_schema()

    def list_schema_versions(self, limit: int = 50) -> list[dict]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT id, created_at, source FROM schema_versions "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_schema_version(self, version_id: int) -> dict | None:
        with self._connect() as c:
            row = c.execute(
                "SELECT id, created_at, node_labels, triplets, extra, source "
                "FROM schema_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["node_labels"] = json.loads(d["node_labels"])
        d["triplets"] = json.loads(d["triplets"])
        return d

    # ---------------- documents ----------------
    def upsert_document(
        self,
        *,
        file_name: str,
        title: str | None,
        sha1: str,
        source_path: str,
        size_bytes: int,
    ) -> int:
        now = _now()
        with self._connect() as c:
            row = c.execute("SELECT id, status FROM documents WHERE file_name = ?",
                            (file_name,)).fetchone()
            if row:
                # bump sha1 + path if file replaced; reset status so user can re-ingest
                c.execute(
                    "UPDATE documents SET sha1 = ?, title = ?, source_path = ?, "
                    "size_bytes = ?, status = CASE WHEN sha1 = ? THEN status ELSE 'pending' END, "
                    "error = NULL, updated_at = ? WHERE id = ?",
                    (sha1, title, source_path, size_bytes, sha1, now, row["id"]),
                )
                return int(row["id"])
            cur = c.execute(
                "INSERT INTO documents (file_name, title, sha1, source_path, size_bytes, "
                "status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (file_name, title, sha1, source_path, size_bytes, now, now),
            )
            return int(cur.lastrowid)

    def list_documents(
        self,
        *,
        status: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        sql = """
            SELECT id, file_name, title, sha1, source_path, size_bytes,
                   status, error, chunk_count, entity_count, relationship_count,
                   last_job_id, created_at, updated_at, processed_at
            FROM documents
        """
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def get_document(self, doc_id: int) -> dict | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            return dict(row) if row else None

    def get_document_by_name(self, file_name: str) -> dict | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM documents WHERE file_name = ?",
                            (file_name,)).fetchone()
            return dict(row) if row else None

    def set_status(self, doc_id: int, status: str, *, error: str | None = None,
                   job_id: str | None = None) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE documents SET status = ?, error = ?, last_job_id = COALESCE(?, last_job_id), "
                "updated_at = ? WHERE id = ?",
                (status, error, job_id, _now(), doc_id),
            )

    def set_counts(self, doc_id: int, *, chunk_count: int, entity_count: int,
                   relationship_count: int) -> None:
        now = _now()
        with self._connect() as c:
            c.execute(
                "UPDATE documents SET chunk_count = ?, entity_count = ?, relationship_count = ?, "
                "processed_at = ?, updated_at = ? WHERE id = ?",
                (chunk_count, entity_count, relationship_count, now, now, doc_id),
            )

    def update_counts_progress(self, doc_id: int, *, chunk_count: int | None,
                               entity_count: int, relationship_count: int) -> None:
        """Live mid-extraction progress write — does NOT set processed_at, so
        the DocumentsPage can show partial counts without prematurely flagging
        the document as completed."""
        now = _now()
        with self._connect() as c:
            if chunk_count is None:
                c.execute(
                    "UPDATE documents SET entity_count = ?, relationship_count = ?, "
                    "updated_at = ? WHERE id = ?",
                    (entity_count, relationship_count, now, doc_id),
                )
            else:
                c.execute(
                    "UPDATE documents SET chunk_count = ?, entity_count = ?, "
                    "relationship_count = ?, updated_at = ? WHERE id = ?",
                    (chunk_count, entity_count, relationship_count, now, doc_id),
                )

    def delete_document(self, doc_id: int) -> bool:
        with self._connect() as c:
            cur = c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            return (cur.rowcount or 0) > 0

    # ---------------- bulk reset helpers ----------------
    def count_documents(self) -> int:
        with self._connect() as c:
            return int(c.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def clear_documents(self) -> int:
        with self._connect() as c:
            cur = c.execute("DELETE FROM documents")
            return int(cur.rowcount or 0)

    def count_schema_versions(self) -> int:
        with self._connect() as c:
            return int(c.execute("SELECT COUNT(*) FROM schema_versions").fetchone()[0])

    def has_schema(self) -> bool:
        s = self.get_schema()
        return bool(s["node_labels"] or s["triplets"] or s["extra"])

    def clear_schema(self, *, drop_versions: bool = True) -> int:
        """Reset active schema to empty. Returns count of removed versions."""
        removed = 0
        with self._connect() as c:
            c.execute(
                "UPDATE schemas SET node_labels = '[]', triplets = '[]', extra = '', "
                "updated_at = ?, updated_by = NULL WHERE id = 1",
                (_now(),),
            )
            if drop_versions:
                cur = c.execute("DELETE FROM schema_versions")
                removed = int(cur.rowcount or 0)
        return removed

    def count_runs(self) -> int:
        with self._connect() as c:
            try:
                return int(c.execute("SELECT COUNT(*) FROM ingest_runs").fetchone()[0])
            except sqlite3.OperationalError:
                return 0

    def clear_runs(self) -> int:
        with self._connect() as c:
            try:
                cur = c.execute("DELETE FROM ingest_runs")
                c.execute("DELETE FROM ingest_events")
                return int(cur.rowcount or 0)
            except sqlite3.OperationalError:
                return 0

    def count_custom_prompts(self) -> int:
        with self._connect() as c:
            return int(c.execute("SELECT COUNT(*) FROM prompts WHERE is_custom = 1").fetchone()[0])

    def list_custom_prompt_keys(self) -> list[str]:
        with self._connect() as c:
            rows = c.execute("SELECT key FROM prompts WHERE is_custom = 1").fetchall()
        return [r["key"] for r in rows]

    # ---------------- prompts ----------------
    def upsert_prompt_default(
        self,
        *,
        key: str,
        template: str,
        description: str,
        variables: list[dict],
        default_hash: str,
    ) -> None:
        """Seed a prompt from disk. Preserves user edits if `is_custom = 1`."""
        now = _now()
        vars_json = json.dumps(variables, ensure_ascii=False)
        with self._connect() as c:
            row = c.execute("SELECT is_custom FROM prompts WHERE key = ?", (key,)).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO prompts (key, template, description, variables, "
                    "is_custom, default_hash, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
                    (key, template, description, vars_json, default_hash, now),
                )
            else:
                # update description + variables + default_hash; preserve user template if customized
                if row["is_custom"]:
                    c.execute(
                        "UPDATE prompts SET description = ?, variables = ?, "
                        "default_hash = ? WHERE key = ?",
                        (description, vars_json, default_hash, key),
                    )
                else:
                    c.execute(
                        "UPDATE prompts SET template = ?, description = ?, variables = ?, "
                        "default_hash = ?, updated_at = ? WHERE key = ?",
                        (template, description, vars_json, default_hash, now, key),
                    )

    def list_prompts(self) -> list[dict]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT key, template, description, variables, is_custom, "
                "default_hash, updated_at FROM prompts ORDER BY key"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["variables"] = json.loads(d["variables"])
            d["is_custom"] = bool(d["is_custom"])
            out.append(d)
        return out

    def get_prompt(self, key: str) -> dict | None:
        with self._connect() as c:
            row = c.execute(
                "SELECT key, template, description, variables, is_custom, "
                "default_hash, updated_at FROM prompts WHERE key = ?",
                (key,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["variables"] = json.loads(d["variables"])
        d["is_custom"] = bool(d["is_custom"])
        return d

    def save_prompt(self, key: str, template: str) -> dict | None:
        with self._connect() as c:
            cur = c.execute(
                "UPDATE prompts SET template = ?, is_custom = 1, updated_at = ? WHERE key = ?",
                (template, _now(), key),
            )
            if (cur.rowcount or 0) == 0:
                return None
        return self.get_prompt(key)

    def reset_prompt(self, key: str, default_template: str) -> dict | None:
        with self._connect() as c:
            cur = c.execute(
                "UPDATE prompts SET template = ?, is_custom = 0, updated_at = ? WHERE key = ?",
                (default_template, _now(), key),
            )
            if (cur.rowcount or 0) == 0:
                return None
        return self.get_prompt(key)

    # ---------------- app_settings ----------------
    def get_setting(self, key: str, default=None):
        with self._connect() as c:
            row = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def set_setting(self, key: str, value) -> None:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        with self._connect() as c:
            c.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, encoded, _now()),
            )

    def all_settings(self) -> dict:
        with self._connect() as c:
            rows = c.execute("SELECT key, value FROM app_settings").fetchall()
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except Exception:
                out[r["key"]] = r["value"]
        return out

    # ---------------- ingest jobs ----------------
    def create_run(self, run_id: str, *, kind: str = "ingest", scope: dict | None = None) -> None:
        with self._connect() as c:
            c.execute(
                "INSERT INTO ingest_runs (id, kind, status, scope_json, created_at) "
                "VALUES (?, ?, 'queued', ?, ?)",
                (run_id, kind, json.dumps(scope or {}, ensure_ascii=False, default=str), _now()),
            )

    def start_run(self, run_id: str) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE ingest_runs SET status='running', started_at=? WHERE id=?",
                (_now(), run_id),
            )

    def update_run_progress(self, run_id: str, *, stage: str, message: str, progress: float) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE ingest_runs SET stage=?, message=?, "
                "progress=MAX(progress, ?) WHERE id=?",
                (stage, message, progress, run_id),
            )

    def finish_run(self, run_id: str, *, status: str, error: str | None = None,
                   result: dict | None = None) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE ingest_runs SET status=?, error=?, result_json=?, ended_at=?, "
                "progress=CASE WHEN ?='succeeded' THEN 1.0 ELSE progress END WHERE id=?",
                (status, error,
                 json.dumps(result, ensure_ascii=False, default=str) if result else None,
                 _now(), status, run_id),
            )

    def append_event(self, run_id: str, *, stage: str, message: str, progress: float,
                     file_name: str | None = None, level: str = "info",
                     extra: dict | None = None) -> int:
        with self._connect() as c:
            cur = c.execute(
                "INSERT INTO ingest_events (run_id, ts, stage, message, progress, "
                "file_name, level, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, _now(), stage, message, progress, file_name, level,
                 json.dumps(extra, ensure_ascii=False, default=str) if extra else None),
            )
            return int(cur.lastrowid)

    def list_runs(self, *, status: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        sql = (
            "SELECT id, kind, status, progress, stage, message, error, scope_json, "
            "result_json, started_at, ended_at, created_at FROM ingest_runs"
        )
        params: list = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as c:
            rows = [dict(r) for r in c.execute(sql, params).fetchall()]
        for r in rows:
            r["scope"] = json.loads(r.pop("scope_json") or "{}")
            r["result"] = json.loads(r.pop("result_json") or "null")
        return rows

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM ingest_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["scope"] = json.loads(d.pop("scope_json") or "{}")
        d["result"] = json.loads(d.pop("result_json") or "null")
        return d

    def list_events(self, run_id: str, *, after_id: int = 0, limit: int = 500,
                    level: str | None = None) -> list[dict]:
        sql = "SELECT id, ts, stage, message, progress, file_name, level, extra_json " \
              "FROM ingest_events WHERE run_id=? AND id>?"
        params: list = [run_id, after_id]
        if level:
            sql += " AND level=?"
            params.append(level)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(limit)
        with self._connect() as c:
            rows = [dict(r) for r in c.execute(sql, params).fetchall()]
        for r in rows:
            r["extra"] = json.loads(r.pop("extra_json") or "null")
        return rows

    def runs_overview(self) -> dict:
        with self._connect() as c:
            row = c.execute(
                """
                SELECT
                  COUNT(*)                                                AS total,
                  SUM(CASE WHEN status='running'   THEN 1 ELSE 0 END)     AS running,
                  SUM(CASE WHEN status='queued'    THEN 1 ELSE 0 END)     AS queued,
                  SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END)     AS succeeded,
                  SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END)     AS failed
                FROM ingest_runs
                """
            ).fetchone()
        return {k: row[k] for k in row.keys()}

    def stats(self) -> dict:
        with self._connect() as c:
            row = c.execute(
                """
                SELECT
                  COUNT(*)                                                AS total,
                  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END)     AS completed,
                  SUM(CASE WHEN status='pending'   THEN 1 ELSE 0 END)     AS pending,
                  SUM(CASE WHEN status='processing' THEN 1 ELSE 0 END)    AS processing,
                  SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END)     AS failed,
                  COALESCE(SUM(chunk_count), 0)                           AS chunks,
                  COALESCE(SUM(entity_count), 0)                          AS entities,
                  COALESCE(SUM(relationship_count), 0)                    AS relationships
                FROM documents
                """
            ).fetchone()
        return {k: row[k] for k in row.keys()}
