import { useEffect, useRef, useState } from "react";
import {
  Loader2, RefreshCw, Trash2, Wand2, AlertTriangle,
  CheckCircle2, StopCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { PageHeader } from "@/components/PageHeader";
import { PageContainer } from "@/components/PageContainer";
import { api, EmbeddingsStatus, JobEvent, JobRun } from "@/lib/api";
import { confirm } from "@/lib/confirm";

type NodeType = "chunk" | "entity" | "community";
const ALL_TYPES: NodeType[] = ["chunk", "entity", "community"];

export function EmbeddingsPage() {
  const [status, setStatus] = useState<EmbeddingsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [types, setTypes] = useState<Set<NodeType>>(new Set(ALL_TYPES));
  const [scope, setScope] = useState<"missing" | "stale" | "all">("missing");
  const [submitting, setSubmitting] = useState(false);

  const [active, setActive] = useState<JobRun | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const lastEventId = useRef(0);
  const eventsScrollRef = useRef<HTMLDivElement>(null);

  // ----- status -----
  const loadStatus = async () => {
    try {
      const s = await api.embeddingsStatus();
      setStatus(s);
      setError(null);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { loadStatus(); }, []);

  // poll status while a re-embed job runs
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(loadStatus, 4000);
    return () => window.clearInterval(id);
  }, [active?.id, active?.status]);

  // tail events for the active job
  useEffect(() => {
    if (!active) { setEvents([]); lastEventId.current = 0; return; }
    let stopped = false;
    const tick = async () => {
      try {
        const r = await api.listJobEvents(active.id, lastEventId.current);
        if (stopped) return;
        if (r.events.length) {
          lastEventId.current = r.next_after;
          setEvents((p) => [...p, ...r.events]);
        }
        const snap = await api.getJob(active.id);
        setActive(snap);
        if (snap.status === "succeeded" || snap.status === "failed" || snap.status === "cancelled") {
          stopped = true;
          loadStatus();
          return;
        }
      } catch { /* swallow */ }
      if (!stopped) setTimeout(tick, 1500);
    };
    tick();
    return () => { stopped = true; };
  }, [active?.id]);

  useEffect(() => {
    eventsScrollRef.current?.scrollTo({ top: eventsScrollRef.current.scrollHeight });
  }, [events.length]);

  const toggleType = (t: NodeType) => {
    setTypes((p) => {
      const n = new Set(p);
      n.has(t) ? n.delete(t) : n.add(t);
      return n;
    });
  };

  const submitReembed = async () => {
    if (types.size === 0) return;
    setSubmitting(true);
    try {
      const r = await api.reembed({
        scope,
        types: Array.from(types),
        clear_first: scope === "all",
      });
      const snap = await api.getJob(r.job_id);
      setActive(snap);
      setEvents([]);
      lastEventId.current = 0;
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const submitClear = async () => {
    if (types.size === 0) return;
    const ok = await confirm({
      title: "Clear embeddings?",
      description: `Null out the embedding property on ${Array.from(types).join(", ")} nodes. Vector indexes stay; chat retrieval will return no results until you re-embed.`,
      confirmText: "Clear",
      variant: "destructive",
    });
    if (!ok) return;
    setSubmitting(true);
    try {
      await api.clearEmbeddings({
        types: Array.from(types),
        confirm: true,
      });
      await loadStatus();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const cancelActive = async () => {
    if (!active) return;
    try {
      await api.cancelJob(active.id);
      const snap = await api.getJob(active.id);
      setActive(snap);
    } catch { /* ignore */ }
  };

  const isRunning = active && (active.status === "running" ||
    active.status === "queued" || active.status === "cancelling");

  return (
    <PageContainer>
      <PageHeader
        title="Embeddings"
        description="Backfill or re-embed chunks / entities / communities with the configured embedding model. Change the model in Settings → LLM & Embeddings."
        actions={
          <Button variant="ghost" size="sm" onClick={loadStatus} disabled={loading}>
            <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
          </Button>
        }
      />

      {error && (
        <div className="mb-3 flex items-start gap-2 rounded-sm border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span className="break-all">{error}</span>
        </div>
      )}

      {status && (
        <Card className="mb-4">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Current configuration</CardTitle>
            <CardDescription className="text-2xs">
              Provider: <span className="font-mono">{status.provider}</span>
              {" — "}Model: <span className="font-mono">{status.current_model}</span>
              {" — "}Dim: <span className="font-mono">{status.current_dim}</span>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <table className="w-full text-xs">
              <thead className="border-b border-border text-muted-foreground">
                <tr>
                  <th className="py-1 text-left">Type</th>
                  <th className="text-right">Total</th>
                  <th className="text-right">Embedded</th>
                  <th className="text-right">Missing</th>
                  <th className="text-right">Ineligible</th>
                  <th className="text-right">Stale</th>
                  <th className="text-right">Index dim</th>
                  <th className="text-left pl-3">By model</th>
                </tr>
              </thead>
              <tbody>
                {ALL_TYPES.map((nt) => {
                  const s = status.types[nt];
                  if (!s) return null;
                  return (
                    <tr key={nt} className="border-b border-border/40 last:border-0">
                      <td className="py-1 font-medium capitalize">{nt}</td>
                      <td className="text-right tabular-nums">{s.total ?? 0}</td>
                      <td className="text-right tabular-nums">{s.embedded ?? 0}</td>
                      <td className="text-right tabular-nums">
                        {(s.missing ?? 0) > 0
                          ? <Badge variant="outline" className="px-1 py-0">{s.missing}</Badge>
                          : 0}
                      </td>
                      <td
                        className="text-right tabular-nums text-muted-foreground"
                        title={
                          nt === "community"
                            ? "Communities with no summary — cannot embed until summaries are generated."
                            : nt === "chunk"
                              ? "Chunks with empty text — cannot embed."
                              : "Nodes that cannot be embedded under current eligibility rules."
                        }
                      >
                        {s.ineligible ?? 0}
                      </td>
                      <td className="text-right tabular-nums">
                        {(s.stale ?? 0) > 0
                          ? <Badge variant="destructive" className="px-1 py-0">{s.stale}</Badge>
                          : 0}
                      </td>
                      <td className="text-right tabular-nums">{s.index_dim ?? "—"}</td>
                      <td className="pl-3 font-mono text-2xs text-muted-foreground">
                        {Object.entries(s.by_model || {})
                          .map(([m, c]) => `${m}: ${c}`)
                          .join(" · ") || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      <Card className="mb-4">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Wand2 className="h-4 w-4" /> Re-embed
          </CardTitle>
          <CardDescription className="text-2xs">
            Backfill or regenerate vectors using the configured model.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Scope */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                Scope
              </span>
              <span className="text-2xs text-muted-foreground">
                {scope === "missing" && "Only NULL embeddings"}
                {scope === "stale" && "NULL or different model from current"}
                {scope === "all" && "Clear + re-embed everything"}
              </span>
            </div>
            <div
              role="radiogroup"
              className="inline-flex w-full rounded-md border border-border bg-muted/30 p-0.5"
            >
              {(["missing", "stale", "all"] as const).map((s) => {
                const active = scope === s;
                return (
                  <button
                    key={s}
                    role="radio"
                    aria-checked={active}
                    onClick={() => setScope(s)}
                    disabled={!!isRunning}
                    className={`flex-1 rounded-[5px] px-3 py-1.5 text-xs font-medium capitalize transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                      active
                        ? "bg-background text-foreground shadow-sm ring-1 ring-border"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {s}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Types */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                Types
              </span>
              <span className="text-2xs text-muted-foreground">
                {types.size} of {ALL_TYPES.length} selected
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {ALL_TYPES.map((t) => {
                const checked = types.has(t);
                return (
                  <button
                    key={t}
                    type="button"
                    role="checkbox"
                    aria-checked={checked}
                    onClick={() => toggleType(t)}
                    disabled={!!isRunning}
                    className={`group flex items-center gap-2 rounded-md border px-3 py-2 text-xs font-medium capitalize transition-all disabled:cursor-not-allowed disabled:opacity-50 ${
                      checked
                        ? "border-foreground/60 bg-accent text-foreground"
                        : "border-border bg-background text-muted-foreground hover:border-foreground/30 hover:text-foreground"
                    }`}
                  >
                    <span
                      className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-[4px] border transition-colors ${
                        checked
                          ? "border-foreground bg-foreground text-background"
                          : "border-border bg-background group-hover:border-foreground/50"
                      }`}
                    >
                      {checked && <CheckCircle2 className="h-3 w-3" strokeWidth={3} />}
                    </span>
                    {t}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2 border-t border-border pt-4">
            <Button
              size="sm"
              onClick={submitReembed}
              disabled={!!isRunning || submitting || types.size === 0}
              className="min-w-[110px]"
            >
              {submitting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Wand2 className="h-3.5 w-3.5" />
              )}
              Re-embed
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={submitClear}
              disabled={!!isRunning || submitting || types.size === 0}
            >
              <Trash2 className="h-3.5 w-3.5" />
              Clear
            </Button>
            {types.size === 0 && (
              <span className="ml-auto text-2xs text-muted-foreground">
                Select at least one type
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {active && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              {isRunning
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
              Active job
              <Badge variant="outline" className="ml-2">{active.status}</Badge>
              {isRunning && (
                <Button size="sm" variant="ghost" className="ml-auto" onClick={cancelActive}>
                  <StopCircle className="h-4 w-4" />
                </Button>
              )}
            </CardTitle>
            <CardDescription className="text-2xs font-mono">
              {active.id} — {active.stage}: {active.message}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Progress value={Math.round((active.progress || 0) * 100)} className="mb-2" />
            <div ref={eventsScrollRef} className="max-h-64 overflow-y-auto rounded-sm border border-border bg-muted/30 px-3 py-2 font-mono text-2xs">
              {events.length === 0
                ? <span className="text-muted-foreground">waiting for events…</span>
                : events.map((e) => (
                  <div key={e.id} className="leading-tight">
                    <span className="text-muted-foreground">[{(e.progress * 100).toFixed(1)}%]</span>{" "}
                    <span className="text-foreground/80">{e.stage}</span>:{" "}
                    <span>{e.message}</span>
                  </div>
                ))}
            </div>
          </CardContent>
        </Card>
      )}
    </PageContainer>
  );
}
