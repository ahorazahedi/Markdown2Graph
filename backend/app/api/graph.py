from __future__ import annotations

from flask import Blueprint, jsonify

from ..repositories.graph_repository import GraphRepository

bp = Blueprint("graph", __name__)


@bp.get("/graph/stats")
def stats():
    repo = GraphRepository()
    return jsonify(repo.stats())


@bp.get("/graph/schema")
def schema():
    repo = GraphRepository()
    return jsonify(repo.schema())


@bp.delete("/graph")
def clear():
    repo = GraphRepository()
    repo.clear_all()
    return jsonify({"status": "ok", "cleared": True})


@bp.get("/graph/documents")
def documents():
    repo = GraphRepository()
    return jsonify({"documents": repo.list_documents()})


@bp.get("/graph/explore")
def explore():
    from flask import request
    limit = int(request.args.get("limit", 200))
    limit = max(10, min(limit, 1000))
    file_name = request.args.get("file_name") or None
    label = request.args.get("label") or None
    include_structure = request.args.get("include_structure", "false").lower() in ("1", "true", "yes")
    include_communities = request.args.get("include_communities", "false").lower() in ("1", "true", "yes")
    repo = GraphRepository()
    return jsonify(repo.explore(
        limit_nodes=limit, file_name=file_name, label=label,
        include_structure=include_structure,
        include_communities=include_communities,
    ))


@bp.get("/graph/neighborhood")
def neighborhood():
    """Return the k-hop induced subgraph around a single node.

    Query params:
      element_id  (required) — Neo4j elementId of the focal node
      depth       (default 1, clamped 1..4)
      limit       (default 200, clamped 10..1000) — soft cap on returned nodes
      include_structure (default false) — also include Document/Chunk nodes
                                          touching any returned entity
    """
    from flask import request
    element_id = request.args.get("element_id")
    if not element_id:
        return jsonify({"error": "element_id required"}), 400
    depth = int(request.args.get("depth", 1))
    depth = max(1, min(depth, 4))
    limit = int(request.args.get("limit", 200))
    limit = max(10, min(limit, 1000))
    include_structure = request.args.get("include_structure", "false").lower() in ("1", "true", "yes")
    include_communities = request.args.get("include_communities", "false").lower() in ("1", "true", "yes")
    repo = GraphRepository()
    return jsonify(repo.neighborhood(
        element_id=element_id, depth=depth, limit_nodes=limit,
        include_structure=include_structure,
        include_communities=include_communities,
    ))


@bp.post("/graph/post-process")
def post_process():
    """Submit a post-processing job (async). Returns ``{job_id}``.

    Body (all optional):
        {
          "cleanup":         bool (default true),
          "dedup":           bool (default false),
          "orphans":         bool (default false),
          "communities":     bool (default true),
          "summaries":       bool (default true),
          "chunk_embeddings":     bool (default false) — backfill missing
                                  Chunk.embedding values (no LLM cost),
          "entity_embeddings":    bool (default true),
          "community_embeddings": bool (default true),
          "community_levels": int (default 2)
        }

    409 Conflict if another post_process job is already running.
    Progress and cancellation flow through the standard /api/jobs API.
    """
    from flask import request
    from ..repositories.app_state_repository import AppStateRepository
    from ..services.job_registry import job_registry
    from ..services.post_processing import PostProcessingService
    body = request.get_json(silent=True) or {}

    # single-flight guard — racing two community rebuilds corrupts state
    state = AppStateRepository()
    for st in ("running", "queued", "cancelling"):
        existing = [r for r in state.list_runs(status=st, kind="post_process", limit=10)]
        if existing:
            return jsonify({
                "error": "post-process job already in progress",
                "job_id": existing[0]["id"],
                "status": existing[0]["status"],
            }), 409

    opts = {
        "cleanup": bool(body.get("cleanup", True)),
        "dedup": bool(body.get("dedup", False)),
        "orphans": bool(body.get("orphans", False)),
        "communities": bool(body.get("communities", True)),
        "summaries": bool(body.get("summaries", True)),
        "chunk_embeddings": bool(body.get("chunk_embeddings", False)),
        "entity_embeddings": bool(body.get("entity_embeddings", True)),
        "community_embeddings": bool(body.get("community_embeddings", True)),
        "community_levels": int(body.get("community_levels", 2) or 2),
    }

    def runner(update, cancelled):
        from ..services.job_registry import JobUpdate
        svc = PostProcessingService()

        def emit(msg: str, prog: float, _extra=None):
            update(JobUpdate(stage="post_process", message=msg, progress=prog))

        rep = svc.run(emit=emit, is_cancelled=cancelled, **opts)
        return {
            "cleanup": rep.cleanup,
            "dedup": rep.dedup,
            "orphans": rep.orphans,
            "communities": rep.communities,
            "chunk_embeddings": rep.chunk_embeddings,
            "entity_embeddings": rep.entity_embeddings,
            "community_embeddings": rep.community_embeddings,
            "errors": rep.errors,
            "elapsed_seconds": rep.elapsed_seconds,
        }

    job_id = job_registry.submit(runner, kind="post_process", scope=opts)
    return jsonify({"job_id": job_id, "options": opts})


@bp.get("/graph/duplicates")
def list_duplicates():
    from flask import request
    repo = GraphRepository()
    limit = max(1, min(int(request.args.get("limit", 50)), 500))
    min_size = max(2, int(request.args.get("min_size", 2)))
    return jsonify({"groups": repo.list_duplicate_entities(
        limit_groups=limit, min_group_size=min_size,
    )})


@bp.post("/graph/duplicates/merge")
def merge_duplicates():
    """Body: { "groups": [{"canonical_element_id": "...", "alias_element_ids": ["..."]}, ...] }"""
    from flask import request
    from ..errors import ValidationError
    body = request.get_json(silent=True) or {}
    groups = body.get("groups") or []
    if not isinstance(groups, list):
        raise ValidationError("groups must be a list")
    repo = GraphRepository()
    out = []
    for g in groups:
        canon = g.get("canonical_element_id")
        aliases = g.get("alias_element_ids") or []
        if not canon or not isinstance(aliases, list):
            raise ValidationError("each group needs canonical_element_id + alias_element_ids[]")
        out.append({
            "canonical_element_id": canon,
            **repo.merge_entities(canon, [a for a in aliases if a and a != canon]),
        })
    return jsonify({"merged_groups": out})


@bp.get("/graph/orphans")
def list_orphans():
    from flask import request
    repo = GraphRepository()
    limit = max(1, min(int(request.args.get("limit", 500)), 2000))
    return jsonify({"orphans": repo.list_orphan_entities(limit=limit)})


@bp.delete("/graph/orphans")
def delete_orphans():
    from flask import request
    body = request.get_json(silent=True) or {}
    repo = GraphRepository()
    ids = body.get("element_ids") if isinstance(body, dict) else None
    deleted = repo.delete_orphan_entities(element_ids=ids)
    return jsonify({"deleted": deleted})
