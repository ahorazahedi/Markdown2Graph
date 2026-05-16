import { useEffect, useState } from "react";
import { CheckCircle2, XCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/PageHeader";
import { api, AppConfig } from "@/lib/api";

export function SetupPage({ config }: { config: AppConfig | null }) {
  const [health, setHealth] = useState<{ status: string; neo4j: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    setBusy(true);
    try { setHealth(await api.health()); }
    catch { setHealth({ status: "down", neo4j: "down" }); }
    finally { setBusy(false); }
  };
  useEffect(() => { refresh(); }, []);

  const neoUp = health?.neo4j === "up";
  const llmOk = !!config?.llm.configured;

  return (
    <>
      <PageHeader
        title="Setup"
        description="Loaded from .env at the repo root. Restart the backend after editing."
        actions={
          <Button variant="outline" size="sm" onClick={refresh} disabled={busy}>
            <RefreshCw className={busy ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
            Re-check
          </Button>
        }
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Neo4j</CardTitle>
            {neoUp
              ? <Badge variant="success"><CheckCircle2 className="mr-1 h-3 w-3" />up</Badge>
              : <Badge variant="destructive"><XCircle className="mr-1 h-3 w-3" />down</Badge>}
          </CardHeader>
          <CardContent className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <span className="text-muted-foreground">URI</span><span className="font-mono text-xs">{config?.neo4j.uri}</span>
            <span className="text-muted-foreground">User</span><span className="font-mono text-xs">{config?.neo4j.username}</span>
            <span className="text-muted-foreground">DB</span><span className="font-mono text-xs">{config?.neo4j.database}</span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>LLM</CardTitle>
            {llmOk
              ? <Badge variant="success"><CheckCircle2 className="mr-1 h-3 w-3" />ready</Badge>
              : <Badge variant="destructive">no api key</Badge>}
          </CardHeader>
          <CardContent className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <span className="text-muted-foreground">Model</span><span className="font-mono text-xs">{config?.llm.model}</span>
            <span className="text-muted-foreground">Base URL</span><span className="break-all font-mono text-xs">{config?.llm.base_url}</span>
            <span className="text-muted-foreground">Embedding</span>
            <span className="font-mono text-xs">{config?.embedding.model} <span className="text-muted-foreground">({config?.embedding.dimension}d)</span></span>
            <span className="text-muted-foreground">Provider</span><span className="font-mono text-xs">{config?.embedding.provider}</span>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
