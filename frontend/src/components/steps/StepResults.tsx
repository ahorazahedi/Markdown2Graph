import { useEffect, useState } from "react";
import { Database, Trash2, RotateCcw, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api, AppConfig } from "@/lib/api";

export function StepResults({
  config,
  onRestart,
}: {
  config: AppConfig | null;
  onRestart: () => void;
}) {
  const [stats, setStats] = useState<Record<string, number> | null>(null);
  const [schema, setSchema] = useState<{ labels: string[]; relationship_types: string[] } | null>(null);
  const [docs, setDocs] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    setBusy(true);
    try {
      const [s, sc, d] = await Promise.all([api.stats(), api.schema(), api.documents()]);
      setStats(s);
      setSchema(sc);
      setDocs(d.documents);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    refresh();
  }, []);

  const browserHref = config?.neo4j.uri.replace(/^bolt:\/\//, "http://").replace(/:7687/, ":7474");

  return (
    <Card>
      <CardHeader>
        <CardTitle>Results</CardTitle>
        <CardDescription>Live counts from the Neo4j database.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {[
            ["documents", "Documents"],
            ["chunks", "Chunks"],
            ["entities", "Entities"],
            ["entity_relationships", "Entity rels"],
            ["has_entity_relationships", "HAS_ENTITY"],
          ].map(([k, label]) => (
            <Card key={k} className="border-border/60">
              <CardHeader className="pb-2">
                <CardDescription>{label}</CardDescription>
                <CardTitle className="text-3xl tabular-nums">{stats?.[k as string] ?? "—"}</CardTitle>
              </CardHeader>
            </Card>
          ))}
        </div>

        {schema && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <div className="mb-2 text-sm font-medium">Node labels in DB</div>
              <div className="flex flex-wrap gap-1.5">
                {schema.labels.map((l) => (
                  <Badge key={l} variant="secondary">{l}</Badge>
                ))}
              </div>
            </div>
            <div>
              <div className="mb-2 text-sm font-medium">Relationship types in DB</div>
              <div className="flex flex-wrap gap-1.5">
                {schema.relationship_types.map((l) => (
                  <Badge key={l} variant="outline">{l}</Badge>
                ))}
              </div>
            </div>
          </div>
        )}

        {docs.length > 0 && (
          <div className="space-y-2">
            <div className="text-sm font-medium">Documents</div>
            <div className="overflow-auto rounded-md border border-border">
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-left">
                  <tr>
                    <th className="px-3 py-2">File</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2 text-right">Chunks</th>
                    <th className="px-3 py-2 text-right">Entities</th>
                    <th className="px-3 py-2 text-right">Rels</th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) => (
                    <tr key={d.fileName} className="border-t border-border">
                      <td className="px-3 py-1.5 font-mono text-xs">{d.fileName}</td>
                      <td className="px-3 py-1.5">
                        <Badge variant={d.status === "Completed" ? "default" : d.status === "Failed" ? "destructive" : "secondary"}>
                          {d.status || "—"}
                        </Badge>
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{d.chunks ?? 0}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{d.entities ?? 0}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{d.rels ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={refresh} disabled={busy}>
            <Database className="h-4 w-4" /> Refresh
          </Button>
          {browserHref && (
            <a
              href={browserHref}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-10 items-center gap-2 rounded-md border border-input bg-transparent px-4 text-sm font-medium hover:bg-accent"
            >
              <ExternalLink className="h-4 w-4" /> Open Neo4j Browser
            </a>
          )}
          <Button variant="destructive" onClick={async () => {
            if (!confirm("Delete every node and relationship in the graph?")) return;
            await api.clear();
            await refresh();
          }}>
            <Trash2 className="h-4 w-4" /> Clear graph
          </Button>
          <div className="flex-1" />
          <Button onClick={onRestart}>
            <RotateCcw className="h-4 w-4" /> Run another ingest
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
