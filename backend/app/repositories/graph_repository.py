"""Cypher persistence layer.

All writes go through here so the rest of the codebase never builds Cypher
inline. Schema mirrors the reference llm-graph-builder graph:

    (:Document {fileName, sha1, title, source, createdAt, updatedAt})
    (:Chunk    {id, text, position, length, fileName, content_offset, embedding})
    (:__Entity__) – node also gets a domain label (e.g. :Disease)

    (Document)-[:FIRST_CHUNK]->(Chunk)
    (Chunk)-[:NEXT_CHUNK]->(Chunk)
    (Chunk)-[:PART_OF]->(Document)
    (Chunk)-[:HAS_ENTITY]->(:__Entity__)
    (Chunk)-[:SIMILAR {score}]-(Chunk)         (post-processing)
    (:__Entity__)-[:<REL_TYPE>]->(:__Entity__) (extracted)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

from ..extensions import neo4j_manager


class GraphRepository:
    def __init__(self):
        self._mgr = neo4j_manager

    # ---------- helpers ----------
    def _run(self, cypher: str, **params):
        with self._mgr.driver.session(database=self._mgr.database) as s:
            return list(s.run(cypher, **params))

    # ---------- schema bootstrap ----------
    def ensure_constraints(self) -> None:
        """Constraints compatible with Neo4j Community Edition.

        Property-existence constraints (`IS NOT NULL`) are Enterprise-only,
        so we only declare uniqueness on Document.fileName and Chunk.id.
        Entity dedupe relies on `apoc.merge.node({id: ...})` keyed on the
        node's id property, matching the reference llm-graph-builder.
        """
        statements = [
            "CREATE CONSTRAINT document_fileName IF NOT EXISTS "
            "FOR (d:Document) REQUIRE d.fileName IS UNIQUE",
            "CREATE CONSTRAINT chunk_id IF NOT EXISTS "
            "FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
        ]
        for q in statements:
            try:
                self._run(q)
            except Exception as e:
                # log + continue: an unsupported constraint shouldn't kill ingest
                import logging
                logging.warning("constraint create failed (%s): %s", q.split()[2], e)

    # ---------- documents ----------
    def upsert_document(self, file_name: str, sha1: str, title: str | None, source: str, length: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._run(
            """
            MERGE (d:Document {fileName: $fileName})
            ON CREATE SET d.createdAt = $now, d.status = 'New'
            SET d.sha1 = $sha1,
                d.title = $title,
                d.source = $source,
                d.length = $length,
                d.updatedAt = $now
            """,
            fileName=file_name,
            sha1=sha1,
            title=title,
            source=source,
            length=length,
            now=now,
        )

    def checkpoint_document_progress(self, file_name: str, *,
                                     processed_chunks: int,
                                     entity_count: int,
                                     relationship_count: int) -> None:
        """Mid-extraction progress write. Does NOT touch status — leaves it
        as 'Processing'. Property names mirror llm-graph-builder
        (`processedChunkCount`, `entityNodeCount`, `entityEntityRelCount`)
        so a crash mid-run leaves recoverable state on the Document node
        for a future resume-from-position retry.
        """
        self._run(
            """
            MATCH (d:Document {fileName: $fileName})
            SET d.processedChunkCount   = $processed,
                d.entityNodeCount       = $ents,
                d.entityEntityRelCount  = $rels,
                d.updatedAt             = $now
            """,
            fileName=file_name,
            processed=int(processed_chunks),
            ents=int(entity_count),
            rels=int(relationship_count),
            now=datetime.now(timezone.utc).isoformat(),
        )

    def set_document_status(self, file_name: str, status: str, error: str | None = None,
                            chunk_count: int | None = None, entity_count: int | None = None,
                            relationship_count: int | None = None) -> None:
        """Property names mirror llm-graph-builder:
            chunkNodeCount, entityNodeCount, entityEntityRelCount.
        We also roll up `chunkRelCount` (FIRST_CHUNK + NEXT_CHUNK + PART_OF
        + HAS_ENTITY out of this document) so the per-doc snapshot matches
        the reference's nodeCount/relationshipCount maths.
        """
        self._run(
            """
            MATCH (d:Document {fileName: $fileName})
            SET d.status                = $status,
                d.error                 = $error,
                d.chunkNodeCount        = coalesce($chunkCount,        d.chunkNodeCount),
                d.entityNodeCount       = coalesce($entityCount,       d.entityNodeCount),
                d.entityEntityRelCount  = coalesce($relationshipCount, d.entityEntityRelCount),
                d.processedAt           = $now
            """,
            fileName=file_name,
            status=status,
            error=error,
            chunkCount=chunk_count,
            entityCount=entity_count,
            relationshipCount=relationship_count,
            now=datetime.now(timezone.utc).isoformat(),
        )
        # chunkRelCount roll-up: structural edges originating from this doc.
        # Best-effort — a stale value is preferable to crashing on shutdown.
        try:
            self._run(
                """
                MATCH (d:Document {fileName: $fileName})
                OPTIONAL MATCH (d)-[fc:FIRST_CHUNK]->(:Chunk)
                OPTIONAL MATCH (d)<-[po:PART_OF]-(c:Chunk)
                OPTIONAL MATCH (c)-[nc:NEXT_CHUNK]->(:Chunk)
                OPTIONAL MATCH (c)-[he:HAS_ENTITY]->(:__Entity__)
                WITH d,
                     count(DISTINCT fc) + count(DISTINCT po) +
                     count(DISTINCT nc) + count(DISTINCT he) AS crel
                SET d.chunkRelCount = crel
                """,
                fileName=file_name,
            )
        except Exception:
            pass

    # ---------- chunks ----------
    def write_chunks(self, file_name: str, chunks: List[dict]) -> None:
        """`chunks`: list of dicts with keys id, text, position, length, content_offset."""
        self._run(
            """
            UNWIND $batch AS row
            MERGE (c:Chunk {id: row.id})
            SET c.text = row.text,
                c.position = row.position,
                c.length = row.length,
                c.content_offset = row.content_offset,
                c.fileName = $fileName
            WITH c
            MATCH (d:Document {fileName: $fileName})
            MERGE (c)-[:PART_OF]->(d)
            """,
            batch=chunks,
            fileName=file_name,
        )

    def link_first_and_next(self, file_name: str, chunk_ids: List[str]) -> None:
        if not chunk_ids:
            return
        self._run(
            """
            MATCH (d:Document {fileName: $fileName})
            MATCH (c0:Chunk {id: $first})
            MERGE (d)-[:FIRST_CHUNK]->(c0)
            """,
            fileName=file_name,
            first=chunk_ids[0],
        )
        if len(chunk_ids) > 1:
            pairs = [{"prev": a, "next": b} for a, b in zip(chunk_ids, chunk_ids[1:])]
            self._run(
                """
                UNWIND $pairs AS p
                MATCH (a:Chunk {id: p.prev})
                MATCH (b:Chunk {id: p.next})
                MERGE (a)-[:NEXT_CHUNK]->(b)
                """,
                pairs=pairs,
            )

    def write_chunk_embeddings(self, rows: List[dict]) -> None:
        """`rows`: dicts with id and embedding (list[float])."""
        if not rows:
            return
        self._run(
            """
            UNWIND $rows AS row
            MATCH (c:Chunk {id: row.id})
            CALL db.create.setNodeVectorProperty(c, 'embedding', row.embedding)
            """,
            rows=rows,
        )

    # ---------- entities & relationships ----------
    def write_graph_documents(self, file_name: str, graph_docs: list) -> tuple[int, int]:
        """Persist nodes + relationships extracted by LLMGraphTransformer.

        Pure-Cypher: groups nodes/rels by sanitized label/type and emits one
        MERGE per group with the literal label interpolated into the query.
        Label/type strings are validated by `_sanitize_label` and
        `_sanitize_rel`, which restrict the output to `[A-Za-z0-9_]`, so
        string interpolation is safe.

        This avoids the apoc.merge.* procedures which require the APOC
        plugin to be installed in Neo4j.
        """
        from collections import defaultdict

        nodes_by_label: dict[str, list[dict]] = defaultdict(list)
        rels_by_type: dict[str, list[dict]] = defaultdict(list)
        chunk_entity_links: list[dict] = []
        seen_node_ids: set[str] = set()

        for gd in graph_docs:
            meta = gd.source.metadata or {}
            # When the pipeline combines N chunks per LLM call we get back ONE
            # graph_doc but N source-chunk ids. Fan HAS_ENTITY out to every
            # contributing chunk so per-doc entity provenance stays correct.
            combined = meta.get("combined_chunk_ids")
            chunk_ids = (
                [c for c in combined if c]
                if isinstance(combined, list) and combined
                else ([meta.get("chunk_id")] if meta.get("chunk_id") else [])
            )
            for n in gd.nodes:
                label = self._sanitize_label(n.type) or "Entity"
                nid = str(n.id)
                props = {k: v for k, v in (getattr(n, "properties", {}) or {}).items() if v is not None}
                nodes_by_label[label].append({"id": nid, "props": props})
                seen_node_ids.add(nid)
                for cid in chunk_ids:
                    chunk_entity_links.append({"chunk_id": cid, "node_id": nid})
            for r in gd.relationships:
                rel_type = self._sanitize_rel(r.type) or "RELATED_TO"
                rels_by_type[rel_type].append({
                    "src_id": str(r.source.id),
                    "dst_id": str(r.target.id),
                    "props": {k: v for k, v in (getattr(r, "properties", {}) or {}).items() if v is not None},
                })

        # ---- nodes: one MERGE query per distinct sanitized label ----
        for label, batch in nodes_by_label.items():
            # `label` is sanitized to [A-Za-z0-9_]; safe to backtick-quote.
            self._run(
                f"""
                UNWIND $batch AS row
                MERGE (n:`{label}` {{id: row.id}})
                  ON CREATE SET n :`__Entity__`
                SET n += row.props
                """,
                batch=batch,
            )

        # ---- chunk -> entity links ----
        if chunk_entity_links:
            self._run(
                """
                UNWIND $links AS l
                MATCH (c:Chunk {id: l.chunk_id})
                MATCH (e:__Entity__ {id: l.node_id})
                MERGE (c)-[:HAS_ENTITY]->(e)
                """,
                links=chunk_entity_links,
            )

        # ---- relationships: one MERGE query per distinct sanitized type ----
        total_rel = 0
        for rel_type, batch in rels_by_type.items():
            total_rel += len(batch)
            self._run(
                f"""
                UNWIND $batch AS row
                MATCH (a:__Entity__ {{id: row.src_id}})
                MATCH (b:__Entity__ {{id: row.dst_id}})
                MERGE (a)-[r:`{rel_type}`]->(b)
                SET r += row.props
                """,
                batch=batch,
            )

        return len(seen_node_ids), total_rel

    # ---------- post-processing ----------
    def create_entity_vector_index(self, dimension: int) -> None:
        """Vector index on __Entity__.embedding — needed for `entity_vector`
        chat mode. Cosine similarity, same dimension as Chunk.embedding."""
        try:
            self._run("DROP INDEX entity_vector IF EXISTS")
        except Exception:
            pass
        self._run(
            f"""
            CREATE VECTOR INDEX entity_vector IF NOT EXISTS
            FOR (n:__Entity__) ON (n.embedding)
            OPTIONS {{
              indexConfig: {{
                `vector.dimensions`: {int(dimension)},
                `vector.similarity_function`: 'cosine'
              }}
            }}
            """
        )

    def create_community_vector_index(self, dimension: int) -> None:
        try:
            self._run("DROP INDEX community_vector IF EXISTS")
        except Exception:
            pass
        self._run(
            f"""
            CREATE VECTOR INDEX community_vector IF NOT EXISTS
            FOR (n:__Community__) ON (n.embedding)
            OPTIONS {{
              indexConfig: {{
                `vector.dimensions`: {int(dimension)},
                `vector.similarity_function`: 'cosine'
              }}
            }}
            """
        )

    def list_entities_needing_embedding(self, *, limit: int = 5000) -> list[dict]:
        """__Entity__ nodes without an `embedding` property yet."""
        rows = self._run(
            """
            MATCH (e:__Entity__)
            WHERE e.embedding IS NULL
            RETURN elementId(e) AS eid,
                   coalesce(e.id, '') AS id,
                   coalesce(e.description, '') AS description
            LIMIT $limit
            """,
            limit=int(limit),
        )
        return [dict(r) for r in rows]

    def list_communities_needing_embedding(self, *, limit: int = 5000) -> list[dict]:
        """__Community__ nodes with a summary but no embedding yet."""
        rows = self._run(
            """
            MATCH (c:__Community__)
            WHERE c.embedding IS NULL AND c.summary IS NOT NULL AND c.summary <> ''
            RETURN elementId(c) AS eid,
                   coalesce(c.title, c.id, '') AS title,
                   coalesce(c.summary, '') AS summary
            LIMIT $limit
            """,
            limit=int(limit),
        )
        return [dict(r) for r in rows]

    def write_entity_embeddings(self, rows: list[dict]) -> None:
        """rows = [{eid, embedding}, ...]"""
        if not rows:
            return
        self._run(
            """
            UNWIND $rows AS row
            MATCH (e:__Entity__) WHERE elementId(e) = row.eid
            CALL db.create.setNodeVectorProperty(e, 'embedding', row.embedding)
            """,
            rows=rows,
        )

    def write_community_embeddings(self, rows: list[dict]) -> None:
        if not rows:
            return
        self._run(
            """
            UNWIND $rows AS row
            MATCH (c:__Community__) WHERE elementId(c) = row.eid
            CALL db.create.setNodeVectorProperty(c, 'embedding', row.embedding)
            """,
            rows=rows,
        )

    def create_chat_indexes(self) -> None:
        """Indexes the chat/RAG retriever depends on.

        - `keyword`: fulltext on Chunk.text — feeds the `hybrid` search type
          on Neo4jVector for the default chat mode (graph_vector_fulltext).
        - `entities`: fulltext on __Entity__.id + description — used by
          entity-mode retrieval and the entity-panel search.

        Idempotent. Safe to call from post-processing on every run.
        """
        for q in (
            "CREATE FULLTEXT INDEX keyword IF NOT EXISTS "
            "FOR (n:Chunk) ON EACH [n.text]",
            "CREATE FULLTEXT INDEX entities IF NOT EXISTS "
            "FOR (n:__Entity__) ON EACH [n.id, n.description]",
        ):
            try:
                self._run(q)
            except Exception as e:
                import logging
                logging.warning("chat index skipped (%s): %s", q.split()[3], e)

    def create_chunk_vector_index(self, dimension: int) -> None:
        """Create the Chunk-embedding vector index under the name `vector`.

        Matches llm-graph-builder's index name so its Cypher retrieval
        snippets work verbatim. If a legacy `chunk_vector` index exists
        from a prior build, drop it first — having both means writes pay
        double cost and `Neo4jVector` may bind to the wrong one.
        """
        try:
            self._run("DROP INDEX chunk_vector IF EXISTS")
        except Exception:
            pass
        self._run(
            f"""
            CREATE VECTOR INDEX vector IF NOT EXISTS
            FOR (c:Chunk) ON (c.embedding)
            OPTIONS {{
              indexConfig: {{
                `vector.dimensions`: {int(dimension)},
                `vector.similarity_function`: 'cosine'
              }}
            }}
            """
        )

    # ---------- dedup / orphan management ----------
    def list_duplicate_entities(self, limit_groups: int = 50,
                                min_group_size: int = 2) -> list[dict]:
        """Group __Entity__ nodes whose normalized id **and domain labels**
        match. Same-label gating matches llm-graph-builder's
        `get_duplicate_nodes_list` — without it, an entity `id="java"`
        labeled `Language` and another `id="java"` labeled `Island` would
        get folded into one node, corrupting the graph.

        Normalization (lower-case, strip non-alphanumeric) catches the most
        common LLM duplicates: 'Type 2 Diabetes' vs 'type-2-diabetes' vs
        'TYPE 2 DIABETES'. We deliberately stop short of embedding-based
        clustering — that lands when entity embeddings ship.

        Pure-Cypher (APOC-free): we pull every entity row and group in
        Python. For the sizes typical of this app (<100k entities) the
        round-trip is cheaper than implementing a regex normalizer in
        plain Cypher.
        """
        rows = self._run(
            """
            MATCH (e:__Entity__)
            OPTIONAL MATCH (e)<-[he:HAS_ENTITY]-()
            WITH e, count(he) AS chunk_count
            OPTIONAL MATCH (e)-[r]-(:__Entity__)
            WITH e, chunk_count, count(r) AS rel_count
            RETURN e.id AS id, elementId(e) AS element_id,
                   [l IN labels(e) WHERE l <> '__Entity__'] AS labels,
                   chunk_count, rel_count, e.description AS description
            """
        )
        from collections import defaultdict
        # group key = (normalized id, sorted-domain-label-tuple). Same-label
        # gating mirrors reference behavior.
        groups: dict[tuple[str, tuple[str, ...]], list[dict]] = defaultdict(list)
        for r in rows:
            raw = r["id"] or ""
            norm = "".join(ch for ch in str(raw).lower() if ch.isalnum())
            if not norm:
                continue
            label_key = tuple(sorted(r.get("labels") or []))
            groups[(norm, label_key)].append(dict(r))
        out = []
        for (norm, label_key), members in groups.items():
            if len(members) < min_group_size:
                continue
            members.sort(key=lambda m: (-int(m["chunk_count"] or 0), m["id"] or ""))
            # surface labels in the key so the UI can show what's being merged
            display_key = norm if not label_key else f"{norm} [{', '.join(label_key)}]"
            out.append({"key": display_key, "members": members})
        out.sort(key=lambda g: -len(g["members"]))
        return out[:limit_groups]

    def merge_entities(self, canonical_element_id: str,
                       alias_element_ids: list[str]) -> dict:
        """Fold each alias node into the canonical node.

        Steps for every alias:
          1. Re-point every incoming relationship to the canonical node.
          2. Re-point every outgoing relationship to the canonical node.
          3. Copy any property the canonical lacks (canonical wins ties).
          4. Detach-delete the alias.

        Returns counts of affected aliases / rels. Self-loops created in
        step 1+2 are cleaned at the end.
        """
        if not alias_element_ids:
            return {"merged": 0, "moved_relationships": 0}
        # use apoc.refactor.mergeNodes when available — much faster + atomic
        try:
            res = self._run(
                """
                MATCH (canon) WHERE elementId(canon) = $canon
                UNWIND $aliases AS aid
                MATCH (alias) WHERE elementId(alias) = aid AND elementId(alias) <> $canon
                WITH collect(alias) AS aliases, canon
                CALL apoc.refactor.mergeNodes(
                    [canon] + aliases,
                    {properties: 'combine', mergeRels: true}
                ) YIELD node
                RETURN count(aliases) AS merged
                """,
                canon=canonical_element_id, aliases=alias_element_ids,
            )
            return {"merged": int(res[0]["merged"]) if res else 0,
                    "engine": "apoc"}
        except Exception:
            pass

        # ---- pure Cypher path ----
        moved = 0
        for aid in alias_element_ids:
            if aid == canonical_element_id:
                continue
            in_rows = self._run(
                """
                MATCH (alias) WHERE elementId(alias) = $alias
                MATCH (x)-[r]->(alias)
                RETURN elementId(r) AS rid, elementId(x) AS xid,
                       type(r) AS t, properties(r) AS props
                """,
                alias=aid,
            )
            out_rows = self._run(
                """
                MATCH (alias) WHERE elementId(alias) = $alias
                MATCH (alias)-[r]->(y)
                RETURN elementId(r) AS rid, elementId(y) AS yid,
                       type(r) AS t, properties(r) AS props
                """,
                alias=aid,
            )
            for row in in_rows:
                if row["xid"] == canonical_element_id:
                    continue
                self._run(
                    f"""
                    MATCH (x) WHERE elementId(x) = $xid
                    MATCH (canon) WHERE elementId(canon) = $canon
                    MERGE (x)-[nr:`{self._sanitize_rel(row['t']) or 'RELATED_TO'}`]->(canon)
                    SET nr += $props
                    """,
                    xid=row["xid"], canon=canonical_element_id, props=row["props"] or {},
                )
                moved += 1
            for row in out_rows:
                if row["yid"] == canonical_element_id:
                    continue
                self._run(
                    f"""
                    MATCH (y) WHERE elementId(y) = $yid
                    MATCH (canon) WHERE elementId(canon) = $canon
                    MERGE (canon)-[nr:`{self._sanitize_rel(row['t']) or 'RELATED_TO'}`]->(y)
                    SET nr += $props
                    """,
                    yid=row["yid"], canon=canonical_element_id, props=row["props"] or {},
                )
                moved += 1
            # copy missing scalar props onto canonical (canon wins ties)
            self._run(
                """
                MATCH (canon) WHERE elementId(canon) = $canon
                MATCH (alias) WHERE elementId(alias) = $alias
                WITH canon, alias, [k IN keys(alias) WHERE NOT k IN keys(canon)] AS missing
                FOREACH (k IN missing |
                    SET canon[k] = alias[k]
                )
                """,
                canon=canonical_element_id, alias=aid,
            )
            # copy alias domain labels onto canonical — one statement per label
            # since Cypher cannot SET a dynamic label
            alias_labels = self._run(
                """
                MATCH (alias) WHERE elementId(alias) = $alias
                RETURN [l IN labels(alias) WHERE l <> '__Entity__'] AS ls
                """,
                alias=aid,
            )
            if alias_labels:
                for lab in (alias_labels[0]["ls"] or []):
                    safe = self._sanitize_label(lab)
                    if not safe:
                        continue
                    self._run(
                        f"MATCH (canon) WHERE elementId(canon) = $canon SET canon:`{safe}`",
                        canon=canonical_element_id,
                    )
            # detach-delete the alias
            self._run(
                "MATCH (alias) WHERE elementId(alias) = $alias DETACH DELETE alias",
                alias=aid,
            )
        # clean up self-loops we might have introduced
        self._run(
            """
            MATCH (canon)-[r]->(canon) WHERE elementId(canon) = $canon
            DELETE r
            """,
            canon=canonical_element_id,
        )
        return {"merged": len([a for a in alias_element_ids if a != canonical_element_id]),
                "moved_relationships": moved, "engine": "cypher"}

    def list_orphan_entities(self, limit: int = 500) -> list[dict]:
        """__Entity__ nodes with no Chunk pointing at them. These survive
        deletion of a Document if a previous run didn't sweep them, or are
        produced as a side-effect of merges / rewrites."""
        rows = self._run(
            """
            MATCH (e:__Entity__)
            WHERE NOT (e)<-[:HAS_ENTITY]-(:Chunk)
            RETURN e.id AS id, elementId(e) AS element_id,
                   [l IN labels(e) WHERE l <> '__Entity__'] AS labels,
                   e.description AS description
            ORDER BY id
            LIMIT $limit
            """,
            limit=int(limit),
        )
        return [dict(r) for r in rows]

    def delete_orphan_entities(self, element_ids: list[str] | None = None) -> int:
        """If `element_ids` is given, delete just those; otherwise sweep every
        orphan in the graph. Returns the count deleted."""
        if element_ids:
            res = self._run(
                """
                MATCH (e:__Entity__) WHERE elementId(e) IN $ids
                  AND NOT (e)<-[:HAS_ENTITY]-(:Chunk)
                WITH collect(e) AS doomed, count(e) AS n
                FOREACH (x IN doomed | DETACH DELETE x)
                RETURN n
                """,
                ids=element_ids,
            )
        else:
            res = self._run(
                """
                MATCH (e:__Entity__)
                WHERE NOT (e)<-[:HAS_ENTITY]-(:Chunk)
                WITH collect(e) AS doomed, count(e) AS n
                FOREACH (x IN doomed | DETACH DELETE x)
                RETURN n
                """
            )
        return int(res[0]["n"]) if res else 0

    def create_similar_chunk_relationships(self, top_k: int = 5, min_score: float = 0.8) -> int:
        try:
            res = self._run(
                """
                MATCH (c:Chunk) WHERE c.embedding IS NOT NULL
                CALL db.index.vector.queryNodes('vector', $k, c.embedding) YIELD node, score
                WITH c, node, score
                WHERE c <> node AND score >= $min
                MERGE (c)-[r:SIMILAR]-(node)
                SET r.score = score
                RETURN count(*) AS rels
                """,
                k=top_k + 1,  # nearest is self
                min=min_score,
            )
            return int(res[0]["rels"]) if res else 0
        except Exception:
            return 0

    # ---------- read APIs ----------
    def stats(self) -> dict:
        rows = self._run(
            """
            RETURN
              COUNT { MATCH (d:Document) }                            AS docs,
              COUNT { MATCH (c:Chunk) }                               AS chunks,
              COUNT { MATCH (e:__Entity__) }                          AS entities,
              COUNT { MATCH (:__Entity__)-[r]->(:__Entity__) }        AS entity_rels,
              COUNT { MATCH (:Chunk)-[r:HAS_ENTITY]->() }             AS has_entity_rels
            """
        )
        if not rows:
            return {"documents": 0, "chunks": 0, "entities": 0, "entity_relationships": 0,
                    "has_entity_relationships": 0}
        r = rows[0]
        return {
            "documents": r["docs"],
            "chunks": r["chunks"],
            "entities": r["entities"],
            "entity_relationships": r["entity_rels"],
            "has_entity_relationships": r["has_entity_rels"],
        }

    def schema(self) -> dict:
        labels = [r[0] for r in self._run("CALL db.labels() YIELD label RETURN label")]
        rels = [r[0] for r in self._run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")]
        return {"labels": labels, "relationship_types": rels}

    def list_documents(self) -> List[dict]:
        # Read the reference-aligned property names, but coalesce against the
        # pre-rename names so a Document written before the migration still
        # surfaces correct counts in the frontend.
        rows = self._run(
            """
            MATCH (d:Document)
            RETURN d.fileName AS fileName, d.title AS title, d.status AS status,
                   coalesce(d.chunkNodeCount,       d.chunkCount)       AS chunks,
                   coalesce(d.entityNodeCount,      d.entityCount)      AS entities,
                   coalesce(d.entityEntityRelCount, d.relationshipCount) AS rels,
                   d.communityNodeCount AS communities,
                   d.processedAt AS processedAt,
                   d.updatedAt AS updatedAt
            ORDER BY coalesce(d.updatedAt, '') DESC
            """
        )
        return [dict(r) for r in rows]

    def clear_all(self) -> None:
        self._run("MATCH (n) DETACH DELETE n")

    # ---------- per-document ops ----------
    def delete_document(self, file_name: str) -> None:
        """Remove a single Document and everything attached to it that's not
        shared with another doc: its chunks (always) and entities that no
        other chunk references after this delete."""
        # delete chunks (cascades HAS_ENTITY edges); entities then orphan-clean
        self._run(
            """
            MATCH (d:Document {fileName: $fileName})
            OPTIONAL MATCH (d)<-[:PART_OF]-(c:Chunk)
            DETACH DELETE c, d
            """,
            fileName=file_name,
        )
        self._run(
            """
            MATCH (e:__Entity__)
            WHERE NOT (e)<-[:HAS_ENTITY]-(:Chunk)
            DETACH DELETE e
            """
        )

    def list_document_entities(self, file_name: str, limit: int = 500) -> dict:
        nodes = self._run(
            """
            MATCH (d:Document {fileName: $fileName})<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e:__Entity__)
            WITH DISTINCT e
            RETURN e.id AS id,
                   [l IN labels(e) WHERE l <> '__Entity__'] AS labels,
                   e.description AS description
            ORDER BY id
            LIMIT $limit
            """,
            fileName=file_name,
            limit=limit,
        )
        rels = self._run(
            """
            MATCH (d:Document {fileName: $fileName})<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e1:__Entity__)
            MATCH (e1)-[r]->(e2:__Entity__)
            WHERE EXISTS {
                MATCH (d)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e2)
            }
            RETURN DISTINCT e1.id AS source, type(r) AS type, e2.id AS target
            LIMIT $limit
            """,
            fileName=file_name,
            limit=limit,
        )
        return {
            "nodes": [{"id": r["id"], "labels": r["labels"], "description": r.get("description")} for r in nodes],
            "relationships": [{"source": r["source"], "type": r["type"], "target": r["target"]} for r in rels],
        }

    # ---------- explorer ----------
    def _append_communities(self, nodes_out: list, rels_out: list,
                            ids: list[str]) -> None:
        """Mutate `nodes_out` / `rels_out` to include __Community__ nodes
        attached to any entity in `ids`, plus parent communities up the
        hierarchy and the IN_COMMUNITY / PARENT_COMMUNITY edges. Mirrors
        the include_structure pattern for Document/Chunk."""
        if not ids:
            return
        rows = self._run(
            """
            MATCH (e:__Entity__) WHERE elementId(e) IN $ids
            OPTIONAL MATCH (e)-[ic:IN_COMMUNITY]->(c0:__Community__)
            WITH collect(DISTINCT c0) AS c0s, collect(DISTINCT ic) AS ics
            // walk up via PARENT_COMMUNITY (any depth)
            UNWIND c0s AS root
            OPTIONAL MATCH path = (root)-[:PARENT_COMMUNITY*0..4]->(p:__Community__)
            WITH c0s, ics,
                 collect(DISTINCT p) AS ancestors,
                 collect(DISTINCT relationships(path)) AS pc_lists
            // flatten the list-of-lists of PARENT_COMMUNITY edges
            UNWIND pc_lists AS pcs
            UNWIND pcs AS pc
            WITH c0s, ics, ancestors, collect(DISTINCT pc) AS pcs
            RETURN c0s + ancestors AS comms, ics AS ic_edges, pcs AS pc_edges
            """,
            ids=ids,
        )
        if not rows:
            return
        row = rows[0]
        seen_node_ids = {n["element_id"] for n in nodes_out}
        seen_rel_ids = {r["element_id"] for r in rels_out}

        for c in row["comms"] or []:
            if c is None:
                continue
            eid = c.element_id
            if eid in seen_node_ids:
                continue
            props = {k: v for k, v in dict(c).items() if k != "embedding"}
            nodes_out.append({
                "element_id": eid,
                "id": c.get("id") or eid,
                "labels": ["__Community__"],
                "description": c.get("summary") or c.get("title"),
                "properties": props,
                "sources": [],
            })
            seen_node_ids.add(eid)

        for bucket in ("ic_edges", "pc_edges"):
            for r in row[bucket] or []:
                if r is None:
                    continue
                eid = r.element_id
                if eid in seen_rel_ids:
                    continue
                s = r.start_node.element_id
                t = r.end_node.element_id
                if s not in seen_node_ids or t not in seen_node_ids:
                    continue
                rels_out.append({
                    "element_id": eid,
                    "source": s,
                    "target": t,
                    "type": r.type,
                    "properties": dict(r),
                })
                seen_rel_ids.add(eid)

    def explore(self, limit_nodes: int = 200, file_name: str | None = None,
                label: str | None = None, include_structure: bool = False,
                include_communities: bool = False) -> dict:
        """Return a bounded sample of entity nodes + their relationships, with
        document provenance attached to each entity (which fileName(s) they
        came from). Used by the frontend graph viewer."""
        if file_name:
            nodes = self._run(
                """
                MATCH (d:Document {fileName: $fileName})<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e:__Entity__)
                WITH DISTINCT e
                RETURN elementId(e) AS element_id,
                       e.id AS id,
                       [l IN labels(e) WHERE l <> '__Entity__'] AS labels,
                       e.description AS description,
                       properties(e) AS properties
                LIMIT $limit
                """,
                fileName=file_name, limit=limit_nodes,
            )
        elif label:
            nodes = self._run(
                f"""
                MATCH (e:`{label}`:`__Entity__`)
                RETURN elementId(e) AS element_id,
                       e.id AS id,
                       [l IN labels(e) WHERE l <> '__Entity__'] AS labels,
                       e.description AS description,
                       properties(e) AS properties
                LIMIT $limit
                """,
                limit=limit_nodes,
            )
        else:
            nodes = self._run(
                """
                MATCH (e:__Entity__)
                RETURN elementId(e) AS element_id,
                       e.id AS id,
                       [l IN labels(e) WHERE l <> '__Entity__'] AS labels,
                       e.description AS description,
                       properties(e) AS properties
                LIMIT $limit
                """,
                limit=limit_nodes,
            )
        ids = [n["element_id"] for n in nodes]
        rels = []
        sources_by_node: dict[str, list[str]] = {}
        if ids:
            rels = self._run(
                """
                MATCH (a:__Entity__)-[r]->(b:__Entity__)
                WHERE elementId(a) IN $ids AND elementId(b) IN $ids
                RETURN elementId(r) AS element_id,
                       elementId(a) AS source,
                       elementId(b) AS target,
                       type(r) AS type,
                       properties(r) AS properties
                """,
                ids=ids,
            )
            src_rows = self._run(
                """
                MATCH (e:__Entity__)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d:Document)
                WHERE elementId(e) IN $ids
                RETURN elementId(e) AS element_id, collect(DISTINCT d.fileName) AS files
                """,
                ids=ids,
            )
            sources_by_node = {r["element_id"]: r["files"] for r in src_rows}

        nodes_out = []
        for n in nodes:
            props = {k: v for k, v in (n.get("properties") or {}).items() if k != "embedding"}
            nodes_out.append({
                "element_id": n["element_id"],
                "id": n["id"],
                "labels": n["labels"],
                "description": n["description"],
                "properties": props,
                "sources": sources_by_node.get(n["element_id"], []),
            })
        rels_out = [
            {
                "element_id": r["element_id"],
                "source": r["source"],
                "target": r["target"],
                "type": r["type"],
                "properties": {k: v for k, v in (r.get("properties") or {}).items()},
            }
            for r in rels
        ]

        if include_structure and ids:
            struct = self._run(
                """
                MATCH (e:__Entity__) WHERE elementId(e) IN $ids
                OPTIONAL MATCH (e)<-[he:HAS_ENTITY]-(c:Chunk)-[po:PART_OF]->(d:Document)
                WITH collect(DISTINCT c) AS chunks,
                     collect(DISTINCT d) AS docs,
                     collect(DISTINCT he) AS hes,
                     collect(DISTINCT po) AS pos
                OPTIONAL MATCH (c1:Chunk)-[nc:NEXT_CHUNK]->(c2:Chunk)
                  WHERE c1 IN chunks AND c2 IN chunks
                OPTIONAL MATCH (d2:Document)-[fc:FIRST_CHUNK]->(c3:Chunk)
                  WHERE d2 IN docs AND c3 IN chunks
                RETURN chunks, docs, hes, pos,
                       collect(DISTINCT nc) AS ncs,
                       collect(DISTINCT fc) AS fcs
                """,
                ids=ids,
            )
            if struct:
                row = struct[0]
                seen_node_ids = {n["element_id"] for n in nodes_out}
                seen_rel_ids = {r["element_id"] for r in rels_out}

                for d in row["docs"] or []:
                    eid = d.element_id
                    if eid in seen_node_ids:
                        continue
                    props = {k: v for k, v in dict(d).items() if k != "embedding"}
                    nodes_out.append({
                        "element_id": eid,
                        "id": d.get("fileName") or d.get("title") or eid,
                        "labels": ["Document"],
                        "description": d.get("title"),
                        "properties": props,
                        "sources": [d.get("fileName")] if d.get("fileName") else [],
                    })
                    seen_node_ids.add(eid)

                for c in row["chunks"] or []:
                    eid = c.element_id
                    if eid in seen_node_ids:
                        continue
                    full_text = c.get("text") or ""
                    # keep text out of properties (shown separately) but expose
                    # full chunk content as description for the drawer
                    props = {k: v for k, v in dict(c).items() if k not in ("embedding", "text")}
                    nodes_out.append({
                        "element_id": eid,
                        "id": c.get("id") or eid,
                        "labels": ["Chunk"],
                        "description": full_text,
                        "properties": props,
                        "sources": [c.get("fileName")] if c.get("fileName") else [],
                    })
                    seen_node_ids.add(eid)

                for bucket, _ in (("hes", "HAS_ENTITY"), ("pos", "PART_OF"),
                                  ("ncs", "NEXT_CHUNK"), ("fcs", "FIRST_CHUNK")):
                    for r in row[bucket] or []:
                        eid = r.element_id
                        if eid in seen_rel_ids:
                            continue
                        s = r.start_node.element_id
                        t = r.end_node.element_id
                        if s not in seen_node_ids or t not in seen_node_ids:
                            continue
                        rels_out.append({
                            "element_id": eid,
                            "source": s,
                            "target": t,
                            "type": r.type,
                            "properties": dict(r),
                        })
                        seen_rel_ids.add(eid)

        if include_communities:
            self._append_communities(nodes_out, rels_out, ids)

        return {"nodes": nodes_out, "relationships": rels_out}

    def neighborhood(
        self,
        element_id: str,
        depth: int = 1,
        limit_nodes: int = 200,
        include_structure: bool = False,
        include_communities: bool = False,
    ) -> dict:
        """Return the k-hop induced subgraph around a single focal node.

        Traversal goes over **entity-to-entity** relationships only. When
        ``include_structure`` is true, Document/Chunk nodes that touch any
        entity in the resulting set are appended, plus their structural edges.
        """
        depth = max(1, min(int(depth), 4))
        limit_nodes = max(1, min(int(limit_nodes), 1000))

        rows = self._run(
            f"""
            MATCH (focal) WHERE elementId(focal) = $eid
            CALL {{
                WITH focal
                MATCH (focal)-[*0..{depth}]-(n:__Entity__)
                RETURN DISTINCT n
                LIMIT $limit
            }}
            WITH collect(DISTINCT n) AS ns
            RETURN [x IN ns | {{
                element_id: elementId(x),
                id: x.id,
                labels: [l IN labels(x) WHERE l <> '__Entity__'],
                description: x.description,
                properties: properties(x)
            }}] AS nodes
            """,
            eid=element_id, limit=limit_nodes,
        )
        node_rows = (rows[0]["nodes"] if rows else []) or []
        ids = [n["element_id"] for n in node_rows]

        rels: list[dict] = []
        sources_by_node: dict[str, list[str]] = {}
        if ids:
            rels = self._run(
                """
                MATCH (a:__Entity__)-[r]->(b:__Entity__)
                WHERE elementId(a) IN $ids AND elementId(b) IN $ids
                RETURN elementId(r) AS element_id,
                       elementId(a) AS source,
                       elementId(b) AS target,
                       type(r) AS type,
                       properties(r) AS properties
                """,
                ids=ids,
            )
            src_rows = self._run(
                """
                MATCH (e:__Entity__)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d:Document)
                WHERE elementId(e) IN $ids
                RETURN elementId(e) AS element_id, collect(DISTINCT d.fileName) AS files
                """,
                ids=ids,
            )
            sources_by_node = {r["element_id"]: r["files"] for r in src_rows}

        nodes_out = []
        for n in node_rows:
            props = {k: v for k, v in (n.get("properties") or {}).items() if k != "embedding"}
            nodes_out.append({
                "element_id": n["element_id"],
                "id": n["id"],
                "labels": n["labels"],
                "description": n.get("description"),
                "properties": props,
                "sources": sources_by_node.get(n["element_id"], []),
            })
        rels_out = [
            {
                "element_id": r["element_id"],
                "source": r["source"],
                "target": r["target"],
                "type": r["type"],
                "properties": {k: v for k, v in (r.get("properties") or {}).items()},
            }
            for r in rels
        ]

        if include_structure and ids:
            struct = self._run(
                """
                MATCH (e:__Entity__) WHERE elementId(e) IN $ids
                OPTIONAL MATCH (e)<-[he:HAS_ENTITY]-(c:Chunk)-[po:PART_OF]->(d:Document)
                WITH collect(DISTINCT c) AS chunks,
                     collect(DISTINCT d) AS docs,
                     collect(DISTINCT he) AS hes,
                     collect(DISTINCT po) AS pos
                OPTIONAL MATCH (c1:Chunk)-[nc:NEXT_CHUNK]->(c2:Chunk)
                  WHERE c1 IN chunks AND c2 IN chunks
                OPTIONAL MATCH (d2:Document)-[fc:FIRST_CHUNK]->(c3:Chunk)
                  WHERE d2 IN docs AND c3 IN chunks
                RETURN chunks, docs, hes, pos,
                       collect(DISTINCT nc) AS ncs,
                       collect(DISTINCT fc) AS fcs
                """,
                ids=ids,
            )
            if struct:
                row = struct[0]
                seen_node_ids = {n["element_id"] for n in nodes_out}
                seen_rel_ids = {r["element_id"] for r in rels_out}

                for d in row["docs"] or []:
                    eid = d.element_id
                    if eid in seen_node_ids:
                        continue
                    props = {k: v for k, v in dict(d).items() if k != "embedding"}
                    nodes_out.append({
                        "element_id": eid,
                        "id": d.get("fileName") or d.get("title") or eid,
                        "labels": ["Document"],
                        "description": d.get("title"),
                        "properties": props,
                        "sources": [d.get("fileName")] if d.get("fileName") else [],
                    })
                    seen_node_ids.add(eid)

                for c in row["chunks"] or []:
                    eid = c.element_id
                    if eid in seen_node_ids:
                        continue
                    full_text = c.get("text") or ""
                    # keep text out of properties (shown separately) but expose
                    # full chunk content as description for the drawer
                    props = {k: v for k, v in dict(c).items() if k not in ("embedding", "text")}
                    nodes_out.append({
                        "element_id": eid,
                        "id": c.get("id") or eid,
                        "labels": ["Chunk"],
                        "description": full_text,
                        "properties": props,
                        "sources": [c.get("fileName")] if c.get("fileName") else [],
                    })
                    seen_node_ids.add(eid)

                for bucket, _ in (("hes", "HAS_ENTITY"), ("pos", "PART_OF"),
                                  ("ncs", "NEXT_CHUNK"), ("fcs", "FIRST_CHUNK")):
                    for r in row[bucket] or []:
                        eid = r.element_id
                        if eid in seen_rel_ids:
                            continue
                        s = r.start_node.element_id
                        t = r.end_node.element_id
                        if s not in seen_node_ids or t not in seen_node_ids:
                            continue
                        rels_out.append({
                            "element_id": eid,
                            "source": s,
                            "target": t,
                            "type": r.type,
                            "properties": dict(r),
                        })
                        seen_rel_ids.add(eid)

        if include_communities:
            self._append_communities(nodes_out, rels_out, ids)

        return {
            "nodes": nodes_out,
            "relationships": rels_out,
            "focal": element_id,
            "depth": depth,
        }

    def document_chunks(self, file_name: str, limit: int = 200) -> list[dict]:
        rows = self._run(
            """
            MATCH (d:Document {fileName: $fileName})<-[:PART_OF]-(c:Chunk)
            RETURN c.id AS id, c.position AS position, c.length AS length, c.text AS text
            ORDER BY c.position
            LIMIT $limit
            """,
            fileName=file_name,
            limit=limit,
        )
        return [dict(r) for r in rows]

    # ---------- internal ----------
    @staticmethod
    def _sanitize_label(s: str) -> str:
        if not s:
            return ""
        out = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in s.strip())
        return out[:1].upper() + out[1:] if out else ""

    @staticmethod
    def _sanitize_rel(s: str) -> str:
        if not s:
            return ""
        out = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in s.strip())
        return out.upper()
