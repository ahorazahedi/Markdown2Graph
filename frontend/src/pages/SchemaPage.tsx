import { useEffect, useState } from "react";
import { Sparkles, Plus, X, Save, Check, History } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/PageHeader";
import { api, Schema, SchemaVersion, Triplet } from "@/lib/api";

export function SchemaPage() {
  const [schema, setSchema] = useState<Schema | null>(null);
  const [nodes, setNodes] = useState<string[]>([]);
  const [triplets, setTriplets] = useState<Triplet[]>([]);
  const [extra, setExtra] = useState("");
  const [versions, setVersions] = useState<SchemaVersion[]>([]);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newNode, setNewNode] = useState("");
  const [newRel, setNewRel] = useState({ src: "", type: "", dst: "" });
  const [discoverExtra, setDiscoverExtra] = useState("");
  const [sampleSize, setSampleSize] = useState(5);

  const refresh = async () => {
    const s = await api.getSchema();
    setSchema(s);
    setNodes(s.node_labels);
    setTriplets(s.triplets as Triplet[]);
    setExtra(s.extra || "");
    setVersions((await api.schemaVersions()).versions);
  };
  useEffect(() => { refresh(); }, []);

  const dirty =
    schema &&
    (JSON.stringify(nodes) !== JSON.stringify(schema.node_labels) ||
      JSON.stringify(triplets) !== JSON.stringify(schema.triplets) ||
      extra !== (schema.extra || ""));

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveSchema({ node_labels: nodes, triplets, extra, source: "manual" });
      setSchema(saved);
      setSavedAt(Date.now());
      setVersions((await api.schemaVersions()).versions);
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  const discover = async () => {
    setDiscovering(true);
    setError(null);
    try {
      const r = await api.discoverSchema({
        sample_size: sampleSize,
        extra_instructions: discoverExtra || undefined,
      });
      const newNodes = Array.from(new Set([...nodes, ...r.node_labels]));
      const newTriplets: Triplet[] = [...triplets];
      for (const t of r.triplets) {
        const m = /^(.+?)-(.+?)->(.+)$/.exec(t);
        if (!m) continue;
        const tri: Triplet = [m[1].trim(), m[2].trim(), m[3].trim()];
        if (!newTriplets.some(([a, b, c]) => a === tri[0] && b === tri[1] && c === tri[2])) {
          newTriplets.push(tri);
        }
      }
      setNodes(newNodes);
      setTriplets(newTriplets);
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setDiscovering(false);
    }
  };

  const removeNode = (n: string) => {
    setNodes(nodes.filter((x) => x !== n));
    setTriplets(triplets.filter(([s, , d]) => s !== n && d !== n));
  };

  const addNode = () => {
    const v = newNode.trim();
    if (v && !nodes.includes(v)) setNodes([...nodes, v]);
    setNewNode("");
  };

  const addRel = () => {
    const { src, type, dst } = newRel;
    if (!src || !type || !dst) return;
    if (!nodes.includes(src) || !nodes.includes(dst)) {
      setError("source and target must be in node labels");
      return;
    }
    setTriplets([...triplets, [src, type.toUpperCase().replaceAll(" ", "_"), dst]]);
    setNewRel({ src: "", type: "", dst: "" });
  };

  return (
    <>
      <PageHeader
        title="Schema"
        description="Define the allowed node labels and relationship types. Saved to the app database — survives restarts and applies to every ingest run."
        actions={
          <>
            {savedAt && Date.now() - savedAt < 4000 && (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <Check className="h-3 w-3" /> saved
              </span>
            )}
            <Button onClick={save} disabled={!dirty || saving} size="sm">
              <Save className="h-3.5 w-3.5" />
              {saving ? "Saving…" : "Save schema"}
            </Button>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Node labels</CardTitle>
            <CardDescription>PascalCase, singular. Used as primary entity labels alongside <code>__Entity__</code>.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="mb-3 flex flex-wrap gap-1.5">
              {nodes.length === 0 && (
                <div className="text-sm text-muted-foreground">
                  No labels yet — discover from your documents, or add manually below.
                </div>
              )}
              {nodes.map((n) => (
                <Badge key={n} variant="outline" className="gap-1 pr-1 text-xs">
                  {n}
                  <button onClick={() => removeNode(n)} className="rounded-sm p-0.5 hover:bg-destructive/20 hover:text-destructive">
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
            </div>
            <div className="flex gap-2">
              <Input value={newNode} placeholder="e.g. Disease"
                     onChange={(e) => setNewNode(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && addNode()} />
              <Button size="sm" variant="outline" onClick={addNode}><Plus className="h-3.5 w-3.5" />Add</Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AI assist</CardTitle>
            <CardDescription>Sample your registered documents and propose a schema.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1">
              <Label className="text-xs">Sample size</Label>
              <Input type="number" min={1} max={50} value={sampleSize}
                     onChange={(e) => setSampleSize(Math.max(1, parseInt(e.target.value || "1", 10)))} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Guidance (optional)</Label>
              <Input placeholder="e.g. focus on cardiology" value={discoverExtra}
                     onChange={(e) => setDiscoverExtra(e.target.value)} />
            </div>
            <Button onClick={discover} disabled={discovering} className="w-full">
              <Sparkles className={discovering ? "h-3.5 w-3.5 animate-pulse" : "h-3.5 w-3.5"} />
              {discovering ? "Discovering…" : "Discover & merge"}
            </Button>
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Relationships <span className="text-muted-foreground font-normal">({triplets.length})</span></CardTitle>
          <CardDescription>Allowed triplets <code>Source-TYPE-&gt;Target</code>. Endpoints must be defined node labels.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {triplets.length > 0 && (
            <div className="max-h-72 overflow-auto rounded-sm border border-border">
              <table className="w-full text-sm">
                <tbody>
                  {triplets.map((t, i) => (
                    <tr key={i} className="border-b border-border last:border-0">
                      <td className="px-3 py-1.5 w-1/3"><Badge variant="outline" className="text-xs">{t[0]}</Badge></td>
                      <td className="px-3 py-1.5 w-1/3 font-mono text-xs text-foreground">→ {t[1]} →</td>
                      <td className="px-3 py-1.5 w-1/3"><Badge variant="outline" className="text-xs">{t[2]}</Badge></td>
                      <td className="px-3 py-1.5 text-right">
                        <button onClick={() => setTriplets(triplets.filter((_, idx) => idx !== i))}
                                className="rounded-sm p-1 text-muted-foreground hover:bg-destructive/20 hover:text-destructive">
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2">
            <Input list="schema-nodes" placeholder="Source" value={newRel.src}
                   onChange={(e) => setNewRel({ ...newRel, src: e.target.value })} />
            <Input placeholder="REL_TYPE" value={newRel.type}
                   onChange={(e) => setNewRel({ ...newRel, type: e.target.value })} />
            <Input list="schema-nodes" placeholder="Target" value={newRel.dst}
                   onChange={(e) => setNewRel({ ...newRel, dst: e.target.value })} />
            <Button size="sm" variant="outline" onClick={addRel}><Plus className="h-3.5 w-3.5" />Add</Button>
            <datalist id="schema-nodes">
              {nodes.map((n) => <option key={n} value={n} />)}
            </datalist>
          </div>
        </CardContent>
      </Card>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Extraction guidance</CardTitle>
            <CardDescription>Appended to every extraction prompt.</CardDescription>
          </CardHeader>
          <CardContent>
            <Textarea rows={4} value={extra} onChange={(e) => setExtra(e.target.value)}
                      placeholder="e.g. Capture dosing as a property on Drug-TREATS->Disease edges." />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center gap-2">
            <History className="h-4 w-4 text-muted-foreground" />
            <CardTitle>Version history</CardTitle>
          </CardHeader>
          <CardContent>
            {versions.length === 0
              ? <div className="text-sm text-muted-foreground">No saves yet.</div>
              : (
                <ul className="space-y-1 text-sm">
                  {versions.slice(0, 8).map((v) => (
                    <li key={v.id} className="flex items-center justify-between">
                      <span className="font-mono text-xs">#{v.id}</span>
                      <span className="text-xs text-muted-foreground">{new Date(v.created_at).toLocaleString()}</span>
                      <Badge variant="secondary" className="ml-2">{v.source}</Badge>
                    </li>
                  ))}
                </ul>
              )}
          </CardContent>
        </Card>
      </div>

      {error && (
        <div className="mt-4 rounded-sm border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</div>
      )}
    </>
  );
}
