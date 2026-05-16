import { useEffect, useState } from "react";
import { Database, ExternalLink, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/PageHeader";
import { api, AppConfig } from "@/lib/api";

export function GraphPage({ config }: { config: AppConfig | null }) {
  const [stats, setStats] = useState<Record<string, number> | null>(null);
  const [schema, setSchema] = useState<{ labels: string[]; relationship_types: string[] } | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    setBusy(true);
    try {
      const [s, sc] = await Promise.all([api.stats(), api.graphSchema()]);
      setStats(s);
      setSchema(sc);
    } finally { setBusy(false); }
  };
  useEffect(() => { refresh(); }, []);

  const browserHref = config?.neo4j.uri.replace(/^bolt:\/\//, "http://").replace(/:7687/, ":7474");

  return (
    <>
      <PageHeader
        title="Graph"
        description="Live state of the Neo4j database."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={refresh} disabled={busy}>
              <RefreshCw className={busy ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} /> Refresh
            </Button>
            {browserHref && (
              <a href={browserHref} target="_blank" rel="noreferrer"
                 className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-border px-3 text-sm hover:bg-accent">
                <ExternalLink className="h-3.5 w-3.5" /> Open Browser
              </a>
            )}
            <Button variant="destructive" size="sm"
                    onClick={async () => {
                      if (!confirm("Delete the entire graph? This wipes every node and relationship.")) return;
                      await api.clearGraph(); refresh();
                    }}>
              <Trash2 className="h-3.5 w-3.5" /> Clear
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {[
          ["documents", "Documents"],
          ["chunks", "Chunks"],
          ["entities", "Entities"],
          ["entity_relationships", "Entity rels"],
          ["has_entity_relationships", "HAS_ENTITY"],
        ].map(([k, label]) => (
          <div key={k} className="rounded-sm border border-border bg-card px-3 py-2">
            <div className="text-2xs uppercase tracking-wider text-muted-foreground">{label as string}</div>
            <div className="text-xl font-semibold tabular-nums tracking-tightish">{stats?.[k as string] ?? "—"}</div>
          </div>
        ))}
      </div>

      {schema && (
        <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Node labels</CardTitle>
              <CardDescription>{schema.labels.length} labels in graph</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-1.5">
              {schema.labels.map((l) => <Badge key={l} variant="outline" className="text-xs">{l}</Badge>)}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Relationship types</CardTitle>
              <CardDescription>{schema.relationship_types.length} types</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-1.5">
              {schema.relationship_types.map((l) => <Badge key={l} variant="secondary" className="text-xs">{l}</Badge>)}
            </CardContent>
          </Card>
        </div>
      )}
    </>
  );
}
