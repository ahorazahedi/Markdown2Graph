# Frontend API Client Reference

Single file, single object, no library. `fetch` + JSON + thrown `Error`. Vite proxy routes `/api/*` to Flask at dev time.

**File:** `frontend/src/lib/api.ts`

---

## 1. `jsonFetch` core

```ts
async function jsonFetch<T = any>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!r.ok) {
    let detail = "";
    try { detail = JSON.stringify(await r.json()); }
    catch { detail = await r.text(); }
    throw new Error(`${r.status} ${r.statusText} — ${detail}`);
  }
  return (await r.json()) as T;
}
```

- **Always JSON.** Default Content-Type is JSON; callers spread to override (e.g. FormData uploads strip the header).
- **Errors throw.** Non-2xx → `Error("<status> <statusText> — <body-as-string>")`. Callers `try/catch` and surface via inline banners.
- **Generic return.** `T` is inferred at the call site; no validation/runtime parsing — trust the backend contract.

---

## 2. Base URL + dev proxy

Relative paths (e.g. `/api/jobs`) hit the same origin. In dev, Vite proxies them to Flask:

```ts
// frontend/vite.config.ts
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
```

In prod the frontend is served from the same host so no proxy needed.

---

## 3. The `api` object — naming conventions

```ts
export const api = {
  // health / config
  health, config, me,

  // schema
  getSchema, saveSchema, schemaVersions, schemaVersion, discoverSchema,

  // documents
  listDocuments, getDocument, uploadDocuments, deleteDocument,
  reextractDocument, documentEntities, documentChunks, documentContent,

  // ingest
  runIngest, jobStatus,

  // graph
  stats, graphSchema, clearGraph, runPostProcessing,
  listDuplicates, mergeDuplicates, listOrphans, deleteOrphans,
  exploreGraph, graphNeighborhood,

  // llm audit
  llmCalls, llmCall, llmTags, llmStats, llmClear,

  // settings
  getSettings, saveLLMSettings, saveNeo4jSettings,
  testLLM, testEmbedding, testNeo4j, listModels,
  resetCounts, runReset,

  // prompts
  listPrompts, getPrompt, savePrompt, resetPrompt, previewPrompt,
  listPromptPresets, applyPromptPreset,

  // jobs (durable run history)
  listJobs, getJob, cancelJob, listJobEvents,

  // chat
  chatHealth, chatModes, expandMessage,
  listChatSessions, createChatSession, getChatSession, patchChatSession,
  deleteChatSession, clearChatSession, sendChatMessage,
  messageCitations, rateMessage,

  // embeddings
  embeddingsStatus, reembed, switchEmbeddingModel, clearEmbeddings,

  // runtime tunables
  listRuntime, putRuntime,
};
```

Conventions:
- **`list*`** — collection GET (often with pagination params).
- **`get*` / single-noun** — fetch one (`getJob`, `getSchema`).
- **`save*`** — PUT/POST that replaces or upserts.
- **`run*` / `trigger*`** — POST that kicks an async job (returns `{job_id}`).
- **`delete*` / `clear*`** — DELETE.
- **`test*`** — connection probe; returns `{ok, latency_ms, error?}` shape.

---

## 4. REST patterns

### Pagination

Standard `{limit, offset}` query params; response wraps items in `{items, total, limit, offset}` (sometimes plus `overview`).

```ts
listJobs: (params: { status?: string; kind?: string; limit?: number; offset?: number } = {}) => {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.kind)   q.set("kind",   params.kind);
  if (params.limit  != null) q.set("limit",  String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  return jsonFetch<{ items: JobRun[]; overview: JobOverview }>(`/api/jobs?${q.toString()}`);
}
```

### Cursor / incremental

Event tailing uses `?after=<id>` rather than offset (see [Log System](./log-system.md)).

### POST/PUT/DELETE

```ts
sendChatMessage: (id, body) => jsonFetch<ChatAskResponse>(
  `/api/chat/sessions/${id}/messages`,
  { method: "POST", body: JSON.stringify(body) },
),

saveLLMSettings: (body) => jsonFetch<SettingsView>(
  `/api/settings/llm`,
  { method: "PUT", body: JSON.stringify(body) },
),

deleteChatSession: (id) => jsonFetch<{ deleted: string }>(
  `/api/chat/sessions/${id}`,
  { method: "DELETE" },
),
```

### Uploads (FormData)

```ts
uploadDocuments: async (entries: { file: File; relPath: string }[]) => {
  const fd = new FormData();
  for (const e of entries) {
    fd.append("files[]", e.file);
    fd.append("paths[]", e.relPath);
  }
  const r = await fetch("/api/documents/upload", { method: "POST", body: fd });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${await r.text()}`);
  return r.json();
},
```

Skip `jsonFetch` because we need the browser to set `Content-Type: multipart/form-data; boundary=...` itself.

---

## 5. Exported type shapes

Types live alongside the api object. Key ones (full set in source):

```ts
export interface AppConfig {
  neo4j: { uri, username, database };
  llm: { model, base_url, configured };
  embedding: { provider, model, dimension };
  chunking: { token_size, overlap, combine };
  schema_discovery: { sample_size, max_chars };
  domain: string;
}

export type Triplet = [string, string, string];

export interface Schema {
  node_labels: string[];
  triplets: Triplet[];
  extra: string;
  updated_at: string | null;
  updated_by: string | null;
}

export interface DocumentRow {
  id; file_name; title; sha1; source_path; size_bytes;
  status: "pending" | "processing" | "completed" | "failed";
  error; chunk_count; entity_count; relationship_count;
  last_job_id; created_at; updated_at; processed_at;
}

export interface JobRun { /* see job-system.md */ }
export interface JobEvent { /* see log-system.md */ }
export interface LLMCallRow / LLMCallDetail / LLMLogStats { /* see llm-calls.md */ }
export interface ChatSession / ChatMessage { /* see chat-rag.md */ }
export interface GraphExplore { nodes; relationships; }
export interface RuntimeSettingSpec { /* see settings-system.md */ }
```

---

## 6. Error model end-to-end

1. Backend raises `AppError` (or subclass: `ValidationError`, `NotFoundError`, `UpstreamError`) — see [Errors](./errors.md).
2. Flask handler returns `jsonify({error, status, ...payload})` with proper status code.
3. `jsonFetch` sees `!r.ok`, parses the body as JSON, throws `Error("<status> <text> — <body>")`.
4. Caller catches and shows inline:

```tsx
try {
  await api.save(...);
} catch (e: any) {
  setError(String(e.message || e));
}
```

This means **all error UI is inline + per-page**; no global toast. Combine with the [Confirm Dialog](./confirm-dialog.md) for destructive flows.

---

## 7. Patterns to keep

- **One file, one object.** Don't split per-domain `api/jobs.ts`, `api/chat.ts`. Folding keeps imports trivial (`import { api } from "@/lib/api"`).
- **Inline types.** Co-locate response types with the function that returns them. Re-export only when a component needs to type a prop.
- **No retries / no caching.** Push retries to the user (a Refresh button); use React state for "stale" detection.
- **No abort signal.** Components rely on unmount + ignore-stale pattern (closure-captured `alive` flag). Simpler than threading `AbortController` everywhere.

---

## 8. Anti-patterns to avoid

- ❌ Returning the raw `Response` from any method — always JSON-parse inside.
- ❌ Wrapping `fetch` per call (silently swallows network errors).
- ❌ Throwing custom error subclasses — `Error` with a useful message is enough; consumers grep `.message`.
- ❌ Hardcoding `http://localhost:8000`. Always use relative `/api/...` and rely on the proxy.
