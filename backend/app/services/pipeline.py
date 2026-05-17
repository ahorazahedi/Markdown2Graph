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
from .job_registry import JobCancelled, JobUpdate
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
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict:
        """Ingest a specific set of documents identified by app-state ids."""
        progress = progress or (lambda _u: None)
        is_cancelled = is_cancelled or (lambda: False)
        progress(JobUpdate(stage="setup", message="ensuring constraints", progress=0.01))
        self.repo.ensure_constraints()

        records = []
        for did in doc_ids:
            d = self.state.get_document(did)
            if not d:
                continue
            records.append(d)
        return self._run_records(records, reextract=reextract, progress=progress,
                                 is_cancelled=is_cancelled)

    def run_pending(self, progress: Callable[[JobUpdate], None] | None = None,
                    is_cancelled: Callable[[], bool] | None = None) -> dict:
        """Convenience: ingest every doc not already completed."""
        progress = progress or (lambda _u: None)
        is_cancelled = is_cancelled or (lambda: False)
        self.repo.ensure_constraints()
        records = [
            d for d in self.state.list_documents()
            if d["status"] in ("pending", "failed")
        ]
        return self._run_records(records, reextract=False, progress=progress,
                                 is_cancelled=is_cancelled)

    # ------------------------------------------------------------------
    def _run_records(
        self,
        records: list[dict],
        *,
        reextract: bool,
        progress: Callable[[JobUpdate], None],
        is_cancelled: Callable[[], bool] = lambda: False,
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
            if is_cancelled():
                # bail before touching the file — flip back to pending
                try:
                    self.state.set_status(rec["id"], "pending",
                                          error="cancelled before start")
                except Exception:
                    pass
                return {"id": rec["id"], "file": rec["file_name"],
                        "ok": False, "cancelled": True, "error": "cancelled"}
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
                stats = self._process_document(doc, progress=progress,
                                                file_idx=i, file_total=total,
                                                state_doc_id=rec["id"],
                                                is_cancelled=is_cancelled)
                if stats.get("cancelled"):
                    self.state.set_status(rec["id"], "pending",
                                          error="cancelled mid-extraction")
                    try:
                        self.repo.set_document_status(
                            rec["file_name"], "Cancelled",
                            error="cancelled mid-extraction",
                            chunk_count=stats.get("chunks"),
                            entity_count=stats.get("entities"),
                            relationship_count=stats.get("relationships"),
                        )
                    except Exception:
                        pass
                    return {"id": rec["id"], "file": doc.file_name,
                            "ok": False, "cancelled": True,
                            "error": "cancelled", **stats}
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

        if is_cancelled():
            progress(JobUpdate(
                stage="cancelled",
                message="ingestion cancelled — skipping post-processing",
                progress=max(0.9, min(1.0, 0.05 + 0.85 * done / max(1, total))),
                extra={"level": "warn"},
            ))
            return {
                "files": results,
                "post_processing": {"skipped": "cancelled"},
                "totals": self.repo.stats(),
                "cancelled": True,
            }
        post_stats = self.post.run(progress=progress)
        progress(JobUpdate(stage="done", message="ingestion complete", progress=1.0))
        return {
            "files": results,
            "post_processing": post_stats,
            "totals": self.repo.stats(),
        }

    # ------------------------------------------------------------------
    def _process_document(self, doc: MarkdownDoc,
                          progress: Callable[[JobUpdate], None] | None = None,
                          file_idx: int = 0, file_total: int = 1,
                          state_doc_id: int | None = None,
                          is_cancelled: Callable[[], bool] = lambda: False) -> dict:
        progress = progress or (lambda _u: None)
        # progress portion this file gets within the 0.05..0.90 window
        base = 0.05 + 0.85 * file_idx / max(1, file_total)
        span = 0.85 / max(1, file_total)
        def _emit(local: float, message: str, extra: dict | None = None):
            progress(JobUpdate(
                stage="extracting",
                message=message,
                progress=base + span * max(0.0, min(1.0, local)),
                extra={"file": doc.file_name, **(extra or {})},
            ))
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
        _emit(0.05, f"{doc.file_name}: chunked into {len(chunks)} chunks",
              {"chunks_total": len(chunks)})

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

        _emit(0.15, f"{doc.file_name}: embedding {len(chunks)} chunks")
        try:
            from ..llm import with_tag
            with with_tag("embedding"):
                vectors = self.embedder.embed_documents([c.text for c in chunks])
            self.repo.write_chunk_embeddings(
                [{"id": c.id, "embedding": v} for c, v in zip(chunks, vectors)],
                model=self.settings.embedding_model,
                dim=self.embed_dim,
            )
            _emit(0.30, f"{doc.file_name}: embeddings written")
        except Exception as e:
            log.warning("embedding failed for %s: %s", doc.file_name, e)
            _emit(0.30, f"{doc.file_name}: embedding skipped ({e})",
                  {"level": "warn", "embedding_error": str(e)})

        s = self.settings
        from .settings_service import SettingsService
        _svc = SettingsService()
        try:
            combine = max(1, int(_svc.get("chunks_to_combine") or s.chunks_to_combine))
        except Exception:
            combine = max(1, s.chunks_to_combine)
        from langchain_core.documents import Document
        windows: list[list] = []
        for i in range(0, len(chunks), combine):
            windows.append(chunks[i : i + combine])

        # extract per-window so the progress bar advances continuously over
        # potentially hundreds of LLM calls per document
        from .settings_service import SettingsService
        rs = SettingsService()
        max_retries = int(rs.get("extraction_retry_count"))
        backoff0 = float(rs.get("extraction_retry_backoff_seconds"))
        min_nodes = int(rs.get("extraction_min_nodes_for_success"))

        ent_count = 0
        rel_count = 0
        # checkpoint cadence: every N windows we mirror partial counts into
        # both Neo4j (Document.processedChunkCount + counts) and the SQLite
        # document row, so a crash/cancel mid-run leaves recoverable state.
        checkpoint_every = max(1, int(getattr(self.settings, "checkpoint_every_chunks", 5) or 5))
        last_checkpoint = 0

        def _checkpoint(processed_chunks: int) -> None:
            try:
                self.repo.checkpoint_document_progress(
                    doc.file_name,
                    processed_chunks=processed_chunks,
                    entity_count=ent_count,
                    relationship_count=rel_count,
                )
            except Exception as ex:
                log.warning("graph checkpoint failed for %s: %s", doc.file_name, ex)
            if state_doc_id is not None:
                try:
                    self.state.update_counts_progress(
                        state_doc_id,
                        chunk_count=processed_chunks,
                        entity_count=ent_count,
                        relationship_count=rel_count,
                    )
                except Exception as ex:
                    log.warning("state checkpoint failed for %s: %s", doc.file_name, ex)

        cancelled_mid = False
        processed_chunks = 0
        for w_idx, window in enumerate(windows):
            if is_cancelled():
                cancelled_mid = True
                break
            text = "\n\n".join(w.text for w in window)
            chunk_id = window[0].id
            # Carry EVERY chunk id in the window so HAS_ENTITY edges are
            # written for each chunk that contributed text — mirrors
            # llm-graph-builder's `combined_chunk_ids` metadata. With
            # chunks_to_combine=1 the list has a single element.
            combined_ids = [w.id for w in window]
            lc_docs = [Document(page_content=text,
                                metadata={"chunk_id": chunk_id,
                                          "combined_chunk_ids": combined_ids,
                                          "file_name": doc.file_name})]
            attempts = max_retries + 1
            last_err: str | None = None
            e = r = 0
            succeeded = False

            import time as _time
            for attempt in range(1, attempts + 1):
                try:
                    graph_docs, drops = self.extractor.extract(lc_docs)
                    nodes_seen = sum(len(getattr(gd, "nodes", []) or []) for gd in graph_docs)
                    # heuristic: if extractor returns nothing meaningful, treat
                    # as transient failure and retry. The LLM occasionally
                    # produces empty structured-output on flaky JSON parses.
                    if min_nodes > 0 and nodes_seen < min_nodes and attempt < attempts:
                        last_err = (
                            f"only {nodes_seen} nodes returned (< min {min_nodes})"
                        )
                        _emit(
                            0.30 + 0.65 * (w_idx + 0.5) / max(1, len(windows)),
                            f"{doc.file_name}: chunk {w_idx + 1}/{len(windows)} retry "
                            f"{attempt}/{max_retries} — {last_err}",
                            {"level": "warn", "chunk_id": chunk_id,
                             "chunk_index": w_idx + 1, "chunks_total": len(windows),
                             "attempt": attempt, "max_retries": max_retries},
                        )
                        # sleep in 1-second slices so a cancel during retry
                        # backoff is honored within ~1s rather than the full
                        # exponential delay
                        _remaining = backoff0 * (2 ** (attempt - 1))
                        while _remaining > 0 and not is_cancelled():
                            _slice = min(1.0, _remaining)
                            _time.sleep(_slice)
                            _remaining -= _slice
                        if is_cancelled():
                            break
                        continue
                    e, r = self.repo.write_graph_documents(doc.file_name, graph_docs)
                    ent_count += e
                    rel_count += r
                    succeeded = True
                    if any(drops.get(k, 0) for k in (
                        "nodes_dropped", "rels_dropped_endpoint", "rels_dropped_triplet"
                    )):
                        _emit(
                            0.30 + 0.65 * (w_idx + 0.5) / max(1, len(windows)),
                            f"{doc.file_name}: chunk {w_idx + 1}/{len(windows)} "
                            f"schema-filtered "
                            f"({drops.get('nodes_dropped', 0)} nodes, "
                            f"{drops.get('rels_dropped_endpoint', 0)}+"
                            f"{drops.get('rels_dropped_triplet', 0)} rels)",
                            {"level": "info", "chunk_id": chunk_id,
                             "chunk_index": w_idx + 1, "chunks_total": len(windows),
                             **drops},
                        )
                    break
                except Exception as ex:
                    last_err = f"{type(ex).__name__}: {ex}"
                    log.warning("chunk %s attempt %d/%d failed: %s",
                                chunk_id, attempt, attempts, last_err)
                    if attempt < attempts:
                        _emit(
                            0.30 + 0.65 * (w_idx + 0.5) / max(1, len(windows)),
                            f"{doc.file_name}: chunk {w_idx + 1}/{len(windows)} retry "
                            f"{attempt}/{max_retries} — {last_err}",
                            {"level": "warn", "chunk_id": chunk_id,
                             "chunk_index": w_idx + 1, "chunks_total": len(windows),
                             "attempt": attempt, "max_retries": max_retries,
                             "error": last_err},
                        )
                        # sleep in 1-second slices so a cancel during retry
                        # backoff is honored within ~1s rather than the full
                        # exponential delay
                        _remaining = backoff0 * (2 ** (attempt - 1))
                        while _remaining > 0 and not is_cancelled():
                            _slice = min(1.0, _remaining)
                            _time.sleep(_slice)
                            _remaining -= _slice
                        if is_cancelled():
                            break
                    else:
                        _emit(
                            0.30 + 0.65 * (w_idx + 1) / max(1, len(windows)),
                            f"{doc.file_name}: chunk {w_idx + 1}/{len(windows)} "
                            f"FAILED after {attempts} attempts — {last_err}",
                            {"level": "error", "chunk_id": chunk_id,
                             "chunk_index": w_idx + 1, "chunks_total": len(windows),
                             "error": last_err, "attempts": attempts},
                        )

            if not succeeded:
                continue

            _emit(
                0.30 + 0.65 * (w_idx + 1) / max(1, len(windows)),
                f"{doc.file_name}: chunk {w_idx + 1}/{len(windows)} → +{e} entities, +{r} rels"
                + (f" (after retries)" if attempts > 1 and last_err else ""),
                {"chunk_id": chunk_id, "chunk_index": w_idx + 1,
                 "chunks_total": len(windows), "entities": e, "relationships": r},
            )

            processed = w_idx + 1
            processed_chunks = processed
            if processed - last_checkpoint >= checkpoint_every or processed == len(windows):
                _checkpoint(processed)
                last_checkpoint = processed

        if cancelled_mid:
            # final checkpoint of what we managed to extract before bailing
            _checkpoint(processed_chunks)
            return {"chunks": processed_chunks, "entities": ent_count,
                    "relationships": rel_count, "cancelled": True}

        self.repo.set_document_status(
            doc.file_name,
            "Completed",
            chunk_count=len(chunks),
            entity_count=ent_count,
            relationship_count=rel_count,
        )
        return {"chunks": len(chunks), "entities": ent_count, "relationships": rel_count}
