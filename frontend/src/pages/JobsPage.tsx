import { useEffect, useMemo, useRef, useState } from "react";
import {
  RefreshCw, ChevronLeft, ChevronRight, CheckCircle2, XCircle, Loader2,
  Clock, AlertTriangle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { api, JobEvent, JobOverview, JobRun } from "@/lib/api";
import { cn } from "@/lib/utils";

const PAGE = 50;

/** Full-bleed page (mounted directly under AppShell.main, no PageContainer). */
export function JobsPage() {
  const [items, setItems] = useState<JobRun[]>([]);
  const [overview, setOverview] = useState<JobOverview | null>(null);
  const [status, setStatus] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(idFromHash());
  const [detail, setDetail] = useState<JobRun | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [tailing, setTailing] = useState(true);
  const [busy, setBusy] = useState(false);
  const [levelFilter, setLevelFilter] = useState<string>("");
  const lastEventId = useRef<number>(0);
  const eventsScrollRef = useRef<HTMLDivElement>(null);

  const refresh = async () => {
    setBusy(true);
    try {
      const r = await api.listJobs({ status: status || undefined, limit: PAGE, offset });
      setItems(r.items);
      setOverview(r.overview);
      setTotal(r.overview.total);
      if (!selectedId && r.items.length > 0) setSelectedId(r.items[0].id);
    } finally { setBusy(false); }
  };

  useEffect(() => { refresh(); }, [status, offset]);

  // re-list while anything is running
  useEffect(() => {
    if (!items.some((j) => j.status === "running" || j.status === "queued")) return;
    const id = window.setInterval(refresh, 2500);
    return () => window.clearInterval(id);
  }, [items, status, offset]);

  // detail + tail
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
        const after = lastEventId.current;
        const ev = await api.listJobEvents(selectedId, after, levelFilter || undefined);
        if (!alive) return;
        if (ev.events.length > 0) {
          lastEventId.current = ev.next_after;
          setEvents((prev) => [...prev, ...ev.events]);
        }
        // stop tailing when terminal
        if (j.status === "succeeded" || j.status === "failed") {
          setTailing(false);
        }
      } catch {}
    };
    load();
    const id = window.setInterval(() => { if (tailing) load(); }, 1500);
    return () => { alive = false; window.clearInterval(id); };
  }, [selectedId, levelFilter, tailing]);

  // auto-scroll tail
  useEffect(() => {
    if (!tailing) return;
    const el = eventsScrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events, tailing]);

  // reflect ?id= in hash so banner click works
  useEffect(() => {
    const onHash = () => {
      const id = idFromHash();
      if (id && id !== selectedId) setSelectedId(id);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [selectedId]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE));
  const page = Math.floor(offset / PAGE) + 1;

  return (
    <div className="flex h-full flex-col">
      <header className="shrink-0 border-b border-border bg-card/30">
        <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3 px-6 py-4">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold tracking-tightish">Jobs</h1>
            <p className="text-xs text-muted-foreground">
              Persistent run history. Every ingest writes events here in real time.
            </p>
          </div>
          <div className="flex items-center gap-5 text-sm">
            <Stat label="Total"     value={overview?.total} />
            <Stat label="Running"   value={overview?.running} accent={overview?.running ? "running" : undefined} />
            <Stat label="Succeeded" value={overview?.succeeded} accent={overview?.succeeded ? "success" : undefined} />
            <Stat label="Failed"    value={overview?.failed} accent={overview?.failed ? "destructive" : undefined} />
            <Stat label="Queued"    value={overview?.queued} muted />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-border px-6 py-2">
          <FilterChip
            value={status}
            options={[
              { value: "", label: "All status" },
              { value: "running", label: "running" },
              { value: "succeeded", label: "succeeded" },
              { value: "failed", label: "failed" },
              { value: "queued", label: "queued" },
            ]}
            onChange={(v) => { setOffset(0); setStatus(v); }}
          />
          <div className="ml-auto flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={refresh} disabled={busy}>
              <RefreshCw className={busy ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
            </Button>
          </div>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[340px_minmax(0,1fr)]">
        {/* list */}
        <aside className="flex min-h-0 flex-col border-r border-border">
          <div className="flex-1 overflow-y-auto">
            {items.length === 0
              ? <div className="px-6 py-10 text-center text-sm text-muted-foreground">No runs yet.</div>
              : items.map((j) => <JobRowItem key={j.id} job={j} active={j.id === selectedId} onSelect={() => setSelectedId(j.id)} />)}
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

        {/* detail */}
        <section className="flex min-h-0 flex-col">
          {detail
            ? <JobDetail job={detail} events={events} tailing={tailing} onTailChange={setTailing}
                         levelFilter={levelFilter} onLevelChange={setLevelFilter}
                         eventsScrollRef={eventsScrollRef} />
            : <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Pick a run.</div>}
        </section>
      </div>
    </div>
  );
}

/* ----------------------------- subcomponents ----------------------------- */

function JobRowItem({ job, active, onSelect }: { job: JobRun; active: boolean; onSelect: () => void }) {
  const I = statusIcon(job.status);
  const color = statusColor(job.status);
  const pct = Math.round((job.progress ?? 0) * 100);
  const scopeText = describeScope(job.scope);
  return (
    <button
      onClick={onSelect}
      className={cn(
        "flex w-full items-start gap-3 border-b border-border px-3 py-2.5 text-left transition-colors",
        active ? "bg-accent" : "hover:bg-accent/50",
      )}
    >
      <I.icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", color, job.status === "running" && "animate-spin")} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="font-mono text-2xs">{job.id.slice(0, 8)}</span>
          <span className="text-2xs text-muted-foreground">{fmtRelative(job.created_at)}</span>
        </div>
        <div className="mt-0.5 truncate text-xs text-foreground">{scopeText}</div>
        <div className="mt-1 flex items-center justify-between gap-3 text-2xs">
          <Badge variant={statusVariant(job.status)}>{job.status}</Badge>
          {job.status === "running" && <span className="tabular-nums text-muted-foreground">{pct}%</span>}
          {job.result?.totals?.entities != null && (
            <span className="tabular-nums text-muted-foreground">
              {job.result.totals.entities} entities
            </span>
          )}
        </div>
      </div>
    </button>
  );
}

function JobDetail({
  job, events, tailing, onTailChange, levelFilter, onLevelChange, eventsScrollRef,
}: {
  job: JobRun; events: JobEvent[];
  tailing: boolean; onTailChange: (v: boolean) => void;
  levelFilter: string; onLevelChange: (v: string) => void;
  eventsScrollRef: React.RefObject<HTMLDivElement>;
}) {
  const pct = Math.round((job.progress ?? 0) * 100);
  const elapsed = useMemo(() => {
    if (!job.started_at) return 0;
    const start = new Date(job.started_at).getTime();
    const end = job.ended_at ? new Date(job.ended_at).getTime() : Date.now();
    return Math.max(0, Math.round((end - start) / 1000));
  }, [job]);
  const errorCount = events.filter((e) => e.level === "error").length;
  const warnCount = events.filter((e) => e.level === "warn").length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* meta strip */}
      <div className="shrink-0 border-b border-border bg-card/30 px-6 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold">{job.id.slice(0, 12)}</span>
            <Badge variant={statusVariant(job.status)}>{job.status}</Badge>
            {errorCount > 0 && (
              <Badge variant="destructive">
                <AlertTriangle className="mr-1 h-3 w-3" />{errorCount} error{errorCount === 1 ? "" : "s"}
              </Badge>
            )}
            {warnCount > 0 && (
              <Badge variant="warning">{warnCount} warning{warnCount === 1 ? "" : "s"}</Badge>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-2xs">
            <Meta k="Kind"     v={<span className="font-mono">{job.kind}</span>} />
            <Meta k="Progress" v={`${pct}%`} />
            <Meta k="Elapsed"  v={`${elapsed}s`} />
            <Meta k="Created"  v={fmtTime(job.created_at)} />
            {job.ended_at && <Meta k="Ended" v={fmtTime(job.ended_at)} />}
          </div>
        </div>
        <div className="mt-2 flex items-center gap-3">
          <Progress value={pct} className="h-1.5 flex-1" />
          <span className="text-2xs tabular-nums text-muted-foreground">{pct}%</span>
        </div>
        <div className="mt-2 text-xs text-foreground/90">{job.message}</div>
        {job.scope && Object.keys(job.scope).length > 0 && (
          <div className="mt-1 text-2xs text-muted-foreground">
            {describeScope(job.scope)}
          </div>
        )}
      </div>

      {job.error && (
        <div className="shrink-0 border-b border-destructive/30 bg-destructive/10 px-6 py-2">
          <pre className="whitespace-pre-wrap break-words text-xs text-destructive">{job.error}</pre>
        </div>
      )}

      {/* events */}
      <div className="flex shrink-0 items-center justify-between border-b border-border bg-background/95 px-6 py-2">
        <div className="flex items-center gap-2 text-2xs">
          <span className="font-semibold uppercase tracking-wider">Events</span>
          <span className="text-muted-foreground tabular-nums">{events.length}</span>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={levelFilter}
            onChange={(e) => onLevelChange(e.target.value)}
            className="h-7 rounded-sm border border-border bg-background px-2 text-2xs"
          >
            <option value="">all levels</option>
            <option value="info">info</option>
            <option value="warn">warn</option>
            <option value="error">error</option>
          </select>
          <label className="flex select-none items-center gap-1.5 text-2xs text-muted-foreground">
            <input type="checkbox" checked={tailing} onChange={(e) => onTailChange(e.target.checked)} />
            tail
          </label>
        </div>
      </div>

      <div ref={eventsScrollRef} className="flex-1 overflow-y-auto bg-background px-6 py-2">
        {events.length === 0
          ? <p className="text-sm text-muted-foreground">No events yet.</p>
          : (
            <ul className="space-y-0 font-mono text-xs leading-relaxed">
              {events.map((e) => (
                <li key={e.id} className="grid grid-cols-[5.5rem_4.5rem_3.25rem_1fr] gap-x-2 py-0.5">
                  <span className="text-muted-foreground tabular-nums">
                    {new Date(e.ts).toLocaleTimeString()}
                  </span>
                  <span className={cn("uppercase tracking-wider tabular-nums",
                    e.level === "error" ? "text-destructive" :
                    e.level === "warn" ? "text-[hsl(var(--warning))]" : "text-muted-foreground")}>
                    {e.level}
                  </span>
                  <span className="truncate text-muted-foreground/90">{e.stage}</span>
                  <span className={cn(e.level === "error" ? "text-destructive"
                                       : e.level === "warn" ? "text-[hsl(var(--warning))]"
                                       : "text-foreground")}>
                    {e.message}
                  </span>
                </li>
              ))}
            </ul>
          )}
      </div>
    </div>
  );
}

/* ------------------------------- helpers --------------------------------- */

function idFromHash(): string | null {
  const m = /#\/jobs\?id=([^&]+)/.exec(window.location.hash);
  return m ? decodeURIComponent(m[1]) : null;
}

function Stat({ label, value, accent, muted }: {
  label: string; value?: number | null;
  accent?: "success" | "destructive" | "running"; muted?: boolean;
}) {
  const color =
    accent === "destructive" && value ? "text-destructive"
    : accent === "success"     && value ? "text-[hsl(var(--success))]"
    : accent === "running"     && value ? "text-[hsl(var(--warning))]"
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

function Meta({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="uppercase tracking-wider text-muted-foreground">{k}</span>
      <span className="text-foreground">{v}</span>
    </span>
  );
}

function FilterChip({ value, options, onChange }: {
  value: string; options: { value: string; label: string }[]; onChange: (v: string) => void;
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

function statusIcon(s: string) {
  if (s === "succeeded") return { icon: CheckCircle2 };
  if (s === "failed")    return { icon: XCircle };
  if (s === "running")   return { icon: Loader2 };
  return { icon: Clock };
}
function statusColor(s: string) {
  if (s === "succeeded") return "text-[hsl(var(--success))]";
  if (s === "failed")    return "text-destructive";
  if (s === "running")   return "text-[hsl(var(--warning))]";
  return "text-muted-foreground";
}
function statusVariant(s: string): "success" | "destructive" | "warning" | "secondary" {
  if (s === "succeeded") return "success";
  if (s === "failed")    return "destructive";
  if (s === "running")   return "warning";
  return "secondary";
}

function describeScope(scope: any): string {
  if (!scope) return "";
  const reextract = scope.reextract ? " (re-extract)" : "";
  if (Array.isArray(scope.document_ids)) {
    return `${scope.document_ids.length} document${scope.document_ids.length === 1 ? "" : "s"}${reextract}`;
  }
  if (scope.document_ids === "all-pending") return `all pending${reextract}`;
  return reextract ? "re-extract" : "all pending";
}

function fmtTime(iso: string | null) {
  if (!iso) return "—";
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
