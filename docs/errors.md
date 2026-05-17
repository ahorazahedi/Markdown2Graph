# Errors Reference

Three custom exceptions + a Flask handler trio. Frontend surfaces every error inline via `jsonFetch`.

**Files:**
- `backend/app/errors.py`
- `backend/app/__init__.py` — `register_error_handlers(app)`
- `frontend/src/lib/api.ts` — `jsonFetch`

---

## 1. Exception hierarchy

```python
class AppError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload or {}

    def to_dict(self) -> dict:
        return {"error": self.message, "status": self.status_code, **self.payload}


class NotFoundError(AppError):
    status_code = 404


class ValidationError(AppError):
    status_code = 422


class UpstreamError(AppError):
    status_code = 502
```

| Class | Status | Meaning |
|---|---|---|
| `AppError` | 400 | generic client problem |
| `ValidationError` | 422 | malformed body / missing required field |
| `NotFoundError` | 404 | id doesn't exist |
| `UpstreamError` | 502 | LLM/Neo4j/external API failed |

`payload` merges into the JSON response for extra context (e.g. `{"field": "scope"}`).

---

## 2. Flask handlers

```python
def register_error_handlers(app):
    @app.errorhandler(AppError)
    def _app_err(e: AppError):
        log.warning("AppError: %s", e.message)
        return jsonify(e.to_dict()), e.status_code

    @app.errorhandler(404)
    def _not_found(_e):
        return jsonify({"error": "not found", "status": 404}), 404

    @app.errorhandler(Exception)
    def _unhandled(e: Exception):
        log.exception("unhandled exception")
        return jsonify({"error": "internal server error",
                        "detail": str(e),
                        "status": 500}), 500
```

Registered once in `create_app()`. Order matters — `AppError` handler runs before `Exception` handler.

---

## 3. Response shape

```json
{
  "error": "human-readable message",
  "status": 422,
  "field": "scope"
}
```

`error` + `status` always present. Extra keys come from `payload`.

---

## 4. Raising in routes

```python
@bp.post("/foo")
def create_foo():
    body = request.get_json(silent=True) or {}
    if not body.get("name"):
        raise ValidationError("name is required", payload={"field": "name"})

    foo = repo.get(body["name"])
    if not foo:
        raise NotFoundError(f"foo {body['name']!r} not found")

    try:
        result = upstream.call(...)
    except UpstreamTimeout as e:
        raise UpstreamError(f"upstream timeout: {e}", payload={"upstream": "openrouter"})

    return jsonify(result)
```

Routes never `return jsonify({"error": ...}), 4xx` directly — always raise. The handler standardizes the shape.

---

## 5. Frontend surface

```ts
// frontend/src/lib/api.ts
async function jsonFetch<T = any>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { headers: { "Content-Type": "application/json", ...(init?.headers || {}) }, ...init });
  if (!r.ok) {
    let detail = "";
    try { detail = JSON.stringify(await r.json()); } catch { detail = await r.text(); }
    throw new Error(`${r.status} ${r.statusText} — ${detail}`);
  }
  return (await r.json()) as T;
}
```

Errors become a thrown `Error` with `.message = "422 UNPROCESSABLE ENTITY — {"error":"name is required","status":422,"field":"name"}"`. Callers catch and render:

```tsx
try {
  await api.save(...);
} catch (e: any) {
  setError(String(e.message || e));
}
```

UI convention: render `error` in a red banner with `text-destructive bg-destructive/15 border border-destructive/30 rounded-sm p-2 text-sm`. No global toast.

---

## 6. Logging

- `AppError` → `log.warning` (single line). Client-caused, not actionable.
- Unhandled `Exception` → `log.exception` (full traceback). Indicates a bug.
- Stack traces never leak to the response body (only `str(e)` for `detail` on 500).

---

## 7. Idioms

✅ **Do:**
- Raise `ValidationError` for any missing/typed-wrong input at the route boundary.
- Raise `NotFoundError` immediately after a missing `get()`.
- Use `UpstreamError` for LLM/Neo4j outages so the frontend can surface "upstream is down" distinctly from "you made a bad request".
- Put structured context in `payload` (`{"field": "...", "kind": "..."}`).

❌ **Don't:**
- `return jsonify({"error": ...}), 500` — bypasses the handler and inconsistent shape.
- Swallow exceptions in repos (`try: ... except: pass`) — lets corrupt data accumulate.
- Wrap every call in try/except — let `AppError` bubble; only catch when you can do something useful.
