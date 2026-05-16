async function jsonFetch<T = any>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!r.ok) {
    let detail = "";
    try {
      detail = JSON.stringify(await r.json());
    } catch {
      detail = await r.text();
    }
    throw new Error(`${r.status} ${r.statusText} — ${detail}`);
  }
  return (await r.json()) as T;
}

export interface AppConfig {
  neo4j: { uri: string; username: string; database: string };
  llm: { model: string; base_url: string; configured: boolean };
  embedding: { provider: string; model: string; dimension: number };
  chunking: { token_size: number; overlap: number; combine: number };
  schema_discovery: { sample_size: number; max_chars: number };
  domain: string;
}

export interface DiscoverResult {
  path: string;
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

export const api = {
  health: () => jsonFetch<{ status: string; neo4j: string }>("/api/health"),
  config: () => jsonFetch<AppConfig>("/api/config"),
  discover: (path: string, sample_size?: number, extra_instructions?: string) =>
    jsonFetch<DiscoverResult>("/api/schema/discover", {
      method: "POST",
      body: JSON.stringify({ path, sample_size, extra_instructions }),
    }),
  ingest: (body: {
    path: string;
    allowed_nodes: string[];
    allowed_relationships: [string, string, string][];
    extra_instructions?: string;
  }) =>
    jsonFetch<{ job_id: string; file_count: number }>("/api/ingest", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  jobStatus: (id: string) => jsonFetch<JobSnapshot>(`/api/ingest/${id}`),
  stats: () => jsonFetch<Record<string, number>>("/api/graph/stats"),
  schema: () => jsonFetch<{ labels: string[]; relationship_types: string[] }>("/api/graph/schema"),
  documents: () => jsonFetch<{ documents: any[] }>("/api/graph/documents"),
  clear: () => jsonFetch<{ status: string; cleared: boolean }>("/api/graph", { method: "DELETE" }),
};
