# Data Model Reference

Two stores: **Neo4j** (graph) + **SQLite** (app state, audit, jobs, prompts, settings).

---

## 1. Neo4j Schema

### 1.1 Constraints

```cypher
CREATE CONSTRAINT document_fileName IF NOT EXISTS
  FOR (d:Document) REQUIRE d.fileName IS UNIQUE;
CREATE CONSTRAINT chunk_id IF NOT EXISTS
  FOR (c:Chunk) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT entity_id IF NOT EXISTS
  FOR (e:__Entity__) REQUIRE e.id IS UNIQUE;
CREATE CONSTRAINT community_id IF NOT EXISTS
  FOR (c:__Community__) REQUIRE c.id IS UNIQUE;
```

### 1.2 `:Document`

| Prop | Type | Notes |
|---|---|---|
| `fileName` | string (unique) | Primary identifier |
| `sha1` | string | Content hash |
| `title` | string? | From frontmatter or H1 |
| `source` | string | Absolute path |
| `length` | int | Raw byte size |
| `status` | string | `New \| Processing \| Completed \| Empty \| Failed` |
| `error` | string? | Set when failed |
| `createdAt` / `updatedAt` / `processedAt` | ISO | Timestamps |
| `chunkNodeCount` / `entityNodeCount` / `entityEntityRelCount` / `chunkRelCount` | int | Counters refreshed at finish |
| `communityNodeCount` / `communityRelCount` | int? | Set by post-processing |
| `processedChunkCount` | int? | Mid-run checkpoint |

### 1.3 `:Chunk`

| Prop | Type | Notes |
|---|---|---|
| `id` | string (unique) | `sha1(text)` — idempotent |
| `text` | string | Full content |
| `position` | int | 1-based ordinal in doc |
| `length` | int | char count |
| `fileName` | string | Denormalized parent doc |
| `content_offset` | int | Byte offset in source |
| `embedding` | vector | Dense embedding |
| `embedding_model` / `embedding_dim` / `embedded_at` | meta | Provenance |

**Indexes:** vector index `vector` on `embedding` (cosine), fulltext `keyword` on `text`.

### 1.4 `:__Entity__` (+ domain labels)

Every extracted entity carries the marker label `:__Entity__` plus one or more domain labels from the active schema (e.g. `:Disease`, `:Drug`).

| Prop | Type | Notes |
|---|---|---|
| `id` | string | Entity identifier (canonical name) |
| `description` | string? | LLM-written or merged |
| `embedding` / `embedding_model` / `embedding_dim` / `embedded_at` | vector + meta |

**Indexes:** vector index `entity_vector` on `embedding`, fulltext `entities` on `id`+`description`.

### 1.5 `:__Community__`

| Prop | Type | Notes |
|---|---|---|
| `id` | string (unique) | `comm-L{level}-{idx}` |
| `level` | int | 0 = finest |
| `size` | int | Member count |
| `title` / `summary` | string? | LLM-generated |
| `member_hash` | string | `sha256(sorted member ids)` — preservation key across rebuilds |
| `community_rank` | int | Distinct docs reaching this community |
| `weight` | int | Distinct chunks reaching this community |
| `embedding` / `embedding_model` / `embedding_dim` / `embedded_at` | vector + meta |
| `created_at` | int | Unix ms |

**Indexes:** vector index `community_vector`, fulltext `community_keyword` on `summary`+`title`.

### 1.6 Relationships

**Structural (built by pipeline):**
- `(Document)-[:FIRST_CHUNK]->(Chunk)`
- `(Chunk)-[:NEXT_CHUNK]->(Chunk)`
- `(Chunk)-[:PART_OF]->(Document)`
- `(Chunk)-[:HAS_ENTITY]->(__Entity__)`

**Extracted knowledge (dynamic types from schema triplets):**
- `(__Entity__)-[:<REL_TYPE>]->(__Entity__)` — e.g. `TREATS`, `LOCATED_IN`. Type sanitized to `[A-Za-z0-9_]` uppercase.

**Community structure (post-processing):**
- `(__Entity__)-[:IN_COMMUNITY]->(__Community__)` — entities only at level 0
- `(__Community__)-[:PARENT_COMMUNITY]->(__Community__)` — hierarchy
- `(Chunk)-[:SIMILAR {score}]-(Chunk)` — undirected, post-proc kNN edges, `score ≥ knn_min_score` (default 0.8)

### 1.7 Canonical MERGE patterns

Document upsert:
```cypher
MERGE (d:Document {fileName: $fileName})
ON CREATE SET d.createdAt = $now, d.status = 'New'
SET d.sha1 = $sha1, d.title = $title, d.source = $source,
    d.length = $length, d.updatedAt = $now
```

Chunk write:
```cypher
UNWIND $batch AS row
MERGE (c:Chunk {id: row.id})
SET c.text = row.text, c.position = row.position,
    c.length = row.length, c.content_offset = row.content_offset,
    c.fileName = $fileName
WITH c
MATCH (d:Document {fileName: $fileName})
MERGE (c)-[:PART_OF]->(d)
```

Entity write (label injected after sanitization):
```cypher
UNWIND $batch AS row
MERGE (n:`{label}` {id: row.id})
  ON CREATE SET n :`__Entity__`
SET n += row.props
```

Entity-to-entity:
```cypher
UNWIND $batch AS row
MATCH (a:__Entity__ {id: row.src_id})
MATCH (b:__Entity__ {id: row.dst_id})
MERGE (a)-[r:`{rel_type}`]->(b)
SET r += row.props
```

Embedding write (no APOC required):
```cypher
UNWIND $batch AS row
MATCH (c:Chunk {id: row.id})
CALL db.create.setNodeVectorProperty(c, 'embedding', row.embedding)
SET c.embedding_model = $model,
    c.embedding_dim   = $dim,
    c.embedded_at     = timestamp()
```

---

## 2. SQLite app state (`backend/data/text2graph.db`)

WAL mode, `PRAGMA foreign_keys = ON`. All timestamps ISO 8601.

### 2.1 `schemas` (single row, id=1 — active schema)

```sql
CREATE TABLE schemas (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    node_labels     TEXT NOT NULL DEFAULT '[]',     -- JSON array of strings
    triplets        TEXT NOT NULL DEFAULT '[]',     -- JSON array of [src, rel, dst]
    extra           TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL,
    updated_by      TEXT
);
```

### 2.2 `schema_versions` (immutable audit)

```sql
CREATE TABLE schema_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    node_labels  TEXT NOT NULL,
    triplets     TEXT NOT NULL,
    extra        TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'manual'  -- manual | discovered | imported
);
```

### 2.3 `documents`

```sql
CREATE TABLE documents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name           TEXT NOT NULL UNIQUE,
    title               TEXT,
    sha1                TEXT NOT NULL,
    source_path         TEXT NOT NULL,
    size_bytes          INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'pending', -- pending|processing|completed|failed
    error               TEXT,
    chunk_count         INTEGER NOT NULL DEFAULT 0,
    entity_count        INTEGER NOT NULL DEFAULT 0,
    relationship_count  INTEGER NOT NULL DEFAULT 0,
    last_job_id         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    processed_at        TEXT
);
CREATE INDEX idx_documents_status     ON documents(status);
CREATE INDEX idx_documents_created_at ON documents(created_at DESC);
```

### 2.4 `prompts` — see [Prompts System](./prompts-system.md)

### 2.5 `ingest_runs` / `ingest_events` — see [Log System](./log-system.md)

### 2.6 `app_settings`

```sql
CREATE TABLE app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,       -- JSON-encoded
    updated_at  TEXT NOT NULL
);
```

Stores runtime overrides (`extraction_retry_count`, `chunk_token_size`, …) — see [Settings System](./settings-system.md). Also persists chosen `embedding_model`/`embedding_dimension` after `switch_model`.

---

## 3. SQLite LLM audit (`backend/data/llm_calls.db`)

See [LLM Calls](./llm-calls.md).

---

## 4. SQLite chat (`backend/data/chat.db`)

See [Chat RAG](./chat-rag.md). Tables: `chat_sessions`, `chat_messages`, `chat_citations`.

---

## 5. Design notes

- **Idempotent chunk IDs.** `Chunk.id = sha1(text)` → same text always yields same node. MERGE is safe to retry.
- **Two status fields.** `documents.status` (SQLite) drives UI; `Document.status` (Neo4j) tracks graph-side state. Pipeline keeps them aligned.
- **Dynamic entity labels** rather than a single `Entity` table — preserves type information for selective Cypher queries (`MATCH (d:Disease)`).
- **Embedding provenance columns** (`embedding_model`, `embedding_dim`) let `EmbeddingService.status` detect stale vectors after a model switch.
- **Hierarchical communities** are rebuilt from scratch on every post-processing run, but `member_hash` lets us restore the LLM-written title/summary/embedding for unchanged communities — no LLM cost on no-op rebuilds.
- **No APOC required for writes.** Vector embeddings use `db.create.setNodeVectorProperty` (built-in to Neo4j 5+). Chat graph-mode optionally probes for `apoc.meta.schema` and degrades gracefully if absent — see [Chat RAG](./chat-rag.md).
