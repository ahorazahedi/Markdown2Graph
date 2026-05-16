from __future__ import annotations

import logging

from ..config import get_settings
from ..repositories.graph_repository import GraphRepository

log = logging.getLogger(__name__)


class PostProcessor:
    """Post-extraction tasks: vector index + chunk-similarity edges.

    LLM-driven label cleanup + WCC community detection live in
    PostProcessingService; the Leiden hierarchy from GDS is intentionally
    skipped because it isn't available on Neo4j Community Edition.
    """

    def __init__(self):
        self.settings = get_settings()
        self.repo = GraphRepository()

    def run(self, progress=None) -> dict:
        s = self.settings
        out = {"vector_index": False, "similar_relationships": 0}
        if not s.enable_post_processing:
            return out

        if progress:
            from .job_registry import JobUpdate
            progress(JobUpdate(stage="post_processing", message="creating chunk vector index", progress=0.92))
        self.repo.create_chunk_vector_index(s.embedding_dimension)
        out["vector_index"] = True
        # Chat / RAG fulltext indexes — cheap to create, idempotent.
        self.repo.create_chat_indexes()
        out["chat_indexes"] = True

        if s.enable_similar_chunks:
            if progress:
                from .job_registry import JobUpdate
                progress(JobUpdate(stage="post_processing", message="linking SIMILAR chunks", progress=0.96))
            count = self.repo.create_similar_chunk_relationships(min_score=s.knn_min_score)
            out["similar_relationships"] = count

        return out
