# Log System Reference

Two layers:

1. **Process logs** — stdlib `logging` + `structlog`, written to stdout. For operators tailing the server.
2. **Job event logs** — per-job, durable, queryable from the UI. Captured via a progress callback, written to SQLite, streamed to the frontend by incremental polling. This is what makes long-running background work observable.

Both layers run concurrently; they don't share storage.

---

## 1. Process logging (stdout)

### 1.1 Setup

**File:** `backend/app/extensions.py`

```python
import logging, sys, warnings
import structlog

def init_logging(level: str = "INFO") -> None:
    warnings.filterwarnings("ignore", category=UserWarning, module=r"pydantic.*")

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        level=level,
        stream=sys.stdout,
    )

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, 20)),
    )
```

Called from `create_app()` with `level=config.log_level` (env `LOG_LEVEL`, default `INFO`).

### 1.2 Format

```
2026-05-17 10:42:15,123 INFO app.services.pipeline :: processing 5 documents
```

stdout only — no file handler, no rotation. Production should pipe via systemd/journald or container log driver.

### 1.3 Module pattern

Every module that logs creates a module-level logger:

```python
import logging
log = logging.getLogger(__name__)

log.info("processing %d documents", n)
log.warning("file %s failed: %s", name, exc)
```

Modules using this: `services/pipeline.py`, `services/job_registry.py`, `services/entity_extractor.py`, `services/embedding_service.py`, `services/schema_discovery.py`, `services/post_processor.py`, `services/chat_service.py`, `llm/client.py`, `llm/recorder.py`, `api/upload.py`, `api/documents.py`, `api/chat_api.py`, `api/settings_api.py`, etc.

### 1.4 When to use process logs vs job events

- **Process logs:** debugging the server, startup config, repository errors, anything not tied to a user-visible task.
- **Job events:** anything a user might want to see while watching a job run, or audit after the fact.

A job worker typically does both: `log.warning(...)` for the operator, and `progress(JobUpdate(..., extra={"level": "warn"}))` for the UI.

---

## 2. Job event logging (durable, UI-streamed)

The interesting bit. Designed for long-running pipeline jobs (ingest, re-embed, post-process). Survives backend restart, streamable to the browser with simple polling.

### 2.1 Update shape

**File:** `backend/app/services/job_registry.py`

```python
@dataclass
class JobUpdate:
    stage: str                         # e.g. "loading", "extracting", "post_process", "done"
    message: str = ""                  # human-readable
    progress: float = 0.0              # 0.0..1.0, monotonic
    extra: dict = field(default_factory=dict)  # {file, level, ...}
```

Convention: put per-file context in `extra["file"]`, severity in `extra["level"]` (`"info" | "warn" | "error"`, default `"info"`). Anything else (chunk_id, error_type, …) goes in `extra` and is stored as JSON.

### 2.2 The `Job.update()` write path

In-memory ring buffer (last 500 events) + write-through to SQLite.

```python
def update(self, u: JobUpdate) -> None:
    with self._lock:
        self.stage = u.stage
        self.message = u.message
        self.progress = max(self.progress, u.progress)   # monotonic
        self.events.append({
            "ts": time.time(),
            "stage": u.stage,
            "message": u.message,
            "progress": u.progress,
            **u.extra,
        })
        if len(self.events) > 500:
            self.events = self.events[-500:]

    # Best-effort durable write (outside the lock)
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

Write failures are non-fatal and only logged — the job keeps running.

### 2.3 Emitting events from a worker

```python
def runner(update, cancelled):
    update(JobUpdate(stage="loading", message=f"processing {total} documents", progress=0.03))

    for i, rec in enumerate(records):
        if cancelled():
            raise JobCancelled("user cancelled")

        update(JobUpdate(
            stage="extracting",
            message=f"chunk {i+1}/{total}",
            progress=0.05 + 0.85 * i / max(1, total),
            extra={"file": rec["file_name"]},
        ))

        try:
            process(rec)
        except Exception as e:
            log.warning("file %s failed: %s", rec["file_name"], e)
            update(JobUpdate(
                stage="extracting",
                message=f"file failed: {rec['file_name']}",
                progress=0.05 + 0.85 * i / max(1, total),
                extra={"file": rec["file_name"], "level": "warn", "error_type": type(e).__name__},
            ))

    update(JobUpdate(stage="done", message="complete", progress=1.0))
```

Patterns:
- Emit a top-level update at the start of every stage.
- Update progress before doing the work, not after — users want to see what's happening *now*.
- Use `level: "warn"` for non-fatal issues (retried request, skipped file), `level: "error"` for failures that affected the result.
- Don't go below the previous progress value — the registry clamps with `max(...)` anyway.

---

## 3. Storage

**File:** `backend/app/repositories/app_state_repository.py`

Logs go to the project's main SQLite DB at `backend/data/text2graph.db` (configurable: `Settings.app_state_db_path`). WAL mode (`PRAGMA journal_mode=WAL`) for concurrent readers + a single writer.

### 3.1 Tables

```sql
CREATE TABLE ingest_runs (
    id              TEXT PRIMARY KEY,                 -- uuid4 hex
    kind            TEXT NOT NULL DEFAULT 'ingest',   -- ingest | reembed | post_process | ...
    status          TEXT NOT NULL DEFAULT 'queued',   -- queued | running | succeeded | failed | cancelling | cancelled
    progress        REAL NOT NULL DEFAULT 0,
    stage           TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT '',
    error           TEXT,                              -- traceback if failed
    scope_json      TEXT NOT NULL DEFAULT '{}',        -- input params
    result_json     TEXT,                              -- final stats / payload
    started_at      TEXT,
    ended_at        TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_ingest_runs_status     ON ingest_runs(status);
CREATE INDEX idx_ingest_runs_created_at ON ingest_runs(created_at DESC);

CREATE TABLE ingest_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT, -- per-DB monotonic, used for cursor pagination
    run_id          TEXT NOT NULL,
    ts              TEXT NOT NULL,                     -- ISO 8601
    stage           TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT '',
    progress        REAL NOT NULL DEFAULT 0,
    file_name       TEXT,
    level           TEXT NOT NULL DEFAULT 'info',      -- info | warn | error
    extra_json      TEXT,
    FOREIGN KEY (run_id) REFERENCES ingest_runs(id) ON DELETE CASCADE
);
CREATE INDEX idx_ingest_events_run_id ON ingest_events(run_id, id);
CREATE INDEX idx_ingest_events_level  ON ingest_events(level);
```

Two important properties:

- **`events.id` is global autoincrement.** That makes incremental tailing trivial: client passes `?after=<last_id>` and the server returns everything new.
- **`ON DELETE CASCADE`.** Deleting a run wipes its events.

### 3.2 Repo methods (relevant subset)

```python
state = AppStateRepository()

state.create_run(run_id, kind="ingest", scope={...})
state.start_run(run_id)                        # status='running', started_at=now
state.update_run_progress(run_id, stage=..., message=..., progress=...)
state.append_event(run_id, stage=..., message=..., progress=...,
                   file_name=None, level="info", extra=None) -> int
state.finish_run(run_id, status="succeeded"|"failed"|"cancelled",
                 error=None, result=None)

state.list_runs(status=None, kind=None, limit=50, offset=0) -> list[dict]
state.get_run(run_id) -> dict | None
state.runs_overview() -> {"total":..,"running":..,"queued":..,"succeeded":..,"failed":..}

state.list_events(run_id, after_id=0, limit=500, level=None) -> list[dict]
```

No retention policy. If the DB grows too big, prune manually (`DELETE FROM ingest_runs WHERE created_at < ...`).

---

## 4. HTTP endpoints

**File:** `backend/app/api/jobs.py`

### 4.1 `GET /api/jobs`

```
?status=running   # optional: queued|running|succeeded|failed|cancelling|cancelled
?kind=ingest      # optional
?limit=50         # 1..200
?offset=0
```

Response:

```json
{
  "items": [
    {
      "id": "abc123...",
      "kind": "ingest",
      "status": "running",
      "progress": 0.45,
      "stage": "extracting",
      "message": "chunk 23/50",
      "error": null,
      "scope": { "document_ids": [1,2,3] },
      "result": null,
      "started_at": "2026-05-17T10:42:15...",
      "ended_at": null,
      "created_at": "2026-05-17T10:40:00..."
    }
  ],
  "overview": { "total": 42, "running": 3, "queued": 1, "succeeded": 35, "failed": 3 }
}
```

### 4.2 `GET /api/jobs/<id>`

Full run row (single object, same shape as `items[i]`).

### 4.3 `GET /api/jobs/<id>/events`

```
?after=0          # last event id seen; returns only id > after
?limit=500        # 1..2000
?level=warn       # optional: info|warn|error
```

Response:

```json
{
  "events": [
    {
      "id": 17,
      "ts": "2026-05-17T10:42:18.789Z",
      "stage": "extracting",
      "message": "LLM extraction failed (retry 1/3)",
      "progress": 0.06,
      "file_name": "my-doc.md",
      "level": "warn",
      "extra": { "error_type": "timeout" }
    }
  ],
  "next_after": 17,
  "count": 1
}
```

Client uses `next_after` as the next `?after` value. This is the entire tailing contract — no SSE, no WebSocket.

### 4.4 `POST /api/jobs/<id>/cancel`

Sets the job's cancel flag (see [Job System](./job-system.md)). Workers stop voluntarily; events keep flowing until the worker actually exits.

---

## 5. Frontend

### 5.1 API client

**File:** `frontend/src/lib/api.ts`

```ts
export interface JobEvent {
  id: number;
  ts: string;                           // ISO
  stage: string;
  message: string;
  progress: number;
  file_name: string | null;
  level: "info" | "warn" | "error";
  extra: any;
}

api.listJobEvents = (id: string, after = 0, level?: string) => {
  const q = new URLSearchParams();
  q.set("after", String(after));
  if (level) q.set("level", level);
  return jsonFetch<{ events: JobEvent[]; next_after: number; count: number }>(
    `/api/jobs/${id}/events?${q.toString()}`,
  );
};
```

### 5.2 Tailing pattern (`JobsPage.tsx`)

Two polling loops:

**List poll — only while something is running**

```ts
useEffect(() => {
  if (!items.some((j) => j.status === "running" || j.status === "queued")) return;
  const id = window.setInterval(refresh, 2500);
  return () => window.clearInterval(id);
}, [items, status, offset]);
```

**Event tail — incremental, append-only**

```ts
const lastEventId = useRef(0);

useEffect(() => {
  if (!selectedId) return;
  lastEventId.current = 0;
  setEvents([]);

  const load = async () => {
    const j = await api.getJob(selectedId);
    setDetail(j);

    const ev = await api.listJobEvents(selectedId, lastEventId.current, levelFilter || undefined);
    if (ev.events.length > 0) {
      lastEventId.current = ev.next_after;
      setEvents((prev) => [...prev, ...ev.events]);   // append only — never re-fetch
    }

    if (j.status === "succeeded" || j.status === "failed" || j.status === "cancelled") {
      setTailing(false);                              // stop polling at terminal
    }
  };

  load();
  const id = window.setInterval(() => { if (tailing) load(); }, 1500);
  return () => window.clearInterval(id);
}, [selectedId, levelFilter, tailing]);
```

Three rules that make this work:
1. **Cursor reset** when switching jobs (`lastEventId.current = 0`).
2. **Append, never replace** (so the user's scroll position is preserved).
3. **Auto-stop at terminal status** (saves bandwidth; a "Tail" checkbox can resume for re-runs).

### 5.3 Rendering

Standard layout: monospace, fixed grid, level colored.

```tsx
<ul className="space-y-0 font-mono text-xs leading-relaxed">
  {events.map((e) => (
    <li key={e.id} className="grid grid-cols-[5.5rem_4.5rem_3.25rem_1fr] gap-x-2 py-0.5">
      <span>{new Date(e.ts).toLocaleTimeString()}</span>
      <span className={levelColor(e.level)}>{e.level.toUpperCase()}</span>
      <span className="truncate">{e.stage}</span>
      <span>{e.message}</span>
    </li>
  ))}
</ul>
```

`levelColor`: `error → text-destructive`, `warn → text-[hsl(var(--warning))]`, `info → text-muted-foreground`.

Auto-scroll-to-bottom on append while `tailing` is on; pause when the user manually scrolls up (common UX trick — record `scrollTop` before append, restore if it changed by more than the new content height).

### 5.4 Floating banner (`ActiveJobsBanner.tsx`)

Polls `GET /api/jobs?status=running` and `?status=cancelling` every 2 seconds. Renders one card per active job. Doesn't poll events — the banner only shows `stage` + `message` + `progress`. Click "view logs" to jump to `JobsPage` for the full tail.

---

## 6. Cross-layer cheatsheet

| Need | Layer |
|------|-------|
| "Why did the server start?" | Process log (stdout) |
| "Why is the DB throwing?" | Process log |
| "What's this job doing right now?" | Job events (UI) |
| "Why did the ingest fail at 73%?" | Job events (`level=error`) + the run's `error` field |
| "Did it skip any files?" | Job events filtered `level=warn` |
| "How long did stage X take?" | Compare consecutive `ts` values for that stage |
| "Which file did this event refer to?" | `extra.file` / `file_name` column |

---

## 7. Portability notes

If you copy this system to another project:

1. The `ingest_runs` / `ingest_events` schema is self-contained — drop in `app_state_repository.py`'s migration block.
2. `Job.update()` is the only place that touches both in-memory + DB. Keep them in sync there.
3. Frontend tailing is *just* `?after=<id>` polling. No SSE needed unless you're tailing thousands of events per second.
4. If you want multi-process workers (Celery, RQ), swap the in-memory `_jobs` dict for a Redis hash and keep the SQLite write path identical — readers don't care which worker wrote.
5. The `level` column is intentionally just a string. Add new levels if you need them (`"debug"`, `"trace"`); the UI filter is a free-form `?level=` param.
