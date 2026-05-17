"""Embedding management endpoints.

Surface the EmbeddingService over HTTP so the frontend can:
- show counts + by-model breakdown per node type
- run a re-embedding job (scope = missing | stale | all)
- switch the embedding model (persists settings + clears + re-embeds all)
- clear embeddings (destructive)

All long-running ops are submitted to `job_registry` and surface the same
job-id contract the Logs page already understands, so cancel/progress/SSE
just work.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..errors import ValidationError
from ..services.embedding_service import (
    EmbeddingService,
    NODE_TYPES,
    SCOPES,
)
from ..services.job_registry import job_registry

bp = Blueprint("embeddings", __name__)


def _parse_types(raw) -> list[str]:
    if raw is None:
        return list(NODE_TYPES)
    if isinstance(raw, str):
        raw = [t.strip() for t in raw.split(",") if t.strip()]
    if not isinstance(raw, list):
        raise ValidationError("`types` must be a list or comma-separated string")
    bad = [t for t in raw if t not in NODE_TYPES]
    if bad:
        raise ValidationError(
            f"unknown node types: {bad}. expected subset of {list(NODE_TYPES)}"
        )
    return raw or list(NODE_TYPES)


@bp.get("/embeddings/status")
def status():
    return jsonify(EmbeddingService().status())


@bp.post("/embeddings/reembed")
def reembed():
    """Body:
       { "scope": "missing" | "stale" | "all",
         "types": ["chunk","entity","community"]   (optional),
         "model": "..."                            (optional override),
         "dim":   3072                              (optional override),
         "clear_first": bool                        (optional) }
    """
    data = request.get_json(silent=True) or {}
    scope = (data.get("scope") or "missing").strip().lower()
    if scope not in SCOPES:
        raise ValidationError(f"scope must be one of {list(SCOPES)}")
    types = _parse_types(data.get("types"))
    model = data.get("model")
    dim = data.get("dim")
    clear_first = bool(data.get("clear_first", scope == "all"))

    svc = EmbeddingService()

    def runner(update, cancelled):
        return svc.reembed(
            scope=scope, types=types, model=model,
            dim=int(dim) if dim is not None else None,
            clear_first=clear_first,
            update=update, is_cancelled=cancelled,
        )

    jid = job_registry.submit(
        runner,
        scope={"scope": scope, "types": types, "model": model, "dim": dim,
               "clear_first": clear_first},
        kind="reembed",
    )
    return jsonify({"job_id": jid, "scope": scope, "types": types,
                    "clear_first": clear_first})


@bp.post("/embeddings/switch-model")
def switch_model():
    """Body:
       { "model":    "openai/text-embedding-3-large",
         "dim":      3072,
         "provider": "openrouter"   (optional) }

    Persists the new settings, drops the existing vector indexes, clears
    all embeddings, then re-embeds every chunk/entity/community.
    """
    data = request.get_json(silent=True) or {}
    model = (data.get("model") or "").strip()
    if not model:
        raise ValidationError("`model` is required")
    try:
        dim = int(data.get("dim"))
    except (TypeError, ValueError):
        raise ValidationError("`dim` must be an integer (embedding dimension)")
    provider = (data.get("provider") or "").strip() or None
    types = _parse_types(data.get("types"))

    svc = EmbeddingService()

    def runner(update, cancelled):
        return svc.switch_model(
            model=model, dim=dim, provider=provider,
            types=types,
            update=update, is_cancelled=cancelled,
        )

    jid = job_registry.submit(
        runner,
        scope={"model": model, "dim": dim, "provider": provider,
               "types": types},
        kind="switch_embedding_model",
    )
    return jsonify({"job_id": jid, "model": model, "dim": dim,
                    "provider": provider})


@bp.delete("/embeddings")
def clear():
    """Destructive — null out embeddings.
    Query/body:
       - types=chunk,entity,community  (optional)
       - where_model=...               (optional; clear only this model's vectors)
       - confirm=true                  (required)
    """
    data = request.get_json(silent=True) or {}
    if not (data.get("confirm") is True
            or request.args.get("confirm") == "true"):
        raise ValidationError(
            "destructive operation — pass {\"confirm\": true} in the body"
        )
    types = _parse_types(data.get("types") or request.args.get("types"))
    where_model = data.get("where_model") or request.args.get("where_model")
    cleared = EmbeddingService().clear(node_types=types, where_model=where_model)
    return jsonify({"cleared": cleared})
