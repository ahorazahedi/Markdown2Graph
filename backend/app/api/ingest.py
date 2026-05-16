from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..errors import NotFoundError, ValidationError
from ..repositories.app_state_repository import AppStateRepository
from ..services.job_registry import job_registry
from ..services.pipeline import IngestionPipeline, PipelineConfig

bp = Blueprint("ingest", __name__)


@bp.post("/ingest/run")
def run_ingest():
    """Body:
       { "document_ids": [...]   (optional; default = all pending/failed),
         "reextract":   bool      (optional; default false) }

    Schema is loaded from app state — no need to pass it.
    """
    data = request.get_json(silent=True) or {}
    state = AppStateRepository()
    schema = state.get_schema()
    if not schema.get("node_labels"):
        raise ValidationError("no schema configured — save one via PUT /api/schema first")

    cfg = PipelineConfig(
        allowed_nodes=schema["node_labels"],
        allowed_relationships=[tuple(t) for t in schema["triplets"]],
        extra_instructions=schema.get("extra") or None,
    )
    pipeline = IngestionPipeline(cfg)

    doc_ids = data.get("document_ids")
    reextract = bool(data.get("reextract", False))

    if doc_ids:
        ids = [int(x) for x in doc_ids]
        for did in ids:
            if not state.get_document(did):
                raise ValidationError(f"document {did} not found")

        def runner(update, cancelled):
            return pipeline.run_documents(ids, reextract=reextract,
                                          progress=update, is_cancelled=cancelled)
    else:
        def runner(update, cancelled):
            return pipeline.run_pending(progress=update, is_cancelled=cancelled)

    job_id = job_registry.submit(runner)
    return jsonify({"job_id": job_id, "document_ids": doc_ids or "all-pending",
                    "reextract": reextract})


@bp.get("/ingest/<job_id>")
def status(job_id: str):
    job = job_registry.get(job_id)
    if not job:
        raise NotFoundError(f"job {job_id} not found")
    return jsonify(job.snapshot())
