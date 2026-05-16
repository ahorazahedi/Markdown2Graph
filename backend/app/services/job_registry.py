"""In-process background job registry.

For a single-worker dev / single-pod prod deployment this is enough.
For horizontal scale, swap in Celery/RQ — same `submit/get/snapshot` API.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class JobUpdate:
    stage: str
    message: str = ""
    progress: float = 0.0
    extra: dict = field(default_factory=dict)


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued|running|succeeded|failed
    started_at: float = 0.0
    ended_at: float = 0.0
    progress: float = 0.0
    stage: str = ""
    message: str = ""
    error: Optional[str] = None
    events: List[dict] = field(default_factory=list)
    result: Optional[dict] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

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
            }


class JobRegistry:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, fn: Callable[[Callable[[JobUpdate], None]], dict]) -> str:
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job

        def runner():
            job.status = "running"
            job.started_at = time.time()
            try:
                result = fn(job.update)
                job.result = result if isinstance(result, dict) else {"value": result}
                job.status = "succeeded"
                job.progress = 1.0
            except Exception as e:
                job.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                job.status = "failed"
            finally:
                job.ended_at = time.time()

        threading.Thread(target=runner, daemon=True, name=f"job-{job.id[:8]}").start()
        return job.id

    def get(self, jid: str) -> Optional[Job]:
        return self._jobs.get(jid)


job_registry = JobRegistry()
