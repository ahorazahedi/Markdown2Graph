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
    repo = GraphRepository()
    return jsonify(repo.explore(limit_nodes=limit, file_name=file_name, label=label))
