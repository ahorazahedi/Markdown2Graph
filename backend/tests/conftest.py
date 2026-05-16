import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_state_dbs(tmp_path, monkeypatch):
    """Redirect every per-test process to a throwaway SQLite so tests can't
    contaminate the developer's real app_state / llm_calls database."""
    monkeypatch.setenv("APP_STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("LLM_LOG_DB_PATH", str(tmp_path / "calls.db"))
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def sample_md_dir() -> Path:
    return Path(__file__).parent / "sample_md"
