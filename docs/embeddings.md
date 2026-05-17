# Embeddings Reference

OpenAI-compatible client (OpenRouter, LM Studio) with retry + backoff, or local sentence-transformers fallback. Lifecycle service handles re-embed and model switch with cancel support.

**Files:**
- `backend/app/services/embedding_service.py`
- `backend/app/llm/client.py` (embedder factory + `_OpenAICompatEmbedder`)
- `backend/app/api/embeddings_api.py`
- `frontend/src/pages/EmbeddingsPage.tsx`

---

## 1. Client: `_OpenAICompatEmbedder`

LangChain `Embeddings` protocol. Always sends `encoding_format=float` (required for Google Gemini embeddings via OpenRouter).

```python
class _OpenAICompatEmbedder(Embeddings):
    def __init__(self, *, model, api_key, base_url, batch_size=32, timeout=60):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.batch_size = max(1, int(batch_size))
        self.timeout = timeout

    def embed_documents(self, texts):
        url = f"{self.base_url}/embeddings"
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json",
                   "HTTP-Referer": "https://github.com/text2graph",
                   "X-Title": "text2graph"}
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t if t else " " for t in texts[i:i + self.batch_size]]
            body = {"model": self.model, "input": batch, "encoding_format": "float"}
            attempts, delay = 5, 2.0
            for attempt in range(attempts):
                resp = requests.post(url, headers=headers, json=body, timeout=self.timeout)
                payload = resp.json()
                if payload and "data" in payload and payload["data"]:
                    break
                transient = (resp.status_code in (429, 500, 502, 503, 504)
                             or "RESOURCE_EXHAUSTED" in str(payload) or "rate" in str(payload).lower())
                if not transient or attempt == attempts - 1:
                    break
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
            data = sorted(payload["data"], key=lambda d: d.get("index", 0))
            out.extend([d["embedding"] for d in data])
        return out

    def embed_query(self, text): return self.embed_documents([text])[0]
```

Retry: 5 attempts, exponential backoff (2s → 30s cap). Treats `429`, `5xx`, `RESOURCE_EXHAUSTED`, or any "rate"-containing message as transient.

### Factory

```python
def build_embedder(settings=None) -> Tuple[object, int]:
    s = settings or get_settings()
    provider = s.embedding_provider.strip().lower()
    if provider in ("sentence-transformers", "huggingface", "local"):
        return _local_embedder(s.embedding_model), s.embedding_dimension
    if provider in ("openai", "openrouter", "openai-compatible", "lm-studio"):
        return _OpenAICompatEmbedder(
            model=s.embedding_model,
            api_key=s.effective_llm_api_key,
            base_url=s.effective_llm_base_url,
            batch_size=32,
        ), s.embedding_dimension
    raise ValueError(f"unknown embedding_provider: {provider}")
```

---

## 2. EmbeddingService

```python
NODE_TYPES = ("chunk", "entity", "community")

class EmbeddingService:
    def __init__(self, repo: GraphRepository | None = None):
        self.repo = repo or GraphRepository()

    def status(self) -> dict:
        """Snapshot per type: total, by_model counts, stale (mismatched model), index_dim."""
        ...

    def reembed(self, *, scope="missing", types=NODE_TYPES,
                model=None, dim=None, provider=None, clear_first=False,
                update=None, is_cancelled=None) -> dict:
        ...

    def switch_model(self, *, model, dim, provider=None,
                     update=None, is_cancelled=None, types=NODE_TYPES) -> dict:
        """Persist new model/dim → reload settings → reembed(scope='all', clear_first=True)."""
        SettingsRepository().save({"embedding_model": model, "embedding_dimension": str(dim),
                                   **({"embedding_provider": provider} if provider else {})})
        reload_settings()
        return self.reembed(scope="all", types=tuple(types), model=model, dim=dim,
                            provider=provider, clear_first=True,
                            update=update, is_cancelled=is_cancelled)

    def clear(self, *, node_types=NODE_TYPES, where_model=None) -> dict:
        """Null out embeddings. Does NOT drop vector index."""
        ...
```

---

## 3. `reembed()` lifecycle

```python
def reembed(self, *, scope, types, model, dim, provider, clear_first, update, is_cancelled):
    s = get_settings()
    target_model = model or s.embedding_model
    target_dim   = dim   or s.embedding_dimension

    # Build embedder once
    embedder, build_dim = build_embedder(s)
    if build_dim != target_dim:
        target_dim = build_dim                       # client always wins

    report = {"scope": scope, "model": target_model, "dim": target_dim, "types": {}}

    for nt in types:
        if is_cancelled and is_cancelled(): break

        type_report = {"cleared": 0, "embedded": 0, "skipped": 0, "errors": []}

        # 1. Optional wipe
        if clear_first or scope == "all":
            type_report["cleared"] = self.repo.clear_embeddings(nt)

        # 2. Drop+recreate index if dim changed
        idx_name = self._index_name(nt)
        current_dim = self.repo.vector_index_dim(idx_name)
        if current_dim is not None and current_dim != target_dim:
            self.repo.drop_vector_index(idx_name)

        # 3. Determine listing scope
        list_scope = "missing" if (clear_first or scope == "all") else scope
        pending = self.repo.list_nodes_for_embedding(
            nt, scope=list_scope,
            target_model=target_model, target_dim=target_dim,
            limit=200_000,
        )

        # 4. Batch embed
        batch_n = max(8, int(s.entity_embedding_batch))
        done = 0
        for i in range(0, len(pending), batch_n):
            if is_cancelled and is_cancelled(): break          # check BEFORE batch
            batch = pending[i:i + batch_n]
            texts = [self._truncate(nt, r.get("text") or " ") for r in batch]
            vectors = embedder.embed_documents(texts)
            self.repo.write_embeddings_unified(
                nt,
                [{"id": r["id"], "embedding": v} for r, v in zip(batch, vectors)],
                model=target_model, dim=target_dim,
            )
            done += len(batch)
            if is_cancelled and is_cancelled(): break          # AND after each batch
            if update: update(JobUpdate(stage="reembed", message=f"{nt}: {done}/{len(pending)}",
                                       progress=done/max(1,len(pending)),
                                       extra={"type": nt}))

        type_report["embedded"] = done

        # 5. Recreate index
        self._ensure_index(nt, target_dim)
        report["types"][nt] = type_report

    return report
```

### Scope semantics

| `scope` | What gets embedded |
|---|---|
| `"missing"` | Only nodes where `embedding IS NULL` |
| `"stale"` | Missing OR `embedding_model != target` OR `embedding_dim != target_dim` |
| `"all"` | Forces `clear_first=True`; everything re-embedded |

### Text truncation per type

```python
def _truncate(node_type, text):
    if node_type == "chunk":   return text[:8000]
    if node_type == "entity":  return text[:2000]
    return text[:4000]                                       # community
```

Entity text constructed as `f"{id} — {description}"`. Community text constructed as `f"{title} — {summary}"`.

---

## 4. Cancel handling

`is_cancelled()` checked:
- Between node-type loops (`for nt in types`).
- **Before** starting each batch.
- **After** each batch completes (so an in-flight batch isn't wasted, but we still bail out fast).

Recent commit `fix(embeddings): honor cancel between batches and after each type` enforces this.

---

## 5. HTTP endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/embeddings/status` | Per-type counts + current model |
| `POST /api/embeddings/reembed` | Async job; body `{scope, types, model?, dim?, clear_first?}` |
| `POST /api/embeddings/switch-model` | Async job; body `{model, dim, provider?}` |
| `DELETE /api/embeddings` | Null out embeddings; body `{types, where_model?, confirm: true}` |

All re-embed/switch operations return `{job_id}` — track via [Job System](./job-system.md).

---

## 6. Frontend (`EmbeddingsPage.tsx`)

Shows:
- Header strip: current model + dim + provider.
- Per-type cards: total, embedded, missing, stale, by-model breakdown.
- Re-embed controls: scope dropdown, type checkboxes, optional model override, clear-first checkbox.
- Switch-model dialog: model + dim required, destructive confirmation (clears + re-embeds everything).
- Live progress via running job (banner + page poll).

---

## 7. Configuration

```python
embedding_provider:  str = "openrouter"            # openrouter | openai | local | lm-studio
embedding_model:     str = "google/gemini-embedding-2-preview"
embedding_dimension: int = 3072
entity_embedding_batch: int = 64                   # batch size (clamped to >= 8)
```

After a `switch_model` job, `embedding_model` and `embedding_dimension` are persisted to `app_settings` (runtime override). `reload_settings()` picks them up immediately.
