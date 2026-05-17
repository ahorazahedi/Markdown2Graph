# LLM Calls + Recording Reference

End-to-end LLM audit. Every chat completion sent through the app is recorded — request, response, tokens, latency, errors, plus a user-defined `tag` that says *why* the call was made. Browser-side viewer lets you filter, inspect, and compare. Zero plumbing at the callsite: tagging is context-local, the recorder is a LangChain callback.

---

## 1. Architecture

```
service code
   │
   │ with with_tag("entity_extraction"):
   │     llm = build_chat_llm()              ──┐
   │     llm.invoke([SystemMessage, ...])      │
   │                                            ▼
   │                                ┌────────────────────────┐
   │                                │  ChatOpenAI            │
   │                                │  callback=LLMCallRecorder
   │                                └─────────┬──────────────┘
   │                                          │ on_chat_model_start
   │                                          │   → insert_pending(...)
   │                                          │ on_llm_end / on_llm_error
   │                                          │   → mark_success / mark_error
   │                                          ▼
   │                                ┌────────────────────────┐
   │                                │  llm_calls.db (SQLite) │
   │                                └─────────┬──────────────┘
   │                                          │
   ▼                                          ▼
HTTP                                  GET /api/llm-calls
                                      GET /api/llm-calls/<id>
                                      GET /api/llm-calls/tags
                                      GET /api/llm-calls/stats
                                      DELETE /api/llm-calls
                                                │
                                                ▼
                                       LLMCallsPage (React)
```

Two important separations:
- **Different DB from the rest of app state.** `llm_calls.db` is independent — you can wipe it without touching documents, jobs, prompts.
- **Tagging is context-local** (Python `ContextVar`). The recorder reads `current_tag()` at the moment the callback fires — callers never plumb tags through function signatures.

---

## 2. Client — `build_chat_llm()`

**File:** `backend/app/llm/client.py`

```python
def build_chat_llm(settings: Settings | None = None, *, tag: str | None = None) -> ChatOpenAI:
    """Build a chat LLM with the audit-log callback attached.

    `tag` is optional — pass it for one-off calls outside a `with_tag(...)` block.
    """
    s = settings or get_settings()
    callbacks = []
    if s.llm_log_enabled:
        callbacks.append(LLMCallRecorder(
            model=s.llm_model,
            base_url=s.llm_base_url,
            provider="openrouter" if "openrouter" in (s.llm_base_url or "") else "openai-compatible",
        ))
    llm = ChatOpenAI(
        model=s.llm_model,
        temperature=s.llm_temperature,
        max_tokens=s.llm_max_tokens,
        timeout=s.llm_timeout,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        callbacks=callbacks,
    )
    if tag:
        # Wrap invoke/stream in a with_tag(...) shim
        ...
    return llm
```

- **Provider model.** Single `ChatOpenAI` instance against any OpenAI-compatible endpoint (OpenRouter, LM Studio, local servers). No provider switch needed in callers.
- **Defaults.** `temperature=0.0`, `max_tokens=4096`, `timeout=120`. Tune per-deployment via settings.
- **Disable recording.** Set `llm_log_enabled=False` in settings — callback isn't attached, no DB writes.

### Embeddings

Same provider story. `build_embedding_model()` returns either an OpenAI-compatible client or a local HuggingFace model (fallback). Default model `google/gemini-embedding-2-preview`, 3072 dimensions. Custom `_OpenAICompatEmbedder` handles batch requests with exponential backoff on 429/5xx.

---

## 3. Tagging — `with_tag(...)`

**File:** `backend/app/llm/recorder.py`

```python
_current_tag: ContextVar[str] = ContextVar("llm_call_tag", default="uncategorized")

@contextmanager
def with_tag(tag: str):
    token = _current_tag.set(tag)
    try:
        yield
    finally:
        _current_tag.reset(token)

def current_tag() -> str:
    return _current_tag.get()
```

Usage at every callsite that wants a meaningful tag:

```python
from ..llm import build_chat_llm, with_tag

llm = build_chat_llm()
with with_tag("entity_extraction"):
    graph_docs = transformer.convert_to_graph_documents(docs)   # any LLM call inside is tagged
```

Why `ContextVar`:
- Survives across function boundaries without parameter plumbing.
- Thread-safe by construction.
- Works inside `asyncio` tasks (each task has its own context).
- The recorder reads the tag at *callback fire time*, so nested `with_tag(...)` blocks work.

Default tag is `"uncategorized"` — if you forget to wrap, the call is still recorded, just less identifiable.

### Conventional tags in this codebase

| Tag | Where |
|---|---|
| `schema_discovery` | `services/schema_discovery.py` |
| `entity_extraction` | `services/entity_extractor.py` |
| `graph_cleanup` | `services/post_processing.py` |
| `community_summary` | `services/post_processing.py` |
| `chat_rewrite` | `services/chat_service.py` (history-aware question rewriter) |
| `chat_retrieve` | `services/chat_service.py` (retrieval planner) |
| `chat_answer` | `services/chat_service.py` (final RAG answer) |

Tags become the primary filter in the UI, so make them stable and human-readable.

---

## 4. The recorder

**File:** `backend/app/llm/recorder.py`

A LangChain `BaseCallbackHandler` that hooks chat models + text LLMs.

```python
class LLMCallRecorder(BaseCallbackHandler):
    def __init__(self, model, base_url, provider="openai-compatible"):
        self._repo = LLMCallRepository()
        self._inflight: dict[str, dict] = {}     # run_id -> {call_id, started}
        self._enabled = get_settings().llm_log_enabled
        self.model, self.base_url, self.provider = model, base_url, provider

    # --- callback hooks ---

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        request = {"messages": [
            {"type": m.type, "content": m.content}
            for msg_list in messages for m in msg_list
        ]}
        self._begin(run_id, request, kwargs)

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self._begin(run_id, {"prompts": prompts}, kwargs)

    def on_llm_end(self, response, *, run_id, **kwargs):
        rec = self._inflight.pop(str(run_id), None)
        if not rec: return
        text, payload = self._flatten(response)
        usage = self._usage(response) or {}
        self._repo.mark_success(
            rec["call_id"],
            finished_at=_now_iso(),
            latency_ms=int((time.time() - rec["started"]) * 1000),
            response_text=text,
            response_json=payload,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

    def on_llm_error(self, error, *, run_id, **kwargs):
        rec = self._inflight.pop(str(run_id), None)
        if not rec: return
        self._repo.mark_error(
            rec["call_id"],
            finished_at=_now_iso(),
            latency_ms=int((time.time() - rec["started"]) * 1000),
            error=f"{type(error).__name__}: {error}",
        )

    # --- internal ---

    def _begin(self, run_id, request, kwargs):
        if not self._enabled: return
        call_id = self._repo.insert_pending(
            created_at=_now_iso(),
            tag=current_tag(),
            model=self.model, base_url=self.base_url, provider=self.provider,
            request_json=request,
            extra={
                "invocation_params": kwargs.get("invocation_params"),
                "metadata": kwargs.get("metadata"),
            },
        )
        self._inflight[str(run_id)] = {"call_id": call_id, "started": time.time()}
```

Properties:
- **Pending row written first.** If the process dies mid-call, the row is left as `status='pending'` — a useful signal that something hung.
- **`_inflight` is keyed by LangChain's `run_id`** so concurrent invocations don't cross-talk.
- **Tag is read at `_begin`** — the tag in effect when the call starts.
- **`invocation_params` + `metadata`** (from LangChain) go to `extra_json` for debugging odd model behavior.

---

## 5. Storage

**File:** `backend/app/repositories/llm_call_repository.py`

Standalone SQLite at `backend/data/llm_calls.db`, WAL mode, max body size truncated at 200,000 chars (configurable, suffix `…[truncated N chars]`).

### 5.1 Schema

```sql
CREATE TABLE llm_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT    NOT NULL,                  -- ISO 8601 UTC
    finished_at       TEXT,                              -- NULL while pending
    tag               TEXT    NOT NULL,                  -- e.g. entity_extraction
    model             TEXT,
    base_url          TEXT,
    provider          TEXT,
    status            TEXT    NOT NULL,                  -- pending | success | error
    latency_ms        INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    request_json      TEXT    NOT NULL,                  -- {messages:[...]} or {prompts:[...]}
    response_text     TEXT,                              -- raw completion (truncated if huge)
    response_json     TEXT,                              -- structured generations payload
    error             TEXT,                              -- error message if status=error
    extra_json        TEXT                               -- {invocation_params, metadata}
);

CREATE INDEX idx_llm_calls_tag        ON llm_calls(tag);
CREATE INDEX idx_llm_calls_status     ON llm_calls(status);
CREATE INDEX idx_llm_calls_created_at ON llm_calls(created_at DESC);
```

### 5.2 Repo methods

```python
class LLMCallRepository:
    def insert_pending(self, *, created_at, tag, model, base_url, provider,
                       request_json, extra=None) -> int: ...
    def mark_success(self, call_id, *, finished_at, latency_ms,
                     response_text, response_json,
                     prompt_tokens, completion_tokens, total_tokens) -> None: ...
    def mark_error(self, call_id, *, finished_at, latency_ms, error: str) -> None: ...

    def list(self, *, tag=None, status=None, limit=50, offset=0) -> list[dict]: ...
    def get(self, call_id: int) -> dict | None: ...
    def count(self, *, tag=None, status=None) -> int: ...
    def distinct_tags(self) -> list[str]: ...
    def stats(self) -> dict: ...      # {total, ok, err, pending, tokens, avg_latency_ms}
    def clear(self) -> int: ...       # wipe everything
```

`list()` returns slim rows (no `request_json`/`response_*`). `get()` returns the full row with JSON fields parsed.

---

## 6. HTTP endpoints

**File:** `backend/app/api/llm_calls.py`

| Endpoint | Purpose |
|---|---|
| `GET /api/llm-calls?tag=&status=&limit=&offset=` | Paginated list (limit 1–500) |
| `GET /api/llm-calls/<int:call_id>` | Full detail incl. request/response |
| `GET /api/llm-calls/tags` | `{tags: [...]}` — for the filter dropdown |
| `GET /api/llm-calls/stats` | `{total, ok, err, pending, tokens, avg_latency_ms}` |
| `DELETE /api/llm-calls` | `{deleted: N}` — nukes the audit log |

Response shapes match the TypeScript types in §8.

---

## 7. Prompts (related but separate)

The system stores editable prompt templates in `text2graph.db` (different DB from calls):

```sql
CREATE TABLE prompts (
    key          TEXT PRIMARY KEY,        -- e.g. chat_system
    template     TEXT NOT NULL,           -- Jinja2 template
    description  TEXT NOT NULL DEFAULT '',
    variables    TEXT NOT NULL DEFAULT '[]',
    is_custom    INTEGER NOT NULL DEFAULT 0,
    default_hash TEXT,                    -- sha1(disk default), for "is default?" detection
    updated_at   TEXT NOT NULL
);
```

**File:** `backend/app/services/prompt_store.py`

Specs registered up-front (`SPECS`); on startup, defaults are seeded from `backend/app/prompts/templates/*.md`. Users can edit prompts (`is_custom=1`), reset them to the on-disk default, or apply a whole preset (e.g. `medical/`) which copies preset files into the DB.

```python
class PromptStore:
    def render(self, key: str, **vars) -> str: ...     # fetch + Jinja render
    def preview(self, template: str, vars) -> str: ...  # render without saving
    def save(self, key, template) -> dict | None: ...  # validate + save + mark custom
    def reset(self, key) -> dict | None: ...           # restore disk default
    def apply_preset(self, name: str) -> dict: ...     # copy a preset dir into DB
```

**There is no foreign key from `llm_calls` to `prompts`.** Each call's `request_json` contains the exact messages that were sent, so the prompt is reconstructable from the record. If you need strict prompt versioning, hash the rendered template into `extra_json` at callsite or add a `prompt_version` column.

Example callsite:

```python
sys_prompt = PromptStore().render(
    "schema_discovery_system",
    extra_instructions=(extra or "").strip(),
)
llm = build_chat_llm()
with with_tag("schema_discovery"):
    resp = llm.invoke([SystemMessage(sys_prompt), HumanMessage(user_prompt)])
```

---

## 8. Frontend

### 8.1 Types — `frontend/src/lib/api.ts`

```ts
export interface LLMCallRow {
  id: number;
  created_at: string;
  finished_at: string | null;
  tag: string;
  model: string | null;
  status: "pending" | "success" | "error";
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error: string | null;
}

export interface LLMCallDetail extends LLMCallRow {
  base_url: string | null;
  provider: string | null;
  request_json: any;                    // {messages:[{type,content},...]} | {prompts:[...]}
  response_text: string | null;
  response_json: any;
  extra_json: any;                      // {invocation_params, metadata}
}

export interface LLMLogStats {
  total: number; ok: number; err: number;
  pending: number; tokens: number; avg_latency_ms: number;
}

api.llmCalls = (p: {tag?, status?, limit?, offset?}) =>
  jsonFetch<{ items: LLMCallRow[]; total: number; limit: number; offset: number }>(...);
api.llmCall  = (id) => jsonFetch<LLMCallDetail>(`/api/llm-calls/${id}`);
api.llmTags  = ()   => jsonFetch<{ tags: string[] }>("/api/llm-calls/tags");
api.llmStats = ()   => jsonFetch<LLMLogStats>("/api/llm-calls/stats");
api.llmClear = ()   => jsonFetch<{ deleted: number }>("/api/llm-calls", { method: "DELETE" });
```

### 8.2 Page — `frontend/src/pages/LLMCallsPage.tsx`

Two-pane layout (same shape as `JobsPage`):

```
┌──────────────────────────────────────────────────────────────┐
│ LLM Calls                                                    │
│ Stats: total · ok · err · pending · tokens · avg latency     │
│ Filters: [Tag ▼] [Status ▼]  [☐ auto-refresh]  [Refresh] [Delete all] │
├──────────────────┬───────────────────────────────────────────┤
│ List (340px)     │ Detail                                    │
│                  │ Meta strip · model · provider · latency · │
│ #123 [tag] ok    │   tokens · created                        │
│   2m ago, 432ms  │                                           │
│   1,250 tok      │ [error banner if failed]                  │
│                  │                                           │
│ #122 [tag] err   │ ┌─ Request ───┬─ Response ──┐             │
│   3m ago, 1.2s   │ │ messages…    │ completion │             │
│                  │ │ pretty/raw   │ pretty/raw │             │
│ Total: 500       │ │ copy         │ copy       │             │
│ [Prev] [Next]    │ └──────────────┴────────────┘             │
└──────────────────┴───────────────────────────────────────────┘
```

State + polling:

```tsx
const [tag, setTag] = useState("");
const [status, setStatus] = useState("");      // "" | pending | success | error
const [offset, setOffset] = useState(0);
const [auto, setAuto] = useState(false);

const refresh = async () => {
  const list = await api.llmCalls({
    tag: tag || undefined,
    status: status || undefined,
    limit: 30,
    offset,
  });
  // ...
};

useEffect(() => { refresh(); }, [tag, status, offset]);

useEffect(() => {
  if (!auto) return;
  const id = window.setInterval(refresh, 3000);
  return () => window.clearInterval(id);
}, [auto, tag, status, offset]);
```

### 8.3 Row + detail rendering

- **List row** — status icon (CheckCircle2 green / XCircle red / Loader2 spinning), tag badge, short ID, relative time, latency, total tokens.
- **Detail meta** — same fields as row + model + provider + created time.
- **Error banner** — only when `status === "error"`, red background, monospace error text.
- **Request block** — if `request_json.messages` exists, render each message as `[type] content` (system/human/ai). Else dump prompts. Pretty/Raw toggle (parse JSON or keep text). Copy-to-clipboard button.
- **Response block** — `response_text` for the easy case; `response_json` for tool-call/structured outputs. Pretty/Raw toggle. Copy button.
- **Syntax-highlight tokens** in pretty mode: keys → `text-primary`, strings → `text-foreground`, numbers → `text-[hsl(var(--success))]`, booleans → `text-[hsl(var(--warning))]`, null → `text-muted-foreground`.

### 8.4 Delete-all

`api.llmClear()` is guarded by the global `confirm({variant:"destructive"})` dialog before firing. Returns `{deleted: N}` so the UI can show a toast.

---

## 9. Configuration

**File:** `backend/app/config.py`

```python
# LLM API (OpenRouter or any OpenAI-compatible endpoint, e.g. LM Studio)
llm_base_url: Optional[str] = None
llm_api_key: Optional[str] = None
llm_model: str = "google/gemini-2.5-flash"
llm_temperature: float = 0.0
llm_max_tokens: int = 4096
llm_timeout: int = 120

# Audit log
llm_log_db_path: str = "backend/data/llm_calls.db"
llm_log_enabled: bool = True
llm_log_max_body_chars: int = 200000

# Embeddings
embedding_provider: str = "openrouter"
embedding_model: str = "google/gemini-embedding-2-preview"
embedding_dimension: int = 3072
```

Override via env vars (`LLM_MODEL`, `LLM_LOG_ENABLED`, etc.) or runtime settings UI.

---

## 10. Retention + linkage

- **No automatic expiry.** Logs accumulate forever until you `DELETE /api/llm-calls`.
- **Body truncation** at 200,000 chars per field — long contexts get clipped with a clear suffix.
- **No FK to jobs.** If you want to pivot from a job to its LLM calls, filter by `tag` + `created_at` window (jobs record their start/end times in `ingest_runs`). Adding a `job_id` column to `llm_calls` is a sensible enhancement if you need direct linkage; the recorder would set it from a new `current_job_id()` `ContextVar`.
- **No FK to prompts.** The request body is the canonical record of what was sent.

---

## 11. End-to-end flow

1. Service code wraps work in `with with_tag("entity_extraction"):`.
2. `build_chat_llm()` returns a `ChatOpenAI` with `LLMCallRecorder` attached.
3. `llm.invoke(messages)` → LangChain fires `on_chat_model_start`.
4. Recorder writes a `pending` row to `llm_calls.db` and remembers `(call_id, start_ts)`.
5. Provider responds → `on_llm_end` → recorder updates the row with response, tokens, latency.
6. (Or error → `on_llm_error` → recorder marks row as `error` with message and latency.)
7. Frontend `LLMCallsPage` lists rows from `/api/llm-calls` and opens details on click.
8. Operator can clear the entire log from the UI.

Total integration cost for a new service: one `with_tag(...)` block. Everything else is free.

---

## 12. Files

**Backend**
- `backend/app/llm/__init__.py` — re-exports `build_chat_llm`, `with_tag`, `current_tag`
- `backend/app/llm/client.py` — chat LLM + embedding builders
- `backend/app/llm/recorder.py` — `LLMCallRecorder` + tag ContextVar
- `backend/app/repositories/llm_call_repository.py` — SQLite schema + queries
- `backend/app/api/llm_calls.py` — HTTP endpoints
- `backend/app/services/prompt_store.py` — prompt CRUD + presets
- `backend/app/repositories/app_state_repository.py` — `prompts` table

**Frontend**
- `frontend/src/lib/api.ts` — typed client + `LLMCallRow` / `LLMCallDetail`
- `frontend/src/pages/LLMCallsPage.tsx` — list + detail viewer
- `frontend/src/pages/PromptsPage.tsx` — prompt editor (separate concern)

---

## 13. Porting checklist

1. Copy `recorder.py` + `client.py` (drop the embedder if you don't need it).
2. Copy the `llm_calls` schema and a slim `LLMCallRepository`.
3. Add five HTTP endpoints (list/get/tags/stats/delete).
4. At every LLM callsite, wrap with `with with_tag("..."):`.
5. Copy `LLMCallsPage.tsx` and wire the four `api.llm*` methods.
6. If you want job linkage, add a `job_id` column + a `current_job_id` ContextVar set inside the job runner.

Resist building the same audit log on top of opaque provider dashboards — having request/response text locally, in one searchable place, is the entire point.
