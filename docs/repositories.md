# Repositories Reference

Three SQLite repos + one Neo4j repo, all stateless and thread-safe via short-lived connections.

**Files:**
- `backend/app/repositories/app_state_repository.py` — schemas, docs, prompts, jobs, settings
- `backend/app/repositories/llm_call_repository.py` — LLM audit
- `backend/app/repositories/chat_repository.py` — chat sessions + messages
- `backend/app/repositories/graph_repository.py` — Neo4j writes/reads
- `backend/app/repositories/settings_repository.py` — narrow allowed-key overrides

---

## 1. Pattern (every SQLite repo)

```python
class AppStateRepository:
    _lock = threading.Lock()
    _initialized: set[str] = set()

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_settings().app_state_db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure()                              # idempotent migration

    @contextmanager
    def _connect(self):
        c = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
        finally:
            c.close()

    def _ensure(self) -> None:
        with self._lock:
            if self.db_path in self._initialized:
                return
            with self._connect() as c:
                c.execute("PRAGMA journal_mode=WAL")
                c.executescript(_SCHEMA)            # CREATE TABLE IF NOT EXISTS ...
            self._initialized.add(self.db_path)
```

Properties:
- **Connection per call.** Open at the method boundary, close on exit. No long-lived connections, no pool. SQLite is fast enough; serialization is handled by WAL.
- **`isolation_level=None`** → autocommit. Each `INSERT/UPDATE` flushes immediately. Simpler than managing transactions for the access patterns here (mostly point reads + single writes).
- **`PRAGMA foreign_keys = ON`** every connection — SQLite's default is OFF.
- **`PRAGMA journal_mode=WAL`** once at init — survives subsequent connections.
- **Lock-guarded migration.** Class-level `_lock` + `_initialized` set prevents double-init when multiple instances are constructed in parallel (common during tests).
- **Row factory.** `sqlite3.Row` lets call sites do `row["id"]` and `dict(row)`.

---

## 2. Migration block

Each repo holds its DDL as a multi-statement constant and applies it via `executescript`:

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS schemas (...);
CREATE TABLE IF NOT EXISTS schema_versions (...);
CREATE TABLE IF NOT EXISTS documents (...);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE TABLE IF NOT EXISTS prompts (...);
CREATE TABLE IF NOT EXISTS ingest_runs (...);
CREATE INDEX IF NOT EXISTS idx_ingest_runs_status ON ingest_runs(status);
CREATE TABLE IF NOT EXISTS ingest_events (...);
CREATE INDEX IF NOT EXISTS idx_ingest_events_run_id ON ingest_events(run_id, id);
CREATE TABLE IF NOT EXISTS app_settings (...);
"""
```

`IF NOT EXISTS` everywhere makes this safe to run on every boot. No version table, no migration framework — each schema change must be **additive** (new tables, new columns with defaults). For destructive changes you'd add a one-off `ALTER` guarded by a probe query.

---

## 3. JSON serialization

Most complex fields are stored as TEXT (JSON-encoded):

```python
def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)

def _loads(s: str | None, default):
    if not s: return default
    try: return json.loads(s)
    except json.JSONDecodeError: return default
```

`default=str` coerces dates/UUIDs/etc. silently. On read, fallback prevents one bad row from breaking a list endpoint.

---

## 4. Body truncation (LLMCallRepository)

LLM payloads can be huge. `_truncate` caps each TEXT body to `settings.llm_log_max_body_chars` (default 200_000):

```python
def _truncate(s: str | None) -> str | None:
    if s is None: return None
    cap = get_settings().llm_log_max_body_chars
    if len(s) <= cap: return s
    return s[:cap] + f"\n…[truncated {len(s) - cap} chars]"
```

Always called before INSERT. Original payload is never recoverable — explicit by design.

---

## 5. Thread safety claims

- **Reads:** parallel-safe. WAL allows many readers concurrent with one writer.
- **Writes:** serialized by SQLite. 30-second `timeout` gives plenty of headroom for the in-process write rate.
- **No per-table locks needed.** Single-statement writes are atomic. Multi-statement ops (e.g., merge_entities — Neo4j-side) live in the graph repo.
- **No shared connection state.** Every method opens its own connection; safe across threads, processes, async tasks.

Job worker threads can hammer `AppStateRepository().append_event(...)` thousands of times per minute without serialization issues in practice.

---

## 6. GraphRepository (Neo4j)

```python
class GraphRepository:
    def __init__(self):
        self.driver = neo4j_manager.driver          # global driver, configured in create_app
        self.database = neo4j_manager.database

    def _run(self, cypher, **params):
        with self.driver.session(database=self.database) as s:
            return list(s.run(cypher, **params))

    def ensure_constraints(self) -> None: ...
    def upsert_document(self, *, file_name, sha1, title, source, length) -> None: ...
    def set_document_status(self, file_name, status, **counts) -> None: ...
    def write_chunks(self, file_name, chunks) -> None: ...
    def write_chunk_embeddings(self, batch, *, model, dim) -> None: ...
    def write_graph_documents(self, file_name, graph_docs) -> tuple[int, int]: ...
    def delete_document(self, file_name) -> None: ...
    def checkpoint_document_progress(self, file_name, *, processed_chunks, entity_count, relationship_count) -> None: ...

    def stats(self) -> dict: ...
    def schema(self) -> dict: ...

    def list_duplicate_entities(self, *, limit_groups, min_group_size) -> list[dict]: ...
    def merge_entities(self, canonical_eid: str, alias_eids: list[str]) -> dict: ...
    def delete_orphan_entities(self) -> int: ...

    def create_chunk_vector_index(self, dim) -> None: ...
    def create_entity_vector_index(self, dim) -> None: ...
    def create_community_vector_index(self, dim) -> None: ...
    def vector_index_dim(self, name) -> int | None: ...
    def drop_vector_index(self, name) -> None: ...
    def create_chat_indexes(self) -> None: ...
    def create_similar_chunk_relationships(self, *, min_score) -> int: ...

    def list_chunks_needing_embedding(self, *, limit) -> list[dict]: ...
    def list_entities_needing_embedding(self, *, limit) -> list[dict]: ...
    def list_communities_needing_embedding(self, *, limit) -> list[dict]: ...
    def write_embeddings_unified(self, node_type, batch, *, model, dim) -> None: ...
    def clear_embeddings(self, node_type) -> int: ...
```

Connection lifecycle: one `Session` per `_run` call. Driver is shared at module scope (`neo4j_manager.driver` opened in `create_app`).

For full Cypher patterns see [Data Model §1.7](./data-model.md#17-canonical-merge-patterns) and [Post-Processing](./post-processing.md).

---

## 7. SettingsRepository

Tiny wrapper exposing the `app_settings` table with a `ALLOWED_KEYS` allowlist:

```python
class SettingsRepository:
    ALLOWED_KEYS = {
        "llm_base_url", "llm_api_key", "llm_model", "llm_temperature", "llm_max_tokens",
        "neo4j_uri", "neo4j_username", "neo4j_password", "neo4j_database",
        "embedding_provider", "embedding_model", "embedding_dimension",
    }

    def __init__(self, state: AppStateRepository | None = None):
        self.state = state or AppStateRepository()

    def load(self) -> dict[str, str]:
        rows = self.state.list_settings()
        return {r["key"]: r["value"] for r in rows if r["key"] in self.ALLOWED_KEYS}

    def save(self, updates: dict) -> None:
        for k, v in updates.items():
            if k not in self.ALLOWED_KEYS: continue
            self.state.set_setting(k, str(v))
```

Distinct from `SettingsService` (which handles the typed `SettingSpec` runtime tunables — different keys, different concern). See [Config + Env](./config-env.md) for the override precedence flow.

---

## 8. Adding a new repo

1. Make a new file under `backend/app/repositories/`.
2. Define `_SCHEMA` (CREATE TABLE IF NOT EXISTS …).
3. Copy the `_lock` / `_initialized` / `_connect` / `_ensure` skeleton from `app_state_repository.py`.
4. Add methods using `with self._connect() as c: c.execute(...)`.
5. Use `Settings.<your>_db_path` if you want a separate file; otherwise piggy-back on `app_state_db_path`.

No registration step. Just `MyRepo()` whenever you need it.
