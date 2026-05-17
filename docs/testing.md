# Testing Reference

pytest, no special framework. One autouse fixture isolates per-test DBs so tests can't pollute each other or the dev environment.

**Directory:** `backend/tests/`

---

## 1. Layout

```
backend/tests/
├── __init__.py
├── conftest.py                              # autouse fixtures
├── sample_md/                               # test corpus (.md files)
├── test_app_state_repository.py
├── test_llm_call_repository.py
├── test_job_registry.py
├── test_chunker.py
├── test_config.py
├── test_markdown_loader.py
├── test_prompt_store.py
├── test_upload.py
├── test_post_process_api.py
├── test_schema_discovery_parsing.py
└── test_repository_sanitizers.py
```

---

## 2. Core fixture (autouse)

```python
# backend/tests/conftest.py
import pytest
from pathlib import Path
from app.config import get_settings

@pytest.fixture(autouse=True)
def _isolate_state_dbs(tmp_path, monkeypatch):
    """Redirect APP_STATE and LLM_LOG paths to a fresh tmpdir per test.
    Prevents tests from contaminating dev databases."""
    monkeypatch.setenv("APP_STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("LLM_LOG_DB_PATH",   str(tmp_path / "calls.db"))
    monkeypatch.setenv("CHAT_DB_PATH",      str(tmp_path / "chat.db"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

@pytest.fixture
def sample_md_dir() -> Path:
    return Path(__file__).parent / "sample_md"
```

Every test gets:
- Fresh SQLite paths (autouse).
- Cleared `get_settings()` cache (so settings reflect the temp paths).
- Optional `sample_md_dir` for loader/chunker tests.

No Neo4j fixture — Neo4j-touching tests require a running instance and are tagged separately (skipped if `NEO4J_URI` not set).

---

## 3. Conventions

| Concern | Convention |
|---|---|
| File name | `test_<module_name>.py` |
| Function name | `test_<scenario>` (snake_case, descriptive) |
| Fixtures | leading underscore for autouse/internal (`_isolate_state_dbs`); plain name for opt-in (`sample_md_dir`) |
| Assertions | plain `assert`; no helper framework |
| Mocking | `monkeypatch` for env + settings; `unittest.mock` for service classes; real SQLite always |

---

## 4. Unit vs integration

- **Unit** — single class/function, no I/O beyond temp SQLite (e.g. `test_job_runs_and_reports_progress`).
- **Integration** — multiple components glued together, still tmp-DB-isolated (e.g. `test_documents_upsert_status_counts`).
- **External-service** — anything touching Neo4j or a real LLM; `@pytest.mark.skipif(not _neo4j_reachable(), reason="...")` to skip in CI.

No formal boundary — pytest discovers everything in `tests/`.

---

## 5. Representative tests

### Job registry

```python
def test_job_runs_and_reports_progress():
    reg = JobRegistry()
    def task(update):
        update(JobUpdate(stage="run", message="ok", progress=0.5))
        return {"ok": True}
    jid = reg.submit(task)

    # Poll until terminal
    for _ in range(100):
        if reg.get(jid).status in ("succeeded", "failed"): break
        time.sleep(0.05)

    snap = reg.get(jid).snapshot()
    assert snap["status"] == "succeeded"
    assert snap["progress"] == 1.0
    assert snap["result"] == {"ok": True}

def test_job_captures_failure():
    reg = JobRegistry()
    def task(_u): raise RuntimeError("boom")
    jid = reg.submit(task)
    # ... poll ...
    snap = reg.get(jid).snapshot()
    assert snap["status"] == "failed"
    assert "boom" in (snap["error"] or "")
```

### Repository

```python
def test_schema_default_and_save(tmp_path):
    repo = AppStateRepository(db_path=str(tmp_path / "state.db"))
    assert repo.get_schema()["node_labels"] == []

    repo.save_schema(
        node_labels=["Disease", "Drug"],
        triplets=[["Drug", "TREATS", "Disease"]],
        extra="medical", source="manual",
    )
    s = repo.get_schema()
    assert s["node_labels"] == ["Disease", "Drug"]
    assert len(repo.list_schema_versions()) == 1
```

### Truncation guard

```python
def test_truncates_huge_bodies(tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "llm_log_max_body_chars", 100)
    repo = LLMCallRepository(db_path=str(tmp_path / "calls.db"))
    cid = repo.insert_pending(
        created_at="...", tag="test", model="m", base_url=None, provider="t",
        request_json={"data": "x" * 1000},
    )
    item = repo.get(cid)
    assert "…[truncated" in json.dumps(item["request_json"])
```

---

## 6. Running

```bash
cd backend
pytest                                       # all tests
pytest -v                                    # verbose
pytest -s                                    # show print/log
pytest tests/test_job_registry.py            # one file
pytest tests/test_job_registry.py::test_job_runs_and_reports_progress
pytest -k embedding                          # filter by name substring
pytest --tb=short                            # shorter tracebacks
```

Makefile shortcut (if present):

```bash
make test
```

---

## 7. Adding a new test

1. Create `tests/test_<module>.py`.
2. Import the unit-under-test directly (no fixtures needed beyond `_isolate_state_dbs`).
3. Use `tmp_path` (built-in) for any filesystem state.
4. Use `monkeypatch.setenv(...)` + `get_settings.cache_clear()` if you need to alter config mid-test.
5. Prefer real SQLite over mocks — fast enough, exercises real SQL.

---

## 8. What's not tested here

- Neo4j writes (require live DB; covered by manual ops + post-process API tests when DB available).
- LLM responses (mocked at the boundary — assertions check behavior, not text quality).
- Frontend (no test setup checked in; rely on type-check + smoke).

If you want a fast guard for the frontend, add `tsc --noEmit` to CI; for behavior, Playwright against a stubbed backend.
