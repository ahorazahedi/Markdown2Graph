# Architecture

## Goal

Turn a folder of Markdown (medical textbooks, clinical guidelines, references)
into a queryable Neo4j knowledge graph, with the schema discovered or curated
per corpus.

## Runtime topology

```
┌──────────────────────────────┐
│  Frontend (Vite dev / build) │  React + TS + Tailwind + shadcn-style UI
│   localhost:5173             │
└─────────────┬────────────────┘
              │  HTTP /api/*   (Vite proxy → :8000)
              ▼
┌──────────────────────────────┐
│  Backend (Flask)             │  app factory, blueprints, services, repos
│   localhost:8000             │  in-process job registry (ThreadPool)
└─────────────┬────────────────┘
              │  Bolt
              ▼
┌──────────────────────────────┐
│  Neo4j 5 (Docker)            │  graph store + vector index on Chunk.embedding
│   bolt://localhost:7687      │
└──────────────────────────────┘

              ▲                ▲
              │                │
        OpenRouter      LM Studio (local)
        (OpenAI-compat)  (OpenAI-compat)
```

One LLM client speaks to either provider — both expose the OpenAI REST shape.

## Layers (backend)

```
api/            HTTP boundary. Thin. Validates input, calls services.
  └─ blueprints: health, config, schema, documents, ingest, upload,
                 graph, jobs, llm_calls, prompts, runtime_settings, settings
services/       Business logic. One concern per file.
  └─ markdown_loader, chunker, schema_discovery, entity_extractor,
     pipeline, post_processor, job_registry, prompt_store,
     settings_service
repositories/   Only place Cypher is written.
  └─ graph_repository, app_state_repository, llm_call_repository,
     settings_repository
llm/            OpenAI-compatible chat + embedding clients, call recorder.
prompts/        Markdown prompt templates (entity extraction, schema discovery,
                graph cleanup).
config.py       pydantic-settings — single source of runtime config from .env.
extensions.py   Neo4j driver singleton, logging setup.
errors.py       AppError + Flask error handlers.
cli.py          click CLI mirror of the wizard (discover, ingest, stats, clear).
wsgi.py         Entrypoint.
```

**Rule:** the repository layer is the only place that touches the Neo4j
driver. Services and APIs never see `bolt://`.

## Layers (frontend)

```
src/
  pages/         One per top-level route (Documents, Schema, Ingest, Graph,
                 Jobs, LLMCalls, Prompts, Settings).
  components/    Cross-page UI (AppShell, ActiveJobsBanner, GraphViewer,
                 RuntimeSettingsPanel, …) plus components/ui (primitives).
  lib/           Typed fetch client and shared helpers.
  App.tsx        Router.
```

The UI is a multi-page app, not a single wizard. Long-running ingest is
non-blocking: jobs are kicked off, surfaced in the **Active Jobs banner**, and
fully inspectable in the **Jobs page**.

## Background work

- **Job registry** (`services/job_registry.py`): in-process registry with the
  same `submit / get / snapshot` contract a real queue would expose. Durable
  status is mirrored to Neo4j via `app_state_repository` so jobs survive
  process restarts visually (state is observable; in-flight execution does
  not resume across crashes).
- **Per-chunk progress**: the pipeline emits `JobUpdate` events at every
  meaningful step (load, chunk, embed, extract per window, post-process), so
  the UI bar advances continuously over potentially hundreds of LLM calls.
- **Concurrency**: `ThreadPoolExecutor` sized by `INGEST_CONCURRENCY`. One
  worker per document; extraction within a document is sequential per window.

## LLM access

- A single OpenAI-compatible client in `llm/client.py`. Provider is chosen by
  `LLM_BASE_URL`: OpenRouter for hosted models, LM Studio (`:1234/v1`) for
  local.
- Every chat completion is wrapped by a **recorder** that persists request,
  response, model, latency, and a free-form tag (e.g. `embedding`,
  `extraction`, `schema_discovery`) into Neo4j via `llm_call_repository`.
  The **LLM Calls** page inspects these.

## Config

One `.env` at repo root, parsed once at boot by `pydantic-settings`. No
per-module config files. Runtime overridable values (chunk size, retry counts,
schema, prompts) live in `settings_repository` so they can be edited in the UI
without a restart.

## Tests

`backend/tests/` exercises: config loading, markdown loader, chunker, schema
discovery JSON parsing, repository sanitizers, job registry. Real-Neo4j flows
are validated manually via the CLI and the UI wizard.
