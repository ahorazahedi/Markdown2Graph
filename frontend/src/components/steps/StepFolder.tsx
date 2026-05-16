import { useCallback, useRef, useState } from "react";
import { FolderOpen, Upload, FileText, X, ServerCog, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";

interface Entry {
  file: File;
  relPath: string;
}

type Mode = "upload" | "path";

const ALLOWED_RE = /\.(md|markdown)$/i;

export function StepFolder({
  value,
  onChange,
  onBack,
  onNext,
}: {
  value: string;
  onChange: (v: string) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const [mode, setMode] = useState<Mode>("upload");
  const [path, setPath] = useState(value);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);

  // ---- drag & drop ----
  const onDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    setError(null);
    const items = Array.from(e.dataTransfer.items || []);
    try {
      const collected = await collectFromDataTransfer(items, e.dataTransfer.files);
      addEntries(collected);
    } catch (err: any) {
      setError(String(err.message || err));
    }
  }, []);

  const addEntries = (incoming: Entry[]) => {
    setEntries((prev) => {
      const seen = new Set(prev.map((e) => e.relPath));
      const merged = [...prev];
      for (const e of incoming) {
        if (!ALLOWED_RE.test(e.file.name)) continue;
        if (seen.has(e.relPath)) continue;
        seen.add(e.relPath);
        merged.push(e);
      }
      return merged;
    });
  };

  const removeEntry = (rp: string) =>
    setEntries((prev) => prev.filter((e) => e.relPath !== rp));

  // ---- file picker ----
  const onFilePick = (files: FileList | null, asFolder: boolean) => {
    if (!files) return;
    const out: Entry[] = [];
    for (const f of Array.from(files)) {
      const rel = asFolder ? ((f as any).webkitRelativePath || f.name) : f.name;
      out.push({ file: f, relPath: rel });
    }
    addEntries(out);
  };

  // ---- upload + continue ----
  const submit = async () => {
    setError(null);
    if (mode === "path") {
      if (!path.trim()) {
        setError("path is required");
        return;
      }
      onChange(path.trim());
      onNext();
      return;
    }
    if (entries.length === 0) {
      setError("drop at least one .md file");
      return;
    }
    setUploading(true);
    try {
      const r = await api.uploadFiles(entries);
      onChange(r.path);
      onNext();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setUploading(false);
    }
  };

  const totalBytes = entries.reduce((s, e) => s + e.file.size, 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Source</CardTitle>
        <CardDescription>
          Drop medical Markdown files or whole folders. Subfolders are scanned recursively;
          non-<code>.md</code> files are ignored.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* mode toggle */}
        <div className="inline-flex rounded-md border border-border bg-muted/30 p-1 text-sm">
          <ModeBtn active={mode === "upload"} onClick={() => setMode("upload")} icon={<Upload className="h-4 w-4" />}>
            Drag & drop
          </ModeBtn>
          <ModeBtn active={mode === "path"} onClick={() => setMode("path")} icon={<ServerCog className="h-4 w-4" />}>
            Server path
          </ModeBtn>
        </div>

        {mode === "upload" && (
          <>
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              className={cn(
                "rounded-xl border-2 border-dashed p-10 text-center transition",
                dragOver
                  ? "border-primary bg-primary/5"
                  : "border-border bg-muted/10 hover:bg-muted/20",
              )}
            >
              <FolderOpen className="mx-auto h-10 w-10 text-muted-foreground" />
              <div className="mt-3 text-base font-medium">
                Drop .md files or folders here
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                or pick them manually
              </div>
              <div className="mt-4 flex justify-center gap-2">
                <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
                  <FileText className="h-4 w-4" /> Choose files
                </Button>
                <Button variant="outline" size="sm" onClick={() => dirInputRef.current?.click()}>
                  <FolderOpen className="h-4 w-4" /> Choose folder
                </Button>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".md,.markdown,text/markdown"
                multiple
                className="hidden"
                onChange={(e) => onFilePick(e.target.files, false)}
              />
              <input
                ref={dirInputRef}
                type="file"
                multiple
                className="hidden"
                // @ts-expect-error non-standard but widely supported
                webkitdirectory=""
                directory=""
                onChange={(e) => onFilePick(e.target.files, true)}
              />
            </div>

            {entries.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>
                    {entries.length} file{entries.length === 1 ? "" : "s"} ·{" "}
                    {(totalBytes / 1024).toFixed(1)} KB
                  </Label>
                  <Button size="sm" variant="ghost" onClick={() => setEntries([])}>
                    Clear
                  </Button>
                </div>
                <div className="max-h-64 overflow-auto rounded-md border border-border bg-background/40">
                  <table className="w-full text-sm">
                    <tbody>
                      {entries.map((e) => (
                        <tr key={e.relPath} className="border-b border-border/50 last:border-0">
                          <td className="px-3 py-1.5 font-mono text-xs">{e.relPath}</td>
                          <td className="px-3 py-1.5 text-right text-xs text-muted-foreground tabular-nums">
                            {(e.file.size / 1024).toFixed(1)} KB
                          </td>
                          <td className="px-2 py-1 text-right">
                            <button
                              onClick={() => removeEntry(e.relPath)}
                              className="rounded p-1 text-muted-foreground hover:bg-destructive hover:text-destructive-foreground"
                              aria-label="remove"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}

        {mode === "path" && (
          <div className="space-y-2">
            <Label htmlFor="path">Absolute folder path on the backend host</Label>
            <div className="flex gap-2">
              <FolderOpen className="mt-2 h-5 w-5 text-muted-foreground" />
              <Input
                id="path"
                placeholder="/Users/you/medical-textbooks"
                value={path}
                onChange={(e) => setPath(e.target.value)}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              The backend reads files from this path directly — no upload. Use when your data
              already lives where the API runs.
            </p>
          </div>
        )}

        {error && (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}

        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack} disabled={uploading}>
            Back
          </Button>
          <Button onClick={submit} disabled={uploading || (mode === "upload" && entries.length === 0)}>
            {uploading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> Uploading…
              </>
            ) : (
              <>{mode === "upload" ? "Upload & continue" : "Continue"}</>
            )}
          </Button>
        </div>

        {value && (
          <div className="text-xs text-muted-foreground">
            Staged at <span className="font-mono">{value}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ModeBtn({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 rounded px-3 py-1.5 transition",
        active
          ? "bg-primary text-primary-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      {children}
    </button>
  );
}

// -------- DataTransfer / folder traversal --------

interface FSEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  fullPath: string;
  file?: (cb: (file: File) => void, err: (e: any) => void) => void;
  createReader?: () => {
    readEntries: (cb: (entries: FSEntry[]) => void, err: (e: any) => void) => void;
  };
}

async function collectFromDataTransfer(
  items: DataTransferItem[],
  fallback: FileList | null,
): Promise<Entry[]> {
  const out: Entry[] = [];
  let usedItems = false;
  for (const it of items) {
    const entry: FSEntry | null =
      // @ts-expect-error vendor API
      typeof it.webkitGetAsEntry === "function" ? it.webkitGetAsEntry() : null;
    if (!entry) continue;
    usedItems = true;
    await walkEntry(entry, "", out);
  }
  if (!usedItems && fallback) {
    for (const f of Array.from(fallback)) {
      out.push({ file: f, relPath: f.name });
    }
  }
  return out;
}

function walkEntry(entry: FSEntry, prefix: string, out: Entry[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isFile && entry.file) {
      entry.file(
        (file) => {
          out.push({ file, relPath: rel });
          resolve();
        },
        (e) => reject(e),
      );
      return;
    }
    if (entry.isDirectory && entry.createReader) {
      const reader = entry.createReader();
      const readBatch = () => {
        reader.readEntries(
          async (entries) => {
            if (entries.length === 0) {
              resolve();
              return;
            }
            try {
              for (const child of entries) {
                await walkEntry(child, rel, out);
              }
              // readEntries returns in batches; keep going until empty
              readBatch();
            } catch (e) {
              reject(e);
            }
          },
          (e) => reject(e),
        );
      };
      readBatch();
      return;
    }
    resolve();
  });
}
