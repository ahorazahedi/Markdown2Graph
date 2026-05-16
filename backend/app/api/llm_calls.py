from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..errors import NotFoundError
from ..repositories.llm_call_repository import LLMCallRepository

bp = Blueprint("llm_calls", __name__)


@bp.get("/llm-calls")
def list_calls():
    repo = LLMCallRepository()
    tag = request.args.get("tag") or None
    status = request.args.get("status") or None
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 500))
    except ValueError:
        limit = 50
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0

    items = repo.list(tag=tag, status=status, limit=limit, offset=offset)
    return jsonify(
        {
            "items": items,
            "total": repo.count(tag=tag, status=status),
            "limit": limit,
            "offset": offset,
        }
    )


@bp.get("/llm-calls/tags")
def tags():
    repo = LLMCallRepository()
    return jsonify({"tags": repo.distinct_tags()})


@bp.get("/llm-calls/stats")
def stats():
    return jsonify(LLMCallRepository().stats())


@bp.get("/llm-calls/<int:call_id>")
def get_call(call_id: int):
    repo = LLMCallRepository()
    item = repo.get(call_id)
    if not item:
        raise NotFoundError(f"call {call_id} not found")
    return jsonify(item)


@bp.delete("/llm-calls")
def clear():
    n = LLMCallRepository().clear()
    return jsonify({"deleted": n})
