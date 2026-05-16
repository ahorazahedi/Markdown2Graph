"""Schema endpoints.

GET  /api/schema             -> active schema (loaded at startup; survives restart)
PUT  /api/schema             -> save new schema (also appended to schema_versions)
GET  /api/schema/versions    -> recent version list
GET  /api/schema/versions/<id>
POST /api/schema/discover    -> ask the LLM for a starting point from a folder
                                of markdown files (does NOT save — frontend posts
                                back via PUT to commit)
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from ..errors import NotFoundError, ValidationError
from ..repositories.app_state_repository import AppStateRepository
from ..services.markdown_loader import MarkdownLoader
from ..services.schema_discovery import SchemaDiscoveryService

bp = Blueprint("schema", __name__)


@bp.get("/schema")
def get_schema():
    return jsonify(AppStateRepository().get_schema())


@bp.put("/schema")
def save_schema():
    data = request.get_json(silent=True) or {}
    nodes = data.get("node_labels") or []
    triplets = data.get("triplets") or []
    extra = (data.get("extra") or "").strip()
    if not isinstance(nodes, list) or not all(isinstance(n, str) for n in nodes):
        raise ValidationError("node_labels must be a list of strings")
    normalized_triplets: list[list[str]] = []
    for t in triplets:
        if isinstance(t, (list, tuple)) and len(t) == 3 and all(isinstance(x, str) for x in t):
            normalized_triplets.append([t[0].strip(), t[1].strip(), t[2].strip()])
        elif isinstance(t, str):
            # accept "Source-REL->Target" string form too
            try:
                src, rest = t.split("-", 1)
                rel, dst = rest.split("->", 1)
                normalized_triplets.append([src.strip(), rel.strip(), dst.strip()])
            except ValueError:
                raise ValidationError(f"invalid triplet string: {t!r}")
        else:
            raise ValidationError(f"invalid triplet: {t!r}")
    saved = AppStateRepository().save_schema(
        node_labels=[n.strip() for n in nodes if n.strip()],
        triplets=normalized_triplets,
        extra=extra,
        source=data.get("source", "manual"),
    )
    return jsonify(saved)


@bp.get("/schema/versions")
def list_versions():
    return jsonify({"versions": AppStateRepository().list_schema_versions()})


@bp.get("/schema/versions/<int:vid>")
def get_version(vid: int):
    v = AppStateRepository().get_schema_version(vid)
    if not v:
        raise NotFoundError(f"version {vid} not found")
    return jsonify(v)


@bp.post("/schema/discover")
def discover():
    """Ask the LLM to suggest a starting schema.

    Body:
      { "path": "/abs/folder"  OR  "document_ids": [1,2,3],
        "sample_size": int (optional),
        "extra_instructions": str (optional) }
    """
    data = request.get_json(silent=True) or {}
    files: list[Path] = []

    if data.get("document_ids"):
        state = AppStateRepository()
        for did in data["document_ids"]:
            d = state.get_document(int(did))
            if d:
                p = Path(d["source_path"])
                if p.exists():
                    files.append(p)
    elif data.get("path"):
        p = Path(data["path"]).expanduser().resolve()
        if not p.exists() or not p.is_dir():
            raise ValidationError(f"path does not exist: {p}")
        files = MarkdownLoader(p).list_files()
    else:
        # default: discover from every registered document
        state = AppStateRepository()
        for d in state.list_documents():
            p = Path(d["source_path"])
            if p.exists():
                files.append(p)

    if not files:
        raise ValidationError("no markdown files available for discovery")

    result = SchemaDiscoveryService().discover(
        files=files,
        sample_size=int(data.get("sample_size") or 0) or None,
        extra_instructions=data.get("extra_instructions"),
    )
    return jsonify(
        {
            "file_count": len(files),
            **result,
        }
    )
