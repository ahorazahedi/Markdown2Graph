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

    def set_document_status(self, file_name: str, status: str, error: str | None = None,
                            chunk_count: int | None = None, entity_count: int | None = None,
                            relationship_count: int | None = None) -> None:
        self._run(
            """
            MATCH (d:Document {fileName: $fileName})
            SET d.status = $status,
                d.error = $error,
                d.chunkCount = coalesce($chunkCount, d.chunkCount),
                d.entityCount = coalesce($entityCount, d.entityCount),
                d.relationshipCount = coalesce($relationshipCount, d.relationshipCount),
                d.processedAt = $now
            """,
            fileName=file_name,
            status=status,
            error=error,
            chunkCount=chunk_count,
            entityCount=entity_count,
            relationshipCount=relationship_count,
            now=datetime.now(timezone.utc).isoformat(),
        )

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
            chunk_id = (gd.source.metadata or {}).get("chunk_id")
            for n in gd.nodes:
                label = self._sanitize_label(n.type) or "Entity"
                nid = str(n.id)
                props = {k: v for k, v in (getattr(n, "properties", {}) or {}).items() if v is not None}
                nodes_by_label[label].append({"id": nid, "props": props})
                seen_node_ids.add(nid)
                if chunk_id:
                    chunk_entity_links.append({"chunk_id": chunk_id, "node_id": nid})
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
    def create_chunk_vector_index(self, dimension: int) -> None:
        self._run(
            f"""
            CREATE VECTOR INDEX chunk_vector IF NOT EXISTS
            FOR (c:Chunk) ON (c.embedding)
            OPTIONS {{
              indexConfig: {{
                `vector.dimensions`: {int(dimension)},
                `vector.similarity_function`: 'cosine'
              }}
            }}
            """
        )

    def create_similar_chunk_relationships(self, top_k: int = 5, min_score: float = 0.8) -> int:
        try:
            res = self._run(
                """
                MATCH (c:Chunk) WHERE c.embedding IS NOT NULL
                CALL db.index.vector.queryNodes('chunk_vector', $k, c.embedding) YIELD node, score
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
            CALL {
              MATCH (d:Document) RETURN count(d) AS docs
            }
            CALL {
              MATCH (c:Chunk) RETURN count(c) AS chunks
            }
            CALL {
              MATCH (e:__Entity__) RETURN count(e) AS entities
            }
            CALL {
              MATCH (a:__Entity__)-[r]->(b:__Entity__) RETURN count(r) AS entity_rels
            }
            CALL {
              MATCH (c:Chunk)-[r:HAS_ENTITY]->() RETURN count(r) AS has_entity_rels
            }
            RETURN docs, chunks, entities, entity_rels, has_entity_rels
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
        rows = self._run(
            """
            MATCH (d:Document)
            RETURN d.fileName AS fileName, d.title AS title, d.status AS status,
                   d.chunkCount AS chunks, d.entityCount AS entities,
                   d.relationshipCount AS rels, d.processedAt AS processedAt,
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
    def explore(self, limit_nodes: int = 200, file_name: str | None = None,
                label: str | None = None) -> dict:
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
        return {"nodes": nodes_out, "relationships": rels_out}

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
