# Chat + RAG Reference

History-aware question rewrite → retrieval (graph + vector hybrid) → answer generation with citations. Seven retrieval modes. SSE streaming. Per-session persistence.

**Files:**
- `backend/app/services/chat_service.py`
- `backend/app/api/chat_api.py`
- `backend/app/repositories/chat_repository.py`
- `backend/app/prompts/chat_system.md`, `chat_question_rewrite.md`
- `frontend/src/chat/ChatApp.tsx`

---

## 1. Retrieval modes

| Mode | Node | Strategy | Graph expansion | Doc filter | Use case |
|---|---|---|---|---|---|
| `vector` | Chunk | semantic (cosine) | none | yes | scoped semantic QA |
| `fulltext` | Chunk | hybrid (vector + BM25) | none | no | keyword/acronym-heavy |
| `graph_vector` | Chunk | semantic + entity/relation hop | yes | yes | entity-centric, scoped |
| `graph_vector_fulltext` | Chunk | hybrid + graph hop | yes | no | **default** — broadest recall |
| `entity_vector` | __Entity__ | semantic on descriptions | local community | no | "tell me about X" |
| `global_vector` | __Community__ | hybrid on summaries | none | no | thematic / high-level |
| `graph` | — | text→Cypher (no embeddings) | direct Cypher | no | structured (counts, joins) |

Default: `graph_vector_fulltext`. `MODE_DOC_FILTER = {"vector", "graph_vector"}` is enforced backend + frontend.

---

## 2. End-to-end flow (`ChatService.ask`)

```python
def ask(self, *, question, history, mode=DEFAULT_MODE,
        top_k=None, document_names=None, stream_handler=None) -> dict:
    # 1. Rewrite (only if history is non-empty)
    if history:
        rewrite_prompt = self.prompts.render(
            "chat_question_rewrite",
            history=self._format_history_for_prompt(history),
            question=question,
        )
        with with_tag("chat_rewrite"):
            rewritten = build_chat_llm(self.settings, tag="chat_rewrite").invoke(rewrite_prompt)
        search_query = (getattr(rewritten, "content", None) or str(rewritten)).strip() or question
    else:
        search_query = question

    # 2. Retrieve
    retriever = self._retriever(mode, top_k=top_k or self.settings.chat_top_k)
    with with_tag("chat_retrieve"):
        docs = retriever.invoke(search_query)
    if document_names and cfg.get("document_filter"):
        keep = set(document_names)
        docs = [d for d in docs if (d.metadata or {}).get("fileName") in keep]

    # 3. Assemble context
    context_text, info = self._format_docs(docs, mode=mode)

    # 4. Answer
    system_text = self.prompts.render("chat_system", context=context_text, question=question)
    lc_messages = [SystemMessage(content=system_text), *history_messages, HumanMessage(content=question)]
    with with_tag("chat_answer"):
        answer, usage = self._invoke_llm(answer_llm, lc_messages, stream_handler)

    return {
        "answer": answer,
        "sources": info["sources"],
        "nodedetails": info["nodedetails"],
        "entities": info["entities"],
        "context_preview": context_text[:1200],
        "rewritten_question": search_query,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "response_time_ms": elapsed_ms,
        "mode": mode, "model": model_name,
    }
```

Tags: `chat_rewrite`, `chat_retrieve`, `chat_answer`, `chat_graph` (for graph mode). See [LLM Calls](./llm-calls.md).

---

## 3. Retrieval Cypher

**Chunk + graph hop (graph_vector / graph_vector_fulltext):**

```cypher
WITH node AS chunk, score
OPTIONAL MATCH (chunk)-[:PART_OF]->(d:Document)
WITH chunk, score, d
OPTIONAL MATCH (chunk)-[:HAS_ENTITY]->(e:__Entity__)
WITH chunk, score, d,
     collect(DISTINCT {
        id: e.id, elementId: elementId(e),
        labels: [l IN labels(e) WHERE l <> '__Entity__'],
        description: e.description
     })[0..25] AS entities,
     collect(DISTINCT elementId(e)) AS entity_ids
OPTIONAL MATCH (a:__Entity__)-[r]-(b:__Entity__)
  WHERE elementId(a) IN entity_ids AND elementId(b) IN entity_ids
WITH chunk, score, d, entities,
     collect(DISTINCT {
        startId: a.id, endId: b.id, type: type(r),
        elementId: elementId(r)
     })[0..40] AS relationships
RETURN chunk.text AS text, score,
       { chunkId: chunk.id,
         fileName: coalesce(d.fileName, chunk.fileName),
         position: chunk.position,
         entities: entities,
         relationships: relationships } AS metadata
```

**Chunk only:** same shape without entity/relationship expansion.

Built atop `Neo4jVector.from_existing_graph(...)` with `search_type="hybrid"` when a keyword index is configured.

---

## 4. Graph mode (text → Cypher)

```python
def _ask_graph_mode(self, question, history):
    use_validation = self._apoc_available()
    chain = GraphCypherQAChain.from_llm(
        llm=build_chat_llm(self.settings, tag="chat_graph"),
        graph=Neo4jGraph(...),
        verbose=False,
        return_intermediate_steps=True,
        validate_cypher=use_validation,                 # requires apoc.meta.schema
        allow_dangerous_requests=True,
    )
    with with_tag("chat_graph"):
        result = chain.invoke({"query": question})
    return {
        "answer": result["result"],
        "cypher": result["intermediate_steps"][0]["query"],
        "rows":   result["intermediate_steps"][1]["context"],
        ...
    }
```

**APOC probe** (graceful degrade for Community Edition):

```python
@staticmethod
def _apoc_available() -> bool:
    try:
        with neo4j_manager.driver.session(database=neo4j_manager.database) as s:
            row = s.run(
                "SHOW PROCEDURES YIELD name WHERE name = 'apoc.meta.schema' "
                "RETURN count(*) AS n"
            ).single()
        return bool(row and int(row["n"]) > 0)
    except Exception:
        return False
```

Sets `validate_cypher` only if APOC is present. Without APOC the chain still runs but skips early validation.

---

## 5. Context format

```
[1 | fileName | chunk:abc123de]
<chunk text>

[2 | fileName | entity:Aspirin]
Entity: Aspirin
Description: …
Supporting chunks…
```

Info payload:
```python
{
    "sources": [{"source_name": "file.md", "chunk_ids": ["id1", "id2"]}],
    "nodedetails": {
        "chunkdetails":     [{"id": "...", "score": 0.87}],
        "entitydetails":    [{"id": "elem-id", "label": "Aspirin"}],
        "communitydetails": [{"id": "comm-L0-12", "label": "Analgesics"}],
    },
    "entities": {
        "entityids":        [...],
        "relationshipids":  [...],
        "nodes":            [{"id", "elementId", "labels", "description"}, ...],
        "relationships":    [{"startId", "endId", "type", "elementId"}, ...],
    },
}
```

Source-chip gate in frontend uses `sources` to render clickable file chips; entity chips come from `entities.nodes`.

---

## 6. Persistence (SQLite `backend/data/chat.db`)

```sql
CREATE TABLE chat_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'New chat',
    mode TEXT NOT NULL DEFAULT 'graph_vector_fulltext',
    model TEXT, embedding_provider TEXT, embedding_model TEXT,
    document_names TEXT NOT NULL DEFAULT '[]',
    pinned INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    last_message_at TEXT, message_count INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,                  -- user|assistant|system
    content TEXT NOT NULL DEFAULT '',
    mode TEXT, model TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
    response_time_ms INTEGER,
    llm_call_id INTEGER,                 -- link to llm_calls.id (loose)
    error TEXT,
    created_at TEXT NOT NULL,
    sources TEXT NOT NULL DEFAULT '[]',
    entities TEXT NOT NULL DEFAULT '{}',
    nodedetails TEXT NOT NULL DEFAULT '{}',
    metric TEXT NOT NULL DEFAULT '{}',
    meta TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);
```

---

## 7. HTTP endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/chat/health` | per-mode availability + index dims |
| `GET /api/chat/modes` | list of mode keys |
| `GET /api/chat/sessions` | list (params: `archived`, `limit`, `offset`, `search`) |
| `POST /api/chat/sessions` | create |
| `GET /api/chat/sessions/<id>` | session + messages |
| `PATCH /api/chat/sessions/<id>` | title, mode, doc filter, pinned, archived |
| `DELETE /api/chat/sessions/<id>` | delete |
| `POST /api/chat/sessions/<id>/clear` | clear messages |
| `POST /api/chat/sessions/<id>/messages` | non-streaming ask |
| `POST /api/chat/sessions/<id>/messages/stream` | SSE streaming |
| `GET /api/chat/messages/<mid>/expand` | full chunks/entities/communities for citations |
| `POST /api/chat/messages/<mid>/rate` | thumbs up/down |

### SSE protocol

Stream events:
```
event: token
data: {"t": "next chunk of answer text"}

event: meta
data: {...partial info...}    (optional)

event: done
data: {"session_id", "user_message_id", "assistant_message_id", "message", "info": {...}}

event: error
data: {"error": "...", "assistant_message_id": <id>}
```

Frontend `streamChat()` parses these and calls `onToken()` / `onDone()` / `onError()`.

---

## 8. Frontend (`ChatApp.tsx`)

Layout: `grid-cols-[280px_minmax(0,1fr)]` — sidebar (sessions) + main (chat view).

Key sub-components:
- `ChatPage` — header (title, mode select, doc filter, model badge) + thread + input.
- `MessageBubble` — markdown via `react-markdown` + `remark-gfm`, role-aligned, metadata strip (tokens, latency).
- `CitationRow` + `CitationDrawer` — clickable source/entity chips; drawer with tabs (Chunks / Entities / Communities).
- `EntityDrawer` — 1-hop neighborhood via `api.graphNeighborhood()`.
- `StreamingBubble` — live token stream.
- `DocFilterChip` — multi-select doc scope; only rendered for `vector` / `graph_vector` modes.
- `ModeSelect` — dropdown with mode descriptions, ordered by recommendation.

State: `sessions`, `activeId`, `messages`, `streamingText`, `err`. Active session loaded from `/api/chat/sessions/<id>`; messages cached locally and appended on send/stream.

---

## 9. Prompt keys

- `chat_question_rewrite` — vars `{history, question}`. Output: standalone search query, no preamble.
- `chat_system` — vars `{context, question}`. Output: cited answer.

See [Prompts System](./prompts-system.md) for editing.

---

## 10. Configuration

```python
chat_db_path: str = "backend/data/chat.db"
chat_history_max_messages: int = 200            # cap context window
chat_summary_token_target: int = 1500
chat_top_k: int = 5                              # default retrieval k
chat_doc_split_size: int = 3000
chat_embedding_filter_threshold: float = 0.10
```
