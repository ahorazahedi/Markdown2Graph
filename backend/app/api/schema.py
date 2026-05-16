from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from ..errors import ValidationError
from ..services.markdown_loader import MarkdownLoader
from ..services.schema_discovery import SchemaDiscoveryService

bp = Blueprint("schema", __name__)


@bp.post("/schema/discover")
def discover():
    """Body: { "path": "/abs/path/to/folder", "sample_size": 5 (optional),
                "extra_instructions": "optional string" }
    Returns proposed node labels + relationship triplets for user review."""
    data = request.get_json(silent=True) or {}
    folder = data.get("path")
    if not folder:
        raise ValidationError("'path' is required (folder containing .md files)")
    p = Path(folder).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise ValidationError(f"path does not exist or is not a directory: {p}")

    loader = MarkdownLoader(p)
    files = loader.list_files()
    if not files:
        raise ValidationError(f"no .md files in {p}")

    svc = SchemaDiscoveryService()
    result = svc.discover(
        files=files,
        sample_size=int(data.get("sample_size") or 0) or None,
        extra_instructions=data.get("extra_instructions"),
    )
    return jsonify(
        {
            "path": str(p),
            "file_count": len(files),
            **result,
        }
    )
