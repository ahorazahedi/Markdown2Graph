import { useEffect, useRef, useState } from "react";
import {
  Upload, Trash2, RefreshCw, Eye, RotateCcw, FolderOpen, FileText, Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Drawer } from "@/components/ui/drawer";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader } from "@/components/PageHeader";
import { DocumentStatusBadge } from "@/components/StatusBadge";
import { cn } from "@/lib/utils";
import { api, DocumentRow, DocumentStats, EntityGraph } from "@/lib/api";
import { confirm } from "@/lib/confirm";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const ALLOWED_RE = /\.(md|markdown)$/i;

export function DocumentsPage() {
  const [docs, setDocs] = useState<DocumentRow[]>([]);
  const [stats, setStats] = useState<DocumentStats | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<DocumentRow | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const filesRef = useRef<HTMLInputElement>(null);
  const dirRef = useRef<HTMLInputElement>(null);

  const refresh = async () => {
    setBusy(true);
    try {
      const r = await api.listDocuments();
      setDocs(r.items);
      setStats(r.stats);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { refresh(); }, []);

  // poll while anything is processing
  useEffect(() => {
    const hasActive = docs.some((d) => d.status === "processing" || d.status === "pending");
    if (!hasActive) return;
    const id = window.setInterval(refresh, 2000);
    return () => window.clearInterval(id);
  }, [docs]);

  const upload = async (entries: { file: File; relPath: string }[]) => {
    if (entries.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      await api.uploadDocuments(entries);
      await refresh();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setUploading(false);
    }
  };

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const collected = await collectFromDataTransfer(e.dataTransfer.items, e.dataTransfer.files);
    await upload(collected.filter((c) => ALLOWED_RE.test(c.file.name)));
  };

  const onPick = (files: FileList | null, asFolder: boolean) => {
    if (!files) return;
    const out: { file: File; relPath: string }[] = [];
    for (const f of Array.from(files)) {
      if (!ALLOWED_RE.test(f.name)) continue;
      out.push({ file: f, relPath: asFolder ? ((f as any).webkitRelativePath || f.name) : f.name });
    }
    upload(out);
  };

  const remove = async (id: number) => {
    const ok = await confirm({
      title: "Delete document?",
      description: "This removes the document, its chunks, and any orphan entities from the graph. The staged file on disk is also deleted.",
      confirmText: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    await api.deleteDocument(id);
    await refresh();
    setOpen(null);
  };

  const reextract = async (id: number) => {
    await api.reextractDocument(id);
    await refresh();
  };

  return (
    <>
      <PageHeader
        title="Documents"
        description="Upload, inspect, re-extract. State is persistent."
        actions={
          <Button variant="outline" size="sm" onClick={refresh} disabled={busy}>
            <RefreshCw className={busy ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
            Refresh
          </Button>
        }
      />

      {/* stats */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-6">
        <StatTile label="Total"        value={stats?.total} />
        <StatTile label="Completed"    value={stats?.completed} />
        <StatTile label="Pending"      value={stats?.pending} />
        <StatTile label="Failed"       value={stats?.failed} accent={!!stats?.failed ? "destructive" : undefined} />
        <StatTile label="Entities"     value={stats?.entities} />
        <StatTile label="Rels"         value={stats?.relationships} />
      </div>

      {/* dropzone */}
      <Card className="mb-4">
        <CardContent>
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={cn(
              "flex items-center justify-between gap-4 rounded-sm border border-dashed border-border px-4 py-6 transition-colors",
              dragOver && "border-foreground/50 bg-accent/40",
            )}
          >
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <Upload className="h-5 w-5" />
              {uploading
                ? <span className="flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Uploading…</span>
                : <span>Drop <code>.md</code> files or folders here, or pick manually.</span>}
            </div>
            <div className="flex shrink-0 gap-2">
              <Button size="sm" variant="outline" onClick={() => filesRef.current?.click()}>
                <FileText className="h-3.5 w-3.5" /> Files
              </Button>
              <Button size="sm" variant="outline" onClick={() => dirRef.current?.click()}>
                <FolderOpen className="h-3.5 w-3.5" /> Folder
              </Button>
              <input ref={filesRef} type="file" accept=".md,.markdown,text/markdown" multiple className="hidden"
                     onChange={(e) => onPick(e.target.files, false)} />
              <input ref={dirRef} type="file" multiple className="hidden"
                     // @ts-expect-error vendor attr
                     webkitdirectory="" directory=""
                     onChange={(e) => onPick(e.target.files, true)} />
            </div>
          </div>
          {error && <div className="mt-3 text-sm text-destructive">{error}</div>}
        </CardContent>
      </Card>

      {/* table */}
      <Card>
        <CardHeader>
          <CardTitle>Registry</CardTitle>
          <CardDescription>{docs.length} document{docs.length === 1 ? "" : "s"}</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/30 text-2xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-4 py-2 text-left">File</th>
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-right">Chunks</th>
                <th className="px-4 py-2 text-right">Entities</th>
                <th className="px-4 py-2 text-right">Rels</th>
                <th className="px-4 py-2 text-left">Updated</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {docs.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-sm text-muted-foreground">
                  No documents yet. Upload above to get started.
                </td></tr>
              )}
              {docs.map((d) => (
                <tr key={d.id} className="border-b border-border last:border-0 hover:bg-accent/30">
                  <td className="px-4 py-2">
                    <div className="font-mono text-xs">{d.file_name}</div>
                    {d.title && <div className="text-xxs text-muted-foreground">{d.title}</div>}
                  </td>
                  <td className="px-4 py-2"><DocumentStatusBadge status={d.status} /></td>
                  <td className="px-4 py-2 text-right tabular-nums">{d.chunk_count}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{d.entity_count}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{d.relationship_count}</td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">{fmtTime(d.updated_at)}</td>
                  <td className="px-2 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="ghost" onClick={() => setOpen(d)} title="Inspect">
                        <Eye className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => reextract(d.id)} title="Re-extract">
                        <RotateCcw className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => remove(d.id)} title="Delete">
                        <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <DocumentDrawer doc={open} onClose={() => setOpen(null)} onDeleted={refresh} />
    </>
  );
}

function StatTile({ label, value, accent }: { label: string; value?: number; accent?: "destructive" }) {
  return (
    <div className="rounded-sm border border-border bg-card px-3 py-2">
      <div className="text-2xs uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={cn("text-xl font-semibold tabular-nums tracking-tightish",
        accent === "destructive" && value ? "text-destructive" : "")}>
        {value ?? "—"}
      </div>
    </div>
  );
}

function DocumentDrawer({ doc, onClose, onDeleted }: {
  doc: DocumentRow | null; onClose: () => void; onDeleted: () => void;
}) {
  const [entities, setEntities] = useState<EntityGraph>({ nodes: [], relationships: [] });
  const [chunks, setChunks] = useState<{ id: string; position: number; text: string; length: number }[]>([]);
  const [content, setContent] = useState<string | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!doc) return;
    setEntities({ nodes: [], relationships: [] });
    setChunks([]);
    setContent(null);
    setLoading(true);
    Promise.all([
      api.documentEntities(doc.id).catch(() => ({ nodes: [], relationships: [] } as EntityGraph)),
      api.documentChunks(doc.id).then((r) => r.chunks).catch(() => []),
    ]).then(([e, c]) => {
      setEntities(e);
      setChunks(c);
    }).finally(() => setLoading(false));
  }, [doc?.id]);

  const loadContent = async () => {
    if (!doc || content != null) return;
    setContentLoading(true);
    try {
      const r = await api.documentContent(doc.id);
      setContent(r.content);
    } catch (e) {
      setContent(`Failed to load content: ${e}`);
    } finally {
      setContentLoading(false);
    }
  };

  if (!doc) return null;

  return (
    <Drawer
      open={!!doc}
      onClose={onClose}
      title={doc.file_name}
      subtitle={doc.title || doc.source_path}
    >
      <div className="space-y-4 p-5">
        <div className="grid grid-cols-4 gap-3">
          <StatTile label="Status"   value={undefined} />
          <StatTile label="Chunks"   value={doc.chunk_count} />
          <StatTile label="Entities" value={doc.entity_count} />
          <StatTile label="Rels"     value={doc.relationship_count} />
        </div>
        <div className="text-xs text-muted-foreground">
          <div><span className="text-foreground">Status:</span> <DocumentStatusBadge status={doc.status} /></div>
          <div className="mt-1 font-mono">{doc.source_path}</div>
          <div className="mt-1">SHA1: <span className="font-mono">{doc.sha1.slice(0, 12)}…</span> · {(doc.size_bytes / 1024).toFixed(1)} KB</div>
        </div>

        {doc.error && (
          <pre className="rounded-sm border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive whitespace-pre-wrap">
            {doc.error}
          </pre>
        )}

        <Tabs defaultValue="content" onValueChange={(v) => { if (v === "content") loadContent(); }}>
          <TabsList>
            <TabsTrigger value="content">Content</TabsTrigger>
            <TabsTrigger value="entities">Entities ({entities.nodes.length})</TabsTrigger>
            <TabsTrigger value="relationships">Relationships ({entities.relationships.length})</TabsTrigger>
            <TabsTrigger value="chunks">Chunks ({chunks.length})</TabsTrigger>
          </TabsList>

          <TabsContent value="content" className="pt-4">
            {contentLoading && !content ? (
              <div className="text-sm text-muted-foreground">Loading…</div>
            ) : content == null ? (
              <Button size="sm" variant="outline" onClick={loadContent}>
                <FileText className="h-3.5 w-3.5" /> Load markdown
              </Button>
            ) : (
              <div className="prose prose-sm dark:prose-invert max-w-none rounded-sm border border-border bg-muted/20 px-4 py-3">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
              </div>
            )}
          </TabsContent>

          <TabsContent value="entities" className="pt-4">
            {loading
              ? <div className="text-sm text-muted-foreground">Loading…</div>
              : entities.nodes.length === 0
                ? <div className="text-sm text-muted-foreground">No entities extracted for this document. Run ingest to populate.</div>
                : (
                  <div className="overflow-hidden rounded-sm border border-border">
                    <table className="w-full text-sm">
                      <thead className="border-b border-border bg-muted/30 text-2xs uppercase tracking-wider text-muted-foreground">
                        <tr>
                          <th className="px-3 py-1.5 text-left">Label</th>
                          <th className="px-3 py-1.5 text-left">Id</th>
                          <th className="px-3 py-1.5 text-left">Description</th>
                        </tr>
                      </thead>
                      <tbody>
                        {entities.nodes.map((n, i) => (
                          <tr key={i} className="border-b border-border last:border-0">
                            <td className="px-3 py-1.5">
                              {n.labels.map((l) => <Badge key={l} variant="outline" className="mr-1 text-xs">{l}</Badge>)}
                            </td>
                            <td className="px-3 py-1.5 font-mono text-xs">{n.id}</td>
                            <td className="px-3 py-1.5 text-xs text-muted-foreground">{n.description || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
          </TabsContent>

          <TabsContent value="relationships" className="pt-4">
            {entities.relationships.length === 0
              ? <div className="text-sm text-muted-foreground">No relationships.</div>
              : (
                <div className="overflow-hidden rounded-sm border border-border">
                  <table className="w-full text-sm">
                    <tbody>
                      {entities.relationships.map((r, i) => (
                        <tr key={i} className="border-b border-border last:border-0">
                          <td className="px-3 py-1.5 font-mono text-xs">{r.source}</td>
                          <td className="px-3 py-1.5 font-mono text-xs text-foreground">→ {r.type} →</td>
                          <td className="px-3 py-1.5 font-mono text-xs">{r.target}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
          </TabsContent>

          <TabsContent value="chunks" className="pt-4">
            {chunks.length === 0
              ? <div className="text-sm text-muted-foreground">No chunks. Run ingest first.</div>
              : (
                <div className="space-y-2">
                  {chunks.map((c) => (
                    <details key={c.id} className="rounded-sm border border-border">
                      <summary className="flex cursor-pointer items-center justify-between px-3 py-1.5 text-xs">
                        <span className="font-mono">#{c.position} · {c.id.slice(0, 10)}…</span>
                        <span className="text-muted-foreground">{c.length} chars</span>
                      </summary>
                      <pre className="border-t border-border bg-muted/30 p-3 text-xs whitespace-pre-wrap">{c.text}</pre>
                    </details>
                  ))}
                </div>
              )}
          </TabsContent>
        </Tabs>
      </div>
    </Drawer>
  );
}

// ---- DataTransfer / folder traversal ----
async function collectFromDataTransfer(items: DataTransferItemList, fallback: FileList | null) {
  const out: { file: File; relPath: string }[] = [];
  let usedItems = false;
  for (const it of Array.from(items)) {
    const entry =
      typeof (it as any).webkitGetAsEntry === "function"
        ? (it as any).webkitGetAsEntry()
        : null;
    if (!entry) continue;
    usedItems = true;
    await walk(entry, "", out);
  }
  if (!usedItems && fallback) {
    for (const f of Array.from(fallback)) out.push({ file: f, relPath: f.name });
  }
  return out;
}

function walk(entry: any, prefix: string, out: { file: File; relPath: string }[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isFile) {
      entry.file(
        (file: File) => { out.push({ file, relPath: rel }); resolve(); },
        reject,
      );
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const readBatch = () => {
        reader.readEntries(async (entries: any[]) => {
          if (entries.length === 0) return resolve();
          for (const c of entries) await walk(c, rel, out);
          readBatch();
        }, reject);
      };
      readBatch();
    } else resolve();
  });
}

function fmtTime(iso: string) {
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}
