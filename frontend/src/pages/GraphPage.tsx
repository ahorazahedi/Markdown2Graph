import { useEffect, useState } from "react";
import { ExternalLink, RefreshCw, Trash2, Sparkles, X, Copy, Unlink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/PageHeader";
import { PageContainer } from "@/components/PageContainer";
import { GraphViewer } from "@/components/GraphViewer";
import { api, AppConfig, DocumentRow, DuplicateGroup, GraphExplore, OrphanEntity } from "@/lib/api";
import { confirm } from "@/lib/confirm";

export function GraphPage({ config }: { config: AppConfig | null }) {
  const [stats, setStats] = useState<Record<string, number> | null>(null);
  const [schema, setSchema] = useState<{ labels: string[]; relationship_types: string[] } | null>(null);
  const [explore, setExplore] = useState<GraphExplore | null>(null);
  const [docs, setDocs] = useState<DocumentRow[]>([]);
  const [filterFile, setFilterFile] = useState("");
  const [filterLabel, setFilterLabel] = useState("");
  const [limit, setLimit] = useState(150);
  const [includeStructure, setIncludeStructure] = useState(false);
  const [includeCommunities, setIncludeCommunities] = useState(false);
  const [busy, setBusy] = useState(false);

  // focus / neighborhood state
  const [focusInfo, setFocusInfo] = useState<{ elementId: string; depth: number } | null>(null);
  const [expanding, setExpanding] = useState(false);

  // dedup + orphan management dialogs
  const [dupOpen, setDupOpen] = useState(false);
  const [orphanOpen, setOrphanOpen] = useState(false);

  const refresh = async () => {
    setBusy(true);
    try {
      const [s, sc, d] = await Promise.all([
        api.stats(),
        api.graphSchema(),
        api.listDocuments().then((r) => r.items).catch(() => []),
      ]);
      setStats(s);
      setSchema(sc);
      setDocs(d);
    } finally { setBusy(false); }
  };

  const loadExplore = async () => {
    setBusy(true);
    setFocusInfo(null);
    try {
      const r = await api.exploreGraph({
        limit,
        file_name: filterFile || undefined,
        label: filterLabel || undefined,
        include_structure: includeStructure,
        include_communities: includeCommunities,
      });
      setExplore(r);
    } finally { setBusy(false); }
  };

  const expandNeighborhood = async (
    elementId: string, depth: number,
    withStructure: boolean, withCommunities: boolean,
  ) => {
    setExpanding(true);
    try {
      const r = await api.graphNeighborhood({
        element_id: elementId,
        depth,
        limit: Math.max(limit, 400),
        include_structure: withStructure,
        include_communities: withCommunities,
      });
      setExplore(r);
      setFocusInfo({ elementId, depth });
    } finally { setExpanding(false); }
  };

  useEffect(() => { refresh(); }, []);
  useEffect(() => { loadExplore(); /* fires on toggle/filter change */ },
            // eslint-disable-next-line react-hooks/exhaustive-deps
            [includeStructure, includeCommunities, filterFile, filterLabel]);

  const browserHref = config?.neo4j.uri.replace(/^bolt:\/\//, "http://").replace(/:7687/, ":7474");

  return (
    <PageContainer
      maxWidth="max-w-[1600px]"
      header={
        <PageHeader
          title="Graph"
          description="Live state of the Neo4j database."
          actions={
            <>
              <Button variant="outline" size="sm" onClick={refresh} disabled={busy}>
                <RefreshCw className={busy ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} /> Refresh
              </Button>
              <Button variant="outline" size="sm" disabled={busy}
                      onClick={() => { window.location.hash = "#/post-process"; }}>
                <Sparkles className="h-3.5 w-3.5" /> Post-process
              </Button>
              <Button variant="outline" size="sm" onClick={() => setDupOpen(true)}>
                <Copy className="h-3.5 w-3.5" /> Duplicates
              </Button>
              <Button variant="outline" size="sm" onClick={() => setOrphanOpen(true)}>
                <Unlink className="h-3.5 w-3.5" /> Orphans
              </Button>
              {browserHref && (
                <a href={browserHref} target="_blank" rel="noreferrer"
                   className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-border px-3 text-sm hover:bg-accent">
                  <ExternalLink className="h-3.5 w-3.5" /> Open Neo4j Browser
                </a>
              )}
              <Button variant="destructive" size="sm"
                      onClick={async () => {
                        const ok = await confirm({
                          title: "Wipe the entire graph?",
                          description: "Deletes every node and relationship in Neo4j. Document records in the app database are kept (status reset to pending).",
                          confirmText: "Wipe graph",
                          variant: "destructive",
                        });
                        if (!ok) return;
                        await api.clearGraph(); refresh(); loadExplore();
                      }}>
                <Trash2 className="h-3.5 w-3.5" /> Clear
              </Button>
            </>
          }
        />
      }
    >

      {dupOpen && (
        <DuplicateDialog
          onClose={() => setDupOpen(false)}
          onDone={() => { refresh(); loadExplore(); }}
        />
      )}
      {orphanOpen && (
        <OrphanDialog
          onClose={() => setOrphanOpen(false)}
          onDone={() => { refresh(); loadExplore(); }}
        />
      )}

      <Tabs defaultValue="viewer">
        <TabsList className="mb-4">
          <TabsTrigger value="viewer">Viewer</TabsTrigger>
          <TabsTrigger value="stats">Stats</TabsTrigger>
        </TabsList>

        {/* ---- viewer ---- */}
        <TabsContent value="viewer" className="space-y-3">
          <Card>
            <CardContent className="flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <Label className="text-2xs">File</Label>
                <select
                  className="h-8 rounded-sm border border-border bg-background px-2 text-sm"
                  value={filterFile}
                  onChange={(e) => setFilterFile(e.target.value)}
                >
                  <option value="">All</option>
                  {docs.map((d) => <option key={d.id} value={d.file_name}>{d.file_name}</option>)}
                </select>
              </div>
              <div className="space-y-1">
                <Label className="text-2xs">Label</Label>
                <select
                  className="h-8 rounded-sm border border-border bg-background px-2 text-sm"
                  value={filterLabel}
                  onChange={(e) => setFilterLabel(e.target.value)}
                >
                  <option value="">All</option>
                  {schema?.labels.filter((l) => !["Chunk", "Document", "__Entity__", "__Community__"].includes(l))
                    .map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              </div>
              <div className="space-y-1">
                <Label className="text-2xs">Node limit</Label>
                <Input type="number" className="w-24" min={10} max={1000} value={limit}
                       onChange={(e) => setLimit(Math.max(10, Math.min(1000, parseInt(e.target.value || "150", 10))))} />
              </div>
              <label className="flex h-8 items-center gap-2 text-xs text-foreground">
                <input type="checkbox"
                       checked={includeStructure}
                       onChange={(e) => setIncludeStructure(e.target.checked)} />
                Include docs &amp; chunks
              </label>
              <label className="flex h-8 items-center gap-2 text-xs text-foreground">
                <input type="checkbox"
                       checked={includeCommunities}
                       onChange={(e) => setIncludeCommunities(e.target.checked)} />
                Include communities
              </label>
              <Button size="sm" onClick={loadExplore} disabled={busy}>Apply</Button>
              {explore && (
                <span className="ml-auto text-xs text-muted-foreground">
                  {explore.nodes.length} nodes · {explore.relationships.length} edges
                </span>
              )}
            </CardContent>
          </Card>

          {explore && explore.nodes.length === 0
            ? (
              <Card>
                <CardContent>
                  <p className="text-sm text-muted-foreground">
                    No entities to display. Ingest some documents first.
                  </p>
                </CardContent>
              </Card>
            )
            : explore && (
              <GraphViewer
                data={explore}
                onRefresh={loadExplore}
                onExpand={expandNeighborhood}
                expanding={expanding}
                focusInfo={focusInfo}
                onClearFocus={loadExplore}
              />
            )}
        </TabsContent>

        {/* ---- stats ---- */}
        <TabsContent value="stats" className="space-y-4">
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
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
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
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}

// ---------- duplicate-node manager ----------

function DuplicateDialog({ onClose, onDone }:
                         { onClose: () => void; onDone: () => void }) {
  const [groups, setGroups] = useState<DuplicateGroup[] | null>(null);
  const [busy, setBusy] = useState(false);
  // per-group canonical selection (default: highest chunk_count member)
  const [canonByKey, setCanonByKey] = useState<Record<string, string>>({});

  useEffect(() => { (async () => {
    setBusy(true);
    try {
      const r = await api.listDuplicates({ limit: 100, min_size: 2 });
      setGroups(r.groups);
      const init: Record<string, string> = {};
      for (const g of r.groups) init[g.key] = g.members[0]?.element_id;
      setCanonByKey(init);
    } finally { setBusy(false); }
  })(); }, []);

  const mergeAll = async () => {
    if (!groups) return;
    const payload = groups
      .map((g) => {
        const canon = canonByKey[g.key];
        const aliases = g.members.filter((m) => m.element_id !== canon).map((m) => m.element_id);
        return aliases.length ? { canonical_element_id: canon, alias_element_ids: aliases } : null;
      })
      .filter(Boolean) as { canonical_element_id: string; alias_element_ids: string[] }[];
    if (payload.length === 0) return;
    const ok = await confirm({
      title: `Merge ${payload.length} duplicate group(s)?`,
      description: "Aliases are folded into the canonical member. Relationships re-point, properties are unioned (canonical wins ties). This cannot be undone.",
      confirmText: "Merge",
      cancelText: "Cancel",
      variant: "destructive",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.mergeDuplicates(payload);
      onDone();
      onClose();
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="flex max-h-[80vh] w-full max-w-3xl flex-col rounded-md border border-border bg-card p-5 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-base font-semibold">Duplicate entities</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Groups share a normalised id (case + punctuation stripped). Pick the canonical
              member per group; all other members are merged into it (rels re-pointed, props
              copied, labels unioned). Click <strong>Merge selected</strong> to apply.
            </p>
          </div>
          <button onClick={onClose} className="rounded-sm p-1 text-muted-foreground hover:bg-accent">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-4 min-h-0 flex-1 overflow-y-auto rounded-sm border border-border">
          {busy && !groups ? (
            <div className="p-6 text-center text-sm text-muted-foreground">Scanning…</div>
          ) : groups && groups.length === 0 ? (
            <div className="p-6 text-center text-sm text-muted-foreground">No duplicate groups found.</div>
          ) : (
            <ul className="divide-y divide-border">
              {(groups ?? []).map((g) => (
                <li key={g.key} className="px-3 py-2 text-xs">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="rounded-sm bg-accent px-1.5 py-0.5 font-mono text-2xs">{g.key}</span>
                    <span className="text-muted-foreground">{g.members.length} members</span>
                  </div>
                  <div className="space-y-1">
                    {g.members.map((m) => (
                      <label key={m.element_id} className="flex items-center gap-2 hover:bg-accent/40 rounded-sm px-1.5 py-1 cursor-pointer">
                        <input type="radio" name={`canon-${g.key}`}
                               checked={canonByKey[g.key] === m.element_id}
                               onChange={() => setCanonByKey({ ...canonByKey, [g.key]: m.element_id })} />
                        <span className="font-mono">{m.id}</span>
                        <span className="text-muted-foreground">[{m.labels.join(", ") || "—"}]</span>
                        <span className="ml-auto tabular-nums text-muted-foreground">
                          {m.chunk_count} chunks · {m.rel_count} rels
                        </span>
                      </label>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-4 flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>Close</Button>
          <Button size="sm" onClick={mergeAll}
                  disabled={busy || !groups || groups.length === 0}>
            Merge selected
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------- orphan-node manager ----------

function OrphanDialog({ onClose, onDone }:
                      { onClose: () => void; onDone: () => void }) {
  const [orphans, setOrphans] = useState<OrphanEntity[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const load = async () => {
    setBusy(true);
    try {
      const r = await api.listOrphans({ limit: 1000 });
      setOrphans(r.orphans);
      setSelected(new Set(r.orphans.map((o) => o.element_id)));
    } finally { setBusy(false); }
  };
  useEffect(() => { load(); }, []);

  const deleteSelected = async (all: boolean) => {
    if (!orphans) return;
    const ids = all ? undefined : Array.from(selected);
    const label = all ? "ALL orphan entities in the graph" : `${ids?.length ?? 0} selected orphans`;
    const ok = await confirm({
      title: `Delete ${label}?`,
      description: "Orphan entities have no chunk pointing at them. This cannot be undone.",
      confirmText: "Delete",
      cancelText: "Cancel",
      variant: "destructive",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.deleteOrphans(ids);
      onDone();
      onClose();
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="flex max-h-[80vh] w-full max-w-2xl flex-col rounded-md border border-border bg-card p-5 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-base font-semibold">Orphan entities</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              <code>__Entity__</code> nodes no <code>Chunk</code> points at. Usually leftover
              from re-extraction or manual cleanup. Safe to delete.
            </p>
          </div>
          <button onClick={onClose} className="rounded-sm p-1 text-muted-foreground hover:bg-accent">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-4 min-h-0 flex-1 overflow-y-auto rounded-sm border border-border">
          {busy && !orphans ? (
            <div className="p-6 text-center text-sm text-muted-foreground">Scanning…</div>
          ) : orphans && orphans.length === 0 ? (
            <div className="p-6 text-center text-sm text-muted-foreground">No orphan entities.</div>
          ) : (
            <ul className="divide-y divide-border text-xs">
              {(orphans ?? []).map((o) => (
                <li key={o.element_id} className="flex items-center gap-2 px-3 py-1.5 hover:bg-accent/40">
                  <input type="checkbox" checked={selected.has(o.element_id)}
                         onChange={(e) => {
                           const next = new Set(selected);
                           if (e.target.checked) next.add(o.element_id); else next.delete(o.element_id);
                           setSelected(next);
                         }} />
                  <span className="font-mono">{o.id}</span>
                  <span className="text-muted-foreground">[{o.labels.join(", ") || "—"}]</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-4 flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>Close</Button>
          <Button variant="outline" size="sm" onClick={() => deleteSelected(false)}
                  disabled={busy || selected.size === 0}>
            Delete selected ({selected.size})
          </Button>
          <Button variant="destructive" size="sm" onClick={() => deleteSelected(true)}
                  disabled={busy || (orphans?.length ?? 0) === 0}>
            Delete all
          </Button>
        </div>
      </div>
    </div>
  );
}
