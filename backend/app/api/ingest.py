from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from ..errors import NotFoundError, ValidationError
from ..services.job_registry import job_registry
from ..services.markdown_loader import MarkdownLoader
from ..services.pipeline import IngestionPipeline, PipelineConfig

bp = Blueprint("ingest", __name__)


@bp.post("/ingest")
def start_ingest():
    """Body:
       { "path": "/abs/path",
         "allowed_nodes": ["Disease","Drug",...],
         "allowed_relationships": [["Drug","TREATS","Disease"], ...],
         "extra_instructions": "..."  (optional) }
    """
    data = request.get_json(silent=True) or {}
    folder = data.get("path")
    if not folder:
        raise ValidationError("'path' is required")
    p = Path(folder).expanduser().resolve()
    if not p.is_dir():
        raise ValidationError(f"not a directory: {p}")

    files = MarkdownLoader(p).list_files()
    if not files:
        raise ValidationError(f"no .md files in {p}")

    cfg = PipelineConfig(
        allowed_nodes=data.get("allowed_nodes") or [],
        allowed_relationships=[tuple(r) for r in (data.get("allowed_relationships") or [])],
        extra_instructions=data.get("extra_instructions"),
    )
    pipeline = IngestionPipeline(cfg)
    job_id = job_registry.submit(lambda update: pipeline.run(files, progress=update))
    return jsonify({"job_id": job_id, "file_count": len(files)})


@bp.get("/ingest/<job_id>")
def status(job_id: str):
    job = job_registry.get(job_id)
    if not job:
        raise NotFoundError(f"job {job_id} not found")
    return jsonify(job.snapshot())
