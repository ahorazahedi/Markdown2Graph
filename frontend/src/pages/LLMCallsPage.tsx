import { useEffect, useMemo, useRef, useState } from "react";
import {
  RefreshCw, Trash2, ChevronLeft, ChevronRight, Clock, CheckCircle2,
  XCircle, Loader2, Zap, Hash,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api, LLMCallDetail, LLMCallRow, LLMLogStats } from "@/lib/api";
import { confirm } from "@/lib/confirm";
import { cn } from "@/lib/utils";

const PAGE = 30;

export function LLMCallsPage() {
  const [rows, setRows] = useState<LLMCallRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [tag, setTag] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [tags, setTags] = useState<string[]>([]);
  const [stats, setStats] = useState<LLMLogStats | null>(null);
  const [busy, setBusy] = useState(false);
  const [auto, setAuto] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<LLMCallDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const detailAbort = useRef<AbortController | null>(null);

  const refresh = async () => {
    setBusy(true);
    try {
      const [list, t, s] = await Promise.all([
        api.llmCalls({ tag: tag || undefined, status: status || undefined, limit: PAGE, offset }),
        api.llmTags(),
        api.llmStats(),
      ]);
      setRows(list.items);
      setTotal(list.total);
      setTags(t.tags);
      setStats(s);
      // auto-select first if none picked yet
      if (selectedId == null && list.items.length > 0) {
        setSelectedId(list.items[0].id);
      }
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { refresh(); /* on filter / page */ }, [tag, status, offset]);
  useEffect(() => {
    if (!auto) return;
    const id = window.setInterval(refresh, 3000);
    return () => window.clearInterval(id);
  }, [auto, tag, status, offset]);

  // load detail when selection changes
  useEffect(() => {
    if (selectedId == null) { setDetail(null); return; }
    detailAbort.current?.abort();
    const ac = new AbortController();
    detailAbort.current = ac;
    setDetailLoading(true);
    api.llmCall(selectedId)
      .then((d) => { if (!ac.signal.aborted) setDetail(d); })
      .catch(() => {})
      .finally(() => { if (!ac.signal.aborted) setDetailLoading(false); });
    return () => ac.abort();
  }, [selectedId]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE));
  const page = Math.floor(offset / PAGE) + 1;

  return (
    <div className="flex h-full flex-col">
      {/* ============ TOP BAR ============ */}
      <header className="shrink-0 border-b border-border bg-card/30">
        <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3 px-6 py-4">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold tracking-tightish">LLM Calls</h1>
            <p className="text-xs text-muted-foreground">
              Every prompt sent to the LLM and the response received, tagged by purpose.
            </p>
          </div>
          <div className="flex items-center gap-5 text-sm">
            <Stat label="Total"    value={stats?.total} />
            <Stat label="Success"  value={stats?.ok} accent="success" />
            <Stat label="Errors"   value={stats?.err} accent={stats?.err ? "destructive" : undefined} />
            <Stat label="Pending"  value={stats?.pending} />
            <Stat label="Tokens"   value={stats?.tokens} muted />
            <Stat label="Avg ms"   value={stats ? Math.round(stats.avg_latency_ms) : null} muted />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-border px-6 py-2">
          <FilterChip
            value={tag}
            options={[{ value: "", label: "All tags" }, ...tags.map((t) => ({ value: t, label: t }))]}
            onChange={(v) => { setOffset(0); setTag(v); }}
          />
          <FilterChip
            value={status}
            options={[
              { value: "", label: "All status" },
              { value: "success", label: "success" },
              { value: "pending", label: "pending" },
              { value: "error",   label: "error" },
            ]}
            onChange={(v) => { setOffset(0); setStatus(v); }}
          />
          <label className="ml-2 flex select-none items-center gap-1.5 text-2xs text-muted-foreground">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)}
                   className="h-3 w-3 accent-foreground" />
            auto-refresh
          </label>
          <div className="ml-auto flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={refresh} disabled={busy}>
              <RefreshCw className={busy ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
            </Button>
            <Button size="sm" variant="ghost" onClick={async () => {
              const ok = await confirm({
                title: "Delete all LLM call records?",
                description: "Wipes the entire audit log. Cannot be undone.",
                confirmText: "Delete all",
                variant: "destructive",
              });
              if (!ok) return;
              await api.llmClear();
              setSelectedId(null);
              setOffset(0);
              refresh();
            }}>
              <Trash2 className="h-3.5 w-3.5 text-destructive" />
            </Button>
          </div>
        </div>
      </header>

      {/* ============ BODY: list + detail ============ */}
      <div className="grid min-h-0 flex-1 grid-cols-[340px_minmax(0,1fr)]">
        {/* ---------- list column ---------- */}
        <aside className="flex min-h-0 flex-col border-r border-border">
          <div className="flex-1 overflow-y-auto">
            {rows.length === 0
              ? <EmptyList />
              : rows.map((r) => (
                <CallRow
                  key={r.id}
                  row={r}
                  active={r.id === selectedId}
                  onSelect={() => setSelectedId(r.id)}
                />
              ))}
          </div>
          <div className="flex shrink-0 items-center justify-between border-t border-border px-3 py-2 text-2xs text-muted-foreground">
            <span>{total} total · page {page} / {totalPages}</span>
            <div className="flex gap-1">
              <Button size="sm" variant="ghost" disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                <ChevronLeft className="h-3.5 w-3.5" />
              </Button>
              <Button size="sm" variant="ghost" disabled={offset + PAGE >= total}
                      onClick={() => setOffset(offset + PAGE)}>
                <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </aside>

        {/* ---------- detail column ---------- */}
        <section className="flex min-h-0 flex-col">
          {selectedId == null
            ? <EmptyDetail />
            : <CallDetail call={detail} loading={detailLoading} />}
        </section>
      </div>
    </div>
  );
}

/* ============================================================ */
/* small components                                              */
/* ============================================================ */

function Stat({
  label, value, accent, muted,
}: {
  label: string;
  value?: number | null;
  accent?: "success" | "destructive";
  muted?: boolean;
}) {
  const color =
    accent === "destructive" && value ? "text-destructive"
    : accent === "success"     && value ? "text-[hsl(var(--success))]"
    : "text-foreground";
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-2xs uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className={cn("font-semibold tabular-nums tracking-tightish", color, muted && "font-normal text-muted-foreground")}>
        {value ?? "—"}
      </span>
    </div>
  );
}

function FilterChip({
  value, options, onChange,
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-7 rounded-sm border border-border bg-background px-2 text-2xs text-foreground"
    >
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

function EmptyList() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-sm text-muted-foreground">
      <Hash className="h-5 w-5" />
      <p>No calls match the current filters.</p>
    </div>
  );
}

function EmptyDetail() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <Zap className="h-6 w-6 text-muted-foreground" />
      <div className="text-sm text-muted-foreground">Select a call to view request and response.</div>
    </div>
  );
}

function CallRow({
  row, active, onSelect,
}: {
  row: LLMCallRow; active: boolean; onSelect: () => void;
}) {
  const StatusIcon = row.status === "success" ? CheckCircle2
                   : row.status === "error"   ? XCircle
                   : Loader2;
  const statusColor = row.status === "success" ? "text-[hsl(var(--success))]"
                   : row.status === "error"   ? "text-destructive"
                   : "text-muted-foreground animate-spin";
  return (
    <button
      onClick={onSelect}
      className={cn(
        "flex w-full items-start gap-3 border-b border-border px-3 py-2.5 text-left transition-colors",
        active ? "bg-accent" : "hover:bg-accent/50",
      )}
    >
      <StatusIcon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", statusColor)} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <Badge variant="outline" className="font-mono text-2xs">{row.tag}</Badge>
          <span className="text-2xs text-muted-foreground tabular-nums">#{row.id}</span>
        </div>
        <div className="mt-1 truncate text-2xs text-muted-foreground">
          {fmtRelative(row.created_at)}
        </div>
        <div className="mt-1 flex items-center justify-between gap-3 text-2xs">
          <span className="flex items-center gap-1 text-muted-foreground tabular-nums">
            <Clock className="h-3 w-3" />{row.latency_ms ?? "—"}ms
          </span>
          <span className="text-muted-foreground tabular-nums">
            {row.total_tokens ?? "—"} tok
          </span>
        </div>
      </div>
    </button>
  );
}

function CallDetail({ call, loading }: { call: LLMCallDetail | null; loading: boolean }) {
  if (loading && !call) {
    return <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Loading…</div>;
  }
  if (!call) return null;

  const messages = (call.request_json && Array.isArray(call.request_json.messages))
    ? call.request_json.messages.flat?.() ?? call.request_json.messages
    : null;
  const prompts = call.request_json?.prompts;
  const responseText = call.response_text
    || (call.response_json ? JSON.stringify(call.response_json, null, 2) : "");

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* meta strip */}
      <div className="shrink-0 border-b border-border bg-card/30 px-6 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold">#{call.id}</span>
            <Badge variant="outline" className="font-mono text-2xs">{call.tag}</Badge>
            <StatusBadge status={call.status} />
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-2xs">
            <Meta k="Model"      v={<span className="font-mono">{call.model}</span>} />
            <Meta k="Provider"   v={call.provider} />
            <Meta k="Latency"    v={call.latency_ms != null ? `${call.latency_ms} ms` : "—"} />
            <Meta k="Tokens"     v={`${call.prompt_tokens ?? "—"} / ${call.completion_tokens ?? "—"} / ${call.total_tokens ?? "—"}`} />
            <Meta k="Created"    v={fmtTime(call.created_at)} />
          </div>
        </div>
      </div>

      {call.error && (
        <div className="shrink-0 border-b border-destructive/30 bg-destructive/10 px-6 py-2">
          <pre className="whitespace-pre-wrap text-xs text-destructive">{call.error}</pre>
        </div>
      )}

      {/* split body: request / response */}
      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-2">
        {/* request */}
        <section className="flex min-h-0 flex-col border-b border-border xl:border-b-0 xl:border-r">
          <SectionHeader title="Request" sub={messages ? `${messages.length} message${messages.length === 1 ? "" : "s"}` : "raw"} />
          <div className="flex-1 overflow-y-auto px-6 pb-6">
            {messages
              ? <MessagesView messages={messages} />
              : prompts
                ? prompts.map((p: string, i: number) => (
                    <Block key={i} label={`prompt ${i + 1}`} body={p} />
                  ))
                : <Block label="request" body={JSON.stringify(call.request_json, null, 2)} />}
          </div>
        </section>

        {/* response */}
        <section className="flex min-h-0 flex-col">
          <SectionHeader title="Response" sub={call.response_text ? `${call.response_text.length} chars` : "json"} />
          <div className="flex-1 overflow-y-auto px-6 pb-6">
            {responseText
              ? <Block label="completion" body={responseText} />
              : <p className="pt-4 text-sm text-muted-foreground">No response captured.</p>}
          </div>
        </section>
      </div>
    </div>
  );
}

function SectionHeader({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="sticky top-0 z-10 flex shrink-0 items-baseline justify-between border-b border-border bg-background/95 px-6 py-2 backdrop-blur">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-foreground">{title}</h3>
      {sub && <span className="text-2xs text-muted-foreground">{sub}</span>}
    </div>
  );
}

function MessagesView({ messages }: { messages: any[] }) {
  return (
    <div className="space-y-3 pt-4">
      {messages.map((m, i) => (
        <Block
          key={i}
          label={m.type || m.role || "message"}
          body={typeof m.content === "string" ? m.content : JSON.stringify(m.content, null, 2)}
        />
      ))}
    </div>
  );
}

function Block({ label, body }: { label: string; body: string }) {
  return (
    <div className="space-y-1">
      <div className="text-2xs uppercase tracking-wider text-muted-foreground">{label}</div>
      <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-sm border border-border bg-muted/30 px-3 py-2 font-mono text-xs leading-relaxed text-foreground">
        {body}
      </pre>
    </div>
  );
}

function Meta({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="uppercase tracking-wider text-muted-foreground">{k}</span>
      <span className="text-foreground">{v ?? "—"}</span>
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "success") return <Badge variant="success">success</Badge>;
  if (status === "error")   return <Badge variant="destructive">error</Badge>;
  return <Badge variant="warning">{status}</Badge>;
}

function fmtTime(iso: string) {
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function fmtRelative(iso: string): string {
  try {
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60)    return `${Math.round(diff)}s ago`;
    if (diff < 3600)  return `${Math.round(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
    return d.toLocaleString();
  } catch { return iso; }
}
