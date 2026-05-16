from __future__ import annotations

import logging
from typing import List, Tuple

from langchain_core.documents import Document
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_experimental.graph_transformers.llm import _Graph

from ..llm import build_chat_llm
from .prompt_store import PromptStore

log = logging.getLogger(__name__)


def _norm_label(s: str | None) -> str:
    """Normalize a label/type so allowed-vs-extracted comparison is robust to
    case, whitespace and punctuation. Mirrors what `graph_repository` would
    write but also lowercases."""
    if not s:
        return ""
    out = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(s).strip())
    return out.lower()


def _norm_rel(s: str | None) -> str:
    if not s:
        return ""
    out = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(s).strip())
    return out.lower()


class EntityExtractor:
    """Wraps LangChain's LLMGraphTransformer with the active extraction
    prompt (see Prompts page → presets) and the optional user-approved
    schema.

    After the LLM returns, we post-validate the graph against the user
    schema: nodes whose label is not in `allowed_nodes` are dropped, and
    relationships whose `(src_label, rel_type, tgt_label)` is not in
    `allowed_relationships` are dropped. The reference llm-graph-builder
    enforces this only loosely inside LLMGraphTransformer; without the
    post-filter, hallucinated labels can leak into Neo4j.
    """

    def __init__(
        self,
        allowed_nodes: List[str] | None = None,
        allowed_relationships: List[Tuple[str, str, str]] | None = None,
        extra_instructions: str | None = None,
    ):
        self.llm = build_chat_llm()
        self.allowed_nodes_raw = list(allowed_nodes or [])
        self.allowed_relationships_raw = list(allowed_relationships or [])
        self._allowed_node_set = {_norm_label(x) for x in self.allowed_nodes_raw if x}
        self._allowed_triplet_set = {
            (_norm_label(a), _norm_rel(b), _norm_label(c))
            for (a, b, c) in self.allowed_relationships_raw
            if a and b and c
        }

        # Detect structured-output support so we can opt into rich properties.
        try:
            self.llm.with_structured_output(_Graph)
            supports = True
        except Exception:
            supports = False

        node_props = ["description"] if supports else False
        rel_props = ["description"] if supports else False

        instructions = PromptStore().render(
            "entity_extraction_instructions",
            allowed_nodes=allowed_nodes or [],
            allowed_relationships=allowed_relationships or [],
            extra_instructions=self._sanitize(extra_instructions) if extra_instructions else "",
        )

        self.transformer = LLMGraphTransformer(
            llm=self.llm,
            allowed_nodes=allowed_nodes or [],
            allowed_relationships=allowed_relationships or [],
            node_properties=node_props,
            relationship_properties=rel_props,
            ignore_tool_usage=not supports,
            additional_instructions=instructions,
        )

    async def extract_async(self, docs: List[Document]):
        """Returns (graph_docs, drop_stats). Thread-safe — no instance state."""
        from ..llm import with_tag
        with with_tag("entity_extraction"):
            graph_docs = await self.transformer.aconvert_to_graph_documents(docs)
        return self._validate(graph_docs)

    def extract(self, docs: List[Document]):
        """Returns (graph_docs, drop_stats). Thread-safe — no instance state."""
        from ..llm import with_tag
        with with_tag("entity_extraction"):
            graph_docs = self.transformer.convert_to_graph_documents(docs)
        return self._validate(graph_docs)

    # ----------------- validation -----------------
    def _validate(self, graph_docs):
        """Filter out nodes/rels that violate the user schema.

        Open-schema mode (no allowed_nodes) is a no-op. If allowed_nodes is
        set, we drop nodes whose normalized type is not in it, plus any rel
        touching a dropped node. If allowed_relationships triplets are also
        set, we additionally drop rels whose normalized triplet is not in
        the whitelist.
        """
        if not self._allowed_node_set:
            return graph_docs, {
                "nodes_dropped": 0, "rels_dropped_endpoint": 0,
                "rels_dropped_triplet": 0,
            }

        nodes_dropped = 0
        rels_dropped_endpoint = 0
        rels_dropped_triplet = 0

        for gd in graph_docs:
            kept_nodes = []
            kept_ids: set[str] = set()
            for n in getattr(gd, "nodes", []) or []:
                nt = _norm_label(getattr(n, "type", ""))
                if nt in self._allowed_node_set:
                    kept_nodes.append(n)
                    kept_ids.add(str(n.id))
                else:
                    nodes_dropped += 1
            gd.nodes = kept_nodes

            kept_rels = []
            for r in getattr(gd, "relationships", []) or []:
                src_id = str(r.source.id)
                tgt_id = str(r.target.id)
                src_t = _norm_label(getattr(r.source, "type", ""))
                tgt_t = _norm_label(getattr(r.target, "type", ""))
                rel_t = _norm_rel(getattr(r, "type", ""))
                # endpoint must be in allowed labels AND must survive node filter
                if (src_t not in self._allowed_node_set
                        or tgt_t not in self._allowed_node_set
                        or src_id not in kept_ids
                        or tgt_id not in kept_ids):
                    rels_dropped_endpoint += 1
                    continue
                # triplet whitelist (only if user supplied one)
                if self._allowed_triplet_set and (src_t, rel_t, tgt_t) not in self._allowed_triplet_set:
                    rels_dropped_triplet += 1
                    continue
                kept_rels.append(r)
            gd.relationships = kept_rels

        stats = {
            "nodes_dropped": nodes_dropped,
            "rels_dropped_endpoint": rels_dropped_endpoint,
            "rels_dropped_triplet": rels_dropped_triplet,
        }
        if nodes_dropped or rels_dropped_endpoint or rels_dropped_triplet:
            log.info(
                "triplet validation dropped: %d nodes, %d rels(endpoint), %d rels(triplet)",
                nodes_dropped, rels_dropped_endpoint, rels_dropped_triplet,
            )
        return graph_docs, stats

    @staticmethod
    def _sanitize(text: str) -> str:
        # block prompt-injection-y bits, mirror reference repo
        import re

        text = text.replace("{", "[").replace("}", "]")
        for pat in (r"os\.getenv\(", r"eval\(", r"exec\(", r"subprocess\.", r"import os", r"import subprocess"):
            text = re.sub(pat, "[BLOCKED]", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()
