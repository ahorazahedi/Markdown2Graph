from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from ..errors import ValidationError

bp = Blueprint("upload", __name__)
log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
UPLOAD_ROOT = _REPO_ROOT / "backend" / "data" / "uploads"
ALLOWED_SUFFIX = {".md", ".markdown"}
MAX_FILES = 5000
MAX_TOTAL_BYTES = 500 * 1024 * 1024  # 500 MB


def _safe_relpath(raw: str) -> Path:
    """Sanitize a client-supplied relative path. Reject absolute paths and
    any segment that resolves above the staging root."""
    raw = (raw or "").strip().replace("\\", "/").lstrip("/")
    parts: list[str] = []
    for seg in raw.split("/"):
        if not seg or seg == ".":
            continue
        if seg == "..":
            raise ValidationError(f"illegal path segment: {raw}")
        # secure_filename strips slashes; apply per segment so subfolders survive
        cleaned = secure_filename(seg)
        if not cleaned:
            raise ValidationError(f"invalid path segment: {seg!r}")
        parts.append(cleaned)
    if not parts:
        raise ValidationError("empty relative path")
    return Path(*parts)


@bp.post("/upload")
def upload():
    """Stage uploaded Markdown files into a fresh folder under
    backend/data/uploads/<uuid>/. Returns the absolute path, which the
    wizard then passes to /api/schema/discover and /api/ingest."""
    files = request.files.getlist("files")
    if not files:
        raise ValidationError("no files in request")
    if len(files) > MAX_FILES:
        raise ValidationError(f"too many files (>{MAX_FILES})")

    # Client sends the relative path for each file under form key 'paths[i]'
    rel_paths = request.form.getlist("paths")
    if rel_paths and len(rel_paths) != len(files):
        raise ValidationError("paths[] length must match files[] length")

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    staging = UPLOAD_ROOT / uuid.uuid4().hex
    staging.mkdir(parents=True)

    total_bytes = 0
    saved: list[str] = []
    try:
        for i, fs in enumerate(files):
            raw_rel = rel_paths[i] if rel_paths else (fs.filename or f"file_{i}.md")
            rel = _safe_relpath(raw_rel)
            if rel.suffix.lower() not in ALLOWED_SUFFIX:
                log.info("skip non-markdown upload: %s", rel)
                continue
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            fs.save(str(target))
            sz = target.stat().st_size
            total_bytes += sz
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValidationError(f"upload exceeds limit of {MAX_TOTAL_BYTES} bytes")
            saved.append(str(rel))

        if not saved:
            shutil.rmtree(staging, ignore_errors=True)
            raise ValidationError("no .md files in upload")

        return jsonify(
            {
                "path": str(staging),
                "file_count": len(saved),
                "bytes": total_bytes,
                "files": saved[:200],  # cap response payload
            }
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


@bp.delete("/upload")
def clear_uploads():
    """Wipe all staged uploads. Safety net for dev."""
    if UPLOAD_ROOT.exists():
        shutil.rmtree(UPLOAD_ROOT)
    return jsonify({"cleared": True})
