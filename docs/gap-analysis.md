# text2graph vs neo4j-labs/llm-graph-builder — Pipeline Gap Analysis

Scope: graph extraction pipeline only. Comparison anchored on the reference
implementation in `llm-graph-builder/backend/{score.py, src/*}` and our
current implementation in `backend/app/{api,services,repositories}` plus
`frontend/src/`. Each gap below is concrete (named endpoint / function /
algorithm), graded **P0** (blocker for parity / correctness), **P1**
(important feature), **P2** (nice-to-have).

Legend per stage:
- **Ref** = what llm-graph-builder does
- **Us** = what text2graph already has (incl. in-progress files from `git status`)
- **Gaps** = checklist of missing items

---

## 1. Document ingestion / sources

**Ref**
- Source types selectable in UI and `/url/scan` + `/extract`:
  - `local file` (`src/document_sources/local_file.py`) — PDF, .docx, .txt, .md, images via `UnstructuredFileLoader` / `PyMuPDFLoader`.
  - `s3 bucket` (`s3_bucket.py`) with AWS access/secret.
  - `gcs bucket` (`gcs_bucket.py`) with OAuth2 token, optional GCS file cache.
  - `web-url` (`web_pages.py`) via `WebBaseLoader`.
  - `youtube` (`youtube.py`) — transcript via `YOUTUBE_TRANSCRIPT_PROXY`.
  - `Wikipedia` (`wikipedia.py`) via `WikipediaLoader`.
- Chunked upload via `/upload` (`upload_file` in `main.py`, merges `chunk_dir → merged_dir`).
- Per-file `Document` source node with `fileSource`, `fileType`, `fileSize`, `url`, `awsAccessKeyId`, `gcsBucket`, `language`, `model`, `token_usage`, `processed_chunk`, `total_chunks`.
- Sanitize filename (`sanitize_uploaded_fileName`, `sanitize_filename` in score.py).
- `/sources_list` reads from graph (not a separate sqlite store).

**Us**
- Only `.md` from `backend/app/services/markdown_loader.py`. Uploads → SQLite `app_state` (`POST /documents/upload`, `POST /upload`), then ingest by document_id. No remote sources.

**Gaps**
- [ ] **P0** PDF ingestion (PyMuPDF / Unstructured) — current loader only reads `*.md`.
- [ ] **P0** DOCX / TXT / HTML loaders (Unstructured) for local files.
- [ ] **P1** S3 source: `/url/scan` equivalent + AWS creds plumbing to `Document.awsAccessKeyId`.
- [ ] **P1** GCS source: bucket folder, project, OAuth token, `GCS_FILE_CACHE` flag.
- [ ] **P1** Web URL source via `WebBaseLoader`; capture title, language.
- [ ] **P1** YouTube source via `youtube_transcript_api` + proxy env.
- [ ] **P1** Wikipedia source (`WikipediaLoader`) with language detection.
- [ ] **P2** Chunked / resumable upload like `/upload` (`chunkNumber`/`totalChunks`).
- [ ] **P2** Image/OCR ingestion (Diffbot / Unstructured `extract_images`).
- [ ] **P2** Track `fileSource`, `fileType`, `url`, `language`, `gcsBucket/Folder/ProjectId` on `Document` nodes — we only store `source` path + `sha1`.

## 2. Chunking

**Ref**
- `CreateChunksofDocument.split_file_into_chunks(token_chunk_size, chunk_overlap, email)` (`create_chunks.py`) using `TokenTextSplitter`.
- Configurable per-extraction: `params.token_chunk_size`, `params.chunk_overlap`, `params.chunks_to_combine` posted from UI.
- Hard cap from env `MAX_TOKEN_CHUNK_SIZE` (default 10000).
- Bad-char strip (`"`, `\n`, `'`) before chunking.
- Carries `page_number`, `start_timestamp`, `end_timestamp` metadata into `Chunk` node.
- `chunk_id = sha1(page_content)`.

**Us**
- `MarkdownChunker` with `TokenTextSplitter`, sha1 ids, position, content_offset, soft cap. Settings: `chunk_token_size=600`, `chunk_overlap=80`, `chunks_to_combine=1`, `max_token_chunk_size=20000`. Good parity on the core algorithm.

**Gaps**
- [ ] **P1** Carry `page_number`, `start_time`/`end_time` (PDFs & YouTube) into Chunk node.
- [ ] **P2** Per-request override of chunk size/overlap/combine (currently global env-only).
- [ ] **P2** Pre-chunk bad-char stripping (`"`, `'`) to align hashes with reference.

## 3. Embeddings

**Ref**
- Provider-pluggable via `load_embedding_model(provider, model)` in `shared/common_fn.py`: `openai`, `vertexai`, `sentence-transformer` (`all-MiniLM-L6-v2` default, 384-dim), `bedrock`.
- Env `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `IS_EMBEDDING`.
- Per-user persisted embedding model: `/fetch_embedding_model`, `/change_embedding_model` (drops + recreates `vector` index if dim changes).
- Chunk vector index `vector` on `Chunk.embedding` via `Neo4jVector.create_new_index()`.
- Entity vector index `entity_vector` on `__Entity__.embedding` (post-processing, `ENTITY_EMBEDDING=true`).
- Community vector index `community_vector` on `__Community__.embedding`.
- `/drop_create_vector_index` endpoint to fix dim mismatches.

**Us**
- `backend/app/llm/build_embedder` → OpenAI/OpenRouter-compatible single endpoint. Chunk vector index created via `db.create.setNodeVectorProperty` and `chunk_vector` index. No entity / community embeddings.

**Gaps**
- [ ] **P0** Multi-provider embedder: OpenAI / Vertex / SentenceTransformers / Bedrock — currently only OpenAI-compatible HTTP.
- [ ] **P0** Embedding-dimension mismatch handling: detect existing index dim, prompt drop/recreate. Reference has `/drop_create_vector_index` + `EmbeddingDimensionWarningModal`.
- [ ] **P1** `__Entity__` embeddings (text = `id + " " + description`) + `entity_vector` index — required for `entity_vector` chat mode.
- [ ] **P1** `__Community__` embeddings + `community_vector` index — required for `global_vector` chat mode.
- [ ] **P1** Per-user / per-tenant embedding model persistence (`/change_embedding_model`).
- [ ] **P2** Toggle `IS_EMBEDDING=False` to skip chunk embeddings.

## 4. Entity & relationship extraction

**Ref**
- `get_graph_from_llm(model, chunks, allowedNodes, allowedRelationship, chunks_to_combine, additional_instructions)` in `src/llm.py`.
- Uses `LLMGraphTransformer` with `node_properties=["description"]` / `relationship_properties=["description"]` when structured output is supported (skipped for `ChatGroq`).
- `ADDITIONAL_INSTRUCTIONS` constant prepended to user instructions (`shared/constants.py`).
- Validates `allowedRelationship` is triplets of `(source, rel, target)` and that each source/target is in `allowedNodes`.
- Diffbot path: `DiffbotGraphTransformer` for entity extraction (no LLM prompt).
- Multi-model selectable per `/extract` call via `params.model`, mapped from env `LLM_MODEL_CONFIG_<MODEL_KEY>`: OpenAI (5.2, mini, 4.1), Gemini, Anthropic Claude, Groq, Fireworks, Bedrock Nova, Ollama, Azure, Diffbot.
- `additional_instructions` sanitized (`sanitize_additional_instruction`).
- `chunks_to_combine` window before LLM call.

**Us**
- `EntityExtractor` wraps `LLMGraphTransformer` with descriptions + sanitized additional instructions. Triplets supported via schema page. Single LLM (`build_chat_llm`), OpenRouter-only. Per-chunk retry w/ backoff and min-nodes heuristic. `chunks_to_combine` honored.

**Gaps**
- [ ] **P0** Triplet validation: ensure each (source, rel, target) has source/target in `allowed_nodes` (current code stores triplets but does not enforce this constraint).
- [ ] **P1** Multi-provider LLM dispatch: support Vertex/Gemini, Anthropic, Bedrock, Groq, Fireworks, Ollama, Azure OpenAI. Today: single OpenAI-compatible endpoint.
- [ ] **P1** Model selectable per-request (UI dropdown -> param), separate from extraction default.
- [ ] **P1** Diffbot extraction path (`DiffbotGraphTransformer`, no LLM, "facts" extraction).
- [ ] **P1** Token-usage tracking per extraction (TRACK_USER_USAGE / DAILY_TOKENS_LIMIT / MONTHLY_TOKENS_LIMIT in reference).
- [ ] **P2** Async (`aconvert_to_graph_documents`) extraction with batched windows — we use sync `convert_to_graph_documents` from threads. Reference uses async.
- [ ] **P2** `_Graph` structured-output probing per-LLM-class (we already do this; ensure parity for Groq → disabled).

## 5. Graph writes (Neo4j persistence)

**Ref** (`make_relationships.py`, `shared/common_fn.save_graphDocuments_in_neo4j`, `graphDB_dataAccess.py`)
- Uses **APOC**: `apoc.merge.node([type], {id})` for entity merge, `apoc.merge.relationship` for typed rels — requires APOC plugin.
- `MERGE (c:Chunk)-[:HAS_ENTITY]->(n)` per entity.
- Document node properties tracked per pipeline phase: `chunkNodeCount`, `chunkRelCount`, `entityNodeCount`, `entityEntityRelCount`, `communityNodeCount`, `communityRelCount`.
- `update_node_relationship_count(file_name)` rolls up counts after every batch of `UPDATE_GRAPH_CHUNKS_PROCESSED` (default 20) chunks.
- Cancellation: `Document.is_cancelled` polled between batches in `processing_source`.
- Retry conditions: `start_from_beginning`, `start_from_last_processed_position`, `delete_entities_and_start_from_beginning` (`set_status_retry`).
- `handle_backticks_nodes_relationship_id_type` sanitizes labels.
- `connection_check_and_get_vector_dimensions` validates dim before extract.

**Us**
- APOC-free Cypher: groups nodes by sanitized label, single `MERGE (n:`{label}` {id})` per group, then `MATCH ... MERGE (c)-[:HAS_ENTITY]->(e)`. Plus `MERGE (a)-[:REL]->(b)` per type. Constraints on `Document.fileName` + `Chunk.id`. Stats roll-up via single `COUNT { ... }` query in `stats()`.

**Gaps**
- [ ] **P0** Per-batch progress checkpoint (`processed_chunk`, `nodeCount`, `relationshipCount`) updated on the Document node every `UPDATE_GRAPH_CHUNKS_PROCESSED` chunks — we update only on completion of the whole doc.
- [ ] **P0** Cancellation: `is_cancelled` flag polled between batches; expose `/cancelled_job`. We have no per-job cancel.
- [ ] **P1** Granular counts on Document: `chunkNodeCount`, `chunkRelCount`, `entityNodeCount`, `entityEntityRelCount`, `communityNodeCount`, `communityRelCount` (used by frontend dashboards).
- [ ] **P1** Retry modes: `start_from_beginning`, `start_from_last_processed_position`, `delete_entities_and_start_from_beginning` (`POST /retry_processing`). Current reextract only does full delete + restart.
- [ ] **P2** `track_token_usage` per-chunk write to Document for cost visibility.

## 6. Post-processing

**Ref** (`src/post_processing.py`, `src/communities.py`, `score.py /post_processing`)
- Selectable task list posted as JSON:
  - `materialize_text_chunk_similarities` → `update_KNN_graph` writes `(Chunk)-[:SIMILAR {score}]-(Chunk)` keyed by `DUPLICATE_SCORE_VALUE` (0.97) and `KNN_MIN_SCORE` (0.8).
  - `enable_hybrid_search_and_fulltext_search_in_bloom` → fulltext indexes `entities`, `keyword`, `community_keyword`; vector indexes `vector`, `entity_vector` (drops + recreates).
  - `materialize_entity_similarities` (gated on `ENTITY_EMBEDDING=true`) → `create_entity_embedding`.
  - `graph_schema_consolidation` → LLM-driven label/rel rename using `GRAPH_CLEANUP_PROMPT` + `GRAPH_CLEANUP_MODEL` env (default openai_gpt_5_mini).
  - `enable_communities` → GDS Leiden via `graphdatascience.GraphDataScience`, hierarchical levels (up to 3), `__Community__` nodes per level with `PARENT_COMMUNITY` edges, community summaries (system+human templates), community embeddings + `community_vector` index, parent-community summaries.
- Duplicate detection: `get_duplicate_nodes_list` + `merge_duplicate_nodes` using text distance ≤ `DUPLICATE_TEXT_DISTANCE` (3) and score ≥ `DUPLICATE_SCORE_VALUE` (0.97).
- Unconnected (orphan) node listing + deletion: `list_unconnected_nodes`, `delete_unconnected_nodes`.

**Us** (`services/post_processing.py`, `community_summary_system.md` are new/in-progress)
- LLM cleanup (rename labels & rel types) implemented in `PostProcessingService.run_cleanup`.
- Communities: **Cypher-only WCC** (single level, no Leiden, intentionally no GDS) → `__Community__` + `IN_COMMUNITY`. Community ranks from doc count. Fulltext index `community_keyword`.
- Community summarization (`summarize_communities`) using `community_summary_system.md` prompt; stores `c.title`, `c.summary`.
- Chunk SIMILAR edges: `create_similar_chunk_relationships` in `GraphRepository` (called from old `PostProcessor`? — `post_processor.py` is 42 lines, separate from `post_processing.py`). Need to confirm wiring.

**Gaps**
- [ ] **P0** Hierarchical communities — current implementation is single-level WCC. Reference produces 3 levels with `PARENT_COMMUNITY` rollup. Required for `global_vector` retrieval mode.
- [ ] **P0** Leiden / weighted modularity option when GDS is available (env toggle: `COMMUNITY_ALGO=leiden|wcc`). Keep WCC as Community-Edition fallback.
- [ ] **P0** Duplicate-node detection + merge: `get_duplicate_nodes_list` (text distance + embedding score) and `merge_duplicate_nodes`. Endpoints `/get_duplicate_nodes`, `/merge_duplicate_nodes`. Not present.
- [ ] **P0** Orphan-node listing/deletion: `/get_unconnected_nodes_list`, `/delete_unconnected_nodes`. Not present.
- [ ] **P1** Entity embedding pass (`create_entity_embedding`) + `entity_vector` index.
- [ ] **P1** Community embeddings + `community_vector` index + parent-community summarization.
- [ ] **P1** Selectable task list in `POST /graph/post-process` body (similar to ref `tasks=`). Currently only `cleanup`, `communities`, `summaries` toggles.
- [ ] **P1** `community_rank` and `community.weight` (chunk-count) properties — we set `community_rank` but no `weight`.
- [ ] **P2** Drop+recreate `vector`, `entity_vector`, `community_vector` indexes on embedding-model change.
- [ ] **P2** Fulltext `entities` index over all entity labels for hybrid search.

## 7. Retrieval / Chat over the graph

**Ref** (`src/QA_integration.py`, constants `CHAT_MODE_CONFIG_MAP`)
- Endpoint `POST /chat_bot` with `mode` ∈ {`vector`, `entity_vector`, `graph_vector`, `graph_vector_fulltext` (default), `global_vector`, `graph`}.
- Each mode has a dedicated retrieval Cypher (see `constants.py` ~lines 717-820): retrieval_query, index_name, keyword_index, node_label, embedding_node_property, text_node_properties, top_k, document_filter.
- `graph` mode uses `GraphCypherQAChain.from_llm(cypher_llm, qa_llm, validate_cypher=True)`.
- `Neo4jChatMessageHistory` per session; `clear_chat_history` endpoint.
- Question rewriting (`QUESTION_TRANSFORM_TEMPLATE`) using prior messages.
- `ContextualCompressionRetriever` with `EmbeddingsFilter` (score threshold `CHAT_EMBEDDING_FILTER_SCORE_THRESHOLD`), `TokenTextSplitter` (`CHAT_DOC_SPLIT_SIZE`).
- Document filter: `{'fileName': {'$in': document_names}}` in retriever.
- Returns `sources`, `nodedetails` (chunks/entities/communities), `entities`, `total_tokens`, `mode`, `metric_details`.
- Auxiliary endpoints: `/chunk_entities`, `/get_neighbours`, `/graph_query`, `/fetch_chunktext`, `/schema_visualization`, `/clear_chat_bot`.
- RAGAS evaluation: `/metric`, `/additional_metrics` (faithfulness, answer_relevancy, context_precision, etc.).

**Us**
- No chat / RAG layer at all. `frontend/src/pages` has no ChatPage. `backend` has no `qa.py`. The graph viewer (`GraphViewer.tsx`) is the only consumer.

**Gaps**
- [ ] **P0** `POST /chat` endpoint with at least 3 modes: `vector` (chunk-only), `graph_vector_fulltext` (default), `graph` (Cypher).
- [ ] **P0** Chat session history (Neo4j-backed or sqlite-backed) + `/chat/clear`.
- [ ] **P0** Question-rewriting prompt over chat history.
- [ ] **P0** Frontend Chat UI: input, message thread, mode selector, document filter, "sources" panel.
- [ ] **P1** `entity_vector` mode (entity embeddings) + `global_vector` mode (community embeddings).
- [ ] **P1** `GraphCypherQAChain` with cypher validation for the `graph` mode.
- [ ] **P1** `EmbeddingsFilter` contextual compression (drop chunks below similarity threshold).
- [ ] **P1** Document-name filter passthrough to the retriever.
- [ ] **P1** Auxiliary: `/chunk_entities` (entity for a chunk id), `/get_neighbours` (n-hop around element id), `/fetch_chunktext` (paginated chunk text browse).
- [ ] **P1** `/schema_visualization` — distinct (label1)-[rel]->(label2) triples for SchemaViz UI.
- [ ] **P2** RAGAS evaluation harness (faithfulness, answer_relevancy, context_precision) and `MetricsTab` UI.
- [ ] **P2** SSE streaming of chat tokens.

## 8. Job orchestration & progress

**Ref**
- Per-document Server-Sent-Events stream: `GET /update_extract_status/{file_name}` polls `Document` node every tick. Status fields: `Status`, `nodeCount`, `relationshipCount`, `model`, `total_chunks`, `processed_chunk`, `token_usage`, etc.
- Per-document status fields persisted on Document node, not in app-side jobs table.
- `POST /cancelled_job` flips `Document.is_cancelled=true`; polled inside `processing_source`.
- `POST /retry_processing` sets `status=Ready to Reprocess` + `retry_condition`.
- `POST /post_processing` runs all enabled tasks synchronously over the whole graph.

**Us**
- Durable in-process `JobRegistry` (`services/job_registry.py`) with per-job event stream `/jobs/<id>/events` (SSE). Per-chunk progress, retry counts, error logs. Active-jobs banner on frontend.

**Gaps**
- [ ] **P1** Cancel a running ingest job (`POST /jobs/<id>/cancel`) — current registry has no cancellation hook on the worker thread.
- [ ] **P1** Retry-from-position semantics matching the reference (`start_from_last_processed_position`).
- [ ] **P2** Persist job rows across restarts (`job_registry.py` is in-memory; survived to SQLite per commit `169dc7f` — verify durability for events too).
- [ ] **P2** Per-document concurrent-extraction cap (env), to match ref's `UPDATE_GRAPH_CHUNKS_PROCESSED` batching window.

## 9. Settings / model configuration

**Ref**
- Envs: `LLM_MODEL_CONFIG_<key>` maps a UI model token to `(model_name, api_key, …)`; one set per provider. Endpoint `/backend_connection_configuration` exposes server-side default Neo4j config.
- `/connect` validates Neo4j + returns existing vector dim.
- `/populate_graph_schema` (text → schema via LLM): `schema_extraction_from_text(text, model, is_schema_description_checked, is_local_storage)`.
- `/schema` returns existing labels/rel triplets from the graph.
- Token-usage limits per user: `TRACK_USER_USAGE`, `DAILY_TOKENS_LIMIT`, `MONTHLY_TOKENS_LIMIT`, dedicated `TOKEN_TRACKER_DB_*`.

**Us**
- SQLite-backed `Settings` overrides + Pydantic defaults. UI on `SettingsPage` for LLM + Neo4j + embedding. `/settings/test/llm`, `/test/embedding`, `/test/neo4j`. Single LLM provider (OpenRouter-compatible).
- Schema management on `SchemaPage` with versioning (`/schema/versions`), discovery via `/schema/discover` (LLM samples chunks, returns node_labels + triplets).

**Gaps**
- [ ] **P1** Multi-provider LLM picker on Settings page (provider + model name + endpoint + key per provider, mirroring `LLM_MODEL_CONFIG_*`).
- [ ] **P1** Server-side default config endpoint (`/backend_connection_configuration`) so the UI can reuse env-provided Neo4j without prompting.
- [ ] **P1** Schema description toggle (`is_schema_description_checked`) — store per-label descriptions and feed them into the extraction prompt.
- [ ] **P2** Token-usage tracker DB (daily/monthly caps).

## 10. Frontend UI features

**Ref** (`llm-graph-builder/frontend/src/components/`)
- `DataSources/{AWS,GCS,Local}` & `WebSources/{Web,WikiPedia,Youtube}` connectors.
- `FileTable.tsx` with per-row status, model, chunks, nodes, rels, token_usage, retry button.
- `Popups/GraphEnhancementDialog/` — toggles for graph-cleanup / communities / similarity / fulltext post-processing tasks.
- `Popups/RetryConfirmation/` — picks retry condition.
- `Popups/EmbeddingDimensionWarningModal/` — drop/recreate index.
- `Popups/LargeFilePopUp/`, `Popups/DeletePopUp/`, `Popups/ChunkPopUp/`.
- `ChatBot/` — full chat UI: modes switch, sources/chunks/entities/communities tabs, metrics tab.
- `Graph/{GraphViewModal, SchemaViz, LegendsChip, GraphPropertiesPanel, CheckboxSelection, ResultOverview}`.

**Us** (`frontend/src/pages`)
- `DocumentsPage`, `GraphPage`, `IngestPage`, `JobsPage`, `LLMCallsPage`, `PromptsPage`, `SchemaPage`, `SettingsPage`.

**Gaps**
- [ ] **P0** Chat page + chat UI (see §7).
- [ ] **P1** Web/Wikipedia/YouTube/S3/GCS source-add dialogs.
- [ ] **P1** GraphEnhancementDialog with per-task toggles (replace single `/graph/post-process` switches).
- [ ] **P1** SchemaViz visualization of label-rel-label triples.
- [ ] **P1** Per-document retry dialog with three retry conditions.
- [ ] **P1** Embedding-dimension warning modal when picking a new embedding model.
- [ ] **P2** Duplicate-nodes manager + orphan-nodes cleaner (table + merge/delete actions).
- [ ] **P2** Chunk text viewer with pagination (`fetch_chunktext`).
- [ ] **P2** Token-usage column in the documents table.

---

## Priority roll-up

| Priority | Count | Theme |
| --- | --- | --- |
| **P0** | 14 | PDF/DOCX ingestion, multi-provider embeddings + dim safety, triplet validation, per-batch progress + cancel, duplicate/orphan cleanup, hierarchical communities, full chat layer (3 modes + history + UI) |
| **P1** | 30 | Remote sources (S3/GCS/Web/YT/Wiki), retry modes, multi-LLM providers, entity/community embeddings + indexes, graph-enhancement task list, schema viz, document filter in chat |
| **P2** | 17 | Resumable upload, async extraction, RAGAS metrics, token-tracking DB, chunk text browser, streaming chat |

## Top-10 sequenced backlog (recommended order)

1. **PDF + DOCX loaders** (`PyMuPDFLoader`, `UnstructuredFileLoader`) wired into upload + `MarkdownLoader` replacement.
2. **Triplet-validation in `EntityExtractor`** — enforce source/target ∈ `allowed_nodes` before sending to LLM.
3. **Per-batch Document checkpoint** (`processed_chunk`, `entityNodeCount`, etc.) every N chunks; matches ref `UPDATE_GRAPH_CHUNKS_PROCESSED`.
4. **Job cancellation** end-to-end: cooperative flag in pipeline worker + `POST /jobs/<id>/cancel` + UI button.
5. **Duplicate-nodes detector + merger** (text distance + embedding score) and orphan-nodes cleaner — both as endpoints + UI in GraphEnhancementDialog.
6. **Entity embeddings + `entity_vector` index** post-processing task; pre-requisite for `entity_vector` chat mode.
7. **Hierarchical communities** (Leiden via GDS when available, else multi-pass WCC by edge stripping); `PARENT_COMMUNITY` rollup + community embeddings + `community_vector`.
8. **Multi-provider LLM/embedding registry** (`LLM_MODEL_CONFIG_<key>` style) plus dim-safe index drop/recreate.
9. **Chat layer**: `POST /chat` with `vector`, `graph_vector_fulltext`, `graph` modes, Neo4j-backed session history, frontend ChatPage with sources panel.
10. **Remote sources**: Web + Wikipedia + YouTube first (no auth surface), then S3/GCS behind credential forms.
