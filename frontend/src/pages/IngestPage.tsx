import { useEffect, useRef, useState } from "react";
import { Play, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/PageHeader";
import { DocumentStatusBadge } from "@/components/StatusBadge";
import { api, DocumentRow, JobSnapshot } from "@/lib/api";

export function IngestPage() {
  const [docs, setDocs] = useState<DocumentRow[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [reextract, setReextract] = useState(false);
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const refresh = async () => {
    const r = await api.listDocuments();
    setDocs(r.items);
  };
  useEffect(() => { refresh(); }, []);

  const start = async (mode: "selected" | "pending") => {
    setError(null);
    try {
      const body =
        mode === "selected"
          ? { document_ids: Array.from(selected), reextract }
          : { reextract: false };
      const r = await api.runIngest(body);
      setJobId(r.job_id);
    } catch (e: any) { setError(String(e.message || e)); }
  };

  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    const tick = async () => {
      try {
        const j = await api.jobStatus(jobId);
        if (!alive) return;
        setJob(j);
        if (j.status === "succeeded" || j.status === "failed") {
          if (pollRef.current) window.clearInterval(pollRef.current);
          refresh();
        }
      } catch {}
    };
    tick();
    pollRef.current = window.setInterval(tick, 1500);
    return () => { alive = false; if (pollRef.current) window.clearInterval(pollRef.current); };
  }, [jobId]);

  const toggle = (id: number) => {
    const n = new Set(selected);
    n.has(id) ? n.delete(id) : n.add(id);
    setSelected(n);
  };
  const toggleAll = () => {
    if (selected.size === docs.length) setSelected(new Set());
    else setSelected(new Set(docs.map((d) => d.id)));
  };

  const pendingCount = docs.filter((d) => d.status === "pending" || d.status === "failed").length;

  return (
    <>
      <PageHeader
        title="Ingest"
        description="Run extraction over selected documents, or all pending. Re-extract wipes prior graph state for those documents and rebuilds it from scratch."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => start("pending")} disabled={pendingCount === 0}>
              <Play className="h-3.5 w-3.5" /> Run pending ({pendingCount})
            </Button>
            <Button size="sm" onClick={() => start("selected")} disabled={selected.size === 0}>
              {reextract ? <RotateCcw className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
              {reextract ? "Re-extract" : "Run"} selected ({selected.size})
            </Button>
          </>
        }
      />

      <Card className="mb-4">
        <CardContent className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={reextract} onChange={(e) => setReextract(e.target.checked)} />
            Re-extract (wipe previous graph state for selected docs)
          </label>
          <span className="ml-auto text-xs text-muted-foreground">
            {selected.size} / {docs.length} selected
          </span>
        </CardContent>
      </Card>

      {/* progress */}
      {job && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              Job <span className="font-mono text-xs text-muted-foreground">{job.id.slice(0, 8)}</span>
              <Badge variant={job.status === "failed" ? "destructive" : job.status === "succeeded" ? "success" : "warning"}>
                {job.status}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Progress value={(job.progress ?? 0) * 100} />
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{job.message}</span>
              <span className="tabular-nums">{Math.round((job.progress ?? 0) * 100)}%</span>
            </div>
            <div className="max-h-56 overflow-auto rounded-sm border border-border bg-muted/30 p-2 font-mono text-2xs">
              {job.events_tail.map((e, i) => (
                <div key={i}>
                  <span className="text-muted-foreground">[{(e.progress * 100).toFixed(1)}%]</span>{" "}
                  <span className="text-foreground">{e.stage}</span> · {e.message}
                </div>
              ))}
            </div>
            {job.error && (
              <pre className="rounded-sm border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive whitespace-pre-wrap">
                {job.error}
              </pre>
            )}
          </CardContent>
        </Card>
      )}

      {/* selection table */}
      <Card>
        <CardHeader>
          <CardTitle>Documents</CardTitle>
          <CardDescription>Select files to include in the run.</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/30 text-2xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="w-8 px-3 py-2">
                  <input type="checkbox"
                         checked={selected.size > 0 && selected.size === docs.length}
                         onChange={toggleAll} />
                </th>
                <th className="px-3 py-2 text-left">File</th>
                <th className="px-3 py-2 text-left">Status</th>
                <th className="px-3 py-2 text-right">Chunks</th>
                <th className="px-3 py-2 text-right">Entities</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id} className="border-b border-border last:border-0 hover:bg-accent/30">
                  <td className="px-3 py-1.5">
                    <input type="checkbox" checked={selected.has(d.id)} onChange={() => toggle(d.id)} />
                  </td>
                  <td className="px-3 py-1.5 font-mono text-xs">{d.file_name}</td>
                  <td className="px-3 py-1.5"><DocumentStatusBadge status={d.status} /></td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{d.chunk_count}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{d.entity_count}</td>
                </tr>
              ))}
              {docs.length === 0 && (
                <tr><td colSpan={5} className="px-3 py-6 text-center text-sm text-muted-foreground">
                  No documents. Upload some on the Documents page.
                </td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {error && <div className="mt-3 text-sm text-destructive">{error}</div>}
    </>
  );
}
