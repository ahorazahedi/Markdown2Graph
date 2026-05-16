from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ..config import get_settings
from ..llm import build_embedder
from ..repositories.app_state_repository import AppStateRepository
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
    job_id: Optional[str] = None


class IngestionPipeline:
    """End-to-end: markdown -> chunks -> embeddings -> entity extraction -> graph."""

    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.settings = get_settings()
        self.repo = GraphRepository()
        self.state = AppStateRepository()
        self.chunker = MarkdownChunker()
        self.extractor = EntityExtractor(
            allowed_nodes=cfg.allowed_nodes,
            allowed_relationships=cfg.allowed_relationships,
            extra_instructions=cfg.extra_instructions,
        )
        self.embedder, self.embed_dim = build_embedder(self.settings)
        self.post = PostProcessor()

    # ------------------------------------------------------------------
    def run_documents(
        self,
        doc_ids: List[int],
        *,
        reextract: bool = False,
        progress: Callable[[JobUpdate], None] | None = None,
    ) -> dict:
        """Ingest a specific set of documents identified by app-state ids."""
        progress = progress or (lambda _u: None)
        progress(JobUpdate(stage="setup", message="ensuring constraints", progress=0.01))
        self.repo.ensure_constraints()

        records = []
        for did in doc_ids:
            d = self.state.get_document(did)
            if not d:
                continue
            records.append(d)
        return self._run_records(records, reextract=reextract, progress=progress)

    def run_pending(self, progress: Callable[[JobUpdate], None] | None = None) -> dict:
        """Convenience: ingest every doc not already completed."""
        progress = progress or (lambda _u: None)
        self.repo.ensure_constraints()
        records = [
            d for d in self.state.list_documents()
            if d["status"] in ("pending", "failed")
        ]
        return self._run_records(records, reextract=False, progress=progress)

    # ------------------------------------------------------------------
    def _run_records(
        self,
        records: list[dict],
        *,
        reextract: bool,
        progress: Callable[[JobUpdate], None],
    ) -> dict:
        total = len(records)
        if total == 0:
            progress(JobUpdate(stage="done", message="nothing to process", progress=1.0))
            return {"files": [], "post_processing": {}, "totals": self.repo.stats()}

        workers = self.cfg.max_workers or self.settings.ingest_concurrency
        progress(JobUpdate(stage="loading", message=f"processing {total} documents", progress=0.03))

        done = 0
        results: list[dict] = []
        loader = MarkdownLoader(Path("."))  # only used for load_one(path)

        def _do_one(idx_record):
            i, rec = idx_record
            try:
                p = Path(rec["source_path"])
                if not p.exists():
                    raise FileNotFoundError(f"source file missing: {p}")
                self.state.set_status(rec["id"], "processing", job_id=self.cfg.job_id)
                progress(
                    JobUpdate(
                        stage="extracting",
                        message=f"reading {rec['file_name']}",
                        progress=0.05 + 0.85 * done / max(1, total),
                        extra={"file": rec["file_name"], "phase": "start",
                               "files_done": done, "files_total": total},
                    )
                )
                doc = loader.load_one(p)
                if reextract:
                    # purge previous graph state for this file before reprocessing
                    self.repo.delete_document(rec["file_name"])
                stats = self._process_document(doc)
                self.state.set_status(rec["id"], "completed")
                self.state.set_counts(
                    rec["id"],
                    chunk_count=stats["chunks"],
                    entity_count=stats["entities"],
                    relationship_count=stats["relationships"],
                )
                return {"id": rec["id"], "file": doc.file_name, "ok": True, **stats}
            except Exception as e:
                log.exception("file %s failed", rec.get("file_name"))
                self.state.set_status(rec["id"], "failed", error=str(e))
                try:
                    self.repo.set_document_status(rec["file_name"], "Failed", error=str(e))
                except Exception:
                    pass
                return {"id": rec["id"], "file": rec["file_name"], "ok": False, "error": str(e)}

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_do_one, (i, r)) for i, r in enumerate(records)]
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
                               **{k: v for k, v in r.items() if k not in ("file", "ok", "id")}},
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

        try:
            from ..llm import with_tag
            with with_tag("embedding"):
                vectors = self.embedder.embed_documents([c.text for c in chunks])
            self.repo.write_chunk_embeddings(
                [{"id": c.id, "embedding": v} for c, v in zip(chunks, vectors)]
            )
        except Exception as e:
            log.warning("embedding failed for %s: %s", doc.file_name, e)

        s = self.settings
        combine = max(1, s.chunks_to_combine)
        lc_docs = []
        from langchain_core.documents import Document
        for i in range(0, len(chunks), combine):
            window = chunks[i : i + combine]
            text = "\n\n".join(w.text for w in window)
            chunk_id = window[0].id
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
