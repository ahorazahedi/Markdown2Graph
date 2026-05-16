# text2graph

**Markdown → Neo4j knowledge graph, tuned for medical content.**

A production-grade clone of the neo4j-labs *llm-graph-builder* architecture,
scoped to a single source type (Markdown) and a single domain (medical
textbooks, clinical references, guideline documents). LLM access goes through
OpenRouter today; the same client also speaks to a local LM Studio endpoint
because both expose an OpenAI-compatible API.

## Architecture

```
            ┌──────────────────────────────────────────────────┐
            │                   Frontend (Vite)                │
            │   React + TS + Tailwind + shadcn-style UI        │
            │   5-step wizard: Connect → Folder → Schema →     │
            │                  Ingest → Results                │
            └────────────┬─────────────────────────────────────┘
                         │  /api/*   (Vite proxy → :8000)
            ┌────────────▼─────────────────────────────────────┐
            │                Backend (Flask)                   │
            │  ┌────────────────────────────────────────────┐  │
            │  │  api/      blueprints (health, config,     │  │
            │  │             schema, ingest, graph)         │  │
            │  ├────────────────────────────────────────────┤  │
            │  │  services/ markdown_loader, chunker,       │  │
            │  │             schema_discovery,              │  │
            │  │             entity_extractor, pipeline,    │  │
            │  │             post_processor, job_registry   │  │
            │  ├────────────────────────────────────────────┤  │
            │  │  repositories/  graph_repository (Cypher)  │  │
            │  ├────────────────────────────────────────────┤  │
            │  │  llm/      OpenAI-compatible client        │  │
            │  │             (OpenRouter / LM Studio)       │  │
            │  ├────────────────────────────────────────────┤  │
            │  │  prompts/  markdown prompt templates       │  │
            │  └────────────────────────────────────────────┘  │
            └────────────┬─────────────────────────────────────┘
                         │ bolt://
            ┌────────────▼──────────────┐
            │     Neo4j 5 (docker)      │
            └───────────────────────────┘
```

### Data model (mirrors the reference)

```
(:Document {fileName, sha1, title, source, length, status, ...})
(:Chunk    {id (sha1 of text), text, position, length, fileName,
            content_offset, embedding})
(:__Entity__) — also labelled with the domain type, e.g. (:Disease)

(Document)-[:FIRST_CHUNK]->(Chunk)
(Chunk)-[:NEXT_CHUNK]->(Chunk)
(Chunk)-[:PART_OF]->(Document)
(Chunk)-[:HAS_ENTITY]->(:__Entity__)
(Chunk)-[:SIMILAR {score}]-(Chunk)             // post-processing
(:__Entity__)-[:<EXTRACTED_REL>]->(:__Entity__)
```

### Pipeline stages

1. **Schema discovery** — the LLM samples N files (default 5) and proposes
   a list of node labels + relationship triplets. The user reviews and edits
   the schema in the wizard before extraction.
2. **Markdown load** — recursive scan of `*.md`, YAML front matter parsed,
   SHA-1 over raw bytes for dedupe, first H1 captured as title.
3. **Chunk** — `TokenTextSplitter` (default 600 tokens, 80 overlap), SHA-1
   chunk ids, position + content offset preserved for stable re-runs.
4. **Embed** — local `sentence-transformers/all-MiniLM-L6-v2` (dim 384) by
   default; swap to OpenAI-style embeddings via `EMBEDDING_PROVIDER=openai`.
5. **Extract** — `LLMGraphTransformer` (LangChain Experimental) wraps the
   chat model, constrained by the user-approved schema and the medical-domain
   prompt in `app/prompts/`.
6. **Persist** — `apoc.merge.node` / `apoc.merge.relationship`, all batched.
   Structural edges (`FIRST_CHUNK`/`NEXT_CHUNK`/`PART_OF`/`HAS_ENTITY`) are
   written first; entity edges follow.
7. **Post-process** — vector index over `Chunk.embedding`, optional
   `SIMILAR` edges between near-neighbor chunks (cosine ≥ `KNN_MIN_SCORE`).

## Quick start

```bash
# 1. Configure
cp .env.example .env
# edit .env: set NEO4J_PASSWORD and OPENROUTER_API_KEY

# 2. Start Neo4j
make neo4j-up

# 3. Backend
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.wsgi          # http://localhost:8000

# 4. Frontend (new shell)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Open <http://localhost:5173> and walk through the wizard.

### CLI

```bash
# Discover the schema for a folder of medical Markdown
python -m app.cli discover /path/to/folder --out schema.json

# Ingest with the discovered schema
python -m app.cli ingest /path/to/folder --schema-file schema.json

# Stats
python -m app.cli stats

# Wipe
python -m app.cli clear --yes
```

## Configuration

Everything is driven from a single `.env` at the **repo root** — loaded by
`pydantic-settings` when the Flask app boots. See `.env.example` for the
full list. Key settings:

| Variable                  | Meaning                                                                |
|---------------------------|------------------------------------------------------------------------|
| `NEO4J_URI/USERNAME/PASS` | Database connection                                                    |
| `OPENROUTER_API_KEY`      | LLM auth (or use `LLM_API_KEY` for LM Studio)                          |
| `LLM_MODEL`               | Model id (default `google/gemini-2.5-flash`)                           |
| `LLM_BASE_URL`            | Overrides `OPENROUTER_BASE_URL` — point at LM Studio's `:1234/v1`      |
| `EMBEDDING_*`             | Local or OpenAI-compatible embeddings                                  |
| `CHUNK_TOKEN_SIZE`        | Token size per chunk (600 by default)                                  |
| `INGEST_CONCURRENCY`      | Worker threads in the pipeline                                         |
| `KNN_MIN_SCORE`           | Cosine threshold for chunk SIMILAR edges                               |

### Pointing at LM Studio

```env
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=lm-studio
LLM_MODEL=local-model-id
```

## Tests

```bash
cd backend && pytest
```

Unit tests cover: config loading, markdown loader (front matter, titles,
sha1), token chunker (id stability, positions), schema-discovery JSON
parsing, repository sanitizers, and the background job registry. Integration
against a real Neo4j is exercised manually via the CLI on a small fixture
in `backend/tests/sample_md/`.

## Repository layout

```
.
├── .env / .env.example          # single source of runtime config
├── Makefile
├── scripts/                     # neo4j_{start,stop,logs}.sh
├── backend/
│   ├── app/
│   │   ├── __init__.py          # Flask factory
│   │   ├── config.py            # pydantic-settings
│   │   ├── extensions.py        # neo4j driver singleton, logging
│   │   ├── errors.py            # AppError + handlers
│   │   ├── api/                 # blueprints
│   │   ├── services/            # business logic (one concern per file)
│   │   ├── repositories/        # Cypher writes/reads
│   │   ├── llm/                 # OpenAI-compatible client + embedder
│   │   ├── prompts/             # medical-tuned prompt markdown
│   │   ├── cli.py               # click cli
│   │   └── wsgi.py              # entrypoint
│   ├── tests/
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── App.tsx              # wizard shell
    │   ├── components/
    │   │   ├── Stepper.tsx
    │   │   ├── steps/           # one component per wizard step
    │   │   └── ui/              # shadcn-style primitives
    │   └── lib/api.ts           # typed fetch client
    ├── vite.config.ts           # /api proxied to :8000
    └── package.json
```

## Design choices vs the reference repo

* **Single source type.** `MarkdownLoader` instead of the reference's
  six-source matrix (S3, GCS, web, YouTube, Wikipedia, local). Less code,
  less surface to fail, and a closer fit to the medical-textbook workload.
* **Single LLM client.** OpenRouter and LM Studio both speak the OpenAI
  REST shape, so a single `ChatOpenAI` factory handles both. Removed the
  ten-vendor switch in the reference's `get_llm`.
* **Layered backend.** API → services → repositories. The repository is the
  only place Cypher is written; the rest of the code never sees the driver.
* **In-process job registry.** No Celery dependency for v1. Same
  `submit/get/snapshot` contract — swap in RQ/Celery without touching the
  pipeline. Adequate for single-pod deployments; for horizontal scale, move
  to a real queue.
* **Communities deferred.** The reference repo runs Leiden community
  detection via GDS. That adds significant tuning and a heavyweight plugin
  for medical content where named entities are already meaningful; left as
  a v2 hook in `post_processor.py`.
* **Same metadata + data model.** Document/Chunk/Entity nodes, structural
  relationships, and the chunk-id-as-sha1 convention all match the
  reference so the graph is interchangeable with downstream tools built
  for that schema.

## License

MIT.
