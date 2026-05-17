import { useEffect, useMemo, useRef, useState } from "react";
import {
  Sparkles, StopCircle, RefreshCw, CheckCircle2, AlertTriangle, XCircle,
  Loader2, Clock, ChevronRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { PageHeader } from "@/components/PageHeader";
import { PageContainer } from "@/components/PageContainer";
import { api, JobEvent, JobRun } from "@/lib/api";
import { confirm } from "@/lib/confirm";
import { cn } from "@/lib/utils";

interface Options {
  cleanup: boolean;
  dedup: boolean;
  orphans: boolean;
  communities: boolean;
  summaries: boolean;
  chunk_embeddings: boolean;
  entity_embeddings: boolean;
  community_embeddings: boolean;
  community_levels: number;
}

const DEFAULTS: Options = {
  cleanup: true,
  dedup: false,
  orphans: false,
  communities: true,
  summaries: true,
  chunk_embeddings: false,
  entity_embeddings: true,
  community_embeddings: true,
  community_levels: 2,
};

export function PostProcessPage() {
  const [opts, setOpts] = useState<Options>(DEFAULTS);
  const [history, setHistory] = useState<JobRun[]>([]);
  const [active, setActive] = useState<JobRun | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<JobRun | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const lastEventId = useRef(0);
  const eventsScrollRef = useRef<HTMLDivElement>(null);

  const set = <K extends keyof Options>(k: K, v: Options[K]) =>
    setOpts((p) => ({ ...p, [k]: v }));

  const loadHistory = async () => {
    try {
      const r = await api.listJobs({ kind: "post_process", limit: 20 });
      setHistory(r.items);
      const live = r.items.find((j) =>
        j.status === "running" || j.status === "queued" || j.status === "cancelling");
      setActive(live || null);
      if (!selectedId && r.items[0]) setSelectedId(r.items[0].id);
    } catch (e) {
      // ignore — banner / next refresh will retry
    }
  };

  useEffect(() => { loadHistory(); }, []);

  // re-list while a job is live
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(loadHistory, 2000);
    return () => window.clearInterval(id);
  }, [active?.id, active?.status]);

  // detail + event tail for selected run
  useEffect(() => {
    if (!selectedId) { setDetail(null); setEvents([]); return; }
    let alive = true;
    lastEventId.current = 0;
    setEvents([]);
    setDetail(null);
    const load = async () => {
      try {
        const j = await api.getJob(selectedId);
        if (!alive) return;
        setDetail(j);
        const ev = await api.listJobEvents(selectedId, lastEventId.current);
        if (!alive) return;
        if (ev.events.length > 0) {
          lastEventId.current = ev.next_after;
          setEvents((prev) => [...prev, ...ev.events]);
        }
      } catch {}
    };
    load();
    const live = () => detail && (detail.status === "running" ||
                                  detail.status === "queued" ||
                                  detail.status === "cancelling");
    const id = window.setInterval(load, 1500);
    return () => { alive = false; window.clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  // auto-scroll while live
  useEffect(() => {
    if (!detail || (detail.status !== "running" && detail.status !== "cancelling")) return;
    const el = eventsScrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events, detail?.status]);

  const anyChecked = useMemo(
    () => opts.cleanup || opts.dedup || opts.orphans || opts.communities ||
          opts.summaries || opts.chunk_embeddings ||
          opts.entity_embeddings || opts.community_embeddings,
    [opts],
  );

  const run = async () => {
    setSubmitError(null);
    setSubmitting(true);
    try {
      const r = await api.runPostProcessing(opts);
      setSelectedId(r.job_id);
      await loadHistory();
    } catch (e: any) {
      setSubmitError(String(e?.message || e));
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = async (jobId: string) => {
    const ok = await confirm({
      title: "Cancel this post-processing job?",
      description:
        "The worker stops at the next safe checkpoint (between stages or " +
        "embedding batches). An in-flight LLM call finishes before cancel " +
        "takes effect. Partial graph state on disk is preserved — re-running " +
        "post-process resumes from there.",
      confirmText: "Cancel job",
      cancelText: "Keep running",
      variant: "destructive",
    });
    if (!ok) return;
    await api.cancelJob(jobId);
    await loadHistory();
  };

  return (
    <PageContainer
      maxWidth="max-w-[1400px]"
      header={
        <PageHeader
          title="Post-process"
          description="Schema cleanup, deduplication, orphan sweep, community detection, summaries and embeddings. Runs as a tracked, cancellable background job."
          actions={
            <Button variant="outline" size="sm" onClick={loadHistory}>
              <RefreshCw className="h-3.5 w-3.5" /> Refresh
            </Button>
          }
        />
      }
    >
      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        {/* ---- left column: config + active progress + selected run detail ---- */}
        <div className="space-y-4">
          {/* idempotency note */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Safe to re-run</CardTitle>
              <CardDescription className="text-xs">
                Each stage is idempotent. Re-running on the same graph produces the
                same result — duplicate merges become no-ops, orphan sweeps find
                nothing new, embeddings only fill missing vectors. Community
                metadata (title, summary, embedding) is preserved across rebuilds
                by hashing the member set, so the LLM is not re-billed when
                membership is unchanged. A second post-process job is blocked
                while one is already running — no risk of racing rebuilds.
              </CardDescription>
            </CardHeader>
          </Card>

          {/* config */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Stages</CardTitle>
              <CardDescription className="text-xs">
                Pick what to run. Best run after every ingest. Stages execute in
                the listed order.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Task name="Schema cleanup"
                    desc="LLM consolidates synonym labels & relationships (Disease/Illness/Disorder → Disease). Run before dedup so canonicalised labels feed grouping."
                    checked={opts.cleanup} onChange={(v) => set("cleanup", v)} />
              <Task name="Duplicate entity merge"
                    desc="Auto-merge __Entity__ nodes whose id normalises to the same key. Canonical = richest-provenance member. Use the Graph → Duplicates tool for manual review."
                    checked={opts.dedup} onChange={(v) => set("dedup", v)} />
              <Task name="Orphan sweep"
                    desc="Delete every __Entity__ that no Chunk points at. Safe — orphans are leftovers from re-extraction."
                    checked={opts.orphans} onChange={(v) => set("orphans", v)} />
              <Task name="Hierarchical communities (Louvain)"
                    desc="Multi-level community detection via networkx — no GDS required. Existing community titles, summaries and embeddings are preserved when membership is unchanged."
                    checked={opts.communities} onChange={(v) => set("communities", v)} />
              {opts.communities && (
                <div className="flex items-center gap-3 pl-9 text-xs">
                  <Label className="text-2xs">Levels</Label>
                  <Input type="number" min={1} max={4} className="w-20"
                         value={opts.community_levels}
                         onChange={(e) => set("community_levels",
                           Math.max(1, Math.min(4, parseInt(e.target.value || "2", 10))))} />
                  <span className="text-muted-foreground">1 = single level; 2–3 typical.</span>
                </div>
              )}
              <Task name="Community summaries"
                    desc="LLM writes a one-line title + 2–3 sentence summary per community ≥ 2 entities. Idempotent — existing non-empty summaries are kept."
                    checked={opts.summaries} onChange={(v) => set("summaries", v)}
                    disabled={!opts.communities}
                    hint={!opts.communities ? "requires community detection" : undefined} />
              <Task name="Chunk embeddings (backfill)"
                    desc="Backfill Chunk.embedding for chunks created without one — repairs failed-embedding ingests without re-running entity extraction. Skipped by default; enable after an embedding-provider outage."
                    checked={opts.chunk_embeddings}
                    onChange={(v) => set("chunk_embeddings", v)} />
              <Task name="Entity embeddings"
                    desc="Vectorise every __Entity__ missing an embedding. Idempotent — only fills the missing ones."
                    checked={opts.entity_embeddings}
                    onChange={(v) => set("entity_embeddings", v)} />
              <Task name="Community embeddings"
                    desc="Vectorise every __Community__ that has a summary but no embedding. Idempotent."
                    checked={opts.community_embeddings}
                    onChange={(v) => set("community_embeddings", v)}
                    disabled={!opts.communities}
                    hint={!opts.communities ? "requires community detection" : undefined} />

              <div className="flex items-center justify-end gap-2 border-t border-border pt-3">
                {submitError && (
                  <span className="mr-auto text-xs text-destructive">{submitError}</span>
                )}
                <Button size="sm" onClick={run}
                        disabled={!anyChecked || submitting || !!active}>
                  <Sparkles className="h-3.5 w-3.5" />
                  {active ? "Job in progress…" : submitting ? "Starting…" : "Run post-processing"}
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* selected run detail */}
          {detail && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-2">
                  <StatusIcon status={detail.status} />
                  <span>Run {detail.id.slice(0, 8)}</span>
                  <Badge variant="outline" className="text-2xs">{detail.status}</Badge>
                  {detail.status === "running" || detail.status === "cancelling" ? (
                    <Button variant="ghost" size="sm" className="ml-auto h-7 text-xs"
                            onClick={() => cancel(detail.id)}>
                      <StopCircle className="h-3.5 w-3.5" /> Cancel
                    </Button>
                  ) : null}
                </CardTitle>
                <CardDescription className="text-xs flex items-center gap-2">
                  <span>{detail.stage || "—"} · {detail.message || "—"}</span>
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <Progress value={Math.round((detail.progress || 0) * 100)} />
                <div className="grid grid-cols-2 gap-2 text-2xs text-muted-foreground sm:grid-cols-4">
                  <Stat label="Started" value={fmtTime(detail.started_at)} />
                  <Stat label="Ended" value={fmtTime(detail.ended_at)} />
                  <Stat label="Progress" value={`${Math.round((detail.progress || 0) * 100)}%`} />
                  <Stat label="Stages" value={Object.entries(detail.scope || {})
                    .filter(([_, v]) => v === true).map(([k]) => k).join(", ") || "—"} />
                </div>

                {/* result rollup */}
                {detail.result && (
                  <div className="rounded-sm border border-border bg-background/40 p-2 text-xs">
                    <RunResult result={detail.result} />
                  </div>
                )}

                {detail.error && (
                  <div className="rounded-sm border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
                    {detail.error}
                  </div>
                )}

                {/* event tail */}
                <div ref={eventsScrollRef}
                     className="max-h-72 overflow-y-auto rounded-sm border border-border bg-background/40 p-2 font-mono text-2xs">
                  {events.length === 0 ? (
                    <div className="text-muted-foreground">No events yet.</div>
                  ) : events.map((ev) => (
                    <div key={ev.id} className={cn(
                      "flex gap-2 py-0.5",
                      ev.level === "warn" && "text-warning",
                      ev.level === "error" && "text-destructive",
                    )}>
                      <span className="text-muted-foreground">{fmtTime(ev.ts)}</span>
                      <span className="font-semibold">{ev.stage}</span>
                      <span className="truncate">{ev.message}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>

        {/* ---- right column: history ---- */}
        <Card className="self-start">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Run history</CardTitle>
            <CardDescription className="text-xs">
              Most recent post-processing jobs.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {history.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                No runs yet.
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {history.map((j) => (
                  <li key={j.id}>
                    <button
                      onClick={() => setSelectedId(j.id)}
                      className={cn(
                        "flex w-full items-start gap-2 px-3 py-2 text-left text-xs hover:bg-accent/40",
                        selectedId === j.id && "bg-accent/60",
                      )}
                    >
                      <StatusIcon status={j.status} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono">{j.id.slice(0, 8)}</span>
                          <Badge variant="outline" className="text-2xs">{j.status}</Badge>
                        </div>
                        <div className="truncate text-muted-foreground">
                          {j.stage || "—"} · {j.message || "—"}
                        </div>
                        <div className="text-2xs text-muted-foreground">
                          {fmtTime(j.created_at)}
                        </div>
                      </div>
                      <ChevronRight className="h-3 w-3 self-center text-muted-foreground" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}

// ---------- small helpers ----------

function Task({ name, desc, checked, onChange, disabled, hint }: {
  name: string; desc: string; checked: boolean;
  onChange: (b: boolean) => void; disabled?: boolean; hint?: string;
}) {
  return (
    <label className={cn(
      "flex gap-3 rounded-sm border border-border bg-background/40 p-3",
      disabled ? "opacity-60 cursor-not-allowed" : "cursor-pointer hover:bg-accent/40",
    )}>
      <input type="checkbox" className="mt-0.5"
             checked={checked && !disabled}
             disabled={disabled}
             onChange={(e) => onChange(e.target.checked)} />
      <div className="min-w-0">
        <div className="text-sm font-medium">
          {name}
          {hint && <span className="ml-2 text-2xs text-muted-foreground">({hint})</span>}
        </div>
        <p className="text-xs text-muted-foreground">{desc}</p>
      </div>
    </label>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="uppercase tracking-wider">{label}</div>
      <div className="truncate text-foreground">{value}</div>
    </div>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (status === "running") return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-info" />;
  if (status === "queued") return <Clock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />;
  if (status === "cancelling") return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-warning" />;
  if (status === "succeeded") return <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" />;
  if (status === "failed") return <XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />;
  if (status === "cancelled") return <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" />;
  return <span className="h-3.5 w-3.5" />;
}

function RunResult({ result }: { result: any }) {
  const parts: string[] = [];
  if (result.cleanup) {
    parts.push(`cleanup: ${result.cleanup.node_renames ?? 0} labels · ${result.cleanup.rel_renames ?? 0} rels`);
  }
  if (result.dedup) {
    parts.push(`dedup: ${result.dedup.groups_merged ?? 0} groups · ${result.dedup.aliases_merged ?? 0} aliases`);
  }
  if (result.orphans) {
    parts.push(`orphans: ${result.orphans.deleted ?? 0}/${result.orphans.orphans_found ?? 0} deleted`);
  }
  if (result.communities) {
    const c = result.communities;
    parts.push(`communities: ${c.communities ?? 0}` +
      (c.per_level ? ` (per-level [${c.per_level.join(", ")}])` : "") +
      (c.restored ? ` · ${c.restored} restored` : "") +
      (c.summaries ? ` · summaries ${c.summaries.summarized ?? 0}/${c.summaries.considered ?? 0}` : ""));
  }
  if (result.chunk_embeddings) {
    parts.push(`chunk emb: ${result.chunk_embeddings.embedded ?? 0}`);
  }
  if (result.entity_embeddings) {
    parts.push(`entity emb: ${result.entity_embeddings.embedded ?? 0}`);
  }
  if (result.community_embeddings) {
    parts.push(`community emb: ${result.community_embeddings.embedded ?? 0}`);
  }
  if (result.elapsed_seconds != null) {
    parts.push(`elapsed ${result.elapsed_seconds}s`);
  }
  const errs: string[] = result.errors || [];
  return (
    <div>
      <div className="text-xs">{parts.join(" · ") || "—"}</div>
      {errs.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-2xs text-warning">
          {errs.map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      )}
    </div>
  );
}

function fmtTime(t: string | number | null | undefined): string {
  if (!t) return "—";
  try {
    const d = typeof t === "number" ? new Date(t * 1000) : new Date(t);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return String(t);
  }
}
