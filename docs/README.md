# text2graph — Documentation

High-level docs for the **text2graph** system: Markdown → Neo4j knowledge graph,
tuned for medical content.

This folder is a map of the system, not a line-by-line reference. Read the code
for specifics; read here for shape, intent, and how the parts fit.

## Index

| Doc | Purpose |
|-----|---------|
| [architecture.md](./architecture.md) | System diagram, layers, boundaries, runtime topology |
| [pipeline.md](./pipeline.md) | Ingestion pipeline stages: load → chunk → embed → extract → persist → post-process |
| [data-model.md](./data-model.md) | Neo4j graph schema: nodes, relationships, indexes |
| [backend.md](./backend.md) | Flask app layout: api / services / repositories / llm / prompts |
| [frontend.md](./frontend.md) | React app layout: pages, components, API client |
| [CHAT_RAG_REFERENCE.md](./CHAT_RAG_REFERENCE.md) | Reference notes on chat / RAG patterns |

## TL;DR

```
Markdown files
    │
    ▼  load → chunk → embed → LLM extract → write
React UI  ──►  Flask API  ──►  Neo4j 5
    ▲                            ▲
    └── live job progress ───────┘
```

- **Frontend:** Vite + React + TS + Tailwind. Multi-page app (Documents,
  Schema, Ingest, Graph, Jobs, LLM Calls, Prompts, Settings).
- **Backend:** Flask factory app. Layered: `api/` → `services/` → `repositories/`.
  Single OpenAI-compatible LLM client (OpenRouter / LM Studio).
- **Store:** Neo4j 5 (Bolt). Schema mirrors neo4j-labs `llm-graph-builder`
  for interoperability.
- **Jobs:** in-process registry, durable status in Neo4j, per-chunk progress.
- **Config:** single `.env` at repo root, loaded via `pydantic-settings`.
