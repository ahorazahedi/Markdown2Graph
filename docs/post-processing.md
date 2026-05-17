# Post-Processing Reference

Eight stages run sequentially after ingest: cleanup → dedup → orphans → communities → summaries → chunk/entity/community embeddings. Cancel-aware, progress-banded, idempotent (metadata preserved across rebuilds).

**Files:**
- `backend/app/services/post_processing.py` (orchestrator)
- `backend/app/services/post_processor.py` (vector indexes + similar-chunk edges, called from pipeline tail)
- `backend/app/api/graph.py` — `POST /api/graph/post-process`

---

## 1. Entry point

```python
PostProcessingService().run(
    cleanup=True, dedup=False, orphans=False,
    communities=True, summaries=True,
    chunk_embeddings=True, entity_embeddings=True, community_embeddings=True,
    community_levels=2,
    emit=emit_callback,        # (label, progress, extra) -> None
    is_cancelled=cancelled,
) -> PostProcessingReport
```

`PostProcessingReport` dataclass holds per-stage result dicts + `errors: list[str]` + `elapsed_seconds`.

---

## 2. HTTP endpoint

```
POST /api/graph/post-process
```

Body (all optional, all default true except dedup/orphans):
```json
{
  "cleanup": true, "dedup": false, "orphans": false,
  "communities": true, "summaries": true,
  "chunk_embeddings": true, "entity_embeddings": true, "community_embeddings": true,
  "community_levels": 2
}
```

Returns `{job_id, options}`. Submitted via `job_registry.submit(runner, kind="post_process", scope=opts)`.

### Single-flight guard

```python
for st in ("running", "queued", "cancelling"):
    existing = state.list_runs(status=st, kind="post_process", limit=1)
    if existing:
        return jsonify({
            "error": "post-process job already in progress",
            "job_id": existing[0]["id"],
            "status": existing[0]["status"],
        }), 409
```

Prevents racing community rebuilds that would corrupt state.

---

## 3. Stage breakdown

| # | Stage | Band | Idempotent? | Cancellable? |
|---|---|---|---|---|
| 1 | cleanup (LLM canonicalize labels/rels) | 0.05→0.20 | yes | check between stages |
| 2 | dedup (merge duplicate entity ids) | 0.22→0.38 | yes | between stages |
| 3 | orphans (delete entity nodes with no HAS_ENTITY) | 0.42→0.50 | yes | between stages |
| 4 | communities (Louvain hierarchy) | 0.55→0.75 | yes (metadata preserved) | yes |
| 5 | summaries (LLM titles + summaries) | 0.80→0.90 | yes (skip if `c.summary` non-empty) | yes |
| 6 | chunk_embeddings (backfill missing) | 0.91→0.92 | yes | yes |
| 7 | entity_embeddings (backfill missing) | 0.92→0.96 | yes | yes |
| 8 | community_embeddings (embed summaries) | 0.97→0.99 | yes | yes |

`_check_cancel()` raises `JobCancelled` between every stage.

---

## 4. Cleanup (LLM-driven label/rel canonicalization)

1. Fetch `repo.schema() → {labels, relationship_types}`.
2. Drop system labels/rels (`Chunk`, `Document`, `__Entity__`, `__Community__`, `HAS_ENTITY`, `PART_OF`, `NEXT_CHUNK`, `FIRST_CHUNK`, `SIMILAR`, `IN_COMMUNITY`, `PARENT_COMMUNITY`).
3. Render `graph_cleanup_system` prompt.
4. LLM returns `{nodes: {Canonical: [alias, alias]}, relationships: {...}}`.
5. Invert to `{alias: Canonical}`, **filtered to `allowed` set** so hallucinations can't damage schema.
6. Apply Cypher renames:

```cypher
-- Label rename
MATCH (n:`{old}`) SET n:`{new}` REMOVE n:`{old}`

-- Rel rename (copies props)
MATCH (a)-[r:`{old}`]->(b)
CREATE (a)-[r2:`{new}`]->(b)
SET r2 = properties(r)
DELETE r
```

Returns `{node_renames, rel_renames, node_map, rel_map}`.

---

## 5. Dedup

`repo.list_duplicate_entities(limit_groups=200, min_group_size=2)` groups entities by normalized id (e.g. lowercased). For each group:

- Sort by `(-chunk_count, id)` — highest-provenance member becomes canonical.
- `repo.merge_entities(canon_eid, alias_eids)` re-points relationships, unions properties, unions labels.

Returns `{groups_examined, groups_merged, aliases_merged, relationships_moved}`.

Also exposed standalone via `GET /api/graph/duplicates` + `POST /api/graph/duplicates/merge` for manual review.

---

## 6. Orphans

`repo.delete_orphan_entities()` — `MATCH (e:__Entity__) WHERE NOT (e)<-[:HAS_ENTITY]-() DETACH DELETE e`.

Also exposed standalone: `GET /api/graph/orphans` + `DELETE /api/graph/orphans` (body: `{element_ids?: [...]}`).

Returns `{orphans_found, deleted}`.

---

## 7. Communities (Louvain hierarchy)

Tries networkx Louvain; falls back to single-level WCC if networkx unavailable.

### Louvain path

```python
# 1. Edge list
MATCH (a:__Entity__)-[r]-(b:__Entity__)
WHERE elementId(a) < elementId(b)
RETURN elementId(a) AS a, elementId(b) AS b, count(r) AS w

# 2. Build nx.Graph (weighted)

# 3. Resolutions: [4.0, 1.0, 0.4, 0.15][:levels]    # 4.0 = coarsest, 0.15 = finest
for L, res in enumerate(resolutions):
    parts = louvain_communities(g, resolution=res, seed=42)
    partition_by_level.append({eid: idx for idx, mem in enumerate(parts) for eid in mem})

# 4. Snapshot metadata BEFORE wipe (keyed by member-set hash)
preserved = self._snapshot_community_metadata()
# preserved[(level, sha256_of_sorted_member_ids)] = {title, summary, embedding}

# 5. DETACH DELETE all communities

# 6. Create level-0 community nodes (MERGE by id="comm-L0-<idx>")
# 7. Restore title/summary where member_hash matches preserved
# 8. Restore embedding via db.create.setNodeVectorProperty

# 9. Link entities to L0:  (e)-[:IN_COMMUNITY]->(c)
# 10. Link parents by majority vote across L+1's partition
# 11. Community rank: distinct docs reaching c (L0); sum up the hierarchy
# 12. Community weight: distinct chunks reaching c (L0); sum up
# 13. Per-document counters (communityNodeCount, communityRelCount)
# 14. Fulltext index community_keyword on (summary, title)
```

### WCC fallback (Community Edition without networkx)

Repeated `MATCH ... SET` propagation produces a single level 0; no hierarchy.

Returns:
```python
{
    "communities": total_across_levels,
    "members": graph.number_of_nodes(),
    "edges": graph.number_of_edges(),
    "levels": len(partition_by_level),
    "per_level": [size, size, ...],
    "parent_links": int,
    "restored": int,       # how many got their metadata preserved
    "engine": "louvain" or "wcc",
}
```

### Member-hash preservation

```python
member_ids = sorted(id_by_eid.get(e) for e in mem if id_by_eid.get(e))
mhash = hashlib.sha256("|".join(member_ids).encode("utf-8")).hexdigest()
```

If a community's exact membership recurs after a rebuild, its previously-computed title/summary/embedding are restored — **zero LLM spend on no-op rebuilds**.

---

## 8. Community summaries

For each community with `size >= min_size (default 2)` and `summary IS NULL`:

```cypher
MATCH (a:__Entity__)-[r]->(b:__Entity__)
WHERE elementId(a) IN $member_ids AND elementId(b) IN $member_ids
RETURN collect(DISTINCT {id, type, description}) AS nodes,
       collect(DISTINCT {start, type, end}) AS rels
```

Build prompt from `community_summary_system`, invoke LLM with nodes+rels JSON (truncated to 6000 chars). Parse, store:

```cypher
MATCH (c:__Community__ {id: $id})
SET c.title = coalesce($title, c.title), c.summary = $summary
```

Returns `{summarized, considered}`. Tagged `community_summary`.

---

## 9. Embedding backfills

Each `embed_*` method:
1. List nodes without embeddings (`limit=20000` for chunks, `10000` for entities/communities).
2. Batch (size = `max(8, entity_embedding_batch)`).
3. Truncate per type (chunk 8000, entity 2000, community 4000 chars).
4. `embedder.embed_documents(texts)` → `repo.write_chunk_embeddings(...)`.
5. After all batches: `repo.create_*_vector_index(dim)`.

Returns `{embedded, dim}`. Cancel checked between batches.

See [Embeddings](./embeddings.md) for the standalone re-embed flow.

---

## 10. PostProcessor (pipeline tail, separate from PostProcessingService)

`backend/app/services/post_processor.py` — a small wrapper called by `IngestionPipeline.run_*` to do *minimal* post-processing inline (vector index, chat indexes, similar-chunk edges). The full multi-stage post-process is run separately via the API.

```python
class PostProcessor:
    def run(self, progress=None) -> dict:
        if not self.settings.enable_post_processing: return {...}
        self.repo.create_chunk_vector_index(self.settings.embedding_dimension)
        self.repo.create_chat_indexes()
        if self.settings.enable_similar_chunks:
            self.repo.create_similar_chunk_relationships(min_score=self.settings.knn_min_score)
        return {"vector_index": True, "chat_indexes": True, "similar_relationships": <count>}
```

---

## 11. Configuration

```python
enable_post_processing:    bool = True
enable_similar_chunks:     bool = True
enable_entity_embeddings:  bool = True
enable_community_embeddings: bool = True
entity_embedding_batch:    int = 64
knn_min_score:             float = 0.8        # SIMILAR edge threshold
```
