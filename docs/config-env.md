# Config + Env Reference

`Settings` Pydantic model is the single source of truth. Base values come from `.env`; runtime overrides come from the SQLite `app_settings` table; together they form the cached `get_settings()` object.

**Files:**
- `backend/app/config.py` — `Settings` + `get_settings()` + `reload_settings()`
- `backend/app/__init__.py` — `create_app()` wires logging, blueprints, error handlers
- `backend/app/wsgi.py` — entrypoint
- `backend/app/repositories/settings_repository.py` — `SettingsRepository.ALLOWED_KEYS`

---

## 1. Precedence

```
defaults (Settings field) → .env → SQLite app_settings (ALLOWED_KEYS only) → final get_settings()
```

`get_settings()` is `lru_cache(1)` so the merge happens once per process. Call `reload_settings()` (clears cache) after writing overrides — done automatically by `EmbeddingService.switch_model` and `SettingsService.bulk_set` callers when needed.

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    base = Settings()                                # from .env
    try:
        overrides = SettingsRepository().load()      # {key: str}
    except Exception:
        overrides = {}
    return base.model_copy(update=_coerce(overrides)) if overrides else base

def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
```

`ALLOWED_KEYS` is intentionally narrow (LLM/embedding/neo4j basics). Everything else is `.env`-only.

---

## 2. `Settings` fields (with env names)

Every field's env var is the **uppercase field name**. Defaults shown.

### Neo4j
```python
neo4j_uri:      str = "bolt://localhost:7687"   # NEO4J_URI
neo4j_username: str = "neo4j"                   # NEO4J_USERNAME
neo4j_password: str = "neo4j"                   # NEO4J_PASSWORD
neo4j_database: str = "neo4j"                   # NEO4J_DATABASE
```

### LLM (OpenAI-compatible)
```python
openrouter_api_key:  str           = ""                              # OPENROUTER_API_KEY
openrouter_base_url: str           = "https://openrouter.ai/api/v1"  # OPENROUTER_BASE_URL
llm_base_url:        Optional[str] = None                            # LLM_BASE_URL (overrides openrouter_base_url)
llm_api_key:         Optional[str] = None                            # LLM_API_KEY  (overrides openrouter_api_key)
llm_model:           str           = "google/gemini-2.5-flash"       # LLM_MODEL
llm_temperature:     float         = 0.0                              # LLM_TEMPERATURE
llm_max_tokens:      int           = 4096                             # LLM_MAX_TOKENS
llm_timeout:         int           = 120                              # LLM_TIMEOUT
```

`effective_llm_base_url` / `effective_llm_api_key` properties prefer `llm_*` over `openrouter_*`.

### Embeddings
```python
embedding_provider:  str = "openrouter"                              # EMBEDDING_PROVIDER
embedding_model:     str = "google/gemini-embedding-2-preview"       # EMBEDDING_MODEL
embedding_dimension: int = 3072                                       # EMBEDDING_DIMENSION
```

### Chunking
```python
chunk_token_size:     int = 600    # CHUNK_TOKEN_SIZE         (runtime-overridable)
chunk_overlap:        int = 80     # CHUNK_OVERLAP            (runtime-overridable)
chunks_to_combine:    int = 1      # CHUNKS_TO_COMBINE        (runtime-overridable)
max_token_chunk_size: int = 20000  # MAX_TOKEN_CHUNK_SIZE
```

### Schema discovery
```python
schema_discovery_sample_size: int = 5      # SCHEMA_DISCOVERY_SAMPLE_SIZE
schema_discovery_max_chars:   int = 12000  # SCHEMA_DISCOVERY_MAX_CHARS
```

### Pipeline
```python
ingest_concurrency:        int   = 4      # INGEST_CONCURRENCY
checkpoint_every_chunks:   int   = 5      # CHECKPOINT_EVERY_CHUNKS
enable_post_processing:    bool  = True   # ENABLE_POST_PROCESSING
enable_similar_chunks:     bool  = True   # ENABLE_SIMILAR_CHUNKS
enable_entity_embeddings:  bool  = True   # ENABLE_ENTITY_EMBEDDINGS
enable_community_embeddings: bool = True  # ENABLE_COMMUNITY_EMBEDDINGS
entity_embedding_batch:    int   = 64     # ENTITY_EMBEDDING_BATCH
knn_min_score:             float = 0.8    # KNN_MIN_SCORE
```

### Flask + logging + CORS
```python
flask_env:    str = "development"               # FLASK_ENV
flask_host:   str = "0.0.0.0"                   # FLASK_HOST
flask_port:   int = 8000                        # FLASK_PORT
log_level:    str = "INFO"                      # LOG_LEVEL
cors_origins: str = "http://localhost:5173"     # CORS_ORIGINS (comma-separated)
```

### Domain (label for sidebar)
```python
domain: str = "medical"                          # DOMAIN
```

### LLM audit log
```python
llm_log_db_path:        str  = "backend/data/llm_calls.db"  # LLM_LOG_DB_PATH
llm_log_enabled:        bool = True                          # LLM_LOG_ENABLED
llm_log_max_body_chars: int  = 200000                        # LLM_LOG_MAX_BODY_CHARS
```

### App state DB
```python
app_state_db_path: str = "backend/data/text2graph.db"        # APP_STATE_DB_PATH
```

### Chat
```python
chat_db_path:                       str   = "backend/data/chat.db"  # CHAT_DB_PATH
chat_history_max_messages:          int   = 200                      # CHAT_HISTORY_MAX_MESSAGES
chat_summary_token_target:          int   = 1500                     # CHAT_SUMMARY_TOKEN_TARGET
chat_top_k:                         int   = 5                        # CHAT_TOP_K
chat_doc_split_size:                int   = 3000                     # CHAT_DOC_SPLIT_SIZE
chat_embedding_filter_threshold:    float = 0.10                     # CHAT_EMBEDDING_FILTER_THRESHOLD
```

---

## 3. `.env` template

Drop in `<repo_root>/.env`:

```bash
# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-password
NEO4J_DATABASE=neo4j

# LLM (OpenRouter, OpenAI, LM Studio, ...)
OPENROUTER_API_KEY=sk-or-...
LLM_MODEL=google/gemini-2.5-flash
# LLM_BASE_URL=http://localhost:1234/v1     # for LM Studio

# Embeddings
EMBEDDING_PROVIDER=openrouter
EMBEDDING_MODEL=google/gemini-embedding-2-preview
EMBEDDING_DIMENSION=3072

# Pipeline
INGEST_CONCURRENCY=4

# Misc
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:5173
DOMAIN=medical
```

`.env.example` in the repo enumerates all keys with safe defaults.

---

## 4. `create_app()` lifecycle

```python
def create_app() -> Flask:
    init_logging(get_settings().log_level)           # stdlib + structlog (see log-system.md)
    app = Flask(__name__)
    CORS(app, origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()])
    neo4j_manager.configure(get_settings())          # opens driver; lazy
    register_blueprints(app)                          # all /api blueprints
    register_error_handlers(app)                      # AppError → JSON
    register_cli(app)                                 # see cli.md
    return app
```

`wsgi.py`:

```python
from app import create_app
app = create_app()

if __name__ == "__main__":
    s = get_settings()
    app.run(host=s.flask_host, port=s.flask_port, debug=(s.flask_env == "development"))
```

---

## 5. Frontend exposure (`/api/config`)

```http
GET /api/config
```

Returns a sanitized view (no secrets):

```json
{
  "neo4j":   { "uri": "bolt://...", "username": "neo4j", "database": "neo4j" },
  "llm":     { "model": "google/gemini-2.5-flash", "base_url": "https://...", "configured": true },
  "embedding": { "provider": "openrouter", "model": "...", "dimension": 3072 },
  "chunking":  { "token_size": 600, "overlap": 80, "combine": 1 },
  "schema_discovery": { "sample_size": 5, "max_chars": 12000 },
  "domain": "medical"
}
```

Consumed by `useAppConfig()` in `AppShell` to show the domain badge etc.

---

## 6. Runtime overrides

Settings UI (`SettingsPage`) writes `embedding_*`, `llm_*`, `neo4j_*` via PUT to `/api/settings/llm`, `/api/settings/neo4j`. Those endpoints call `SettingsRepository().save(...)` then `reload_settings()`. The runtime tunables (extraction retries, chunk size) use the separate [Settings System](./settings-system.md) (also backed by `app_settings`, different keys).

---

## 7. Common operations

| Need | How |
|---|---|
| Add a new env-only knob | Append a field to `Settings` with default + env name. |
| Add a runtime-tunable knob | Append a `SettingSpec` (see [Settings System](./settings-system.md)). |
| Add a UI-settable LLM/Neo4j field | Append to `Settings`, add to `SettingsRepository.ALLOWED_KEYS`, add to `/api/settings` view + PUT. |
| Pick up a fresh override mid-process | `reload_settings()`. |
