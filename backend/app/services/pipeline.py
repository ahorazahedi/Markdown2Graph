from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ..config import get_settings
from ..llm import build_embedder
from ..repositories.graph_repository import GraphRepository
from .chunker import MarkdownChunker
from .entity_extractor import EntityExtractor
from .job_registry import JobUpdate
from .markdown_loader import MarkdownDoc, MarkdownLoader
from .post_processor import PostProcessor

log = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    allowed_nodes: List[str] = field(default_factory=list)
    allowed_relationships: List[Tuple[str, str, str]] = field(default_factory=list)
    extra_instructions: Optional[str] = None
    max_workers: Optional[int] = None


class IngestionPipeline:
    """End-to-end: markdown -> chunks -> embeddings -> entity extraction -> graph."""

    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.settings = get_settings()
        self.repo = GraphRepository()
        self.chunker = MarkdownChunker()
        self.extractor = EntityExtractor(
            allowed_nodes=cfg.allowed_nodes,
            allowed_relationships=cfg.allowed_relationships,
            extra_instructions=cfg.extra_instructions,
        )
        self.embedder, self.embed_dim = build_embedder(self.settings)
        self.post = PostProcessor()

    def run(self, files: List[Path], progress: Callable[[JobUpdate], None] | None = None) -> dict:
        progress = progress or (lambda _u: None)

        progress(JobUpdate(stage="setup", message="ensuring constraints", progress=0.01))
        self.repo.ensure_constraints()

        loader = MarkdownLoader(files[0].parent if files else Path("."))
        total = len(files)
        results: List[dict] = []
        workers = self.cfg.max_workers or self.settings.ingest_concurrency
        done = 0

        progress(JobUpdate(stage="loading", message=f"processing {total} files", progress=0.03))

        def _do_one(idx_path):
            i, p = idx_path
            try:
                doc = loader.load_one(p)
                progress(
                    JobUpdate(
                        stage="extracting",
                        message=f"reading {p.name}",
                        progress=0.05 + 0.85 * done / max(1, total),
                        extra={"file": p.name, "phase": "start",
                               "files_done": done, "files_total": total},
                    )
                )
                stats = self._process_document(doc)
                return {"file": doc.file_name, "ok": True, **stats}
            except Exception as e:
                log.exception("file %s failed", p)
                try:
                    self.repo.set_document_status(p.name, "Failed", error=str(e))
                except Exception:
                    pass
                return {"file": p.name, "ok": False, "error": str(e)}

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_do_one, (i, p)) for i, p in enumerate(files)]
            for fut in as_completed(futures):
                r = fut.result()
                results.append(r)
                done += 1
                if r.get("ok"):
                    msg = (f"[{done}/{total}] {r['file']}: "
                           f"{r.get('chunks', 0)} chunks, {r.get('entities', 0)} entities, "
                           f"{r.get('relationships', 0)} rels")
                else:
                    msg = f"[{done}/{total}] FAILED {r['file']}: {r.get('error', '?')}"
                progress(
                    JobUpdate(
                        stage="extracting",
                        message=msg,
                        progress=0.05 + 0.85 * done / max(1, total),
                        extra={"file": r["file"], "phase": "done",
                               "files_done": done, "files_total": total,
                               **{k: v for k, v in r.items() if k not in ("file", "ok")}},
                    )
                )

        post_stats = self.post.run(progress=progress)

        progress(JobUpdate(stage="done", message="ingestion complete", progress=1.0))
        return {
            "files": results,
            "post_processing": post_stats,
            "totals": self.repo.stats(),
        }

    # ------------------------------------------------------------------
    def _process_document(self, doc: MarkdownDoc) -> dict:
        self.repo.upsert_document(
            file_name=doc.file_name,
            sha1=doc.sha1,
            title=doc.title,
            source=str(doc.path),
            length=doc.length,
        )
        self.repo.set_document_status(doc.file_name, "Processing")

        chunks = self.chunker.split(doc.file_name, doc.text)
        if not chunks:
            self.repo.set_document_status(doc.file_name, "Empty")
            return {"chunks": 0, "entities": 0, "relationships": 0}

        # 1. write chunks + structural relationships
        self.repo.write_chunks(
            doc.file_name,
            [
                {
                    "id": c.id,
                    "text": c.text,
                    "position": c.position,
                    "length": c.length,
                    "content_offset": c.content_offset,
                }
                for c in chunks
            ],
        )
        self.repo.link_first_and_next(doc.file_name, [c.id for c in chunks])

        # 2. embeddings (best-effort, never fatal)
        try:
            from ..llm import with_tag
            with with_tag("embedding"):
                vectors = self.embedder.embed_documents([c.text for c in chunks])
            self.repo.write_chunk_embeddings(
                [{"id": c.id, "embedding": v} for c, v in zip(chunks, vectors)]
            )
        except Exception as e:
            log.warning("embedding failed for %s: %s", doc.file_name, e)

        # 3. entity / relationship extraction
        s = self.settings
        combine = max(1, s.chunks_to_combine)
        lc_docs = []
        for i in range(0, len(chunks), combine):
            window = chunks[i : i + combine]
            text = "\n\n".join(w.text for w in window)
            chunk_id = window[0].id
            from langchain_core.documents import Document

            lc_docs.append(
                Document(page_content=text, metadata={"chunk_id": chunk_id, "file_name": doc.file_name})
            )
        graph_docs = self.extractor.extract(lc_docs)
        ent_count, rel_count = self.repo.write_graph_documents(doc.file_name, graph_docs)

        self.repo.set_document_status(
            doc.file_name,
            "Completed",
            chunk_count=len(chunks),
            entity_count=ent_count,
            relationship_count=rel_count,
        )
        return {"chunks": len(chunks), "entities": ent_count, "relationships": rel_count}
