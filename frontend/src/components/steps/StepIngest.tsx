import { useEffect, useRef, useState } from "react";
import { Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { api, JobSnapshot } from "@/lib/api";
import { SchemaState } from "./StepSchema";

export function StepIngest({
  path,
  schema,
  onBack,
  onDone,
}: {
  path: string;
  schema: SchemaState;
  onBack: () => void;
  onDone: () => void;
}) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const start = async () => {
    setError(null);
    try {
      const r = await api.ingest({
        path,
        allowed_nodes: schema.nodes,
        allowed_relationships: schema.triplets,
        extra_instructions: schema.extra || undefined,
      });
      setJobId(r.job_id);
    } catch (e: any) {
      setError(String(e.message || e));
    }
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
        }
      } catch (e) {
        // transient
      }
    };
    tick();
    pollRef.current = window.setInterval(tick, 1500);
    return () => {
      alive = false;
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [jobId]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Ingest</CardTitle>
        <CardDescription>
          Build the knowledge graph. Each file is chunked, embedded, and passed to the LLM with the
          approved schema. Live progress below.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {!jobId && (
          <div className="rounded-md border border-border bg-muted/30 p-4 text-sm">
            <div><span className="font-medium">Path:</span> {path}</div>
            <div><span className="font-medium">Node labels:</span> {schema.nodes.length}</div>
            <div><span className="font-medium">Relationship triplets:</span> {schema.triplets.length}</div>
          </div>
        )}

        {!jobId ? (
          <Button onClick={start} disabled={!path || schema.nodes.length === 0}>
            <Play className="h-4 w-4" /> Start ingestion
          </Button>
        ) : (
          <>
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium">{job?.stage || "starting"}</span>
              <Badge variant={job?.status === "failed" ? "destructive" : "secondary"}>
                {job?.status || "queued"}
              </Badge>
            </div>
            <Progress value={(job?.progress ?? 0) * 100} />
            <div className="text-sm text-muted-foreground">{job?.message}</div>
            <div className="max-h-72 overflow-auto rounded-md border border-border bg-background/40 p-2 font-mono text-xs">
              {(job?.events_tail ?? []).map((e, i) => (
                <div key={i}>
                  <span className="text-muted-foreground">[{(e.progress * 100).toFixed(1)}%]</span>{" "}
                  <span className="text-primary">{e.stage}</span> · {e.message}
                </div>
              ))}
            </div>
            {job?.error && (
              <pre className="max-h-40 overflow-auto rounded bg-destructive/10 p-2 text-xs text-destructive">
                {job.error}
              </pre>
            )}
          </>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack} disabled={job?.status === "running"}>
            Back
          </Button>
          <Button
            onClick={onDone}
            disabled={!(job?.status === "succeeded" || job?.status === "failed")}
          >
            View results
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
