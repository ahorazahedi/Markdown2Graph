"""Post-processing pipeline — runs after ingest to make the graph more useful.

Two stages, each independently toggleable:

1. **Cleanup** — LLM-driven label/relationship consolidation. Asks the
   `graph_cleanup_system` prompt to canonicalize observed labels and rel types
   (e.g. {Disease, Diseases, Illness} → Disease). Then rewrites the graph with
   plain Cypher.

2. **Communities** — Cypher-only weakly-connected-components grouping. We
   intentionally avoid the GDS Leiden algorithm used by neo4j-labs/llm-graph-
   builder because GDS isn't available on Neo4j Community Edition. Instead we
   compute WCC over the entity graph by repeated MERGE, then materialize each
   component as a `__Community__` node linked via IN_COMMUNITY. A single level
   is produced (level=0) — no hierarchical roll-up.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from ..llm.client import build_chat_llm
from ..llm.recorder import with_tag
from ..repositories.graph_repository import GraphRepository
from .job_registry import JobCancelled
from .prompt_store import PromptStore

log = logging.getLogger(__name__)

# labels we never feed to the cleanup LLM — structural / system labels
_SYSTEM_LABELS = {"Chunk", "Document", "__Entity__", "__Community__"}
# relationship types we never want to rewrite
_SYSTEM_RELS = {"HAS_ENTITY", "PART_OF", "FIRST_CHUNK", "NEXT_CHUNK",
                "SIMILAR", "IN_COMMUNITY", "PARENT_COMMUNITY"}

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class PostProcessingReport:
    cleanup: dict | None = None
    dedup: dict | None = None
    orphans: dict | None = None
    communities: dict | None = None
    chunk_embeddings: dict | None = None
    entity_embeddings: dict | None = None
    community_embeddings: dict | None = None
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


class PostProcessingService:
    """Stateless facade — every call uses fresh dependencies so it is safe to
    invoke directly from a request handler or a background job."""

    def __init__(self,
                 repo: GraphRepository | None = None,
                 prompts: PromptStore | None = None):
        self.repo = repo or GraphRepository()
        self.prompts = prompts or PromptStore()

    # ---------------- public API ----------------

    def run(self,
            *, cleanup: bool = True,
            dedup: bool = False,
            orphans: bool = False,
            communities: bool = True,
            summaries: bool = True,
            chunk_embeddings: bool = True,
            entity_embeddings: bool = True,
            community_embeddings: bool = True,
            community_levels: int = 2,
            emit: Callable[[str, float, dict | None], None] | None = None,
            is_cancelled: Callable[[], bool] | None = None,
            ) -> PostProcessingReport:
        rep = PostProcessingReport()
        t0 = time.time()
        notify = emit or (lambda *_args, **_kw: None)
        cancelled = is_cancelled or (lambda: False)

        def _check_cancel():
            if cancelled():
                raise JobCancelled("post-processing cancelled")

        _check_cancel()
        if cleanup:
            try:
                notify("post: cleanup starting", 0.05, None)
                rep.cleanup = self.run_cleanup()
                notify(f"post: cleanup done — {rep.cleanup}", 0.20, None)
            except Exception as e:
                log.exception("cleanup failed")
                rep.errors.append(f"cleanup: {e}")

        _check_cancel()
        if dedup:
            try:
                notify("post: dedup starting", 0.22, None)
                rep.dedup = self.run_dedup()
                notify(f"post: dedup done — {rep.dedup}", 0.38, None)
            except Exception as e:
                log.exception("dedup failed")
                rep.errors.append(f"dedup: {e}")

        _check_cancel()
        if orphans:
            try:
                notify("post: orphan sweep starting", 0.42, None)
                rep.orphans = self.run_orphan_sweep()
                notify(f"post: orphan sweep done — {rep.orphans}", 0.50, None)
            except Exception as e:
                log.exception("orphan sweep failed")
                rep.errors.append(f"orphans: {e}")

        _check_cancel()
        if communities:
            try:
                notify("post: communities starting", 0.55, None)
                rep.communities = self.run_communities(
                    levels=max(1, int(community_levels)),
                    is_cancelled=cancelled,
                )
                notify(f"post: communities done — {rep.communities}", 0.75, None)
            except Exception as e:
                log.exception("communities failed")
                rep.errors.append(f"communities: {e}")

        _check_cancel()
        if summaries and communities:
            try:
                notify("post: community summaries starting", 0.80, None)
                rep.communities = {
                    **(rep.communities or {}),
                    "summaries": self.summarize_communities(is_cancelled=cancelled),
                }
                notify("post: community summaries done", 0.90, None)
            except Exception as e:
                log.exception("community summaries failed")
                rep.errors.append(f"summaries: {e}")

        _check_cancel()
        if chunk_embeddings:
            try:
                notify("post: chunk embedding backfill starting", 0.91, None)
                rep.chunk_embeddings = self.embed_chunks(is_cancelled=cancelled)
                notify(f"post: chunk embeddings done — {rep.chunk_embeddings}", 0.92, None)
            except Exception as e:
                log.exception("chunk embeddings failed")
                rep.errors.append(f"chunk_embeddings: {e}")

        _check_cancel()
        if entity_embeddings:
            try:
                notify("post: entity embeddings starting", 0.92, None)
                rep.entity_embeddings = self.embed_entities(is_cancelled=cancelled)
                notify(f"post: entity embeddings done — {rep.entity_embeddings}", 0.96, None)
            except Exception as e:
                log.exception("entity embeddings failed")
                rep.errors.append(f"entity_embeddings: {e}")

        _check_cancel()
        if community_embeddings:
            try:
                notify("post: community embeddings starting", 0.97, None)
                rep.community_embeddings = self.embed_communities(is_cancelled=cancelled)
                notify(f"post: community embeddings done — {rep.community_embeddings}", 0.99, None)
            except Exception as e:
                log.exception("community embeddings failed")
                rep.errors.append(f"community_embeddings: {e}")

        rep.elapsed_seconds = round(time.time() - t0, 2)
        return rep

    # ---------------- embeddings ----------------

    def embed_chunks(self, *, is_cancelled: Callable[[], bool] | None = None) -> dict:
        """Backfill `Chunk.embedding` for chunks created without one.

        Repairs ingests where embedding failed (e.g. provider outage,
        rate-limit). Also (re)creates the `vector` index. Safe to re-run.
        """
        from ..config import get_settings
        from ..llm import build_embedder

        settings = get_settings()
        embedder, dim = build_embedder(settings)
        batch_n = max(8, int(settings.entity_embedding_batch))

        pending = self.repo.list_chunks_needing_embedding(limit=20_000)
        if not pending:
            self.repo.create_chunk_vector_index(dim)
            return {"embedded": 0, "skipped": "no chunks pending"}
        total = 0
        for i in range(0, len(pending), batch_n):
            if is_cancelled and is_cancelled():
                raise JobCancelled("chunk embedding cancelled")
            chunk = pending[i:i + batch_n]
            texts = [(row.get("text") or "")[:8000] for row in chunk]
            try:
                vectors = embedder.embed_documents(texts)
            except Exception as e:
                log.warning("chunk batch embed failed (%d-%d): %s",
                            i, i + len(chunk), e)
                continue
            self.repo.write_chunk_embeddings(
                [{"id": row["cid"], "embedding": vec}
                 for row, vec in zip(chunk, vectors)],
                model=settings.embedding_model,
                dim=dim,
            )
            total += len(chunk)
        try:
            self.repo.create_chunk_vector_index(dim)
        except Exception as e:
            log.warning("chunk vector index create failed: %s", e)
        return {"embedded": total, "dim": dim}

    def embed_entities(self, *, is_cancelled: Callable[[], bool] | None = None) -> dict:
        """Compute `__Entity__.embedding` for nodes that don't have one.

        Text fed to embedder = `id + " — " + description` (mirrors
        llm-graph-builder). Batch size from settings. Creates
        `entity_vector` index after first batch lands so query-time can
        immediately use it.
        """
        from ..config import get_settings
        from ..llm import build_embedder

        settings = get_settings()
        embedder, dim = build_embedder(settings)
        batch_n = max(8, int(settings.entity_embedding_batch))

        pending = self.repo.list_entities_needing_embedding(limit=10_000)
        if not pending:
            self.repo.create_entity_vector_index(dim)
            return {"embedded": 0, "skipped": "no entities pending"}
        total = 0
        for i in range(0, len(pending), batch_n):
            if is_cancelled and is_cancelled():
                raise JobCancelled("entity embedding cancelled")
            chunk = pending[i:i + batch_n]
            texts = [
                ((row.get("id") or "") + " — " + (row.get("description") or ""))[:2000]
                for row in chunk
            ]
            try:
                vectors = embedder.embed_documents(texts)
            except Exception as e:
                log.warning("entity batch embed failed (%d-%d): %s", i, i + len(chunk), e)
                continue
            self.repo.write_entity_embeddings(
                [{"eid": row["eid"], "embedding": vec}
                 for row, vec in zip(chunk, vectors)],
                model=settings.embedding_model,
                dim=dim,
            )
            total += len(chunk)
        try:
            self.repo.create_entity_vector_index(dim)
        except Exception as e:
            log.warning("entity_vector index create failed: %s", e)
        return {"embedded": total, "dim": dim}

    def embed_communities(self, *, is_cancelled: Callable[[], bool] | None = None) -> dict:
        """Same as `embed_entities` but for `__Community__.summary`."""
        from ..config import get_settings
        from ..llm import build_embedder

        settings = get_settings()
        embedder, dim = build_embedder(settings)
        batch_n = max(8, int(settings.entity_embedding_batch))

        pending = self.repo.list_communities_needing_embedding(limit=10_000)
        if not pending:
            self.repo.create_community_vector_index(dim)
            return {"embedded": 0, "skipped": "no community summaries pending"}
        total = 0
        for i in range(0, len(pending), batch_n):
            if is_cancelled and is_cancelled():
                raise JobCancelled("community embedding cancelled")
            chunk = pending[i:i + batch_n]
            texts = [
                ((row.get("title") or "") + " — " + (row.get("summary") or ""))[:4000]
                for row in chunk
            ]
            try:
                vectors = embedder.embed_documents(texts)
            except Exception as e:
                log.warning("community batch embed failed (%d-%d): %s", i, i + len(chunk), e)
                continue
            self.repo.write_community_embeddings(
                [{"eid": row["eid"], "embedding": vec}
                 for row, vec in zip(chunk, vectors)],
                model=settings.embedding_model,
                dim=dim,
            )
            total += len(chunk)
        try:
            self.repo.create_community_vector_index(dim)
        except Exception as e:
            log.warning("community_vector index create failed: %s", e)
        return {"embedded": total, "dim": dim}

    # ---------------- dedup + orphans ----------------

    def run_dedup(self, *, min_group_size: int = 2,
                  limit_groups: int = 200) -> dict:
        """Auto-merge entities sharing the same normalized id.

        Within each group, the canonical is the member with the most
        incoming HAS_ENTITY edges (richest provenance), tie-broken by
        lexicographic id. Use the API endpoints if you want a human in
        the loop for ambiguous groups."""
        groups = self.repo.list_duplicate_entities(
            limit_groups=limit_groups, min_group_size=min_group_size,
        )
        merged_groups = 0
        merged_aliases = 0
        moved_rels = 0
        for g in groups:
            members = g["members"]
            if len(members) < 2:
                continue
            members_sorted = sorted(
                members,
                key=lambda m: (-int(m.get("chunk_count") or 0), str(m.get("id") or "")),
            )
            canon = members_sorted[0]["element_id"]
            aliases = [m["element_id"] for m in members_sorted[1:]]
            try:
                res = self.repo.merge_entities(canon, aliases)
                merged_groups += 1
                merged_aliases += int(res.get("merged", 0))
                moved_rels += int(res.get("moved_relationships", 0))
            except Exception as e:
                log.warning("merge group %s failed: %s", g.get("key"), e)
        return {
            "groups_examined": len(groups),
            "groups_merged": merged_groups,
            "aliases_merged": merged_aliases,
            "relationships_moved": moved_rels,
        }

    def run_orphan_sweep(self) -> dict:
        """Delete every __Entity__ that no Chunk points at."""
        before = self.repo.list_orphan_entities(limit=10_000)
        deleted = self.repo.delete_orphan_entities()
        return {"orphans_found": len(before), "deleted": int(deleted)}

    # ---------------- cleanup ----------------

    def run_cleanup(self) -> dict:
        """Call the LLM with current labels + rel types, apply the mapping."""
        schema = self.repo.schema()
        labels = [l for l in schema["labels"] if l not in _SYSTEM_LABELS]
        rels = [r for r in schema["relationship_types"] if r not in _SYSTEM_RELS]
        if not labels and not rels:
            return {"node_renames": 0, "rel_renames": 0, "skipped": "empty schema"}

        system = self.prompts.render("graph_cleanup_system")
        user = json.dumps({"nodes": labels, "relationships": rels}, indent=2)

        with with_tag("graph_cleanup"):
            llm = build_chat_llm(tag="graph_cleanup")
            resp = llm.invoke([
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ])
        text = getattr(resp, "content", None) or str(resp)
        mapping = self._parse_json(text)
        node_map = self._invert_mapping(mapping.get("nodes") or {}, allowed=set(labels))
        rel_map  = self._invert_mapping(mapping.get("relationships") or {}, allowed=set(rels))

        # apply: rename labels (each old → new)
        n_changed = 0
        for old, new in node_map.items():
            if old == new:
                continue
            self.repo._run(
                f"MATCH (n:`{old}`) SET n:`{new}` REMOVE n:`{old}`"
            )
            n_changed += 1

        r_changed = 0
        for old, new in rel_map.items():
            if old == new:
                continue
            self.repo._run(
                f"""
                MATCH (a)-[r:`{old}`]->(b)
                CREATE (a)-[r2:`{new}`]->(b)
                SET r2 = properties(r)
                DELETE r
                """
            )
            r_changed += 1

        return {
            "node_renames": n_changed,
            "rel_renames": r_changed,
            "node_map": node_map,
            "rel_map": rel_map,
        }

    # ---------------- communities (no GDS) ----------------

    def run_communities(self, *, levels: int = 2,
                        is_cancelled: Callable[[], bool] | None = None) -> dict:
        """Build a hierarchical community structure over the entity subgraph.

        Strategy:
        - If ``networkx`` is importable, run Louvain at descending resolutions
          for ``levels`` levels. Lower resolution = larger communities, so
          level 0 = finest, level N-1 = coarsest. Members of each level-L
          community link UP to its level-(L+1) parent via ``PARENT_COMMUNITY``
          using majority-vote membership.
        - Otherwise, fall back to the legacy single-level Cypher WCC.

        APOC / GDS are not required for either path — this keeps the feature
        working on Neo4j Community Edition.
        """
        try:
            import networkx as nx  # type: ignore
            from networkx.algorithms.community import louvain_communities  # type: ignore
        except Exception:
            log.warning("networkx not available — falling back to single-level WCC")
            return self._run_communities_wcc()

        # ---- pull the entity-entity edge list ----
        edge_rows = self.repo._run(
            """
            MATCH (a:__Entity__)-[r]-(b:__Entity__)
            WHERE elementId(a) < elementId(b)
            RETURN elementId(a) AS a, elementId(b) AS b, count(r) AS w
            """
        )
        node_rows = self.repo._run(
            "MATCH (e:__Entity__) RETURN elementId(e) AS eid, e.id AS id"
        )
        if not node_rows:
            return {"communities": 0, "members": 0, "levels": 0}

        g = nx.Graph()
        id_by_eid: dict[str, str] = {}
        for n in node_rows:
            g.add_node(n["eid"])
            id_by_eid[n["eid"]] = n["id"]
        for r in edge_rows:
            g.add_edge(r["a"], r["b"], weight=int(r["w"]))

        # resolutions: descending so coarser = higher level
        levels = max(1, min(int(levels), 4))
        resolutions = [4.0, 1.0, 0.4, 0.15][:levels]

        # partition_by_level[L] = {eid: comm_index}
        partition_by_level: list[dict[str, int]] = []
        community_members_by_level: list[list[list[str]]] = []
        for L, res in enumerate(resolutions):
            try:
                parts = louvain_communities(g, resolution=res, seed=42)
            except Exception as e:
                log.warning("louvain level %d failed: %s — stopping hierarchy", L, e)
                break
            members: list[list[str]] = [list(p) for p in parts]
            eid_to_idx = {}
            for idx, mem in enumerate(members):
                for eid in mem:
                    eid_to_idx[eid] = idx
            partition_by_level.append(eid_to_idx)
            community_members_by_level.append(members)

        if not partition_by_level:
            return self._run_communities_wcc()

        if is_cancelled and is_cancelled():
            raise JobCancelled("communities cancelled before wipe")

        # ---- preserve metadata across rebuilds, keyed by member-set hash ----
        # Stops re-running from burning LLM/embedding tokens on the same group.
        preserved = self._snapshot_community_metadata()

        # ---- wipe old community structure ----
        self.repo._run("MATCH (c:__Community__) DETACH DELETE c")
        try:
            self.repo._run(
                "CREATE CONSTRAINT IF NOT EXISTS FOR (c:__Community__) REQUIRE c.id IS UNIQUE"
            )
        except Exception:
            pass

        # ---- materialize each level ----
        community_node_id_by_level: list[list[str]] = []
        restored_total = 0
        for L, members in enumerate(community_members_by_level):
            cids = []
            batch = []
            restore_rows = []
            for idx, mem in enumerate(members):
                cid = f"comm-L{L}-{idx}"
                cids.append(cid)
                title_seed = id_by_eid.get(mem[0]) if mem else None
                # member-set hash: stable digest of sorted entity ids
                member_ids = sorted(id_by_eid.get(e) for e in mem if id_by_eid.get(e))
                mhash = hashlib.sha256(
                    ("|".join(member_ids)).encode("utf-8")
                ).hexdigest() if member_ids else None
                row = {
                    "id": cid, "level": L, "size": len(mem),
                    "title_seed": title_seed, "member_hash": mhash,
                }
                batch.append(row)
                prev = preserved.get((L, mhash)) if mhash else None
                if prev:
                    restore_rows.append({
                        "id": cid,
                        "title": prev.get("title"),
                        "summary": prev.get("summary"),
                        "embedding": prev.get("embedding"),
                    })
            community_node_id_by_level.append(cids)
            if batch:
                self.repo._run(
                    """
                    UNWIND $batch AS row
                    MERGE (c:__Community__ {id: row.id})
                      ON CREATE SET c.created_at = timestamp()
                    SET c.level = row.level,
                        c.size = row.size,
                        c.member_hash = row.member_hash,
                        c.title = coalesce(c.title, row.title_seed)
                    """,
                    batch=batch,
                )
            if restore_rows:
                # restore title/summary
                self.repo._run(
                    """
                    UNWIND $rows AS row
                    MATCH (c:__Community__ {id: row.id})
                    SET c.title   = coalesce(row.title, c.title),
                        c.summary = coalesce(row.summary, c.summary)
                    """,
                    rows=[{"id": r["id"], "title": r["title"], "summary": r["summary"]}
                          for r in restore_rows],
                )
                # restore embedding via setNodeVectorProperty (vector type)
                for r in restore_rows:
                    if r.get("embedding"):
                        try:
                            self.repo._run(
                                """
                                MATCH (c:__Community__ {id: $id})
                                CALL db.create.setNodeVectorProperty(c, 'embedding', $vec)
                                """,
                                id=r["id"], vec=r["embedding"],
                            )
                        except Exception as e:
                            log.warning("restore community embedding failed for %s: %s",
                                        r["id"], e)
                restored_total += len(restore_rows)

        # ---- link entities to level-0 communities ----
        links = []
        for eid, idx in partition_by_level[0].items():
            links.append({"eid": eid, "cid": f"comm-L0-{idx}"})
        if links:
            self.repo._run(
                """
                UNWIND $rows AS row
                MATCH (e:__Entity__) WHERE elementId(e) = row.eid
                MATCH (c:__Community__ {id: row.cid})
                MERGE (e)-[:IN_COMMUNITY]->(c)
                """,
                rows=links,
            )

        # ---- link each level L community to its parent at level L+1 via
        # majority vote of its members. ----
        parent_links_total = 0
        for L in range(0, len(partition_by_level) - 1):
            child_members = community_members_by_level[L]
            parent_map = partition_by_level[L + 1]
            parent_links = []
            for idx, mem in enumerate(child_members):
                if not mem:
                    continue
                # majority vote
                from collections import Counter
                votes = Counter(parent_map.get(eid, -1) for eid in mem)
                top, _count = votes.most_common(1)[0]
                if top < 0:
                    continue
                parent_links.append({
                    "child": f"comm-L{L}-{idx}",
                    "parent": f"comm-L{L + 1}-{top}",
                })
            if parent_links:
                self.repo._run(
                    """
                    UNWIND $rows AS row
                    MATCH (cc:__Community__ {id: row.child})
                    MATCH (pc:__Community__ {id: row.parent})
                    MERGE (cc)-[:PARENT_COMMUNITY]->(pc)
                    """,
                    rows=parent_links,
                )
                parent_links_total += len(parent_links)

        # community ranks: # distinct docs reaching the community (level 0 only)
        try:
            self.repo._run(
                """
                MATCH (c:__Community__ {level: 0})<-[:IN_COMMUNITY]-(:__Entity__)
                      <-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d:Document)
                WITH c, count(DISTINCT d) AS rank
                SET c.community_rank = rank
                """
            )
        except Exception as e:
            log.warning("community rank failed: %s", e)

        # propagate rank up the hierarchy as sum of children
        for L in range(1, len(partition_by_level)):
            try:
                self.repo._run(
                    """
                    MATCH (parent:__Community__ {level: $L})
                          <-[:PARENT_COMMUNITY]-(child:__Community__)
                    WITH parent, sum(coalesce(child.community_rank, 0)) AS r
                    SET parent.community_rank = r
                    """,
                    L=int(L),
                )
            except Exception:
                pass

        # community weight = distinct chunks reaching the community.
        # Reference (`communities.py:CREATE_COMMUNITY_WEIGHTS`) uses this for
        # chat-retriever ranking. Level 0 derives directly; higher levels sum
        # over PARENT_COMMUNITY children.
        try:
            self.repo._run(
                """
                MATCH (c:__Community__ {level: 0})<-[:IN_COMMUNITY]-(:__Entity__)
                      <-[:HAS_ENTITY]-(ch:Chunk)
                WITH c, count(DISTINCT ch) AS w
                SET c.weight = w
                """
            )
        except Exception as e:
            log.warning("community weight (L0) failed: %s", e)
        for L in range(1, len(partition_by_level)):
            try:
                self.repo._run(
                    """
                    MATCH (parent:__Community__ {level: $L})
                          <-[:PARENT_COMMUNITY]-(child:__Community__)
                    WITH parent, sum(coalesce(child.weight, 0)) AS w
                    SET parent.weight = w
                    """,
                    L=int(L),
                )
            except Exception:
                pass

        # best-effort fulltext index on (title, summary)
        try:
            self.repo._run(
                "CREATE FULLTEXT INDEX community_keyword IF NOT EXISTS "
                "FOR (n:__Community__) ON EACH [n.summary, n.title]"
            )
        except Exception as e:
            log.warning("community index skipped: %s", e)

        # per-Document community counters (matches reference Document props).
        # communityNodeCount = distinct __Community__ this doc's entities reach.
        # communityRelCount  = distinct IN_COMMUNITY edges originating from
        # this doc's entities (NOT the same as node count — an entity can
        # only have one IN_COMMUNITY at L0 but a doc with many entities will
        # have many edges).
        try:
            self.repo._run(
                """
                MATCH (d:Document)
                OPTIONAL MATCH (d)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]
                              ->(:__Entity__)-[ic:IN_COMMUNITY]->(c:__Community__)
                WITH d, count(DISTINCT c) AS cn, count(DISTINCT ic) AS cr
                SET d.communityNodeCount = cn,
                    d.communityRelCount  = cr
                """
            )
        except Exception as e:
            log.warning("per-doc community rollup failed: %s", e)

        return {
            "communities": sum(len(m) for m in community_members_by_level),
            "members": int(g.number_of_nodes()),
            "edges": int(g.number_of_edges()),
            "levels": len(partition_by_level),
            "per_level": [len(m) for m in community_members_by_level],
            "parent_links": parent_links_total,
            "restored": restored_total,
            "engine": "louvain",
        }

    def _snapshot_community_metadata(self) -> dict[tuple[int, str], dict]:
        """Snapshot {title, summary, embedding} of existing communities, keyed
        by (level, member-set hash). Used to restore LLM-derived metadata
        across a rebuild so re-running post-process is idempotent in practice
        (no wasted LLM/embedding calls when membership is unchanged)."""
        try:
            rows = self.repo._run(
                """
                MATCH (c:__Community__)
                OPTIONAL MATCH (c)<-[:IN_COMMUNITY]-(e:__Entity__)
                WITH c, collect(DISTINCT e.id) AS member_ids
                RETURN coalesce(c.level, 0) AS level,
                       c.title AS title, c.summary AS summary,
                       c.embedding AS embedding,
                       c.member_hash AS stored_hash,
                       member_ids
                """
            )
        except Exception as e:
            log.warning("community metadata snapshot failed: %s", e)
            return {}
        out: dict[tuple[int, str], dict] = {}
        for r in rows:
            if not (r.get("title") or r.get("summary") or r.get("embedding")):
                continue
            ids = sorted(x for x in (r.get("member_ids") or []) if x)
            if not ids:
                continue
            mhash = hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()
            # prefer the freshly-computed hash; fall back to stored_hash so we
            # still match if a prior run stored one and membership is identical
            out[(int(r["level"]), mhash)] = {
                "title": r.get("title"),
                "summary": r.get("summary"),
                "embedding": r.get("embedding"),
            }
            stored = r.get("stored_hash")
            if stored and stored != mhash:
                out[(int(r["level"]), stored)] = out[(int(r["level"]), mhash)]
        return out

    # ---- legacy single-level WCC fallback ----
    def _run_communities_wcc(self) -> dict:
        self.repo._run(
            "MATCH (e:__Entity__) SET e.component_id = coalesce(e.component_id, elementId(e))"
        )
        for it in range(40):  # hard cap — graph diameter is tiny in practice
            res = self.repo._run(
                """
                MATCH (a:__Entity__)-[]-(b:__Entity__)
                WHERE a.component_id > b.component_id
                WITH a, min(b.component_id) AS m
                WHERE a.component_id <> m
                SET a.component_id = m
                RETURN count(*) AS changed
                """
            )
            changed = int(res[0]["changed"]) if res else 0
            if changed == 0:
                break

        # purge old community structure
        self.repo._run("MATCH (c:__Community__) DETACH DELETE c")
        # ensure constraint once (idempotent)
        try:
            self.repo._run(
                "CREATE CONSTRAINT IF NOT EXISTS FOR (c:__Community__) REQUIRE c.id IS UNIQUE"
            )
        except Exception:
            pass

        rows = self.repo._run(
            """
            MATCH (e:__Entity__) WHERE e.component_id IS NOT NULL
            WITH e.component_id AS cid, collect(e) AS members
            UNWIND members AS m
            MERGE (c:__Community__ {id: cid})
              ON CREATE SET c.level = 0,
                            c.size  = size(members),
                            c.created_at = timestamp()
            MERGE (m)-[:IN_COMMUNITY]->(c)
            RETURN count(DISTINCT c) AS communities, count(m) AS members
            """
        )
        out = dict(rows[0]) if rows else {"communities": 0, "members": 0}

        # add a stable digest so the same component_id collapses on rerun
        self.repo._run(
            """
            MATCH (c:__Community__)<-[:IN_COMMUNITY]-(e:__Entity__)
            WITH c, collect(DISTINCT e.id)[0..5] AS sample, count(e) AS n
            SET c.size = n,
                c.title = coalesce(c.title, sample[0])
            """
        )

        # drop the working property so reruns are clean
        self.repo._run("MATCH (e:__Entity__) REMOVE e.component_id")

        # best-effort indexes (Community Edition supports both)
        for q in (
            "CREATE FULLTEXT INDEX community_keyword IF NOT EXISTS "
            "FOR (n:__Community__) ON EACH [n.summary, n.title]",
        ):
            try:
                self.repo._run(q)
            except Exception as e:
                log.warning("community index skipped: %s", e)

        # community ranks: # of distinct docs reaching the community
        try:
            self.repo._run(
                """
                MATCH (c:__Community__)<-[:IN_COMMUNITY]-(:__Entity__)
                      <-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d:Document)
                WITH c, count(DISTINCT d) AS rank
                SET c.community_rank = rank
                """
            )
        except Exception as e:
            log.warning("community rank failed: %s", e)

        return out

    def summarize_communities(self,
                              *, max_communities: int = 200,
                              min_size: int = 2,
                              is_cancelled: Callable[[], bool] | None = None) -> dict:
        """LLM-write title + summary for each community with at least
        `min_size` members. Stored as c.summary, c.title. Idempotent for
        communities that already have a non-empty summary."""
        rows = self.repo._run(
            """
            MATCH (c:__Community__)<-[:IN_COMMUNITY]-(e:__Entity__)
            WITH c, collect(e) AS members
            WHERE size(members) >= $min_size
              AND (c.summary IS NULL OR c.summary = '')
            RETURN c.id AS id, members
            ORDER BY size(members) DESC
            LIMIT $limit
            """,
            min_size=min_size, limit=max_communities,
        )
        if not rows:
            return {"summarized": 0, "skipped": "no communities need a summary"}

        system = self.prompts.render("community_summary_system")
        llm = build_chat_llm(tag="community_summary")
        done = 0
        for row in rows:
            if is_cancelled and is_cancelled():
                raise JobCancelled("community summaries cancelled")
            cid = row["id"]
            member_ids = [m.element_id for m in row["members"]]
            sub = self.repo._run(
                """
                MATCH (a:__Entity__)-[r]->(b:__Entity__)
                WHERE elementId(a) IN $ids AND elementId(b) IN $ids
                RETURN collect(DISTINCT {id: a.id, type: [l IN labels(a) WHERE l <> '__Entity__'][0],
                                        description: a.description}) AS nodes,
                       collect(DISTINCT {start: a.id, type: type(r), end: b.id}) AS rels
                """,
                ids=member_ids,
            )
            payload = sub[0] if sub else {"nodes": [], "rels": []}
            user = json.dumps({
                "nodes": [n for n in payload["nodes"] if n.get("id")][:60],
                "relationships": payload["rels"][:120],
            }, default=str)[:6000]
            try:
                with with_tag("community_summary"):
                    resp = llm.invoke([
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ])
                text = getattr(resp, "content", None) or str(resp)
                obj = self._parse_json(text)
                title = (obj.get("title") or "").strip()[:120]
                summary = (obj.get("summary") or "").strip()[:1200]
                if not summary:
                    continue
                self.repo._run(
                    "MATCH (c:__Community__ {id: $id}) "
                    "SET c.title = coalesce($title, c.title), c.summary = $summary",
                    id=cid, title=title or None, summary=summary,
                )
                done += 1
            except Exception as e:
                log.warning("community %s summary failed: %s", cid, e)
        return {"summarized": done, "considered": len(rows)}

    # ---------------- helpers ----------------

    @staticmethod
    def _parse_json(text: str) -> dict:
        m = _JSON_FENCE.search(text)
        if m:
            text = m.group(1)
        # take first balanced {...}
        start = text.find("{")
        if start < 0:
            return {}
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        return {}
        return {}

    @staticmethod
    def _invert_mapping(canon_to_aliases: dict, *, allowed: set[str]) -> dict[str, str]:
        """Turn {Canonical: [alias, alias]} into {alias: Canonical}. Skip any
        canonical or alias not in `allowed` so a hallucinating LLM can't damage
        the graph."""
        out: dict[str, str] = {}
        for canonical, aliases in canon_to_aliases.items():
            if not isinstance(aliases, list) or canonical not in allowed:
                continue
            for a in aliases:
                if a in allowed:
                    out[a] = canonical
        return out
