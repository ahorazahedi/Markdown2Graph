"""RAG chat over the extracted Neo4j knowledge graph.

Modes (mirrors llm-graph-builder):
    vector                 — pure semantic top-k on Chunk
    fulltext               — hybrid (vector + bm25) on Chunk, no graph hop
    graph_vector           — vector on Chunk + graph-hop entities
    graph_vector_fulltext  — hybrid on Chunk + graph-hop entities  (default)
    entity_vector          — semantic on __Entity__ + local context
    global_vector          — semantic + bm25 on __Community__ summaries
    graph                  — text→Cypher via GraphCypherQAChain

Critical embedding parity: same embedder used for ingest must be used at
retrieval time. `health()` checks vector-index dim against settings.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from langchain.retrievers import ContextualCompressionRetriever
from langchain.retrievers.document_compressors import (
    DocumentCompressorPipeline,
    EmbeddingsFilter,
)
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_text_splitters import TokenTextSplitter

try:
    from langchain_neo4j import Neo4jVector, Neo4jGraph
except Exception:  # pragma: no cover
    Neo4jVector = None  # type: ignore[assignment]
    Neo4jGraph = None  # type: ignore[assignment]

from ..config import get_settings
from ..extensions import neo4j_manager
from ..llm.client import build_chat_llm, build_embedder
from ..llm.recorder import with_tag
from .prompt_store import PromptStore

log = logging.getLogger(__name__)


# ----------------- per-mode retrieval Cypher -----------------

_CHUNK_GRAPH_RETRIEVAL = """
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
"""

_CHUNK_ONLY_RETRIEVAL = """
WITH node AS chunk, score
OPTIONAL MATCH (chunk)-[:PART_OF]->(d:Document)
RETURN chunk.text AS text, score,
       { chunkId: chunk.id,
         fileName: coalesce(d.fileName, chunk.fileName),
         position: chunk.position,
         entities: [], relationships: [] } AS metadata
"""

_ENTITY_RETRIEVAL = """
WITH node AS e, score
OPTIONAL MATCH (e)<-[:HAS_ENTITY]-(c:Chunk)-[:PART_OF]->(d:Document)
WITH e, score, collect(DISTINCT {chunkId: c.id, fileName: d.fileName,
                                  text: c.text})[0..5] AS chunks
OPTIONAL MATCH (e)-[r]-(n:__Entity__)
WITH e, score, chunks,
     collect(DISTINCT {startId: e.id, endId: n.id, type: type(r),
                       elementId: elementId(r)})[0..20] AS rels,
     collect(DISTINCT {id: n.id, elementId: elementId(n),
                       labels: [l IN labels(n) WHERE l <> '__Entity__'],
                       description: n.description})[0..20] AS neighbours
OPTIONAL MATCH (e)-[:IN_COMMUNITY]->(co:__Community__)
RETURN
  ('Entity: ' + coalesce(e.id, '') +
   CASE WHEN e.description IS NULL THEN '' ELSE '\nDescription: ' + e.description END +
   CASE WHEN size(chunks) = 0 THEN '' ELSE
     '\n\nSupporting chunks:\n' +
     reduce(s = '', c IN chunks | s + '- [' + coalesce(c.fileName, '?') + '] ' +
            substring(coalesce(c.text, ''), 0, 400) + '\n')
   END) AS text,
  score,
  { entityId: elementId(e), id: e.id,
    fileName: head([c IN chunks | c.fileName]),
    entities: [{id: e.id, elementId: elementId(e),
                labels: [l IN labels(e) WHERE l <> '__Entity__'],
                description: e.description}] + neighbours,
    relationships: rels,
    community: { id: co.id, title: co.title, summary: co.summary } } AS metadata
"""

_COMMUNITY_RETRIEVAL = """
WITH node AS c, score
RETURN
  ('Community: ' + coalesce(c.title, c.id, '') +
   CASE WHEN c.summary IS NULL THEN '' ELSE '\n\n' + c.summary END) AS text,
  score,
  { communityId: elementId(c), id: c.id, title: c.title,
    level: c.level, weight: c.weight,
    entities: [], relationships: [] } AS metadata
"""


# mode → retrieval config
_MODES: dict[str, dict] = {
    "vector": {
        "index_name": "vector",
        "keyword_index_name": None,
        "search_type": "vector",
        "node_label": "Chunk",
        "embedding_node_property": "embedding",
        "text_node_properties": ["text"],
        "retrieval_query": _CHUNK_ONLY_RETRIEVAL,
        "document_filter": True,
    },
    "fulltext": {
        "index_name": "vector",
        "keyword_index_name": "keyword",
        "search_type": "hybrid",
        "node_label": "Chunk",
        "embedding_node_property": "embedding",
        "text_node_properties": ["text"],
        "retrieval_query": _CHUNK_ONLY_RETRIEVAL,
        "document_filter": False,
    },
    "graph_vector": {
        "index_name": "vector",
        "keyword_index_name": None,
        "search_type": "vector",
        "node_label": "Chunk",
        "embedding_node_property": "embedding",
        "text_node_properties": ["text"],
        "retrieval_query": _CHUNK_GRAPH_RETRIEVAL,
        "document_filter": True,
    },
    "graph_vector_fulltext": {
        "index_name": "vector",
        "keyword_index_name": "keyword",
        "search_type": "hybrid",
        "node_label": "Chunk",
        "embedding_node_property": "embedding",
        "text_node_properties": ["text"],
        "retrieval_query": _CHUNK_GRAPH_RETRIEVAL,
        "document_filter": False,
    },
    "entity_vector": {
        "index_name": "entity_vector",
        "keyword_index_name": None,
        "search_type": "vector",
        "node_label": "__Entity__",
        "embedding_node_property": "embedding",
        "text_node_properties": ["id", "description"],
        "retrieval_query": _ENTITY_RETRIEVAL,
        "document_filter": False,
    },
    "global_vector": {
        "index_name": "community_vector",
        "keyword_index_name": "community_keyword",
        "search_type": "hybrid",
        "node_label": "__Community__",
        "embedding_node_property": "embedding",
        "text_node_properties": ["summary", "title"],
        "retrieval_query": _COMMUNITY_RETRIEVAL,
        "document_filter": False,
    },
}

DEFAULT_MODE = "graph_vector_fulltext"
SUPPORTED_MODES = list(_MODES.keys()) + ["graph"]


class EmbeddingDimMismatch(RuntimeError):
    """Raised when the configured embedder dim != the dim already in the index."""


class ChatService:
    """Stateless façade over LangChain's Neo4jVector + a RAG chain."""

    def __init__(self,
                 prompts: PromptStore | None = None,
                 settings: Optional[Any] = None):
        self.settings = settings or get_settings()
        self.prompts = prompts or PromptStore()

    # ------------------------------------------------------------------
    def health(self) -> dict:
        """Pre-flight check the API layer surfaces before the user fires a
        question into a broken RAG. Reports vector-index presence,
        embedding-dim alignment, and per-mode index availability."""
        out: dict = {"ok": False, "messages": [], "modes": {}}
        try:
            with neo4j_manager.driver.session(database=neo4j_manager.database) as s:
                # walk every named index once
                idx_rows = list(s.run("SHOW INDEXES YIELD name, type, options"))
                idx_by_name = {r["name"]: r for r in idx_rows}

                vec = idx_by_name.get("vector")
                if not vec:
                    out["messages"].append(
                        "Vector index 'vector' missing — run post-processing."
                    )
                    return out
                indexed_dim = int(
                    ((vec["options"] or {}).get("indexConfig") or {})
                    .get("vector.dimensions") or 0
                )
                out["indexed_dim"] = indexed_dim
                if indexed_dim and indexed_dim != int(self.settings.embedding_dimension):
                    out["messages"].append(
                        f"Embedding dim mismatch: index={indexed_dim}, "
                        f"settings={self.settings.embedding_dimension}."
                    )
                    return out

                for mode, cfg in _MODES.items():
                    need = [cfg["index_name"]]
                    if cfg.get("keyword_index_name"):
                        need.append(cfg["keyword_index_name"])
                    missing = [n for n in need if n not in idx_by_name]
                    out["modes"][mode] = {"available": not missing, "missing_indexes": missing}
                out["modes"]["graph"] = {"available": True, "missing_indexes": []}

                chunks = s.run("MATCH (c:Chunk) RETURN count(c) AS n").single()
                out["chunks"] = int(chunks["n"]) if chunks else 0
                if out["chunks"] == 0:
                    out["messages"].append("No Chunk nodes in the graph yet.")
                    return out
            out["ok"] = True
            return out
        except Exception as e:
            out["messages"].append(f"health check failed: {e}")
            return out

    # ------------------------------------------------------------------
    def ask(self, *, question: str,
            history: list[dict],
            mode: str = DEFAULT_MODE,
            top_k: int | None = None,
            document_names: list[str] | None = None,
            stream_handler=None) -> dict:
        """Single RAG turn. `stream_handler(token: str)` if streaming."""
        t0 = time.time()
        mode = mode if mode in SUPPORTED_MODES else DEFAULT_MODE

        if mode == "graph":
            return self._ask_graph_mode(
                question=question, history=history, t0=t0,
                stream_handler=stream_handler,
            )

        cfg = _MODES[mode]
        if Neo4jVector is None:
            raise RuntimeError("langchain-neo4j Neo4jVector not importable")

        embedder, _ = build_embedder(self.settings)
        store = Neo4jVector.from_existing_graph(
            embedding=embedder,
            url=self.settings.neo4j_uri,
            username=self.settings.neo4j_username,
            password=self.settings.neo4j_password,
            database=neo4j_manager.database,
            index_name=cfg["index_name"],
            keyword_index_name=cfg.get("keyword_index_name") or "keyword",
            search_type=cfg["search_type"],
            node_label=cfg["node_label"],
            embedding_node_property=cfg["embedding_node_property"],
            text_node_properties=cfg["text_node_properties"],
            retrieval_query=cfg["retrieval_query"],
        )
        k = int(top_k or self.settings.chat_top_k)
        base_retriever = store.as_retriever(search_kwargs={"k": k})

        # compression only applies on Chunk-text modes — entity/community
        # retrieval already returns short summary strings, splitting them
        # discards graph context.
        if cfg["node_label"] == "Chunk":
            splitter = TokenTextSplitter(
                chunk_size=int(self.settings.chat_doc_split_size),
                chunk_overlap=0,
            )
            emb_filter = EmbeddingsFilter(
                embeddings=embedder,
                similarity_threshold=float(self.settings.chat_embedding_filter_threshold),
            )
            retriever = ContextualCompressionRetriever(
                base_compressor=DocumentCompressorPipeline(transformers=[splitter, emb_filter]),
                base_retriever=base_retriever,
            )
        else:
            retriever = base_retriever

        # ---- 1. history-aware rewrite ----
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

        # ---- 2. retrieve ----
        with with_tag("chat_retrieve"):
            docs = retriever.invoke(search_query)

        if document_names and cfg.get("document_filter"):
            keep = set(document_names)
            docs = [d for d in docs
                    if (d.metadata or {}).get("fileName") in keep]

        # ---- 3. context + structured info ----
        context_text, info = self._format_docs(docs, mode=mode)

        # ---- 4. answer ----
        answer_llm = build_chat_llm(self.settings, tag="chat_answer")
        system_text = self.prompts.render(
            "chat_system",
            context=context_text,
            question=question,
        )
        lc_messages = (
            [SystemMessage(content=system_text)] +
            self._history_to_lc(history) +
            [HumanMessage(content=question)]
        )
        text, usage = self._invoke_llm(answer_llm, lc_messages, stream_handler)

        elapsed_ms = int((time.time() - t0) * 1000)
        return {
            "answer": text,
            "sources": info["sources"],
            "nodedetails": info["nodedetails"],
            "entities": info["entities"],
            "context_preview": context_text[:1200],
            "rewritten_question": search_query,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "response_time_ms": elapsed_ms,
            "mode": mode,
            "model": getattr(answer_llm, "model_name", None) or self.settings.llm_model,
        }

    # ------------------------------------------------------------------
    def _ask_graph_mode(self, *, question: str, history: list[dict],
                        t0: float, stream_handler=None) -> dict:
        """Text → Cypher → answer. Uses GraphCypherQAChain. The schema is
        sourced from `db.schema.visualization` so a stale schema can't
        produce broken queries."""
        try:
            from langchain_neo4j import GraphCypherQAChain
        except Exception as e:
            raise RuntimeError(f"GraphCypherQAChain unavailable: {e}")

        graph = Neo4jGraph(
            url=self.settings.neo4j_uri,
            username=self.settings.neo4j_username,
            password=self.settings.neo4j_password,
            database=neo4j_manager.database,
            refresh_schema=True,
        )
        llm = build_chat_llm(self.settings, tag="chat_graph")
        # validate_cypher=True calls apoc.meta.schema() — APOC required.
        # We probe once and pick the safer variant. Without validation the
        # LLM-generated Cypher may query non-existent labels but the mode
        # still works on Community Edition.
        validate = self._apoc_available()
        kwargs = dict(graph=graph, llm=llm, validate_cypher=validate,
                      return_intermediate_steps=True)
        try:
            chain = GraphCypherQAChain.from_llm(
                **kwargs, allow_dangerous_requests=True,
            )
        except TypeError:
            chain = GraphCypherQAChain.from_llm(**kwargs)

        with with_tag("chat_graph"):
            res = chain.invoke({"query": question})
        text = res.get("result") or res.get("output") or ""
        steps = res.get("intermediate_steps") or []
        cypher = ""
        rows: list = []
        for s in steps:
            if isinstance(s, dict):
                cypher = s.get("query") or cypher
                rows = s.get("context") or rows
        if stream_handler and text:
            for tok in text.split(" "):
                stream_handler(tok + " ")
        elapsed_ms = int((time.time() - t0) * 1000)
        return {
            "answer": text,
            "sources": [],
            "nodedetails": {"chunkdetails": [], "entitydetails": [], "communitydetails": []},
            "entities": {"entityids": [], "relationshipids": [], "nodes": [], "relationships": []},
            "context_preview": (cypher or "")[:1200],
            "rewritten_question": question,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "response_time_ms": elapsed_ms,
            "mode": "graph",
            "model": getattr(llm, "model_name", None) or self.settings.llm_model,
            "cypher": cypher,
            "rows": rows[:50] if isinstance(rows, list) else [],
        }

    # ------------------------------------------------------------------
    def _invoke_llm(self, llm, lc_messages, stream_handler):
        if stream_handler is None:
            with with_tag("chat_answer"):
                msg = llm.invoke(lc_messages)
            text = getattr(msg, "content", None) or str(msg)
            return text, self._extract_usage(msg)
        # streaming path
        parts: list[str] = []
        with with_tag("chat_answer"):
            for chunk in llm.stream(lc_messages):
                tok = getattr(chunk, "content", None) or ""
                if tok:
                    parts.append(tok)
                    try:
                        stream_handler(tok)
                    except Exception:
                        pass
        return "".join(parts), {}

    # ------------------------------------------------------------------
    def _format_docs(self, docs: list[Document], *, mode: str) -> tuple[str, dict]:
        sources_by_file: dict[str, dict] = {}
        chunkdetails: list[dict] = []
        entitydetails: list[dict] = []
        communitydetails: list[dict] = []
        all_entities: list[dict] = []
        all_rels: list[dict] = []
        seen_eids: set[str] = set()
        seen_rids: set[str] = set()
        seen_chunk: set[str] = set()

        parts: list[str] = []
        for i, d in enumerate(docs, 1):
            meta = d.metadata or {}
            fname = meta.get("fileName") or meta.get("source")
            cid = meta.get("chunkId")
            eid = meta.get("entityId")
            comm_id = meta.get("communityId")
            text = d.page_content or meta.get("text") or ""
            score = meta.get("score") or meta.get("query_similarity_score")

            if cid and cid not in seen_chunk:
                seen_chunk.add(cid)
                chunkdetails.append({"id": cid, "score": score})
            if eid:
                entitydetails.append({"id": eid, "label": meta.get("id")})
            if comm_id:
                communitydetails.append({"id": comm_id, "label": meta.get("title")})

            if fname:
                row = sources_by_file.setdefault(fname, {"source_name": fname, "chunk_ids": []})
                if cid:
                    row["chunk_ids"].append(cid)

            for e in (meta.get("entities") or []):
                e_eid = e.get("elementId") or e.get("id")
                if e_eid and e_eid not in seen_eids:
                    seen_eids.add(e_eid)
                    all_entities.append(e)
            for r in (meta.get("relationships") or []):
                rid = r.get("elementId") or f"{r.get('startId')}-{r.get('type')}-{r.get('endId')}"
                if rid not in seen_rids:
                    seen_rids.add(rid)
                    all_rels.append(r)

            header_bits = []
            if fname: header_bits.append(fname)
            if cid: header_bits.append(f"chunk:{cid[:8]}")
            if eid: header_bits.append(f"entity:{meta.get('id')}")
            if comm_id: header_bits.append(f"community:{meta.get('title')}")
            header = " | ".join(header_bits) or "context"
            parts.append(f"[{i} | {header}]\n{text.strip()}")

        info = {
            "sources": list(sources_by_file.values()),
            "nodedetails": {
                "chunkdetails": chunkdetails,
                "entitydetails": entitydetails,
                "communitydetails": communitydetails,
            },
            "entities": {
                "entityids": [e.get("elementId") for e in all_entities if e.get("elementId")],
                "relationshipids": [r.get("elementId") for r in all_rels if r.get("elementId")],
                "nodes": all_entities,
                "relationships": all_rels,
            },
        }
        return "\n\n".join(parts), info

    @staticmethod
    def _history_to_lc(history: list[dict]) -> list:
        out = []
        for m in history:
            role = (m.get("role") or "").lower()
            content = m.get("content") or ""
            if not content:
                continue
            if role == "assistant":
                out.append(AIMessage(content=content))
            elif role == "system":
                out.append(SystemMessage(content=content))
            else:
                out.append(HumanMessage(content=content))
        return out

    @staticmethod
    def _format_history_for_prompt(history: list[dict], limit: int = 8) -> str:
        tail = history[-limit:]
        return "\n".join(
            f"{(m.get('role') or 'user')}: {(m.get('content') or '').strip()}"
            for m in tail
        )

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

    @staticmethod
    def _extract_usage(msg) -> dict:
        u = (getattr(msg, "response_metadata", None) or {}).get("token_usage") \
            or getattr(msg, "usage_metadata", None) \
            or {}
        return {
            "prompt_tokens": u.get("prompt_tokens") or u.get("input_tokens"),
            "completion_tokens": u.get("completion_tokens") or u.get("output_tokens"),
            "total_tokens": u.get("total_tokens"),
        }
