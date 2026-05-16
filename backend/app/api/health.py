from flask import Blueprint, jsonify

from ..extensions import neo4j_manager

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "neo4j": "up" if neo4j_manager.verify() else "down",
        }
    )
