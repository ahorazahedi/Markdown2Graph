# Ingestion pipeline

End-to-end flow that converts a folder of Markdown into nodes and
relationships in Neo4j. Implemented in `backend/app/services/pipeline.py`,
orchestrated per job by `services/job_registry.py`.

## Stage diagram

```
  ┌────────────────┐
  │ 0. Schema      │   user-approved node labels + relationship triplets
  │    discovery   │   (LLM-proposed, editable in UI)
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │ 1. Load        │   recursive *.md scan, YAML front matter,
  │                │   SHA-1 over raw bytes for dedupe, H1 → title
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │ 2. Chunk       │   TokenTextSplitter (default 600 tokens, 80 overlap),
  │                │   SHA-1 chunk ids, position + content offset preserved
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │ 3. Embed       │   sentence-transformers MiniLM-L6-v2 (dim 384) by
  │                │   default; OpenAI-compatible swap via env
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │ 4. Extract     │   LangChain LLMGraphTransformer constrained by the
  │                │   approved schema + medical prompt. Per-window LLM
  │                │   calls with retry + min-nodes heuristic.
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │ 5. Persist     │   apoc-free MERGEs into Neo4j. Structural edges
  │                │   (FIRST_CHUNK / NEXT_CHUNK / PART_OF / HAS_ENTITY)
  │                │   first; entity edges follow. Batched.
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │ 6. Post-       │   vector index on Chunk.embedding, optional SIMILAR
  │    process     │   edges between near-neighbor chunks
  │                │   (cosine ≥ KNN_MIN_SCORE).
  └────────────────┘
```

## Stage notes

### 0. Schema discovery
- LLM samples N files (default 5).
- Returns proposed node labels + relationship triplets `(src, REL, dst)`.
- User reviews / edits in the **Schema** page before extraction.
- Persisted via `settings_repository` so re-runs and CLI use the same shape.

### 1. Load (`markdown_loader.py`)
- Recursive `*.md` walk.
- YAML front matter parsed and surfaced as document metadata.
- SHA-1 over raw bytes is the dedupe key.
- First H1 becomes the document title.

### 2. Chunk (`chunker.py`)
- Token-aware splitter; size and overlap configurable via runtime settings.
- Chunk id = SHA-1 of chunk text → stable across re-runs.
- `position` and `content_offset` preserved so chunks can be replayed.

### 3. Embed (`llm/__init__.py` → `build_embedder`)
- Default: local sentence-transformers MiniLM-L6-v2 (384 dim).
- Alternate: any OpenAI-compatible embedding endpoint via env.
- Failures here are warned, not fatal — extraction still runs.

### 4. Extract (`entity_extractor.py`)
- Wraps `LLMGraphTransformer` from `langchain-experimental`.
- Constrained by user-approved `allowed_nodes` and `allowed_relationships`.
- Prompt is the medical-tuned template in `app/prompts/`.
- Windowed: N consecutive chunks combined per LLM call
  (`CHUNKS_TO_COMBINE`) to amortize overhead.
- **Retry policy** (runtime-configurable):
  - `extraction_retry_count` attempts.
  - Exponential backoff from `extraction_retry_backoff_seconds`.
  - Heuristic: if `nodes_returned < extraction_min_nodes_for_success`, treat
    as transient empty-output and retry.
- Failures isolated per window — one bad chunk does not poison the document.

### 5. Persist (`graph_repository.py`)
- **APOC-free.** Plain Cypher `MERGE`s, batched. Works on Neo4j Community.
- Structural writes first: `Document → FIRST_CHUNK → Chunk`,
  `Chunk → NEXT_CHUNK → Chunk`, `Chunk → PART_OF → Document`.
- Then chunk embeddings.
- Then entity nodes (labelled both `:__Entity__` and the discovered domain
  label, e.g. `:Disease`), then `Chunk → HAS_ENTITY → :__Entity__`.
- Then entity-to-entity relationships using the extracted relationship type.

### 6. Post-process (`post_processor.py`)
- Ensures vector index exists on `Chunk.embedding`.
- Optionally computes KNN over chunks and writes `(Chunk)-[:SIMILAR {score}]-(Chunk)`
  when cosine ≥ `KNN_MIN_SCORE`.
- Community detection (Leiden / GDS) is a deliberate v2 hook — not run.

## Progress reporting

Every stage emits `JobUpdate(stage, message, progress, extra)` into the
job registry. Progress is partitioned across files, then across windows
inside a file, so the bar moves smoothly even on hundred-chunk documents.
The frontend **ActiveJobsBanner** and **Jobs page** consume the snapshot.

## Reprocessing

- `run_pending`: ingest every document in app-state with status
  `pending` or `failed`.
- `run_documents(ids, reextract=False)`: targeted re-run; with
  `reextract=True` the file's existing graph state is deleted before
  reprocessing.

## CLI parity

`backend/app/cli.py` exposes the same flow:

```bash
python -m app.cli discover /path/to/folder --out schema.json
python -m app.cli ingest   /path/to/folder --schema-file schema.json
python -m app.cli stats
python -m app.cli clear --yes
```
