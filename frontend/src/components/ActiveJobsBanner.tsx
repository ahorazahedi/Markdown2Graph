import { useEffect, useState } from "react";
import { Loader2, ChevronRight, X, StopCircle } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api, JobRun } from "@/lib/api";
import { cn } from "@/lib/utils";
import { confirm } from "@/lib/confirm";

/**
 * Fixed bottom banner that surfaces running jobs across every page.
 * Polls /api/jobs?status=running while at least one job is active.
 * Click → navigates to /jobs/<id> detail (Logs page).
 */
export function ActiveJobsBanner() {
  const [running, setRunning] = useState<JobRun[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState(false);

  const [cancelling, setCancelling] = useState<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        // include cancelling jobs so the banner reflects the transition state
        const [r1, r2] = await Promise.all([
          api.listJobs({ status: "running", limit: 10 }),
          api.listJobs({ status: "cancelling", limit: 10 }),
        ]);
        if (!alive) return;
        setRunning([...r1.items, ...r2.items]);
      } catch {}
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  const onCancel = async (id: string) => {
    const ok = await confirm({
      title: "Cancel this job?",
      description: "An in-flight LLM call finishes before the worker exits. Partial progress is preserved.",
      confirmText: "Cancel job",
      cancelText: "Keep running",
      variant: "destructive",
    });
    if (!ok) return;
    setCancelling((s) => new Set([...s, id]));
    try { await api.cancelJob(id); } catch (e) { console.error(e); }
  };

  const visible = running.filter((j) => !dismissed.has(j.id));
  if (visible.length === 0) return null;

  return (
    <div className="pointer-events-none fixed bottom-3 right-3 z-40 flex w-[min(420px,calc(100vw-1.5rem))] flex-col gap-2">
      {visible.map((j) => (
        <div key={j.id}
             className="pointer-events-auto overflow-hidden rounded-md border border-border bg-card shadow-lg">
          <div className="flex items-center gap-2 px-3 py-2">
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-foreground" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-2xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {j.stage || "running"}
                </span>
                <span className="font-mono text-2xs text-muted-foreground">{j.id.slice(0, 8)}</span>
                <Badge variant="warning" className="ml-auto">{j.status}</Badge>
              </div>
              {!collapsed && (
                <div className="mt-1 truncate text-xs text-foreground">{j.message}</div>
              )}
            </div>
            {j.status === "running" && !cancelling.has(j.id) && (
              <button
                onClick={() => onCancel(j.id)}
                className="rounded-sm p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                title="Cancel job (cooperative — finishes current LLM call first)"
              >
                <StopCircle className="h-3.5 w-3.5" />
              </button>
            )}
            <button
              onClick={() => setDismissed((s) => new Set([...s, j.id]))}
              className="rounded-sm p-1 text-muted-foreground hover:bg-accent"
              title="Hide (job keeps running)"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <Progress value={Math.round((j.progress ?? 0) * 100)} className="h-1 rounded-none" />
          <button
            onClick={() => { window.location.hash = `#/jobs?id=${j.id}`; }}
            className={cn(
              "flex w-full items-center justify-between gap-2 border-t border-border px-3 py-1.5",
              "text-2xs uppercase tracking-wider text-muted-foreground hover:bg-accent",
            )}
          >
            <span>{Math.round((j.progress ?? 0) * 100)}% · view logs</span>
            <ChevronRight className="h-3 w-3" />
          </button>
        </div>
      ))}
      {visible.length > 1 && (
        <Button size="sm" variant="ghost" className="self-end pointer-events-auto"
                onClick={() => setCollapsed((v) => !v)}>
          {collapsed ? "expand" : "collapse"}
        </Button>
      )}
    </div>
  );
}
