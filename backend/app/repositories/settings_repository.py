"""Runtime settings overrides persisted in SQLite.

These overlay the .env-loaded `Settings` so users can change LLM / Neo4j
config from the UI without restarting the backend.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from ..config import Settings


_REPO_ROOT = Path(__file__).resolve().parents[3]


# Whitelist of overridable Settings field names. Keep this tight — anything
# not listed here cannot be set from the UI.
ALLOWED_KEYS: set[str] = {
    "llm_base_url",
    "llm_api_key",
    "llm_model",
    "llm_temperature",
    "llm_max_tokens",
    "embedding_provider",
    "embedding_model",
    "embedding_dimension",
    "neo4j_uri",
    "neo4j_username",
    "neo4j_password",
    "neo4j_database",
}


def _resolve(raw: str) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else _REPO_ROOT / p


_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class SettingsRepository:
    _lock = threading.Lock()
    _initialized: set[str] = set()

    def __init__(self, db_path: Optional[str] = None) -> None:
        # Use a fresh Settings() (no overlay) just to find the DB path.
        # Avoids recursion through get_settings().
        base_path = db_path or Settings().app_state_db_path
        self.path = _resolve(base_path)
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
            self._initialized.add(key)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def load(self) -> dict[str, str]:
        with self._connect() as c:
            rows = c.execute("SELECT key, value FROM app_settings").fetchall()
        return {r["key"]: r["value"] for r in rows if r["key"] in ALLOWED_KEYS}

    def save(self, updates: dict[str, object]) -> None:
        rows = [(k, "" if v is None else str(v)) for k, v in updates.items() if k in ALLOWED_KEYS]
        if not rows:
            return
        with self._connect() as c:
            c.executemany(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                rows,
            )

    def delete(self, key: str) -> None:
        if key not in ALLOWED_KEYS:
            return
        with self._connect() as c:
            c.execute("DELETE FROM app_settings WHERE key = ?", (key,))
