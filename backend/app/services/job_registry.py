"""In-process background job registry with SQLite write-through.

For a single-worker dev / single-pod prod deployment this is enough.
For horizontal scale, swap in Celery/RQ — same `submit/get/snapshot` API.

Every job's lifecycle and events are mirrored into ingest_runs +
ingest_events so the frontend Logs page / Active Job banner can survive
page navigation and even a backend restart.

Cancellation
------------
Each Job carries a `threading.Event`. `request_cancel()` sets the flag and
flips status to ``cancelling``; the worker thread cooperatively polls
``is_cancelled()`` and raises :class:`JobCancelled` to bail out. When the
worker finishes, the row is persisted as ``cancelled``. Threads cannot be
forcibly killed, so an in-flight LLM call runs to completion before the
cancel takes effect — which is the same trade-off the reference repo
makes.
"""
from __future__ import annotations

import inspect
import logging
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

log = logging.getLogger(__name__)


class JobCancelled(Exception):
    """Raised inside a job worker to bail out of the run cooperatively."""


@dataclass
class JobUpdate:
    stage: str
    message: str = ""
    progress: float = 0.0
    extra: dict = field(default_factory=dict)


@dataclass
class Job:
    id: str
    # queued | running | succeeded | failed | cancelling | cancelled
    status: str = "queued"
    started_at: float = 0.0
    ended_at: float = 0.0
    progress: float = 0.0
    stage: str = ""
    message: str = ""
    error: Optional[str] = None
    events: List[dict] = field(default_factory=list)
    result: Optional[dict] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def update(self, u: JobUpdate) -> None:
        with self._lock:
            self.stage = u.stage
            self.message = u.message
            self.progress = max(self.progress, u.progress)
            self.events.append(
                {
                    "ts": time.time(),
                    "stage": u.stage,
                    "message": u.message,
                    "progress": u.progress,
                    **u.extra,
                }
            )
            # cap event log
            if len(self.events) > 500:
                self.events = self.events[-500:]
        # write-through (outside lock) — best-effort
        try:
            from ..repositories.app_state_repository import AppStateRepository
            state = AppStateRepository()
            state.update_run_progress(self.id, stage=u.stage, message=u.message, progress=u.progress)
            state.append_event(
                self.id,
                stage=u.stage, message=u.message, progress=u.progress,
                file_name=(u.extra or {}).get("file"),
                level=(u.extra or {}).get("level", "info"),
                extra=u.extra or None,
            )
        except Exception as e:
            log.warning("failed to persist job event: %s", e)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "stage": self.stage,
                "message": self.message,
                "progress": self.progress,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "error": self.error,
                "events_tail": self.events[-50:],
                "result": self.result,
                "cancel_requested": self.cancel_event.is_set(),
            }


class JobRegistry:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, fn: Callable[..., dict],
               *, scope: dict | None = None, kind: str = "ingest") -> str:
        """Run `fn` on a daemon thread.

        `fn` may accept either ``(update)`` (legacy) or
        ``(update, is_cancelled)`` — we inspect the signature and only pass
        the cancel callable when the runner asks for it. This keeps every
        existing call-site working without an opt-in flag.
        """
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job

        # durable row — best-effort
        try:
            from ..repositories.app_state_repository import AppStateRepository
            AppStateRepository().create_run(job.id, kind=kind, scope=scope or {})
        except Exception as e:
            log.warning("failed to create durable run row: %s", e)

        try:
            takes_cancel = len(inspect.signature(fn).parameters) >= 2
        except (TypeError, ValueError):
            takes_cancel = False

        def runner():
            job.status = "running"
            job.started_at = time.time()
            try:
                from ..repositories.app_state_repository import AppStateRepository
                AppStateRepository().start_run(job.id)
            except Exception:
                pass
            cancelled = False
            try:
                if takes_cancel:
                    result = fn(job.update, job.is_cancelled)
                else:
                    result = fn(job.update)
                job.result = result if isinstance(result, dict) else {"value": result}
                if job.cancel_event.is_set():
                    # worker honored the cancel and returned normally
                    job.status = "cancelled"
                    cancelled = True
                else:
                    job.status = "succeeded"
                    job.progress = 1.0
            except JobCancelled as e:
                job.status = "cancelled"
                job.error = str(e) or "cancelled by user"
                cancelled = True
            except Exception as e:
                job.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                job.status = "failed"
            finally:
                job.ended_at = time.time()
                try:
                    from ..repositories.app_state_repository import AppStateRepository
                    AppStateRepository().finish_run(
                        job.id, status=job.status, error=job.error,
                        result=job.result,
                    )
                except Exception as e:
                    log.warning("failed to finish durable run row: %s", e)
                if cancelled:
                    # one terminal event so the UI's event tail explains why
                    try:
                        job.update(JobUpdate(
                            stage="cancelled", message="job cancelled by user",
                            progress=job.progress,
                            extra={"level": "warn"},
                        ))
                    except Exception:
                        pass

        threading.Thread(target=runner, daemon=True, name=f"job-{job.id[:8]}").start()
        return job.id

    def request_cancel(self, jid: str) -> Optional[Job]:
        """Mark a job for cooperative cancellation. Idempotent — returns the
        Job snapshot if the job exists. The worker thread then notices
        `is_cancelled()` between safe checkpoints and bails out."""
        job = self._jobs.get(jid)
        if not job:
            return None
        if job.status in ("succeeded", "failed", "cancelled"):
            return job
        job.cancel_event.set()
        # surface the intent immediately even if the worker hasn't noticed yet
        if job.status == "running":
            job.status = "cancelling"
        elif job.status == "queued":
            job.status = "cancelling"
        try:
            from ..repositories.app_state_repository import AppStateRepository
            AppStateRepository().update_run_progress(
                jid, stage="cancelling", message="cancel requested",
                progress=job.progress,
            )
        except Exception:
            pass
        try:
            job.update(JobUpdate(
                stage="cancelling", message="cancel requested",
                progress=job.progress,
                extra={"level": "warn"},
            ))
        except Exception:
            pass
        return job

    def get(self, jid: str) -> Optional[Job]:
        return self._jobs.get(jid)

    def clear(self) -> int:
        """Drop all completed jobs from memory. Running jobs are kept."""
        with self._lock:
            keep = {jid: j for jid, j in self._jobs.items()
                    if j.status in ("running", "queued", "cancelling")}
            dropped = len(self._jobs) - len(keep)
            self._jobs = keep
        return dropped


job_registry = JobRegistry()
