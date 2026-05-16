"""RAG chat over the extracted Neo4j knowledge graph.

V1 ships only the `graph_vector_fulltext` mode from
llm-graph-builder — hybrid (vector + BM25 fulltext) retrieval on
`(:Chunk)` followed by a graph hop that pulls neighbouring entities and
relationships into the context. Additional modes (entity_vector,
global_vector, graph) can be slotted into `_MODES` later.

Embedding model parity is critical: the same embedder used to ingest
chunks must be used at retrieval time, otherwise scores are meaningless.
`ChatService.health()` reports a mismatch when the configured embedding
dimension differs from the indexed chunks.
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
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_text_splitters import TokenTextSplitter

try:
    from langchain_neo4j import Neo4jVector
except Exception:  # pragma: no cover
    Neo4jVector = None  # type: ignore[assignment]

from ..config import get_settings
from ..extensions import neo4j_manager
from ..llm.client import build_chat_llm, build_embedder
from ..llm.recorder import with_tag
from .prompt_store import PromptStore

log = logging.getLogger(__name__)


# Cypher run AFTER Neo4jVector's vector + fulltext match. Returns one
# row per matched chunk with the chunk's text plus a JSON-friendly map
# of graph context to render in the prompt. Mirrors
# `VECTOR_GRAPH_SEARCH_QUERY` in llm-graph-builder/constants.py.
_RETRIEVAL_QUERY = """
WITH node AS chunk, score
OPTIONAL MATCH (chunk)-[:PART_OF]->(d:Document)
WITH chunk, score, d
OPTIONAL MATCH (chunk)-[:HAS_ENTITY]->(e:__Entity__)
WITH chunk, score, d,
     collect(DISTINCT {
        id: e.id,
        elementId: elementId(e),
        labels: [l IN labels(e) WHERE l <> '__Entity__'],
        description: e.description
     })[0..25] AS entities,
     collect(DISTINCT elementId(e)) AS entity_ids
OPTIONAL MATCH (a:__Entity__)-[r]-(b:__Entity__)
  WHERE elementId(a) IN entity_ids AND elementId(b) IN entity_ids
WITH chunk, score, d, entities,
     collect(DISTINCT {
        startId: a.id,
        endId:   b.id,
        type:    type(r),
        elementId: elementId(r)
     })[0..40] AS relationships
RETURN
  chunk.text AS text,
  score,
  {
    chunkId:   chunk.id,
    fileName:  coalesce(d.fileName, chunk.fileName),
    position:  chunk.position,
    entities:  entities,
    relationships: relationships
  } AS metadata
"""


class EmbeddingDimMismatch(RuntimeError):
    """Raised when the configured embedder dim != the dim already in the index."""


class ChatService:
    """Stateless façade over LangChain's Neo4jVector + a RAG chain.

    A fresh `Neo4jVector` is built per call; that's fine — it doesn't
    materialise the index, only opens a session against the existing one.
    """

    def __init__(self,
                 prompts: PromptStore | None = None,
                 settings: Optional[Any] = None):
        self.settings = settings or get_settings()
        self.prompts = prompts or PromptStore()

    # ------------------------------------------------------------------
    def health(self) -> dict:
        """Pre-flight check the API layer can surface before the user fires
        a question into a broken RAG. Reports vector-index presence,
        embedding-dim alignment, and chunk count."""
        out: dict = {"ok": False, "messages": []}
        try:
            with neo4j_manager.driver.session(database=neo4j_manager.database) as s:
                row = s.run(
                    "SHOW INDEXES YIELD name, type, options "
                    "WHERE name = 'vector' RETURN options"
                ).single()
                if not row:
                    out["messages"].append(
                        "Vector index 'vector' missing — run post-processing first."
                    )
                    return out
                opts = row["options"] or {}
                indexed_dim = int(
                    (opts.get("indexConfig") or {}).get("vector.dimensions") or 0
                )
                out["indexed_dim"] = indexed_dim

                if indexed_dim and indexed_dim != int(self.settings.embedding_dimension):
                    out["messages"].append(
                        f"Embedding dim mismatch: index={indexed_dim}, "
                        f"settings={self.settings.embedding_dimension}. Re-embed or "
                        f"reset the index before chatting."
                    )
                    return out

                kw = s.run(
                    "SHOW INDEXES YIELD name WHERE name='keyword' RETURN count(*) AS n"
                ).single()
                if not kw or int(kw["n"]) == 0:
                    out["messages"].append(
                        "Fulltext index 'keyword' missing — run post-processing."
                    )
                    return out

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
            mode: str = "graph_vector_fulltext",
            top_k: int | None = None,
            document_names: list[str] | None = None) -> dict:
        """Run one RAG turn.

        Args:
            question: latest user message (raw).
            history: prior messages in chronological order, each shaped
                `{"role": "user"|"assistant"|"system", "content": str}`.
                The current `question` is NOT in this list.
            mode: only `graph_vector_fulltext` honored in V1.
            top_k: override default chunk count.
            document_names: optional list of filenames to scope retrieval to.

        Returns:
            `{"answer", "sources", "nodedetails", "entities",
              "prompt_tokens", "completion_tokens", "total_tokens",
              "response_time_ms", "mode", "model"}`
        """
        t0 = time.time()
        if mode != "graph_vector_fulltext":
            # Keep the door open — fall back to the same path for now.
            log.warning("mode %r not yet supported, using graph_vector_fulltext", mode)
            mode = "graph_vector_fulltext"

        embedder, embed_dim = build_embedder(self.settings)

        if Neo4jVector is None:
            raise RuntimeError("langchain-neo4j Neo4jVector not importable")

        store = Neo4jVector.from_existing_graph(
            embedding=embedder,
            url=self.settings.neo4j_uri,
            username=self.settings.neo4j_username,
            password=self.settings.neo4j_password,
            database=neo4j_manager.database,
            index_name="vector",
            keyword_index_name="keyword",
            search_type="hybrid",
            node_label="Chunk",
            embedding_node_property="embedding",
            text_node_properties=["text"],
            retrieval_query=_RETRIEVAL_QUERY,
        )

        k = int(top_k or self.settings.chat_top_k)
        base_retriever = store.as_retriever(search_kwargs={"k": k})

        # Compression pipeline: split very long chunks (rare), filter by
        # cosine similarity to the rewritten question.
        splitter = TokenTextSplitter(
            chunk_size=int(self.settings.chat_doc_split_size),
            chunk_overlap=0,
        )
        emb_filter = EmbeddingsFilter(
            embeddings=embedder,
            similarity_threshold=float(self.settings.chat_embedding_filter_threshold),
        )
        compressor = DocumentCompressorPipeline(transformers=[splitter, emb_filter])
        retriever = ContextualCompressionRetriever(
            base_compressor=compressor,
            base_retriever=base_retriever,
        )

        # ---- 1. history-aware rewrite ----
        llm = build_chat_llm(self.settings, tag="chat_rewrite")
        rewriter_template = self.prompts.render(
            "chat_question_rewrite",
            history=self._format_history_for_prompt(history),
            question=question,
        )
        if history:
            with with_tag("chat_rewrite"):
                rewritten = llm.invoke(rewriter_template)
            search_query = (getattr(rewritten, "content", None) or str(rewritten)).strip()
            if not search_query:
                search_query = question
        else:
            search_query = question

        # ---- 2. retrieve ----
        with with_tag("chat_retrieve"):
            docs = retriever.invoke(search_query)

        # Optional doc-name filter (V1 doesn't require, but already free here)
        if document_names:
            keep = set(document_names)
            docs = [d for d in docs
                    if (d.metadata or {}).get("fileName") in keep]

        # ---- 3. build context + structured info ----
        context_text, info = self._format_docs(docs)

        # ---- 4. answer ----
        answer_llm = build_chat_llm(self.settings, tag="chat_answer")
        answer_prompt = self.prompts.render(
            "chat_system",
            context=context_text,
            question=question,
        )
        lc_messages = (
            [SystemMessage(content=answer_prompt)] +
            self._history_to_lc(history) +
            [HumanMessage(content=question)]
        )
        with with_tag("chat_answer"):
            answer = answer_llm.invoke(lc_messages)

        text = getattr(answer, "content", None) or str(answer)
        usage = self._extract_usage(answer)

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
    def _format_docs(self, docs: list[Document]) -> tuple[str, dict]:
        """Build the prompt context + the structured metadata payload."""
        sources_by_file: dict[str, dict] = {}
        chunkdetails: list[dict] = []
        all_entities: list[dict] = []
        all_rels: list[dict] = []
        seen_eids: set[str] = set()
        seen_rids: set[str] = set()
        seen_chunk: set[str] = set()

        parts: list[str] = []
        for i, d in enumerate(docs, 1):
            meta = d.metadata or {}
            fname = meta.get("fileName") or meta.get("source") or "unknown"
            cid = meta.get("chunkId") or meta.get("chunk_id")
            # text either lives on d.page_content (preferred) or in metadata.text
            text = d.page_content or meta.get("text") or ""
            score = meta.get("score") or meta.get("query_similarity_score")

            if cid and cid not in seen_chunk:
                seen_chunk.add(cid)
                chunkdetails.append({"id": cid, "score": score})
            if fname:
                row = sources_by_file.setdefault(fname, {"source_name": fname, "chunk_ids": []})
                if cid:
                    row["chunk_ids"].append(cid)

            for e in (meta.get("entities") or []):
                eid = e.get("elementId") or e.get("id")
                if eid and eid not in seen_eids:
                    seen_eids.add(eid)
                    all_entities.append(e)
            for r in (meta.get("relationships") or []):
                rid = r.get("elementId") or f"{r.get('startId')}-{r.get('type')}-{r.get('endId')}"
                if rid not in seen_rids:
                    seen_rids.add(rid)
                    all_rels.append(r)

            ent_blurb = ", ".join(
                f"{e.get('id')}" for e in (meta.get("entities") or [])[:10] if e.get("id")
            )
            rel_blurb = "; ".join(
                f"({r.get('startId')})-[{r.get('type')}]->({r.get('endId')})"
                for r in (meta.get("relationships") or [])[:10]
            )
            parts.append(
                f"[chunk {i} | {fname}]\n"
                f"{text.strip()}\n"
                + (f"  entities: {ent_blurb}\n" if ent_blurb else "")
                + (f"  edges: {rel_blurb}\n" if rel_blurb else "")
            )

        info = {
            "sources": list(sources_by_file.values()),
            "nodedetails": {
                "chunkdetails": chunkdetails,
                "entitydetails": [],
                "communitydetails": [],
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
        # last N turns, simple text
        tail = history[-limit:]
        return "\n".join(
            f"{(m.get('role') or 'user')}: {(m.get('content') or '').strip()}"
            for m in tail
        )

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
