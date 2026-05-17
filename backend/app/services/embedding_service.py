"""Embedding lifecycle management — re-embed, switch model, status.

Sits above `GraphRepository` and the `build_embedder` factory. The
existing `PostProcessingService.embed_chunks/embed_entities/embed_communities`
methods only backfill nodes whose embedding is NULL. This service adds:

- `status()` — counts + by-model breakdown across chunk/entity/community
- `reembed()` — scope-aware re-embed (missing | stale | all) with optional
  vector-index recreate when dimension changes
- `switch_model()` — persist new embedding settings + kick off a stale
  re-embed across all node types
- `clear()` — destructive; null out embeddings (scoped)

All work runs in batches and is cancellable via the standard job-runner
cancel flag. Vector indexes are dropped + recreated when the embedding
dimension changes; otherwise they are recreated idempotently.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Iterable

from ..repositories.graph_repository import GraphRepository
from ..repositories.settings_repository import SettingsRepository
from .job_registry import JobCancelled, JobUpdate

log = logging.getLogger(__name__)

NODE_TYPES = ("chunk", "entity", "community")
SCOPES = ("missing", "stale", "all")


class EmbeddingService:
    def __init__(self, repo: GraphRepository | None = None):
        self.repo = repo or GraphRepository()

    # ---------- status ----------
    def status(self) -> dict:
        """Snapshot embedding state for every node type + current model."""
        from ..config import get_settings

        s = get_settings()
        out: dict = {
            "current_model": s.embedding_model,
            "current_dim": int(s.embedding_dimension),
            "provider": s.embedding_provider,
            "types": {},
        }
        for nt in NODE_TYPES:
            try:
                stat = self.repo.count_embeddings(nt)
            except Exception as e:
                stat = {"error": str(e)}
            # detect stale (embedded under a different model than current)
            by_model = stat.get("by_model", {}) if isinstance(stat, dict) else {}
            stale = sum(v for k, v in by_model.items() if k != s.embedding_model)
            stat["stale"] = stale
            # index dim, if any
            idx = self.repo._target(nt)["index"]
            try:
                stat["index_dim"] = self.repo.vector_index_dim(idx)
            except Exception:
                stat["index_dim"] = None
            out["types"][nt] = stat
        return out

    # ---------- core re-embed ----------
    def reembed(self, *,
                scope: str = "missing",
                types: Iterable[str] = NODE_TYPES,
                model: str | None = None,
                dim: int | None = None,
                provider: str | None = None,
                clear_first: bool = False,
                update: Callable[[JobUpdate], None] | None = None,
                is_cancelled: Callable[[], bool] | None = None) -> dict:
        """Re-embed nodes in scope.

        - `scope='missing'`: only nodes where embedding IS NULL
        - `scope='stale'`: missing OR model/dim mismatch vs `model`/`dim`
          (defaults to current settings)
        - `scope='all'`: every node (forces full re-embed)
        - `clear_first=True`: NULL out embeddings before re-embedding (use
          when changing dimensions to keep the vector index consistent).

        Returns a per-type report with counts.
        """
        from ..config import get_settings
        from ..llm import build_embedder

        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}")
        types = [t for t in types if t in NODE_TYPES]
        if not types:
            raise ValueError("no valid node types selected")

        s = get_settings()
        target_model = model or s.embedding_model
        target_dim = int(dim) if dim is not None else int(s.embedding_dimension)
        embedder, build_dim = build_embedder(s)
        if build_dim != target_dim:
            log.warning("build_embedder dim=%d differs from target_dim=%d — "
                        "using build_dim", build_dim, target_dim)
            target_dim = build_dim
        batch_n = max(8, int(s.entity_embedding_batch))

        def _notify(stage: str, msg: str, progress: float, extra: dict | None = None):
            if update:
                try:
                    update(JobUpdate(stage=stage, message=msg,
                                     progress=progress, extra=extra or {}))
                except Exception:
                    pass

        def _check_cancel():
            if is_cancelled and is_cancelled():
                raise JobCancelled("reembed cancelled")

        report: dict = {
            "scope": scope, "model": target_model, "dim": target_dim,
            "types": {},
        }
        n_types = len(types)
        for ti, nt in enumerate(types):
            _check_cancel()
            type_progress = ti / max(1, n_types)
            _notify(f"reembed:{nt}", f"{nt}: starting", type_progress)
            type_report: dict = {"cleared": 0, "embedded": 0,
                                  "skipped": 0, "errors": []}

            # Optional: clear before re-embedding. Useful when dimensions
            # change so the index can be recreated cleanly.
            if clear_first or scope == "all":
                try:
                    cleared = self.repo.clear_embeddings(nt)
                    type_report["cleared"] = cleared
                    _notify(f"reembed:{nt}",
                            f"{nt}: cleared {cleared} embeddings",
                            type_progress)
                except Exception as e:
                    log.exception("clear_embeddings %s failed", nt)
                    type_report["errors"].append(f"clear: {e}")

            # Drop the vector index if it exists with a different dim.
            idx_name = self.repo._target(nt)["index"]
            try:
                current_dim = self.repo.vector_index_dim(idx_name)
            except Exception:
                current_dim = None
            if current_dim is not None and current_dim != target_dim:
                _notify(f"reembed:{nt}",
                        f"{nt}: vector index dim {current_dim}→{target_dim}, "
                        "dropping",
                        type_progress)
                self.repo.drop_vector_index(idx_name)

            # Listing scope:
            #  - 'all' after a clear becomes 'missing'
            #  - 'all' without a clear becomes a forced relist of every node
            list_scope = "missing" if (clear_first or scope == "all") else scope
            try:
                pending = self.repo.list_nodes_for_embedding(
                    nt, scope=list_scope,
                    target_model=target_model, target_dim=target_dim,
                    limit=200_000,
                )
            except Exception as e:
                log.exception("list nodes %s failed", nt)
                type_report["errors"].append(f"list: {e}")
                report["types"][nt] = type_report
                continue

            if not pending:
                # still (re)create the index so chat retrieval finds it
                try:
                    self.repo.recreate_vector_index(nt, target_dim) \
                        if current_dim is not None and current_dim != target_dim \
                        else self._ensure_index(nt, target_dim)
                except Exception as e:
                    type_report["errors"].append(f"index: {e}")
                type_report["skipped"] = 1
                _notify(f"reembed:{nt}",
                        f"{nt}: nothing to embed", type_progress)
                report["types"][nt] = type_report
                continue

            total = len(pending)
            done = 0
            for i in range(0, total, batch_n):
                _check_cancel()
                batch = pending[i:i + batch_n]
                texts = [self._truncate(nt, r.get("text") or " ") for r in batch]
                try:
                    vectors = embedder.embed_documents(texts)
                except Exception as e:
                    log.warning("%s batch embed failed (%d-%d): %s",
                                nt, i, i + len(batch), e)
                    type_report["errors"].append(
                        f"batch {i}-{i + len(batch)}: {e}"
                    )
                    continue
                try:
                    self.repo.write_embeddings_unified(
                        nt,
                        [{"id": r["id"], "embedding": v}
                         for r, v in zip(batch, vectors)],
                        model=target_model, dim=target_dim,
                    )
                except Exception as e:
                    log.exception("write %s embeddings failed", nt)
                    type_report["errors"].append(f"write: {e}")
                    continue
                done += len(batch)
                type_report["embedded"] = done
                inner = (i + len(batch)) / total
                p = type_progress + (inner / max(1, n_types))
                _notify(f"reembed:{nt}",
                        f"{nt}: {done}/{total}", min(0.99, p),
                        {"done": done, "total": total})

            # Recreate index at the end so it picks up the latest dim.
            try:
                self._ensure_index(nt, target_dim)
            except Exception as e:
                type_report["errors"].append(f"index: {e}")
            report["types"][nt] = type_report

        _notify("reembed:done", "re-embedding complete", 1.0, report)
        return report

    # ---------- switch model ----------
    def switch_model(self, *, model: str, dim: int,
                     provider: str | None = None,
                     update: Callable[[JobUpdate], None] | None = None,
                     is_cancelled: Callable[[], bool] | None = None,
                     types: Iterable[str] = NODE_TYPES) -> dict:
        """Persist new embedding config + re-embed every node.

        Steps:
          1. Save model/dim/provider as runtime overrides (SettingsRepository).
          2. `reload_settings()` so `build_embedder` picks up the new model.
          3. `reembed(scope='all', clear_first=True)` so old vectors are
             nulled out before the index is recreated with the new dim.
        """
        from ..config import reload_settings

        updates: dict = {"embedding_model": model, "embedding_dimension": str(dim)}
        if provider:
            updates["embedding_provider"] = provider
        SettingsRepository().save(updates)
        reload_settings()

        return self.reembed(
            scope="all",
            types=tuple(types),
            model=model,
            dim=dim,
            provider=provider,
            clear_first=True,
            update=update,
            is_cancelled=is_cancelled,
        )

    # ---------- clear ----------
    def clear(self, *, node_types: Iterable[str] = NODE_TYPES,
              where_model: str | None = None) -> dict:
        """Null out embeddings (scoped). Does NOT drop the vector index."""
        out: dict = {}
        for nt in node_types:
            if nt not in NODE_TYPES:
                continue
            try:
                out[nt] = self.repo.clear_embeddings(
                    nt, where_model=where_model
                )
            except Exception as e:
                out[nt] = f"error: {e}"
        return out

    # ---------- helpers ----------
    @staticmethod
    def _truncate(node_type: str, text: str) -> str:
        # mirrors limits used by PostProcessingService backfills
        if node_type == "chunk":
            return text[:8000]
        if node_type == "entity":
            return text[:2000]
        return text[:4000]

    def _ensure_index(self, node_type: str, dim: int) -> None:
        if node_type == "chunk":
            self.repo.create_chunk_vector_index(dim)
        elif node_type == "entity":
            self.repo.create_entity_vector_index(dim)
        elif node_type == "community":
            self.repo.create_community_vector_index(dim)
