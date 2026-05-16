import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Trash2, X, Eye, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api, LLMCallDetail, LLMCallRow, LLMLogStats } from "@/lib/api";

const PAGE = 25;

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
  const [selected, setSelected] = useState<LLMCallDetail | null>(null);

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
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    refresh();
  }, [tag, status, offset]);

  useEffect(() => {
    if (!auto) return;
    const id = window.setInterval(refresh, 3000);
    return () => window.clearInterval(id);
  }, [auto, tag, status, offset]);

  const open = async (id: number) => {
    setSelected(null);
    const d = await api.llmCall(id);
    setSelected(d);
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE));
  const page = Math.floor(offset / PAGE) + 1;

  return (
    <div className="space-y-4">
      <div className="mb-2">
        <h1 className="text-xl font-semibold tracking-tightish">LLM Calls</h1>
        <p className="mt-1 text-sm text-muted-foreground">Every prompt sent to the LLM and the response received, tagged by purpose.</p>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        {[
          ["Total", stats?.total],
          ["OK", stats?.ok],
          ["Errors", stats?.err],
          ["Pending", stats?.pending],
          ["Tokens", stats?.tokens],
          ["Avg ms", stats ? Math.round(stats.avg_latency_ms) : null],
        ].map(([label, v]) => (
          <Card key={label as string} className="border-border/60">
            <CardHeader className="pb-2">
              <CardDescription>{label as string}</CardDescription>
              <CardTitle className="text-2xl tabular-nums">{v ?? "—"}</CardTitle>
            </CardHeader>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <CardTitle>LLM call log</CardTitle>
              <CardDescription>
                Every prompt sent to the LLM and the response it returned, tagged by purpose.
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                value={tag}
                onChange={(e) => {
                  setOffset(0);
                  setTag(e.target.value);
                }}
              >
                <option value="">All tags</option>
                {tags.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <select
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                value={status}
                onChange={(e) => {
                  setOffset(0);
                  setStatus(e.target.value);
                }}
              >
                <option value="">All status</option>
                <option value="success">success</option>
                <option value="pending">pending</option>
                <option value="error">error</option>
              </select>
              <label className="flex items-center gap-1 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={auto}
                  onChange={(e) => setAuto(e.target.checked)}
                />
                auto-refresh
              </label>
              <Button variant="outline" size="sm" onClick={refresh} disabled={busy}>
                <RefreshCw className={busy ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
              </Button>
              <Button variant="destructive" size="sm" onClick={async () => {
                if (!confirm("Delete all LLM call records?")) return;
                await api.llmClear();
                setOffset(0);
                refresh();
              }}>
                <Trash2 className="h-4 w-4" /> Clear
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="overflow-auto rounded-md border border-border">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-left">
                <tr>
                  <th className="px-3 py-2">#</th>
                  <th className="px-3 py-2">When</th>
                  <th className="px-3 py-2">Tag</th>
                  <th className="px-3 py-2">Model</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2 text-right">ms</th>
                  <th className="px-3 py-2 text-right">prompt</th>
                  <th className="px-3 py-2 text-right">compl.</th>
                  <th className="px-3 py-2 text-right">total</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-border hover:bg-muted/20">
                    <td className="px-3 py-1.5 font-mono text-xs">{r.id}</td>
                    <td className="px-3 py-1.5 text-xs text-muted-foreground">
                      {fmtTime(r.created_at)}
                    </td>
                    <td className="px-3 py-1.5">
                      <Badge variant="secondary">{r.tag}</Badge>
                    </td>
                    <td className="px-3 py-1.5 font-mono text-xs">{r.model || "—"}</td>
                    <td className="px-3 py-1.5">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{r.latency_ms ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{r.prompt_tokens ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{r.completion_tokens ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums font-medium">
                      {r.total_tokens ?? "—"}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      <Button size="sm" variant="ghost" onClick={() => open(r.id)}>
                        <Eye className="h-4 w-4" />
                      </Button>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={10} className="px-3 py-6 text-center text-sm text-muted-foreground">
                      No calls yet. Start a schema discovery or an ingest run.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
            <div>
              {total} total · page {page} / {totalPages}
            </div>
            <div className="flex gap-1">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
                disabled={offset === 0}
              >
                <ChevronLeft className="h-4 w-4" /> Prev
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setOffset(offset + PAGE)}
                disabled={offset + PAGE >= total}
              >
                Next <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {selected && <CallDetailDrawer call={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "success") return <Badge>success</Badge>;
  if (status === "error") return <Badge variant="destructive">error</Badge>;
  return <Badge variant="secondary">{status}</Badge>;
}

function fmtTime(iso: string) {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function CallDetailDrawer({ call, onClose }: { call: LLMCallDetail; onClose: () => void }) {
  const requestPretty = useMemo(
    () => JSON.stringify(call.request_json, null, 2),
    [call.request_json],
  );
  const responsePretty = useMemo(
    () =>
      call.response_text
        ? call.response_text
        : JSON.stringify(call.response_json, null, 2),
    [call.response_text, call.response_json],
  );

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/40" onClick={onClose} />
      <div className="flex h-full w-full max-w-3xl flex-col border-l border-border bg-background shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-3">
            <span className="font-mono text-sm text-muted-foreground">#{call.id}</span>
            <Badge variant="secondary">{call.tag}</Badge>
            <StatusBadge status={call.status} />
            {call.latency_ms != null && (
              <span className="text-xs text-muted-foreground">{call.latency_ms} ms</span>
            )}
            {call.total_tokens != null && (
              <span className="text-xs text-muted-foreground">{call.total_tokens} tokens</span>
            )}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex-1 space-y-4 overflow-auto p-4">
          <Section title="Meta">
            <dl className="grid grid-cols-3 gap-x-4 gap-y-1 text-sm">
              <Meta k="Model" v={call.model} />
              <Meta k="Provider" v={call.provider} />
              <Meta k="Base URL" v={call.base_url} />
              <Meta k="Created" v={fmtTime(call.created_at)} />
              <Meta k="Finished" v={call.finished_at ? fmtTime(call.finished_at) : "—"} />
              <Meta k="Tokens" v={`${call.prompt_tokens ?? "—"} / ${call.completion_tokens ?? "—"} / ${call.total_tokens ?? "—"}`} />
            </dl>
          </Section>

          {call.error && (
            <Section title="Error">
              <pre className="overflow-auto rounded bg-destructive/10 p-2 text-xs text-destructive">
                {call.error}
              </pre>
            </Section>
          )}

          <Section title="Request">
            <pre className="max-h-[40vh] overflow-auto rounded bg-muted/40 p-3 font-mono text-xs">
              {requestPretty}
            </pre>
          </Section>

          <Section title="Response">
            <pre className="max-h-[40vh] overflow-auto rounded bg-muted/40 p-3 font-mono text-xs">
              {responsePretty || "—"}
            </pre>
          </Section>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      {children}
    </div>
  );
}

function Meta({ k, v }: { k: string; v: any }) {
  return (
    <>
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="col-span-2 break-all">{v ?? "—"}</dd>
    </>
  );
}
