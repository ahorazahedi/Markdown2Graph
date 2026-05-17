# Ingestion Pipeline Reference

End-to-end orchestration: markdown → chunks → embeddings → entity extraction → graph write → post-processing. Threaded multi-document execution with cancel + retry + checkpoint.

**File:** `backend/app/services/pipeline.py`

---

## 1. PipelineConfig

```python
@dataclass
class PipelineConfig:
    allowed_nodes: List[str] = field(default_factory=list)            # from active schema
    allowed_relationships: List[Tuple[str, str, str]] = field(default_factory=list)
    extra_instructions: Optional[str] = None                          # per-run guidance
    max_workers: Optional[int] = None                                 # overrides settings.ingest_concurrency
    job_id: Optional[str] = None                                      # cosmetic; pipeline does not own jobs
```

Construct from active schema:

```python
schema = AppStateRepository().get_schema()
cfg = PipelineConfig(
    allowed_nodes=schema["node_labels"],
    allowed_relationships=[tuple(t) for t in schema["triplets"]],
    extra_instructions=schema.get("extra") or None,
)
pipeline = IngestionPipeline(cfg)
```

---

## 2. IngestionPipeline class

```python
class IngestionPipeline:
    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.settings = get_settings()
        self.repo = GraphRepository()                                # Neo4j writes
        self.state = AppStateRepository()                            # SQLite app state
        self.chunker = MarkdownChunker()
        self.extractor = EntityExtractor(
            allowed_nodes=cfg.allowed_nodes,
            allowed_relationships=cfg.allowed_relationships,
            extra_instructions=cfg.extra_instructions,
        )
        self.embedder, self.embed_dim = build_embedder(self.settings)
        self.post = PostProcessor()                                  # chunk vector index + similar-chunk edges
```

---

## 3. Entry points

```python
pipeline.run_pending(progress, is_cancelled)               # all docs with status in {pending, failed}
pipeline.run_documents([id1, id2], reextract=False,
                       progress=..., is_cancelled=...)     # specific docs
```

Both return:

```python
{
    "files": [{"id", "file", "ok", "chunks", "entities", "relationships"}, ...],
    "post_processing": {...},     # from PostProcessor.run()
    "totals": {...},              # from GraphRepository.stats()
    "cancelled": True,            # only if cancelled before post-processing
}
```

`reextract=True` calls `repo.delete_document(file_name)` first — wipes chunks/entities created by prior runs of this doc.

---

## 4. Multi-document execution

`_run_records` runs documents on a `ThreadPoolExecutor` with `max_workers = cfg.max_workers or settings.ingest_concurrency` (default 4). Post-processing happens **once after all docs**, on the main thread.

```python
with ThreadPoolExecutor(max_workers=workers) as ex:
    futures = [ex.submit(_do_one, (i, r)) for i, r in enumerate(records)]
    for fut in as_completed(futures):
        results.append(fut.result())

if is_cancelled():
    return {"files": results, "post_processing": {"skipped": "cancelled"}, ...}

post_stats = self.post.run(progress=progress)
```

---

## 5. Per-document flow (`_process_document`)

1. **Upsert document** in Neo4j, set status `"Processing"`.
2. **Chunk** via `MarkdownChunker.split(file_name, text)`.
3. **Write chunks** + `FIRST_CHUNK`/`NEXT_CHUNK`/`PART_OF` edges.
4. **Embed chunk texts**, write to `Chunk.embedding`.
5. **Windowed extraction**: group `chunks_to_combine` adjacent chunks into one LLM call. For each window:
   - `if is_cancelled(): raise JobCancelled`
   - Retry loop: `max_retries` attempts with exponential backoff (`backoff0 * 2^attempt`), sleeping in 1-second slices so cancel takes effect within ~1s.
   - "Empty result" treated as transient if `extraction_min_nodes_for_success > 0`.
   - On success: `repo.write_graph_documents(file_name, graph_docs)` returns `(entity_count, rel_count)`.
6. **Checkpoint** every `checkpoint_every_chunks` windows: persist running counts to Neo4j + SQLite so a crash mid-run doesn't lose progress.
7. **Final status**: `"Completed"` (or `"Failed"` with error message).

---

## 6. Progress bands

| Band | Stage |
|---|---|
| 0.00–0.03 | `setup` (constraints) |
| 0.03–0.05 | `loading` (count docs) |
| 0.05–0.90 | `extracting` (per-doc, allocation = `0.85 / total`) |
| 0.90–1.00 | `post-processing` + `done` |

Per-doc within band:
```python
base = 0.05 + 0.85 * file_idx / max(1, file_total)
span = 0.85 / max(1, file_total)
```

---

## 7. Cancellation

Checked at:
- Before each document (in `_run_records`).
- Before each extraction window (`_process_document`).
- During retry backoff (sleep in 1s slices, exit early if cancelled).

Raises `JobCancelled` → caught by `JobRegistry.runner` → final status `cancelled`. Partial graph state is preserved.

In-flight LLM calls are **not** interrupted — they finish before the cancel takes effect at the next checkpoint.

---

## 8. Retry logic (per window)

```python
max_retries = int(rs.get("extraction_retry_count"))                  # default 2
backoff0    = float(rs.get("extraction_retry_backoff_seconds"))      # default 1.5
min_nodes   = int(rs.get("extraction_min_nodes_for_success"))        # default 0

attempts = max_retries + 1
for attempt in range(1, attempts + 1):
    try:
        graph_docs, drops = self.extractor.extract(lc_docs)
        nodes_seen = sum(len(getattr(gd, "nodes", []) or []) for gd in graph_docs)
        if min_nodes > 0 and nodes_seen < min_nodes and attempt < attempts:
            _sleep_with_cancel(backoff0 * (2 ** (attempt - 1)))
            continue
        e, r = self.repo.write_graph_documents(doc.file_name, graph_docs)
        break
    except Exception as ex:
        if attempt < attempts:
            _sleep_with_cancel(backoff0 * (2 ** (attempt - 1)))
            if is_cancelled():
                break
```

`_sleep_with_cancel` decrements in 1-second slices checking `is_cancelled()` each tick.

---

## 9. JobUpdate emission

Pipeline doesn't own the job — callers (HTTP endpoints) wrap it via `job_registry.submit(runner)`. The pipeline only calls `progress(JobUpdate(...))`. Examples:

```python
JobUpdate(stage="setup", message="ensuring constraints", progress=0.01)
JobUpdate(stage="loading", message=f"processing {total} documents", progress=0.03)
JobUpdate(stage="extracting", message=f"reading {file}",
          progress=..., extra={"file": file, "phase": "start"})
JobUpdate(stage="extracting", message=f"chunk {i}/{n} → +E ents, +R rels",
          progress=..., extra={"file": file, "phase": "window"})
JobUpdate(stage="cancelled", message="ingestion cancelled — skipping post-processing",
          progress=..., extra={"level": "warn"})
JobUpdate(stage="done", message="ingestion complete", progress=1.0)
```

See [Log System](./log-system.md) for how these get persisted + streamed.

---

## 10. Integration points

- `chunker.split(file_name, text) → List[Chunk]` — see [Documents Ingest](./documents-ingest.md).
- `extractor.extract(lc_docs) → (graph_docs, drops)` — see [Schema Discovery](./schema-discovery.md) for how schema constrains extraction.
- `embedder.embed_documents(texts) → List[List[float]]` — see [Embeddings](./embeddings.md).
- `repo.write_graph_documents(...)`, `repo.write_chunks(...)`, etc. — see [Data Model](./data-model.md) and [Repositories](./repositories.md).
- `post.run(progress)` — see [Post-Processing](./post-processing.md).

---

## 11. Callsites

- `backend/app/api/ingest.py` — HTTP entrypoint `POST /api/ingest/run`.
- `backend/app/api/documents.py` — single-doc reextract: `POST /api/documents/<id>/reextract`.
- `backend/app/cli.py` — `python -m app.cli ingest <folder>`.
