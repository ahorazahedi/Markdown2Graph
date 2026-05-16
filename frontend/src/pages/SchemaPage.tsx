import { useEffect, useMemo, useState } from "react";
import {
  Sparkles, Plus, X, Save, Check, History, GitBranch, Tag, Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/PageHeader";
import { PageContainer } from "@/components/PageContainer";
import { api, Schema, SchemaVersion, Triplet } from "@/lib/api";
import { useUnsavedGuard } from "@/lib/unsavedGuard";

export function SchemaPage() {
  const [schema, setSchema] = useState<Schema | null>(null);
  const [nodes, setNodes] = useState<string[]>([]);
  const [triplets, setTriplets] = useState<Triplet[]>([]);
  const [versions, setVersions] = useState<SchemaVersion[]>([]);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const [discoverOpen, setDiscoverOpen] = useState(false);
  const [discoverExtra, setDiscoverExtra] = useState("");
  const [sampleSize, setSampleSize] = useState(5);
  const [discovering, setDiscovering] = useState(false);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [newNode, setNewNode] = useState("");
  const [newRel, setNewRel] = useState({ src: "", type: "", dst: "" });

  const refresh = async () => {
    const s = await api.getSchema();
    setSchema(s);
    setNodes(s.node_labels);
    setTriplets(s.triplets as Triplet[]);
    setVersions((await api.schemaVersions()).versions);
  };
  useEffect(() => { refresh(); }, []);

  const dirty = !!(
    schema &&
    (JSON.stringify(nodes) !== JSON.stringify(schema.node_labels) ||
      JSON.stringify(triplets) !== JSON.stringify(schema.triplets))
  );
  useUnsavedGuard(dirty);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveSchema({
        node_labels: nodes,
        triplets,
        extra: schema?.extra || "", // preserve guidance — edited on Ingest page
        source: "manual",
      });
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
      setDiscoverOpen(false);
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

  const nodeUsage = useMemo(() => {
    const m: Record<string, number> = {};
    for (const [s, , d] of triplets) {
      m[s] = (m[s] || 0) + 1;
      m[d] = (m[d] || 0) + 1;
    }
    return m;
  }, [triplets]);

  return (
    <PageContainer
      header={
        <PageHeader
          title="Schema"
          description="Allowed node labels and relationship types. Persisted in the app database; applied on every ingest run."
          actions={
            <>
              {savedAt && Date.now() - savedAt < 4000 && (
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Check className="h-3 w-3" /> saved
                </span>
              )}
              <Button variant="outline" size="sm" onClick={() => setDiscoverOpen((v) => !v)}>
                <Sparkles className="h-3.5 w-3.5" /> Discover with AI
              </Button>
              <Button size="sm" onClick={save} disabled={!dirty || saving}>
                <Save className="h-3.5 w-3.5" /> {saving ? "Saving…" : "Save"}
              </Button>
            </>
          }
        />
      }
    >
      {/* metric strip */}
      <div className="mb-6 flex flex-wrap items-center gap-x-8 gap-y-2 border-b border-border pb-4">
        <Metric label="Node labels"   value={nodes.length} />
        <Metric label="Relationships" value={triplets.length} />
        <Metric label="Saved"         value={schema?.updated_at ? new Date(schema.updated_at).toLocaleString() : "—"} mono />
        {dirty && (
          <span className="ml-auto inline-flex items-center gap-1 text-2xs uppercase tracking-wider text-[hsl(var(--warning))]">
            <span className="h-1.5 w-1.5 rounded-full bg-[hsl(var(--warning))]" /> unsaved changes
          </span>
        )}
      </div>

      {/* discover panel (collapsible) */}
      {discoverOpen && (
        <Card className="mb-6 border-foreground/15">
          <CardHeader className="flex-row items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4" /> AI assist</CardTitle>
              <CardDescription>Sample your registered documents and merge a proposed schema.</CardDescription>
            </div>
            <Button size="sm" variant="ghost" onClick={() => setDiscoverOpen(false)}>
              <X className="h-3.5 w-3.5" />
            </Button>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-[140px_1fr_auto]">
              <div className="space-y-1">
                <Label className="text-2xs uppercase tracking-wider">Sample size</Label>
                <Input type="number" min={1} max={50} value={sampleSize}
                       onChange={(e) => setSampleSize(Math.max(1, parseInt(e.target.value || "1", 10)))} />
              </div>
              <div className="space-y-1">
                <Label className="text-2xs uppercase tracking-wider">Guidance (optional)</Label>
                <Input placeholder="e.g. focus on cardiology and capture interactions"
                       value={discoverExtra}
                       onChange={(e) => setDiscoverExtra(e.target.value)} />
              </div>
              <div className="flex items-end">
                <Button onClick={discover} disabled={discovering}>
                  <Sparkles className={cn("h-3.5 w-3.5", discovering && "animate-pulse")} />
                  {discovering ? "Discovering…" : "Discover & merge"}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* ============ NODE LABELS ============ */}
      <Section
        icon={<Tag className="h-4 w-4" />}
        title="Node labels"
        sub="PascalCase, singular. Each becomes a label alongside __Entity__."
        right={<span className="text-2xs text-muted-foreground tabular-nums">{nodes.length}</span>}
      >
        {nodes.length === 0 ? (
          <EmptyHint>No labels yet — discover from your documents or add one below.</EmptyHint>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {nodes.map((n) => (
              <span key={n}
                    className="group inline-flex items-center gap-1.5 rounded-sm border border-border bg-muted/40 px-2 py-0.5 text-xs">
                <span className="font-medium">{n}</span>
                {nodeUsage[n] > 0 && (
                  <span className="text-2xs text-muted-foreground tabular-nums">{nodeUsage[n]}</span>
                )}
                <button onClick={() => removeNode(n)}
                        className="ml-0.5 rounded-sm p-0.5 text-muted-foreground opacity-0 transition group-hover:opacity-100 hover:bg-destructive/20 hover:text-destructive">
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        )}

        <div className="mt-4 flex gap-2">
          <Input className="flex-1"
                 placeholder="Add a label, e.g. Disease"
                 value={newNode}
                 onChange={(e) => setNewNode(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && addNode()} />
          <Button size="sm" variant="outline" onClick={addNode} disabled={!newNode.trim()}>
            <Plus className="h-3.5 w-3.5" /> Add
          </Button>
        </div>
      </Section>

      {/* ============ RELATIONSHIPS ============ */}
      <Section
        icon={<GitBranch className="h-4 w-4" />}
        title="Relationships"
        sub="Allowed triplets — Source-TYPE→Target. Both endpoints must exist in node labels."
        right={<span className="text-2xs text-muted-foreground tabular-nums">{triplets.length}</span>}
        className="mt-6"
      >
        {triplets.length === 0 ? (
          <EmptyHint>No relationships defined.</EmptyHint>
        ) : (
          <div className="overflow-hidden rounded-sm border border-border">
            <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,1fr)_2.25rem] items-center gap-x-4 border-b border-border bg-muted/30 px-3 py-1.5 text-2xs uppercase tracking-wider text-muted-foreground">
              <span className="justify-self-start">Source</span>
              <span className="justify-self-center">Type</span>
              <span className="justify-self-start">Target</span>
              <span />
            </div>
            <ul className="max-h-[420px] overflow-y-auto divide-y divide-border">
              {triplets.map((t, i) => (
                <li key={i}
                    className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,1fr)_2.25rem] items-center gap-x-4 px-3 py-2 hover:bg-accent/40">
                  <Badge variant="outline" className="w-fit justify-self-start text-xs">{t[0]}</Badge>
                  <span className="justify-self-center font-mono text-2xs text-foreground">— {t[1]} →</span>
                  <Badge variant="outline" className="w-fit justify-self-start text-xs">{t[2]}</Badge>
                  <button onClick={() => setTriplets(triplets.filter((_, idx) => idx !== i))}
                          className="justify-self-end rounded-sm p-1 text-muted-foreground hover:bg-destructive/15 hover:text-destructive">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-3 grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,1fr)_auto] gap-2">
          <Input list="schema-nodes" placeholder="Source"
                 value={newRel.src} onChange={(e) => setNewRel({ ...newRel, src: e.target.value })} />
          <Input placeholder="REL_TYPE"
                 value={newRel.type} onChange={(e) => setNewRel({ ...newRel, type: e.target.value })} />
          <Input list="schema-nodes" placeholder="Target"
                 value={newRel.dst} onChange={(e) => setNewRel({ ...newRel, dst: e.target.value })} />
          <Button size="sm" variant="outline" onClick={addRel}
                  disabled={!newRel.src || !newRel.type || !newRel.dst}>
            <Plus className="h-3.5 w-3.5" /> Add
          </Button>
          <datalist id="schema-nodes">
            {nodes.map((n) => <option key={n} value={n} />)}
          </datalist>
        </div>
      </Section>

      {/* ============ HISTORY ============ */}
      <Section
        icon={<History className="h-4 w-4" />}
        title="Version history"
        sub="Every save snapshots the schema."
        className="mt-6"
      >
        {versions.length === 0 ? (
          <EmptyHint>No saves yet.</EmptyHint>
        ) : (
          <ul className="divide-y divide-border rounded-sm border border-border">
            {versions.slice(0, 12).map((v) => (
              <li key={v.id} className="flex items-center justify-between px-3 py-2 text-sm">
                <span className="font-mono text-2xs text-muted-foreground">#{v.id}</span>
                <span className="flex-1 px-4 text-xs">{new Date(v.created_at).toLocaleString()}</span>
                <Badge variant="secondary">{v.source}</Badge>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {error && (
        <div className="mt-4 rounded-sm border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}
    </PageContainer>
  );
}

/* ------------- helpers ------------- */

function Section({
  icon, title, sub, right, children, className,
}: {
  icon?: React.ReactNode; title: string; sub?: string;
  right?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={cn("space-y-3", className)}>
      <header className="flex items-end justify-between border-b border-border pb-2">
        <div className="flex items-center gap-2">
          {icon && <span className="text-muted-foreground">{icon}</span>}
          <h2 className="text-sm font-semibold tracking-tightish">{title}</h2>
          {sub && <span className="text-xs text-muted-foreground">— {sub}</span>}
        </div>
        {right}
      </header>
      {children}
    </section>
  );
}

function Metric({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-2xs uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className={cn("text-sm font-semibold tabular-nums tracking-tightish", mono && "font-mono font-normal text-xs text-foreground")}>
        {value}
      </span>
    </div>
  );
}

function EmptyHint({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-sm border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
      {children}
    </div>
  );
}
