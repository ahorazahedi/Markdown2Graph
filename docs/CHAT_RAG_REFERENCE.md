# Graph Chat + RAG — Reference (from `llm-graph-builder`)

Notes for porting chat/RAG over an existing Neo4j knowledge graph into our own frontend. Sourced from `llm-graph-builder/backend` (FastAPI + LangChain) and `llm-graph-builder/frontend/src/components/ChatBot`.

---

## 1. High-level flow

```
User question ─▶ POST /chat_bot ─▶ QA_RAG()
                                    │
                                    ├─ load session history from Neo4j (ChatMessageHistory node)
                                    ├─ pick retriever by `mode`
                                    │     ├─ vector / fulltext / hybrid → Neo4jVector
                                    │     ├─ entity_vector              → __Entity__ index + local community
                                    │     ├─ global_vector              → __Community__ summaries
                                    │     └─ graph                      → GraphCypherQAChain (text→Cypher)
                                    ├─ history-aware rewrite of the question (LLM)
                                    ├─ retrieve top-k docs/entities/communities
                                    ├─ format context (token-budget aware)
                                    ├─ LLM answer with system template
                                    └─ persist AI message + return sources/entities/communities
```

Backed by Neo4j vector index + optional fulltext (`keyword`) index. Post-processing job (`/post_processing`) builds them.

---

## 2. HTTP API (consumed by frontend)

Base: backend FastAPI (`score.py`). All POSTs are `application/x-www-form-urlencoded` plus Neo4j creds (`uri`, `userName`, `password`, `database`, `email`) injected via `get_neo4j_credentials`.

### 2.1 `POST /chat_bot`
Main RAG call.

Form fields:
| field | type | notes |
|-------|------|-------|
| `model` | str | LLM key (`openai_gpt_4o`, `gemini_2.0_flash`, `diffbot`, …) |
| `question` | str | user message |
| `document_names` | JSON str | `'["a.pdf","b.txt"]'`. Only used when mode has `document_filter=true` (`vector`, `graph_vector`) |
| `session_id` | str | client-generated UUID, persisted in Neo4j |
| `mode` | str | one of the 7 modes (see §3) |
| `embedding_provider` | str | e.g. `openai` |
| `embedding_model` | str | e.g. `text-embedding-3-small` |

Response `data`:
```json
{
  "session_id": "...",
  "message": "answer text",
  "user": "chatbot",
  "info": {
    "sources": [{"source_name":"file.pdf","page_numbers":[1,3],"start_time":[]}],
    "model": "gpt-4o-2024-08-06",
    "nodedetails": {
      "chunkdetails": [{"id":"<chunkId>","score":0.83}],
      "entitydetails": [{"id":"<elementId>"}],
      "communitydetails": [{"id":"<elementId>"}]
    },
    "total_tokens": 1234,
    "response_time": 2.41,
    "mode": "graph_vector_fulltext",
    "entities": {"entityids":[...], "relationshipids":[...]},
    "metric_details": {...}
  }
}
```

Errors: `{ "status":"Failed", "message":"Unable to get chat response", "error":"..." }`.

### 2.2 `POST /chunk_entities`
Expand citations into actual chunk text + neighbour entities/relationships.

Form: `nodedetails` (JSON of `info.nodedetails`), `entities` (JSON of `info.entities`), `mode`.
Use: when user clicks a source/entity chip in chat to open the "Sources / Entities / Communities" drawer.

### 2.3 `POST /clear_chat_bot`
Form: `session_id`. Wipes session history nodes.

### 2.4 `POST /get_neighbours`
Form: `elementId`. Returns 1-hop subgraph for entity → use to render mini graph beside chat.

### 2.5 Supporting endpoints used by chat UI
- `POST /post_processing` — must be run after extraction; creates vector + `keyword` fulltext + community indexes. Without it, fulltext/entity/global modes 500.
- `POST /metric` and `POST /additional_metrics` — RAGAS / context-recall scoring of answers (optional UI feature).

---

## 3. Chat modes

`CHAT_MODE_CONFIG_MAP` in `backend/src/shared/constants.py:717`.

| mode | index | retrieval | doc filter | when to use |
|------|-------|-----------|------------|-------------|
| `vector` | `vector` (Chunk) | pure semantic top-k | yes | quick semantic QA, scoped to selected docs |
| `fulltext` | `vector` + `keyword` | hybrid BM25 + vector | no | keyword-heavy queries, acronyms |
| `graph_vector` | `vector` (Chunk) + expand to entities | vector then graph hop | yes | default for doc-scoped graph-aware QA |
| `graph_vector_fulltext` | `vector` + `keyword` + graph | hybrid + graph expand | no | **default** (`CHAT_DEFAULT_MODE`), broadest recall |
| `entity_vector` | `entity_vector` (__Entity__) | entity semantic + local community | no | "tell me about <entity>" |
| `global_vector` | `community_vector` + `community_keyword` (__Community__) | community summaries | no | high-level / thematic Qs ("what are the main topics") |
| `graph` | — | `GraphCypherQAChain` (text→Cypher→answer) | no | structured queries ("how many X connect to Y") |

Frontend should expose mode picker (chips/dropdown). Persist last choice per session.

---

## 4. Session history

- Stored as a chain of `:Message` nodes off a `:Session {id}` node in Neo4j (`langchain_community.chat_message_histories.Neo4jChatMessageHistory`).
- `session_id` = client UUID. Generate on first message, keep in localStorage.
- `write_access=False` (read-only DB) → history kept in-memory map `HISTORY_STORE` (`SessionChatHistory`). Frontend doesn't care.
- Summarization: when history > `N` messages, `summarize_and_log()` collapses old turns into a single `SystemMessage` to cap tokens.

---

## 5. Retrieval internals (what to surface in UI)

### Sources panel
From `info.sources` — per-document list with page numbers (PDF) or timestamps (YouTube/audio). Render as chips under each AI message; click → open `/chunk_entities` drawer.

### Entities panel
From `info.entities.entityids` + relationship ids. Click → `/get_neighbours` → render as subgraph (NVL / Cytoscape / D3).

### Communities panel
Only populated in `entity_vector` and `global_vector` modes. Each community has `summary`, `title`, `level`. Show as expandable cards.

### Scores
`info.nodedetails.chunkdetails[*].score` — render as a relevance bar.

---

## 6. Token / context budget

`CHAT_TOKEN_CUT_OFF` map (per model) defines max chunks fed to LLM (`format_documents`). Frontend doesn't enforce, but should warn if user selects too many docs in `vector`/`graph_vector` modes (`document_filter=true`).

---

## 7. Prompts

`backend/src/shared/constants.py`:
- `CHAT_SYSTEM_TEMPLATE` — main answer prompt (cite from context only, refuse if missing).
- `QUESTION_TRANSFORM_TEMPLATE` — history-aware rewrite (turns "and the second one?" into standalone).
- `GRAPH_QUERY_GENERATION_PROMPT` — text→Cypher for `graph` mode.

Keep these server-side. Frontend never sees them.

---

## 8. Frontend integration checklist

1. **Connection state** — reuse existing Neo4j connect flow; chat call needs same creds in header/cookie.
2. **Session id** — UUID v4 in `localStorage["chatSessionId"]`. New session button → `clear_chat_bot` then regenerate.
3. **Mode selector** — dropdown bound to the 7 modes. Default `graph_vector_fulltext`. If `document_filter=true` mode chosen but no docs selected → allow (treated as "all"); reverse case server returns "Please deselect all documents…" — surface as toast.
4. **Message list** — render markdown, code blocks, latex.
5. **Citations row** — Sources / Entities / Communities tabs lazy-loaded via `/chunk_entities` and `/get_neighbours`.
6. **Streaming** — current backend is non-streaming (`asyncio.to_thread`). If we want SSE, wrap `process_chat_response` with `astream` from LangChain. Acceptable to ship non-streaming first.
7. **Model picker** — pull list from `/backend_connection_configuration` (returns enabled LLMs + embedding models).
8. **Error handling** — `{status:"Failed"}` → toast w/ `error`. Common: missing vector index → instruct to run post-processing.
9. **Loading** — show shimmer until `response_time` returns; can be 2–20 s.
10. **Telemetry** — capture `total_tokens`, `response_time`, `mode` per message for cost view.

---

## 9. Minimum viable port (V1)

- Single mode: `graph_vector_fulltext`.
- One LLM, one embedding model from `.env`.
- No communities tab.
- Sources chips only, no neighbour graph.
- Inline session id, no clear button.

Then layer modes, entity panel, neighbour viz, metrics.

---

## 10. Direct Python usage (if we skip HTTP)

```python
from src.QA_integration import QA_RAG
from src.shared.common_fn import create_graph_database_connection

graph = create_graph_database_connection(creds)
result = QA_RAG(
    graph=graph,
    model="openai_gpt_4o",
    question="What does X depend on?",
    document_names="[]",
    session_id="abc-123",
    mode="graph_vector_fulltext",
    embedding_provider="openai",
    embedding_model="text-embedding-3-small",
)
```

Same response shape as §2.1.

---

## 11. SQLite chat persistence (our backend)

Reuses repo's existing SQLite pattern (`backend/data/*.db`, WAL, schema-on-connect, per-file lock — see `app/repositories/app_state_repository.py` and `llm_call_repository.py`).

### 11.1 Where it lives
- New file: `backend/data/chat.db` (separate from `text2graph.db` and `llm_calls.db` so chat history can be wiped/exported independently).
- Config in `app/config.py`:
  ```python
  chat_db_path: str = "backend/data/chat.db"
  chat_history_max_messages: int = 200      # before summarization kicks in
  chat_summary_token_target: int = 1500
  ```
- Repository: `app/repositories/chat_repository.py` (mirror existing repos).

### 11.2 Schema

```sql
-- 1) Conversation = container of messages, one per "chat tab" the user opens.
CREATE TABLE IF NOT EXISTS chat_sessions (
    id              TEXT PRIMARY KEY,           -- UUID v4 (also sent to Neo4j as session_id)
    title           TEXT NOT NULL DEFAULT 'New chat',
    mode            TEXT NOT NULL DEFAULT 'graph_vector_fulltext',
    model           TEXT,                       -- last selected LLM key
    embedding_provider TEXT,
    embedding_model TEXT,
    document_names  TEXT NOT NULL DEFAULT '[]', -- JSON array of filenames scoped to session
    pinned          INTEGER NOT NULL DEFAULT 0, -- 0/1
    archived        INTEGER NOT NULL DEFAULT 0, -- 0/1
    created_at      TEXT NOT NULL,              -- ISO-8601 UTC
    updated_at      TEXT NOT NULL,              -- bumped on every new message
    last_message_at TEXT,                       -- for sort by recency
    message_count   INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    meta_json       TEXT NOT NULL DEFAULT '{}'  -- forward-compat bag
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated     ON chat_sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_archived    ON chat_sessions(archived);

-- 2) Message = single user / assistant / system turn.
CREATE TABLE IF NOT EXISTS chat_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,              -- 'user' | 'assistant' | 'system'
    content         TEXT NOT NULL,
    mode            TEXT,                       -- mode used to generate this answer
    model           TEXT,                       -- LLM that produced assistant turn
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    response_time_ms INTEGER,
    llm_call_id     INTEGER,                    -- FK into llm_calls.db (logical, cross-db)
    error           TEXT,                       -- set when assistant turn failed
    created_at      TEXT NOT NULL,
    -- denormalized citation payload (avoid join blow-up at render time)
    sources_json    TEXT NOT NULL DEFAULT '[]', -- = info.sources
    entities_json   TEXT NOT NULL DEFAULT '{}', -- = info.entities
    nodedetails_json TEXT NOT NULL DEFAULT '{}',-- = info.nodedetails
    metric_json     TEXT NOT NULL DEFAULT '{}', -- = info.metric_details
    meta_json       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session     ON chat_messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_created     ON chat_messages(created_at DESC);

-- 3) Citation row, normalized for "show all chunks I ever cited from doc X" queries.
--    Optional — if cross-message analytics not needed, drop and rely on sources_json.
CREATE TABLE IF NOT EXISTS chat_citations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,              -- 'chunk' | 'entity' | 'community' | 'document'
    ref_id          TEXT NOT NULL,              -- chunk id / entity elementId / community id / filename
    score           REAL,
    label           TEXT,                       -- display name (filename, entity id, etc.)
    extra_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chat_citations_message    ON chat_citations(message_id);
CREATE INDEX IF NOT EXISTS idx_chat_citations_ref        ON chat_citations(kind, ref_id);

-- 4) Per-session running summary (history compaction; mirrors `summarize_and_log`).
CREATE TABLE IF NOT EXISTS chat_summaries (
    session_id      TEXT PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
    summary         TEXT NOT NULL,
    covers_up_to_message_id INTEGER NOT NULL,   -- summary includes messages with id <= this
    updated_at      TEXT NOT NULL,
    token_estimate  INTEGER
);

-- 5) Optional: user feedback per assistant turn.
CREATE TABLE IF NOT EXISTS chat_feedback (
    message_id      INTEGER PRIMARY KEY REFERENCES chat_messages(id) ON DELETE CASCADE,
    rating          INTEGER NOT NULL,           -- -1 / 0 / +1
    comment         TEXT,
    created_at      TEXT NOT NULL
);
```

Notes:
- `chat_sessions.id` is the **same string** sent as `session_id` to `/chat_bot` and stored as a `:Session {id}` node in Neo4j. One source of truth, two stores.
- `chat_messages.llm_call_id` is a logical FK into `llm_calls.db` (separate file). Not enforced — used to deep-link from chat to the LLM call inspector page.
- `*_json` columns hold the API payload verbatim → rendering the message later needs zero re-fetch.
- `ON DELETE CASCADE` requires `PRAGMA foreign_keys=ON;` on every connection (do this in `_connect`).

### 11.3 Repository API (target shape)

```python
class ChatRepository:
    # sessions
    def create_session(self, *, session_id: str, mode: str, model: str | None,
                       embedding_provider: str | None, embedding_model: str | None,
                       document_names: list[str], title: str = "New chat") -> dict: ...
    def get_session(self, session_id: str) -> dict | None: ...
    def list_sessions(self, *, archived: bool = False, limit: int = 50,
                      offset: int = 0, search: str | None = None) -> list[dict]: ...
    def update_session(self, session_id: str, **fields) -> None: ...   # title, mode, pinned, archived...
    def delete_session(self, session_id: str) -> None: ...             # cascades messages

    # messages
    def append_message(self, *, session_id: str, role: str, content: str,
                       model: str | None = None, mode: str | None = None,
                       tokens: tuple[int|None,int|None,int|None] = (None,None,None),
                       response_time_ms: int | None = None,
                       llm_call_id: int | None = None,
                       sources: list | None = None, entities: dict | None = None,
                       nodedetails: dict | None = None, metric: dict | None = None,
                       error: str | None = None) -> int: ...
    def list_messages(self, session_id: str, *, limit: int | None = None,
                      before_id: int | None = None) -> list[dict]: ...
    def get_message(self, message_id: int) -> dict | None: ...

    # citations (auto-populated from append_message when sources/entities given)
    def list_citations(self, message_id: int) -> list[dict]: ...

    # summary
    def upsert_summary(self, session_id: str, *, summary: str,
                       covers_up_to_message_id: int, token_estimate: int) -> None: ...
    def get_summary(self, session_id: str) -> dict | None: ...

    # feedback
    def set_feedback(self, message_id: int, *, rating: int, comment: str | None) -> None: ...
```

### 11.4 Write path on each `/chat_bot` call

1. If `session_id` absent in DB → `create_session(...)`.
2. `append_message(role='user', content=question)` → returns `user_msg_id`.
3. Call `QA_RAG(...)`.
4. On success: `append_message(role='assistant', ..., sources=info.sources, entities=info.entities, ...)`.
   On failure: same but with `error=...` and empty content.
5. Bump `chat_sessions.last_message_at`, `message_count`, `total_tokens`, `updated_at` (single UPDATE in the same txn).
6. If `message_count` % N == 0 → background task → summarize history → `upsert_summary`.

### 11.5 Backend endpoints to add (thin wrappers over repo)

| method | path | purpose |
|--------|------|---------|
| `GET`  | `/api/chat/sessions` | list (filters: archived, search, pinned) |
| `POST` | `/api/chat/sessions` | create (returns `{id, ...}`) |
| `GET`  | `/api/chat/sessions/{id}` | session + messages |
| `PATCH`| `/api/chat/sessions/{id}` | rename / pin / archive / change mode |
| `DELETE`| `/api/chat/sessions/{id}` | hard delete (cascades; also clear Neo4j history via existing `/clear_chat_bot`) |
| `POST` | `/api/chat/sessions/{id}/messages` | wraps `/chat_bot`: persists user msg → calls QA_RAG → persists assistant msg → returns combined payload |
| `POST` | `/api/chat/messages/{id}/feedback` | thumbs up/down + comment |
| `GET`  | `/api/chat/messages/{id}/citations` | expand citations (uses `/chunk_entities` upstream) |

Wrapping `/chat_bot` server-side (vs. frontend calling both) keeps persistence atomic.

### 11.6 Migrations

No Alembic in repo. Follow existing pattern: idempotent `CREATE TABLE IF NOT EXISTS` in `_SCHEMA` string, run on every `_ensure()`. For column additions, add `ALTER TABLE` guarded by `PRAGMA table_info` check (see `app_state_repository.py` for prior examples).

### 11.7 Retention / housekeeping

- Soft delete: `archived=1` hides from list but keeps data.
- Hard delete via `DELETE FROM chat_sessions WHERE id=?` (cascade).
- Optional cron: drop `archived=1 AND updated_at < now-90d`.
- Export: `SELECT * FROM chat_messages WHERE session_id=? ORDER BY id` → JSONL.

---

## 12. Files to read when implementing

Upstream (`llm-graph-builder/`):
| concern | file |
|---------|------|
| endpoints | `backend/score.py:402` (`/chat_bot`), `:444`, `:521` |
| orchestration | `backend/src/QA_integration.py:665` (`QA_RAG`) |
| retriever setup | `backend/src/QA_integration.py:335` (`initialize_neo4j_vector`) |
| mode config | `backend/src/shared/constants.py:717` |
| text→Cypher | `backend/src/QA_integration.py:538` (`create_graph_chain`) |
| chunk/entity expansion | `backend/src/chunkid_entities.py` |
| neighbours | `backend/src/neighbours.py` |
| communities | `backend/src/communities.py`, `post_processing.py` |
| chat UI ref | `frontend/src/components/ChatBot/` |

Our backend (templates for chat repo):
| concern | file |
|---------|------|
| SQLite repo pattern | `backend/app/repositories/app_state_repository.py` |
| LLM call repo (FK target) | `backend/app/repositories/llm_call_repository.py` |
| Config + db paths | `backend/app/config.py` (add `chat_db_path`) |
| API style | `backend/app/api/settings_api.py` |
