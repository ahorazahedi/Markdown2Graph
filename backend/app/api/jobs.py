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
    kind = request.args.get("kind") or None
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        raise ValidationError("limit and offset must be integers")
    items = state.list_runs(status=status, kind=kind, limit=limit, offset=offset)
    return jsonify({"items": items, "overview": state.runs_overview()})


@bp.get("/jobs/<run_id>")
def get_job(run_id: str):
    run = AppStateRepository().get_run(run_id)
    if not run:
        raise NotFoundError(f"job {run_id} not found")
    return jsonify(run)


@bp.post("/jobs/<run_id>/cancel")
def cancel_job(run_id: str):
    """Cooperatively cancel a running job.

    The worker thread polls a flag between safe checkpoints (between
    chunks / files / before each LLM call); an in-flight LLM call will
    finish before the cancel takes effect. Returns the latest snapshot
    so the UI can immediately show 'cancelling' instead of 'running'.
    """
    from ..services.job_registry import job_registry
    state = AppStateRepository()
    durable = state.get_run(run_id)
    if not durable:
        raise NotFoundError(f"job {run_id} not found")
    if durable.get("status") in ("succeeded", "failed", "cancelled"):
        return jsonify({"id": run_id, "status": durable["status"],
                        "cancel_requested": False,
                        "message": "job already finished"})
    job = job_registry.request_cancel(run_id)
    if job is None:
        # in-process registry forgot it (e.g. backend restarted) — mark the
        # durable row as cancelled so the UI doesn't show it running forever
        state.finish_run(run_id, status="cancelled",
                         error="cancel requested but worker was no longer in-process")
        return jsonify({"id": run_id, "status": "cancelled",
                        "cancel_requested": True,
                        "message": "worker not in-process; marked cancelled"})
    snap = job.snapshot()
    return jsonify({"id": run_id, "status": snap["status"],
                    "cancel_requested": True,
                    "message": snap["message"]})


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
