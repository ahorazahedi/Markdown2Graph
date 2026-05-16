"""Job + event endpoints — durable view over ingest_runs / ingest_events.

Used by:
- the Active Job banner (poll `/api/jobs?status=running`)
- the Logs page (list runs, drill into events, tail incrementally)
- per-document re-extract status (poll `/api/jobs/<id>`)
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..errors import NotFoundError, ValidationError
from ..repositories.app_state_repository import AppStateRepository

bp = Blueprint("jobs", __name__)


@bp.get("/jobs")
def list_jobs():
    state = AppStateRepository()
    status = request.args.get("status") or None
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        raise ValidationError("limit and offset must be integers")
    items = state.list_runs(status=status, limit=limit, offset=offset)
    return jsonify({"items": items, "overview": state.runs_overview()})


@bp.get("/jobs/<run_id>")
def get_job(run_id: str):
    run = AppStateRepository().get_run(run_id)
    if not run:
        raise NotFoundError(f"job {run_id} not found")
    return jsonify(run)


@bp.get("/jobs/<run_id>/events")
def list_events(run_id: str):
    state = AppStateRepository()
    if not state.get_run(run_id):
        raise NotFoundError(f"job {run_id} not found")
    try:
        after_id = int(request.args.get("after", 0))
        limit = max(1, min(int(request.args.get("limit", 500)), 2000))
    except ValueError:
        raise ValidationError("after and limit must be integers")
    level = request.args.get("level") or None
    events = state.list_events(run_id, after_id=after_id, limit=limit, level=level)
    next_after = events[-1]["id"] if events else after_id
    return jsonify({"events": events, "next_after": next_after, "count": len(events)})
