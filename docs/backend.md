# Backend

Flask app, factory pattern. Layered: **api → services → repositories**.
Cypher lives only in repositories. LLM I/O lives only in `llm/`.

## Tree

```
backend/app/
├── __init__.py          Flask factory, blueprint registration
├── config.py            pydantic-settings — single .env source of truth
├── extensions.py        Neo4j driver singleton, logging
├── errors.py            AppError + Flask error handlers
├── wsgi.py              Entrypoint
├── cli.py               click CLI (discover / ingest / stats / clear)
│
├── api/                 HTTP blueprints (thin)
│   ├── health.py            liveness
│   ├── config_api.py        read-only view of effective config
│   ├── runtime_settings.py  GET/PUT runtime-mutable settings
│   ├── settings_api.py      app-state-backed settings used by the UI
│   ├── schema.py            schema discovery + persistence
│   ├── documents.py         list / status / counts of known docs
│   ├── upload.py            file upload into the source folder
│   ├── ingest.py            kick off ingestion jobs
│   ├── jobs.py              list / inspect jobs, snapshot progress
│   ├── graph.py             read-only Neo4j queries for the Graph page
│   ├── llm_calls.py         recorded LLM I/O for inspection
│   └── prompts_api.py       read/write prompt templates
│
├── services/            Business logic (one concern per file)
│   ├── markdown_loader.py   load *.md, front matter, sha1, title
│   ├── chunker.py           token splitter, stable sha1 ids
│   ├── schema_discovery.py  LLM-proposed schema from sample files
│   ├── entity_extractor.py  LLMGraphTransformer wrapper, schema-constrained
│   ├── pipeline.py          orchestrates the whole ingestion
│   ├── post_processor.py    vector index, KNN SIMILAR edges
│   ├── job_registry.py      in-process job runner + JobUpdate events
│   ├── prompt_store.py      load/save prompt templates
│   └── settings_service.py  typed accessors over settings_repository
│
├── repositories/        Cypher / DB writes — the ONLY place that does I/O
│   ├── graph_repository.py        Neo4j writes/reads for the knowledge graph
│   ├── app_state_repository.py    SQLite app state (docs, jobs, counts)
│   ├── llm_call_repository.py     recorded LLM calls
│   └── settings_repository.py     runtime-mutable settings
│
├── llm/                 OpenAI-compatible client + embedder
│   ├── client.py            chat completions, base_url-switched provider
│   ├── recorder.py          persist every call (req/resp/latency/tag)
│   └── __init__.py          build_embedder(), with_tag() context
│
└── prompts/             markdown templates
    ├── entity_extraction_instructions.md
    ├── graph_cleanup_system.md
    └── schema_discovery_system.md
```

## Boundaries

- **API blueprints** validate input, call one service, shape the response.
  No DB calls, no LLM calls.
- **Services** orchestrate. They may call other services, the LLM layer,
  and repositories.
- **Repositories** own all I/O against Neo4j / SQLite. They expose typed
  Python contracts; callers never see a `Session` or a Cypher string.
- **LLM layer** owns provider differences. Everything above sees one
  uniform `chat` + `embed` interface.

## Config

`config.py` (`pydantic-settings`) loads `.env` once at boot. The
**runtime-mutable** subset (chunk size, retry counts, prompts, schema)
lives in `settings_repository` and is edited through the UI without a
restart. `settings_service` is the typed accessor used by code paths
that need a live value.

## Jobs

- `job_registry.submit(fn, …)` → returns a job id. `fn` receives a
  `progress` callable.
- Job state is held in-process and mirrored to SQLite app-state for
  durability of the *record* (in-flight execution does not resume across
  crashes; jobs that were `running` at crash are surfaced as such for the
  UI to clean up).
- `/api/jobs` exposes list, snapshot, and per-job event tail.

## LLM call recording

Every chat completion goes through `llm/recorder.py`:
- captures request, response, model, latency, optional tag (`embedding`,
  `extraction`, `schema_discovery`, …);
- writes to `llm_call_repository`;
- powers the **LLM Calls** page (pretty-printed JSON, retry-from-UI).

## Error model

All raised application errors are subclasses of `AppError` with an
HTTP-mapped status. Flask error handlers in `errors.py` render them
uniformly. Unhandled exceptions log a stack and return a 500 with a
stable error envelope.

## Tests

`backend/tests/` covers the deterministic units: config loading,
markdown loader, chunker, schema discovery JSON parsing, repository
sanitizers, job registry. Real Neo4j flows are exercised through the
CLI on `backend/tests/sample_md/`.
