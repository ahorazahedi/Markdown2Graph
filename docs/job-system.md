# Background Job System Reference

A simple but production-grade background job system. Pure Python `threading` for execution, SQLite for durability, plain HTTP polling for UI. No Celery, no Redis, no queue broker.

**Design goals:**
- Submit work async, get a `job_id` back immediately.
- Cancel cooperatively (workers check a flag between safe checkpoints).
- Survive backend restarts (state lives in SQLite, not memory).
- Stream progress + events to the browser with a single endpoint.
- Add new job types without touching the registry.

---

## 1. Architecture in one picture

```
┌─────────────────┐   POST /api/foo/run    ┌──────────────────┐
│   Frontend      │ ─────────────────────▶ │  Endpoint         │
│  (any page)     │                         │  builds `runner` │
└─────────────────┘                         │  fn(update,cxl)  │
        ▲                                   └────────┬─────────┘
        │  GET /api/jobs (poll 2s/2.5s)              │
        │  GET /api/jobs/<id>/events?after=...       │ job_registry.submit(...)
        │  POST /api/jobs/<id>/cancel                ▼
        │                                  ┌──────────────────┐
        │                                  │  JobRegistry     │
        │                                  │  - _jobs (mem)   │
        │                                  │  - Lock          │
        │                                  └────────┬─────────┘
        │                                           │ spawn daemon Thread
        │                                           ▼
        │                                  ┌──────────────────┐
        │                                  │  runner thread   │
        │                                  │  - calls fn()    │
        │                                  │  - fn emits      │
        │                                  │    JobUpdate(s)  │
        │                                  └────────┬─────────┘
        │                                           │ Job.update(u)
        │                                           ▼
        │                                  ┌──────────────────┐
        └────────────── reads ◀──────────  │  SQLite (WAL)    │
                                           │  ingest_runs     │
                                           │  ingest_events   │
                                           └──────────────────┘
```

In-memory state is fast but volatile. SQLite is the **source of truth**. The HTTP layer always reads from SQLite, so a backend restart that loses the in-memory thread doesn't lose the visible job — it just marks it cancelled on the next cancel request.

---

## 2. Data model

### 2.1 `JobUpdate` — the message workers emit

```python
@dataclass
class JobUpdate:
    stage: str                                  # e.g. "loading", "extracting", "done"
    message: str = ""                           # human-readable
    progress: float = 0.0                       # 0.0..1.0 (monotonic, clamped)
    extra: dict = field(default_factory=dict)   # {file, level, chunk_id, ...}
```

### 2.2 `Job` — in-memory handle

```python
@dataclass
class Job:
    id: str                                     # uuid4 hex
    status: str = "queued"                      # queued|running|succeeded|failed|cancelling|cancelled
    started_at: float = 0.0
    ended_at: float = 0.0
    progress: float = 0.0
    stage: str = ""
    message: str = ""
    error: Optional[str] = None                 # full traceback if status=failed
    events: List[dict] = field(...)             # ring buffer, cap 500
    result: Optional[dict] = None               # whatever the worker returned
    _lock: threading.Lock = ...
    cancel_event: threading.Event = ...

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def update(self, u: JobUpdate) -> None:
        ...   # see §3.2 — in-memory + write-through to SQLite
```

### 2.3 Status lifecycle

```
queued ──start──▶ running ──ok──▶ succeeded
                      │
                      ├──exc──▶ failed
                      │
                      └──cancel──▶ cancelling ──ok/exc──▶ cancelled
```

Terminal: `succeeded | failed | cancelled`.

---

## 3. The registry

**File:** `backend/app/services/job_registry.py`

### 3.1 `JobRegistry.submit(fn, *, scope=None, kind="ingest") -> str`

```python
class JobRegistry:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, fn: Callable, *, scope: dict | None = None, kind: str = "ingest") -> str:
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job

        # Durable: record row before work starts
        AppStateRepository().create_run(job.id, kind=kind, scope=scope or {})

        def runner():
            job.status = "running"
            job.started_at = time.time()
            AppStateRepository().start_run(job.id)
            try:
                # Inspect fn signature: pass is_cancelled if it takes 2+ params
                sig = inspect.signature(fn)
                if len(sig.parameters) >= 2:
                    result = fn(job.update, job.is_cancelled)
                else:
                    result = fn(job.update)
                job.result = result if isinstance(result, dict) else {"value": result}
                job.status = "succeeded"
                job.progress = 1.0
            except JobCancelled as e:
                job.status = "cancelled"
                job.error = str(e) or "cancelled by user"
            except Exception as e:
                job.status = "failed"
                job.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            finally:
                job.ended_at = time.time()
                AppStateRepository().finish_run(
                    job.id, status=job.status, error=job.error, result=job.result,
                )

        threading.Thread(target=runner, daemon=True, name=f"job-{job.id[:8]}").start()
        return job.id
```

Key choices:
- **Daemon thread** → dies when process exits, no graceful shutdown semantics needed.
- **Thread name** → shows up in stack dumps as `job-abc12345`.
- **Signature inspection** → `is_cancelled` is opt-in for backwards compatibility with old `fn(update)` workers.
- **Durable row created *before* `runner` starts** → never a window where the job runs but isn't recorded.
- **`finish_run` in `finally`** → status always lands somewhere.

### 3.2 `Job.update()` — write path

```python
def update(self, u: JobUpdate) -> None:
    with self._lock:
        self.stage = u.stage
        self.message = u.message
        self.progress = max(self.progress, u.progress)
        self.events.append({"ts": time.time(), "stage": u.stage, "message": u.message,
                            "progress": u.progress, **u.extra})
        if len(self.events) > 500:
            self.events = self.events[-500:]

    try:
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
```

DB write happens **outside the lock** to avoid blocking other progress callbacks. Failures are non-fatal (the job keeps running).

### 3.3 Cancellation

```python
def request_cancel(self, jid: str) -> Optional[Job]:
    job = self._jobs.get(jid)
    if not job:
        return None
    if job.status in ("succeeded", "failed", "cancelled"):
        return job             # idempotent
    job.cancel_event.set()
    if job.status == "running":
        job.status = "cancelling"
    AppStateRepository().update_run_progress(jid, stage="cancelling", ...)
    return job
```

**Cooperative, not preemptive.** The worker has to `if cancelled(): raise JobCancelled(...)` at safe points. An in-flight LLM call finishes; the cancel takes effect at the next checkpoint. This is the only sane choice in Python without forcibly killing threads.

### 3.4 Clearing finished jobs from memory

```python
def clear(self) -> int:
    with self._lock:
        keep = {jid: j for jid, j in self._jobs.items()
                if j.status in ("running", "queued", "cancelling")}
        dropped = len(self._jobs) - len(keep)
        self._jobs = keep
    return dropped
```

Releases memory without touching SQLite — historical job rows persist.

### 3.5 Snapshots

```python
def snapshot(self) -> dict:
    with self._lock:
        return {
            "id": self.id, "status": self.status, "stage": self.stage,
            "message": self.message, "progress": self.progress,
            "started_at": self.started_at, "ended_at": self.ended_at,
            "error": self.error,
            "events_tail": self.events[-50:],
            "result": self.result,
            "cancel_requested": self.cancel_event.is_set(),
        }
```

Used internally; the HTTP layer prefers reading durable rows from SQLite.

---

## 4. Durability — SQLite tables

**File:** `backend/app/repositories/app_state_repository.py`. Same DB as the rest of app state (`backend/data/text2graph.db`), WAL mode.

```sql
CREATE TABLE ingest_runs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'ingest',
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
    error TEXT,
    scope_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    started_at TEXT, ended_at TEXT, created_at TEXT NOT NULL
);

CREATE TABLE ingest_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
    progress REAL NOT NULL DEFAULT 0,
    file_name TEXT,
    level TEXT NOT NULL DEFAULT 'info',
    extra_json TEXT,
    FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
);
```

See [Log System](./log-system.md) for the event-side tailing protocol.

---

## 5. HTTP API

**File:** `backend/app/api/jobs.py`

| Endpoint | Purpose |
|---|---|
| `GET /api/jobs` | List + overview counts (params: `status`, `kind`, `limit ≤ 200`, `offset`) |
| `GET /api/jobs/<id>` | Full run row from SQLite |
| `GET /api/jobs/<id>/events?after=N&level=...&limit=...` | Incremental event tail |
| `POST /api/jobs/<id>/cancel` | Cooperative cancel |

### 5.1 Cancel handles backend-restart gracefully

```python
@bp.post("/jobs/<run_id>/cancel")
def cancel_job(run_id):
    durable = AppStateRepository().get_run(run_id)
    if not durable:
        raise NotFoundError(...)
    if durable["status"] in ("succeeded", "failed", "cancelled"):
        return jsonify({...})              # already terminal

    job = job_registry.request_cancel(run_id)
    if job is None:
        # In-memory job is gone (backend restarted) — close out the durable row
        AppStateRepository().finish_run(
            run_id, status="cancelled",
            error="cancel requested but worker was no longer in-process",
        )
        return jsonify({"id": run_id, "status": "cancelled", "cancel_requested": True})

    snap = job.snapshot()
    return jsonify({"id": run_id, "status": snap["status"],
                    "cancel_requested": True, "message": snap["message"]})
```

---

## 6. Registering a job type — the only pattern you need

### 6.1 Plain job

```python
from ..services.job_registry import job_registry, JobUpdate

@bp.post("/api/foo/run")
def run_foo():
    body = request.get_json(silent=True) or {}

    def runner(update, cancelled):
        update(JobUpdate(stage="setup", message="preparing", progress=0.01))
        # ... work ...
        update(JobUpdate(stage="done", message="complete", progress=1.0))
        return {"ok": True, "items_processed": 42}

    job_id = job_registry.submit(
        runner,
        scope={"foo_param": body.get("foo_param")},   # stored in scope_json, shown in UI
        kind="foo",                                   # filter by ?kind=foo
    )
    return jsonify({"job_id": job_id})
```

Worker checklist:
- Take `(update, cancelled)`.
- Emit a `JobUpdate` at every stage transition.
- Call `cancelled()` between safe checkpoints; raise `JobCancelled` if true.
- Return a `dict` — it becomes `result_json` in the durable row.

### 6.2 Single-flight guard (only one running at a time)

Useful for things that can't safely overlap (community rebuild, schema migration).

```python
@bp.post("/api/graph/post-process")
def post_process():
    state = AppStateRepository()
    for st in ("running", "queued", "cancelling"):
        existing = state.list_runs(status=st, kind="post_process", limit=1)
        if existing:
            return jsonify({
                "error": "post-process job already in progress",
                "job_id": existing[0]["id"],
                "status": existing[0]["status"],
            }), 409

    # ... submit as normal ...
```

### 6.3 Real callsites in this codebase

- `api/ingest.py` — kicks `IngestionPipeline`.
- `api/embeddings_api.py` — `kind="reembed"`, scope captures `{scope, types, model, dim, clear_first}`.
- `api/graph.py` — `kind="post_process"`, single-flight guarded, scope captures all toggles.

---

## 7. Frontend

### 7.1 Types — `frontend/src/lib/api.ts`

```ts
export interface JobRun {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelling" | "cancelled";
  progress: number;          // 0..1
  stage: string;
  message: string;
  error: string | null;
  scope: Record<string, any>;
  result: any;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
}

export interface JobOverview {
  total: number; running: number; queued: number;
  succeeded: number; failed: number;
}

export interface JobEvent {
  id: number; ts: string; stage: string; message: string;
  progress: number; file_name: string | null;
  level: "info" | "warn" | "error"; extra: any;
}

api.listJobs   = (p?: {status?,kind?,limit?,offset?}) => ...;
api.getJob     = (id) => ...;
api.cancelJob  = (id) => ...;
api.listJobEvents = (id, after = 0, level?) => ...;
```

### 7.2 Floating banner — `ActiveJobsBanner.tsx`

Mounted once at the app root. Polls `?status=running` and `?status=cancelling` every 2s, renders a fixed bottom-right stack of cards (`fixed bottom-3 right-3 z-40`). Each card shows: spinner, stage label, short id, status badge, message, thin `h-1` `Progress`, view-logs link, cancel + dismiss buttons.

```tsx
useEffect(() => {
  let alive = true;
  const tick = async () => {
    const [r1, r2] = await Promise.all([
      api.listJobs({ status: "running", limit: 10 }),
      api.listJobs({ status: "cancelling", limit: 10 }),
    ]);
    if (!alive) return;
    setRunning([...r1.items, ...r2.items]);
  };
  tick();
  const id = window.setInterval(tick, 2000);
  return () => { alive = false; window.clearInterval(id); };
}, []);
```

Cancel always asks first:

```tsx
const ok = await confirm({
  title: "Cancel this job?",
  description: "An in-flight LLM call finishes before the worker exits. Partial progress is preserved.",
  confirmText: "Cancel job",
  cancelText: "Keep running",
  variant: "destructive",
});
if (!ok) return;
await api.cancelJob(id);
```

### 7.3 Full Jobs page — `JobsPage.tsx`

Two-pane: left list (paginated 50/page), right detail + event tail. Polling cadence:

| Concern | Cadence | Condition |
|---|---|---|
| List refresh | 2.5s | only while any item is `running` or `queued` |
| Selected job detail + events | 1.5s | only while `tailing && !terminal` |

Tail uses `lastEventId.current` as the cursor and **appends** to event state. See [Log System §5.2](./log-system.md#52-tailing-pattern-jobspagetsx) for the full tailing recipe.

---

## 8. Concurrency + safety notes

- **One thread per job.** Fine for I/O-bound work (LLM calls, DB). For CPU-bound work, fork a subprocess from inside the worker; the registry doesn't care.
- **All in-memory mutations under `Job._lock`.** DB writes are outside the lock.
- **SQLite WAL** → many concurrent readers (HTTP requests) + one writer (the job thread).
- **Progress is monotonic** — `max(self.progress, u.progress)` prevents jitter from out-of-order updates.
- **`cancel_event` is `threading.Event`** — thread-safe by construction, no extra locking needed.
- **Errors carry a full traceback** in `error` field. Useful when reviewing failed runs in the UI.

---

## 9. Restart recovery semantics

| Situation | What happens |
|---|---|
| Backend restarts mid-run | In-memory thread dies; durable row stays in `running` |
| User opens Jobs page after restart | Sees the orphaned `running` row (DB has no idea the worker is gone) |
| User clicks Cancel on orphaned run | Endpoint calls `request_cancel`, gets `None`, marks row as `cancelled` with note `"cancel requested but worker was no longer in-process"` |
| User just leaves it | Row sits as `running` forever (no zombie cleanup loop) |

If you want automatic zombie cleanup, add a startup hook that marks all `running`/`cancelling` rows as `failed` with `error="orphaned: server restarted"`. This codebase intentionally doesn't, so partial truth (the run got most of the way through) is preserved for inspection.

---

## 10. Files

**Backend**
- `backend/app/services/job_registry.py` — registry + Job + JobUpdate + JobCancelled
- `backend/app/api/jobs.py` — HTTP endpoints
- `backend/app/repositories/app_state_repository.py` — durable schema + queries
- `backend/app/api/ingest.py`, `embeddings_api.py`, `graph.py` — example submitters

**Frontend**
- `frontend/src/lib/api.ts` — typed client
- `frontend/src/components/ActiveJobsBanner.tsx` — floating banner
- `frontend/src/pages/JobsPage.tsx` — list + detail + tail

---

## 11. Porting checklist

1. Copy `job_registry.py` and the `ingest_runs` / `ingest_events` schema.
2. Add the four HTTP endpoints (`list`, `get`, `events`, `cancel`).
3. Mount `<ActiveJobsBanner />` at app root and wire `api.listJobs`/`getJob`/`cancelJob`/`listJobEvents`.
4. To register a new job type: write a function `runner(update, cancelled)` and `submit(runner, kind="...", scope={...})`.
5. If a job must be single-flight, guard at the endpoint with `list_runs(status="running", kind="...", limit=1)`.

Resist adding a queue broker until the worker thread model actually hurts. For most internal tools, threads + SQLite are enough and the operational burden is near zero.
