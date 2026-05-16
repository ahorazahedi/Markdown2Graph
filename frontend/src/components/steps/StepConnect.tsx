import { useEffect, useState } from "react";
import { CheckCircle2, XCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api, AppConfig } from "@/lib/api";

export function StepConnect({ onNext, config }: { onNext: () => void; config: AppConfig | null }) {
  const [health, setHealth] = useState<{ status: string; neo4j: string } | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      setHealth(await api.health());
    } catch (e) {
      setHealth({ status: "down", neo4j: "down" });
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    refresh();
  }, []);

  const neoUp = health?.neo4j === "up";
  const llmOk = !!config?.llm.configured;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Connect</CardTitle>
        <CardDescription>
          Credentials are loaded from <code className="text-foreground">.env</code> at the repo root. Confirm
          Neo4j and the LLM are reachable before continuing.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Card className="border-border/60">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">Neo4j</CardTitle>
                {neoUp ? (
                  <Badge variant="default" className="gap-1"><CheckCircle2 className="h-3 w-3" /> up</Badge>
                ) : (
                  <Badge variant="destructive" className="gap-1"><XCircle className="h-3 w-3" /> down</Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-1 text-sm text-muted-foreground">
              <div><span className="text-foreground">URI:</span> {config?.neo4j.uri}</div>
              <div><span className="text-foreground">User:</span> {config?.neo4j.username}</div>
              <div><span className="text-foreground">DB:</span> {config?.neo4j.database}</div>
            </CardContent>
          </Card>

          <Card className="border-border/60">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">LLM</CardTitle>
                {llmOk ? (
                  <Badge variant="default" className="gap-1"><CheckCircle2 className="h-3 w-3" /> ready</Badge>
                ) : (
                  <Badge variant="destructive">no api key</Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-1 text-sm text-muted-foreground">
              <div><span className="text-foreground">Model:</span> {config?.llm.model}</div>
              <div className="break-all"><span className="text-foreground">Base URL:</span> {config?.llm.base_url}</div>
              <div><span className="text-foreground">Domain:</span> {config?.domain}</div>
            </CardContent>
          </Card>
        </div>

        <div className="flex items-center justify-between">
          <Button variant="outline" onClick={refresh} disabled={loading}>
            <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Re-check
          </Button>
          <Button onClick={onNext} disabled={!neoUp || !llmOk}>
            Continue
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
