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
