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
    """Run cleanup, dedup, orphan sweep, community detection.

    Body (all optional):
        {
          "cleanup":         bool (default true),
          "dedup":           bool (default false),
          "orphans":         bool (default false),
          "communities":     bool (default true),
          "summaries":       bool (default true),
          "community_levels": int (default 2)
        }
    """
    from flask import request
    from ..services.post_processing import PostProcessingService
    body = request.get_json(silent=True) or {}
    svc = PostProcessingService()
    rep = svc.run(
        cleanup=bool(body.get("cleanup", True)),
        dedup=bool(body.get("dedup", False)),
        orphans=bool(body.get("orphans", False)),
        communities=bool(body.get("communities", True)),
        summaries=bool(body.get("summaries", True)),
        entity_embeddings=bool(body.get("entity_embeddings", True)),
        community_embeddings=bool(body.get("community_embeddings", True)),
        community_levels=int(body.get("community_levels", 2) or 2),
    )
    return jsonify({
        "cleanup": rep.cleanup,
        "dedup": rep.dedup,
        "orphans": rep.orphans,
        "communities": rep.communities,
        "entity_embeddings": rep.entity_embeddings,
        "community_embeddings": rep.community_embeddings,
        "errors": rep.errors,
        "elapsed_seconds": rep.elapsed_seconds,
    })


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
