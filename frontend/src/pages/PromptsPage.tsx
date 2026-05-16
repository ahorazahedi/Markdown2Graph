import { useEffect, useMemo, useState } from "react";
import { Save, RotateCcw, Play, Check, Library } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/PageHeader";
import { PageContainer } from "@/components/PageContainer";
import { api, PromptRow } from "@/lib/api";
import { useUnsavedGuard } from "@/lib/unsavedGuard";
import { confirm } from "@/lib/confirm";

export function PromptsPage() {
  const [items, setItems] = useState<PromptRow[]>([]);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [template, setTemplate] = useState("");
  const [vars, setVars] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState("");
  const [busy, setBusy] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [presets, setPresets] = useState<{ name: string }[]>([]);
  const [presetChoice, setPresetChoice] = useState("");

  const refresh = async () => {
    const r = await api.listPrompts();
    setItems(r.items);
    if (!activeKey && r.items.length) {
      setActiveKey(r.items[0].key);
    }
  };
  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    api.listPromptPresets()
      .then((r) => setPresets(r.items.map((p) => ({ name: p.name }))))
      .catch(() => setPresets([]));
  }, []);

  const applyPreset = async () => {
    if (!presetChoice) return;
    const ok = await confirm({
      title: `Load "${presetChoice}" preset?`,
      description: "Replaces every prompt template with the preset's content. Your current edits will be overwritten.",
      confirmText: "Load preset",
      variant: "destructive",
    });
    if (!ok) return;
    setBusy(true); setError(null);
    try {
      await api.applyPromptPreset(presetChoice);
      await refresh();
      // force editor to reload the now-updated active prompt
      const stillActive = activeKey;
      setActiveKey(null);
      setTimeout(() => setActiveKey(stillActive), 0);
      setSavedAt(Date.now());
    } catch (e: any) {
      setError(String(e.message || e));
    } finally { setBusy(false); }
  };

  const active = useMemo(() => items.find((p) => p.key === activeKey) || null, [items, activeKey]);

  // load the picked prompt into the editor
  useEffect(() => {
    if (!active) return;
    setTemplate(active.template);
    const seeded: Record<string, string> = {};
    for (const v of active.variables) {
      seeded[v.name] = typeof v.sample === "string" ? v.sample : JSON.stringify(v.sample ?? "", null, 2);
    }
    setVars(seeded);
    setPreview("");
    setError(null);
  }, [active?.key]);

  const dirty = !!active && template !== active.template;
  useUnsavedGuard(dirty);

  const parsedVars = useMemo(() => {
    if (!active) return {};
    const out: Record<string, any> = {};
    for (const v of active.variables) {
      const raw = vars[v.name];
      if (raw == null || raw === "") continue;
      // try JSON for lists / objects; fall back to string
      try {
        if (raw.trim().startsWith("[") || raw.trim().startsWith("{")) {
          out[v.name] = JSON.parse(raw);
          continue;
        }
      } catch { /* swallow */ }
      out[v.name] = raw;
    }
    return out;
  }, [vars, active]);

  const renderPreview = async () => {
    if (!active) return;
    setBusy(true); setError(null);
    try {
      const r = await api.previewPrompt(active.key, { template, vars: parsedVars });
      setPreview(r.rendered);
    } catch (e: any) {
      setError(String(e.message || e));
    } finally { setBusy(false); }
  };

  const save = async () => {
    if (!active) return;
    setBusy(true); setError(null);
    try {
      const saved = await api.savePrompt(active.key, template);
      setItems((prev) => prev.map((p) => (p.key === saved.key ? saved : p)));
      setSavedAt(Date.now());
    } catch (e: any) {
      setError(String(e.message || e));
    } finally { setBusy(false); }
  };

  const reset = async () => {
    if (!active) return;
    const ok = await confirm({
      title: "Reset prompt to default?",
      description: "Discards your local edits and restores the on-disk template shipped with the app.",
      confirmText: "Reset",
      variant: "destructive",
    });
    if (!ok) return;
    setBusy(true); setError(null);
    try {
      const r = await api.resetPrompt(active.key);
      setItems((prev) => prev.map((p) => (p.key === r.key ? r : p)));
      setTemplate(r.template);
    } catch (e: any) {
      setError(String(e.message || e));
    } finally { setBusy(false); }
  };

  return (
    <PageContainer
      header={
        <PageHeader
          title="Prompts"
          description="System prompts that drive schema discovery and entity extraction. Templates use Jinja2; variables are injected at runtime."
          actions={
            <>
              {presets.length > 0 && (
                <div className="flex items-center gap-1.5">
                  <Library className="h-3.5 w-3.5 text-muted-foreground" />
                  <select
                    className="h-8 rounded-sm border border-border bg-background px-2 text-xs"
                    value={presetChoice}
                    onChange={(e) => setPresetChoice(e.target.value)}
                  >
                    <option value="">load preset…</option>
                    {presets.map((p) => (
                      <option key={p.name} value={p.name}>{p.name}</option>
                    ))}
                  </select>
                  <Button size="sm" variant="outline" onClick={applyPreset}
                          disabled={!presetChoice || busy}>
                    Apply
                  </Button>
                </div>
              )}
              {savedAt && Date.now() - savedAt < 4000 && (
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Check className="h-3 w-3" /> saved
                </span>
              )}
              <Button variant="outline" size="sm" onClick={reset} disabled={!active || busy}>
                <RotateCcw className="h-3.5 w-3.5" /> Reset to default
              </Button>
              <Button size="sm" onClick={save} disabled={!dirty || busy}>
                <Save className="h-3.5 w-3.5" /> Save
              </Button>
            </>
          }
        />
      }
    >
      <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
        {/* prompt list */}
        <Card>
          <CardHeader>
            <CardTitle>Templates</CardTitle>
            <CardDescription>{items.length} registered</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <ul className="text-sm">
              {items.map((p) => (
                <li key={p.key}>
                  <button
                    onClick={() => setActiveKey(p.key)}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 px-4 py-2 border-b border-border last:border-0 text-left transition-colors",
                      p.key === activeKey ? "bg-accent" : "hover:bg-accent/50",
                    )}
                  >
                    <span className="truncate font-mono text-xs">{p.key}</span>
                    {p.is_custom && <Badge variant="warning">custom</Badge>}
                  </button>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        {/* editor + preview */}
        {active && (
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <span className="font-mono text-sm">{active.key}</span>
                  {active.is_custom
                    ? <Badge variant="warning">custom</Badge>
                    : <Badge variant="secondary">default</Badge>}
                </CardTitle>
                <CardDescription>{active.description}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="text-2xs text-muted-foreground">
                  Variables: {active.variables.length === 0
                    ? <span className="font-mono">—</span>
                    : active.variables.map((v) => (
                        <code key={v.name} className="mx-1 rounded-sm bg-muted px-1.5 py-0.5">{`{{ ${v.name} }}`}</code>
                      ))}
                </div>
                <Textarea
                  rows={20}
                  value={template}
                  onChange={(e) => setTemplate(e.target.value)}
                  className="font-mono text-xs"
                  spellCheck={false}
                />
              </CardContent>
            </Card>

            {active.variables.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>Preview context</CardTitle>
                  <CardDescription>Fill values to render the template. Lists/objects accept JSON.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {active.variables.map((v) => (
                    <div key={v.name} className="space-y-1">
                      <Label htmlFor={`v-${v.name}`} className="font-mono text-xs">{v.name}</Label>
                      <div className="text-2xs text-muted-foreground">{v.description}</div>
                      {typeof v.sample === "string" ? (
                        <Input id={`v-${v.name}`} value={vars[v.name] ?? ""}
                               onChange={(e) => setVars({ ...vars, [v.name]: e.target.value })} />
                      ) : (
                        <Textarea id={`v-${v.name}`} rows={3} value={vars[v.name] ?? ""}
                                  onChange={(e) => setVars({ ...vars, [v.name]: e.target.value })}
                                  className="font-mono text-xs" />
                      )}
                    </div>
                  ))}
                  <Button size="sm" variant="outline" onClick={renderPreview} disabled={busy}>
                    <Play className="h-3.5 w-3.5" /> Render preview
                  </Button>
                </CardContent>
              </Card>
            )}

            {preview && (
              <Card>
                <CardHeader>
                  <CardTitle>Rendered output</CardTitle>
                </CardHeader>
                <CardContent>
                  <pre className="max-h-96 overflow-auto rounded-sm border border-border bg-muted/30 p-3 text-xs whitespace-pre-wrap">
                    {preview}
                  </pre>
                </CardContent>
              </Card>
            )}

            {error && (
              <div className="rounded-sm border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </div>
            )}
          </div>
        )}
      </div>
    </PageContainer>
  );
}
