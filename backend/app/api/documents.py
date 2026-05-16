"""Documents endpoints — the registry of every uploaded markdown file."""
from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from ..errors import NotFoundError, ValidationError
from ..repositories.app_state_repository import AppStateRepository
from ..repositories.graph_repository import GraphRepository
from ..services.markdown_loader import MarkdownLoader

bp = Blueprint("documents", __name__)
log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
UPLOAD_ROOT = _REPO_ROOT / "backend" / "data" / "uploads"
ALLOWED_SUFFIX = {".md", ".markdown"}
MAX_FILES = 5000
MAX_TOTAL_BYTES = 500 * 1024 * 1024  # 500 MB


def _safe_relpath(raw: str) -> Path:
    raw = (raw or "").strip().replace("\\", "/").lstrip("/")
    parts: list[str] = []
    for seg in raw.split("/"):
        if not seg or seg == ".":
            continue
        if seg == "..":
            raise ValidationError(f"illegal path segment: {raw}")
        cleaned = secure_filename(seg)
        if not cleaned:
            raise ValidationError(f"invalid path segment: {seg!r}")
        parts.append(cleaned)
    if not parts:
        raise ValidationError("empty relative path")
    return Path(*parts)


def _unique_name(file_name: str, state: AppStateRepository) -> str:
    """Avoid collisions when two uploads share the same basename."""
    base = file_name
    if not state.get_document_by_name(base):
        return base
    stem = Path(base).stem
    suffix = Path(base).suffix
    for i in range(1, 1000):
        candidate = f"{stem}__{i}{suffix}"
        if not state.get_document_by_name(candidate):
            return candidate
    return f"{stem}__{uuid.uuid4().hex[:8]}{suffix}"


# ----------------------------- list / detail -----------------------------
@bp.get("/documents")
def list_docs():
    state = AppStateRepository()
    return jsonify({"items": state.list_documents(), "stats": state.stats()})


@bp.get("/documents/<int:doc_id>")
def get_doc(doc_id: int):
    d = AppStateRepository().get_document(doc_id)
    if not d:
        raise NotFoundError(f"document {doc_id} not found")
    return jsonify(d)


@bp.get("/documents/<int:doc_id>/entities")
def doc_entities(doc_id: int):
    d = AppStateRepository().get_document(doc_id)
    if not d:
        raise NotFoundError(f"document {doc_id} not found")
    repo = GraphRepository()
    return jsonify(repo.list_document_entities(d["file_name"]))


@bp.get("/documents/<int:doc_id>/content")
def doc_content(doc_id: int):
    d = AppStateRepository().get_document(doc_id)
    if not d:
        raise NotFoundError(f"document {doc_id} not found")
    p = Path(d["source_path"])
    if not p.exists():
        raise NotFoundError(f"source file missing on disk: {p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    return jsonify({
        "file_name": d["file_name"],
        "title": d["title"],
        "size_bytes": d["size_bytes"],
        "content": text,
    })


@bp.get("/documents/<int:doc_id>/chunks")
def doc_chunks(doc_id: int):
    d = AppStateRepository().get_document(doc_id)
    if not d:
        raise NotFoundError(f"document {doc_id} not found")
    repo = GraphRepository()
    return jsonify({"chunks": repo.document_chunks(d["file_name"])})


# ----------------------------- upload -----------------------------
@bp.post("/documents/upload")
def upload():
    """Stage files under data/uploads/<uuid>/ and register them in the
    documents registry. Replaces the older /api/upload endpoint."""
    files = request.files.getlist("files")
    rel_paths = request.form.getlist("paths")
    if not files:
        raise ValidationError("no files in request")
    if len(files) > MAX_FILES:
        raise ValidationError(f"too many files (>{MAX_FILES})")
    if rel_paths and len(rel_paths) != len(files):
        raise ValidationError("paths[] length must match files[] length")

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    staging = UPLOAD_ROOT / uuid.uuid4().hex
    staging.mkdir(parents=True)

    state = AppStateRepository()
    loader = MarkdownLoader(staging)
    total_bytes = 0
    created: list[dict] = []
    skipped: list[dict] = []

    try:
        for i, fs in enumerate(files):
            raw_rel = rel_paths[i] if rel_paths else (fs.filename or f"file_{i}.md")
            rel = _safe_relpath(raw_rel)
            if rel.suffix.lower() not in ALLOWED_SUFFIX:
                skipped.append({"path": str(rel), "reason": "not markdown"})
                continue
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            fs.save(str(target))

            size = target.stat().st_size
            total_bytes += size
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValidationError(f"upload exceeds limit of {MAX_TOTAL_BYTES} bytes")

            sha1 = hashlib.sha1(target.read_bytes()).hexdigest()
            md = loader.load_one(target)
            display_name = _unique_name(target.name, state)
            doc_id = state.upsert_document(
                file_name=display_name,
                title=md.title,
                sha1=sha1,
                source_path=str(target.resolve()),
                size_bytes=size,
            )
            created.append(
                {
                    "id": doc_id,
                    "file_name": display_name,
                    "title": md.title,
                    "size_bytes": size,
                }
            )
        if not created:
            shutil.rmtree(staging, ignore_errors=True)
            raise ValidationError("no .md files in upload")

        return jsonify(
            {
                "staging": str(staging),
                "created": created,
                "skipped": skipped,
                "bytes": total_bytes,
            }
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


# ----------------------------- mutations -----------------------------
@bp.delete("/documents/<int:doc_id>")
def delete_doc(doc_id: int):
    state = AppStateRepository()
    d = state.get_document(doc_id)
    if not d:
        raise NotFoundError(f"document {doc_id} not found")

    # 1. wipe graph state
    try:
        GraphRepository().delete_document(d["file_name"])
    except Exception as e:
        log.warning("graph delete failed for %s: %s", d["file_name"], e)

    # 2. remove staged file (best-effort)
    try:
        p = Path(d["source_path"])
        if p.exists():
            p.unlink()
            # remove parent if empty
            parent = p.parent
            if parent != UPLOAD_ROOT and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
    except Exception as e:
        log.warning("staged file cleanup failed: %s", e)

    # 3. drop from registry
    state.delete_document(doc_id)
    return jsonify({"deleted": doc_id})


@bp.post("/documents/<int:doc_id>/reextract")
def reextract(doc_id: int):
    """Queue a single-document re-extraction job."""
    from ..repositories.app_state_repository import AppStateRepository
    from ..services.job_registry import job_registry
    from ..services.pipeline import IngestionPipeline, PipelineConfig

    state = AppStateRepository()
    d = state.get_document(doc_id)
    if not d:
        raise NotFoundError(f"document {doc_id} not found")

    schema = state.get_schema()
    cfg = PipelineConfig(
        allowed_nodes=schema["node_labels"],
        allowed_relationships=[tuple(t) for t in schema["triplets"]],
        extra_instructions=schema.get("extra") or None,
    )
    pipeline = IngestionPipeline(cfg)

    def runner(update):
        return pipeline.run_documents([doc_id], reextract=True, progress=update)

    job_id = job_registry.submit(runner)
    state.set_status(doc_id, "pending", job_id=job_id)
    return jsonify({"job_id": job_id, "document_id": doc_id})
