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
        statements = [
            "CREATE CONSTRAINT document_fileName IF NOT EXISTS "
            "FOR (d:Document) REQUIRE d.fileName IS UNIQUE",
            "CREATE CONSTRAINT chunk_id IF NOT EXISTS "
            "FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT entity_id IF NOT EXISTS "
            "FOR (e:__Entity__) REQUIRE (e.id) IS NOT NULL",
        ]
        for q in statements:
            self._run(q)

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

        Each graph_doc has .source.metadata.chunk_id (we set it), .nodes, .relationships.
        Returns (entity_count, relationship_count).
        """
        node_batch: list[dict] = []
        rel_batch: list[dict] = []
        chunk_entity_links: list[dict] = []

        for gd in graph_docs:
            chunk_id = (gd.source.metadata or {}).get("chunk_id")
            for n in gd.nodes:
                node_batch.append(
                    {
                        "id": str(n.id),
                        "type": self._sanitize_label(n.type) or "Entity",
                        "props": {k: v for k, v in (getattr(n, "properties", {}) or {}).items() if v is not None},
                    }
                )
                if chunk_id:
                    chunk_entity_links.append({"chunk_id": chunk_id, "node_id": str(n.id),
                                                "node_type": self._sanitize_label(n.type) or "Entity"})
            for r in gd.relationships:
                rel_batch.append(
                    {
                        "src_id": str(r.source.id),
                        "src_type": self._sanitize_label(r.source.type) or "Entity",
                        "dst_id": str(r.target.id),
                        "dst_type": self._sanitize_label(r.target.type) or "Entity",
                        "rel_type": self._sanitize_rel(r.type) or "RELATED_TO",
                        "props": {k: v for k, v in (getattr(r, "properties", {}) or {}).items() if v is not None},
                    }
                )

        if node_batch:
            self._run(
                """
                UNWIND $batch AS row
                CALL apoc.merge.node(['__Entity__', row.type], {id: row.id}, row.props, row.props) YIELD node
                RETURN count(node)
                """,
                batch=node_batch,
            )
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
        if rel_batch:
            self._run(
                """
                UNWIND $batch AS row
                MATCH (a:__Entity__ {id: row.src_id})
                MATCH (b:__Entity__ {id: row.dst_id})
                CALL apoc.merge.relationship(a, row.rel_type, {}, row.props, b, row.props) YIELD rel
                RETURN count(rel)
                """,
                batch=rel_batch,
            )

        return len({n["id"] for n in node_batch}), len(rel_batch)

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
