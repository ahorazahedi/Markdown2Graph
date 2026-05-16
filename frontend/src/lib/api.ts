async function jsonFetch<T = any>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!r.ok) {
    let detail = "";
    try { detail = JSON.stringify(await r.json()); } catch { detail = await r.text(); }
    throw new Error(`${r.status} ${r.statusText} — ${detail}`);
  }
  return (await r.json()) as T;
}

// ---------- types ----------
export interface AppConfig {
  neo4j: { uri: string; username: string; database: string };
  llm: { model: string; base_url: string; configured: boolean };
  embedding: { provider: string; model: string; dimension: number };
  chunking: { token_size: number; overlap: number; combine: number };
  schema_discovery: { sample_size: number; max_chars: number };
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

export interface SchemaVersion {
  id: number;
  created_at: string;
  source: string;
}

export interface DocumentRow {
  id: number;
  file_name: string;
  title: string | null;
  sha1: string;
  source_path: string;
  size_bytes: number;
  status: "pending" | "processing" | "completed" | "failed";
  error: string | null;
  chunk_count: number;
  entity_count: number;
  relationship_count: number;
  last_job_id: string | null;
  created_at: string;
  updated_at: string;
  processed_at: string | null;
}

export interface DocumentStats {
  total: number;
  completed: number;
  pending: number;
  processing: number;
  failed: number;
  chunks: number;
  entities: number;
  relationships: number;
}

export interface DiscoverResult {
  file_count: number;
  node_labels: string[];
  triplets: string[];
  sampled_files: string[];
}

export interface JobSnapshot {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  stage: string;
  message: string;
  progress: number;
  started_at: number;
  ended_at: number;
  error: string | null;
  events_tail: Array<{ ts: number; stage: string; message: string; progress: number; [k: string]: any }>;
  result: any | null;
}

export interface LLMCallRow {
  id: number;
  created_at: string;
  finished_at: string | null;
  tag: string;
  model: string | null;
  status: "pending" | "success" | "error";
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error: string | null;
}

export interface LLMCallDetail extends LLMCallRow {
  base_url: string | null;
  provider: string | null;
  request_json: any;
  response_text: string | null;
  response_json: any;
  extra_json: any;
}

export interface LLMLogStats {
  total: number; ok: number; err: number; pending: number;
  tokens: number; avg_latency_ms: number;
}

export interface EntityGraph {
  nodes: { id: string; labels: string[]; description: string | null }[];
  relationships: { source: string; type: string; target: string }[];
}

// ---------- client ----------
export const api = {
  health: () => jsonFetch<{ status: string; neo4j: string }>("/api/health"),
  config: () => jsonFetch<AppConfig>("/api/config"),

  // schema
  getSchema: () => jsonFetch<Schema>("/api/schema"),
  saveSchema: (body: { node_labels: string[]; triplets: Triplet[]; extra?: string; source?: string }) =>
    jsonFetch<Schema>("/api/schema", { method: "PUT", body: JSON.stringify(body) }),
  schemaVersions: () => jsonFetch<{ versions: SchemaVersion[] }>("/api/schema/versions"),
  schemaVersion: (id: number) => jsonFetch<any>(`/api/schema/versions/${id}`),
  discoverSchema: (body: { path?: string; document_ids?: number[]; sample_size?: number; extra_instructions?: string }) =>
    jsonFetch<DiscoverResult>("/api/schema/discover", { method: "POST", body: JSON.stringify(body) }),

  // documents
  listDocuments: () => jsonFetch<{ items: DocumentRow[]; stats: DocumentStats }>("/api/documents"),
  getDocument: (id: number) => jsonFetch<DocumentRow>(`/api/documents/${id}`),
  uploadDocuments: async (entries: { file: File; relPath: string }[]) => {
    const fd = new FormData();
    for (const e of entries) {
      fd.append("files", e.file, e.file.name);
      fd.append("paths", e.relPath);
    }
    const r = await fetch("/api/documents/upload", { method: "POST", body: fd });
    if (!r.ok) {
      let d = "";
      try { d = JSON.stringify(await r.json()); } catch { d = await r.text(); }
      throw new Error(`${r.status} — ${d}`);
    }
    return (await r.json()) as {
      staging: string;
      created: { id: number; file_name: string; title: string | null; size_bytes: number }[];
      skipped: { path: string; reason: string }[];
      bytes: number;
    };
  },
  deleteDocument: (id: number) =>
    jsonFetch<{ deleted: number }>(`/api/documents/${id}`, { method: "DELETE" }),
  reextractDocument: (id: number) =>
    jsonFetch<{ job_id: string; document_id: number }>(`/api/documents/${id}/reextract`, { method: "POST" }),
  documentEntities: (id: number) => jsonFetch<EntityGraph>(`/api/documents/${id}/entities`),
  documentChunks: (id: number) =>
    jsonFetch<{ chunks: { id: string; position: number; length: number; text: string }[] }>(`/api/documents/${id}/chunks`),

  // ingest
  runIngest: (body: { document_ids?: number[]; reextract?: boolean }) =>
    jsonFetch<{ job_id: string }>("/api/ingest/run", { method: "POST", body: JSON.stringify(body) }),
  jobStatus: (id: string) => jsonFetch<JobSnapshot>(`/api/ingest/${id}`),

  // graph
  stats: () => jsonFetch<Record<string, number>>("/api/graph/stats"),
  graphSchema: () => jsonFetch<{ labels: string[]; relationship_types: string[] }>("/api/graph/schema"),
  clearGraph: () => jsonFetch<{ status: string; cleared: boolean }>("/api/graph", { method: "DELETE" }),

  // llm audit
  llmCalls: (params: { tag?: string; status?: string; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.tag) q.set("tag", params.tag);
    if (params.status) q.set("status", params.status);
    if (params.limit != null) q.set("limit", String(params.limit));
    if (params.offset != null) q.set("offset", String(params.offset));
    return jsonFetch<{ items: LLMCallRow[]; total: number; limit: number; offset: number }>(
      `/api/llm-calls?${q.toString()}`,
    );
  },
  llmCall: (id: number) => jsonFetch<LLMCallDetail>(`/api/llm-calls/${id}`),
  llmTags: () => jsonFetch<{ tags: string[] }>("/api/llm-calls/tags"),
  llmStats: () => jsonFetch<LLMLogStats>("/api/llm-calls/stats"),
  llmClear: () => jsonFetch<{ deleted: number }>("/api/llm-calls", { method: "DELETE" }),
};
