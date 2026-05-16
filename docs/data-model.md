# Data model

Mirrors the neo4j-labs `llm-graph-builder` schema so downstream tools built
for that graph work unchanged.

## Nodes

| Label         | Key properties |
|---------------|----------------|
| `:Document`   | `fileName` (unique), `sha1`, `title`, `source`, `length`, `status`, counts |
| `:Chunk`      | `id` (SHA-1 of text, unique), `text`, `position`, `length`, `fileName`, `content_offset`, `embedding` |
| `:__Entity__` | Generic entity marker. Each entity also carries a domain label (e.g. `:Disease`, `:Drug`) from the user-approved schema. |

## Relationships

```
(Document) -[:FIRST_CHUNK]-> (Chunk)
(Chunk)    -[:NEXT_CHUNK]->  (Chunk)
(Chunk)    -[:PART_OF]->     (Document)
(Chunk)    -[:HAS_ENTITY]->  (:__Entity__)
(Chunk)    -[:SIMILAR {score}]- (Chunk)        // post-processing, optional
(:__Entity__) -[:<EXTRACTED_REL>]-> (:__Entity__)
```

`<EXTRACTED_REL>` is one of the user-approved relationship types from schema
discovery (e.g. `TREATS`, `CAUSES`, `INDICATED_FOR`).

## Indexes and constraints

- Uniqueness constraint on `:Document(fileName)`.
- Uniqueness constraint on `:Chunk(id)`.
- Vector index on `:Chunk(embedding)` — created lazily in post-process.
- **No Enterprise-only constraints.** Property-existence constraint on
  `:__Entity__` was intentionally dropped so the schema runs on Neo4j
  Community.

## Document status lifecycle

```
New ──► Processing ──► Completed
              │
              ├─► Empty       (no chunks produced)
              └─► Failed      (error string preserved)
```

Two state stores hold this:
- **Neo4j** (`Document.status`) — what the graph knows.
- **App state** (`app_state_repository` → SQLite) — what the UI shows for
  documents that have not yet reached the graph (e.g. uploaded but not
  ingested), plus job ids and counts.

## Chunk id stability

Chunk id = SHA-1 of the chunk text. This means:
- Same input → same chunk ids across runs → `MERGE` is idempotent.
- Edits to a document produce new chunk ids only for changed regions;
  unchanged regions reuse existing nodes and embeddings.

## Why this shape

- **Document / Chunk separation** lets RAG, search, and graph traversal
  share the same backbone.
- **`:__Entity__` plus domain label** keeps generic queries simple
  (`MATCH (:__Entity__)`) while preserving domain semantics.
- **Structural edges first** means partial extraction failures still leave
  a navigable document/chunk skeleton in the graph.
