import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Brain, CheckCircle2, Database, Eye, EyeOff, Loader2, RefreshCw,
  Save, Trash2, XCircle, Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { SearchableSelect, SearchableOption } from "@/components/ui/searchable-select";
import { PageHeader } from "@/components/PageHeader";
import { PageContainer } from "@/components/PageContainer";
import { cn } from "@/lib/utils";
import { api, ModelOption, ResetCounts, ResetTarget, SettingsView } from "@/lib/api";
import { confirm } from "@/lib/confirm";
import { RuntimeSettingsPanel } from "@/components/RuntimeSettingsPanel";

type Section = "llm" | "neo4j" | "chat" | "pipeline" | "reset";

const SECTIONS: { key: Section; label: string; icon: any; hint: string }[] = [
  { key: "llm",      label: "LLM & Embeddings", icon: Brain,    hint: "Provider, model, key" },
  { key: "neo4j",    label: "Neo4j",            icon: Database, hint: "Graph connection" },
  { key: "chat",     label: "Chat / RAG",       icon: Brain,    hint: "Retrieval tuning knobs" },
  { key: "pipeline", label: "Pipeline",         icon: Brain,    hint: "Retries & extraction tuning" },
  { key: "reset",    label: "Reset / Cleanup",  icon: Trash2,   hint: "Wipe data, fresh setup" },
];

// UI metadata for each reset target.
interface ResetTargetMeta {
  key: ResetTarget;
  label: string;
  description: string;
  destructive: boolean;       // require typed confirmation
  inPreset: boolean;          // included in "Fresh setup"
}

const RESET_META: ResetTargetMeta[] = [
  { key: "graph",        label: "Neo4j graph",       description: "Delete every node and relationship in Neo4j.",                                destructive: true,  inPreset: true  },
  { key: "documents",    label: "Documents",         description: "Drop document rows and clear staging files. Cascades to graph and runs.",     destructive: true,  inPreset: true  },
  { key: "schema",       label: "Schema",            description: "Reset active schema to empty and drop version history. Cascades to runs.",    destructive: false, inPreset: true  },
  { key: "runs",         label: "Ingest runs",       description: "Drop ingest_runs and ingest_events tables.",                                  destructive: false, inPreset: true  },
  { key: "llm_logs",     label: "LLM call audit log", description: "Drop every recorded LLM request and response.",                              destructive: false, inPreset: true  },
  { key: "prompts",      label: "Customized prompts", description: "Reset every is_custom prompt back to its on-disk default.",                  destructive: false, inPreset: true  },
  { key: "app_settings", label: "App settings",      description: "Drop UI-configured LLM/Neo4j overrides. Backend falls back to .env values.",  destructive: false, inPreset: false },
];

const CASCADE: Record<string, ResetTarget[]> = {
  documents: ["graph", "runs"],
  schema:    ["runs"],
};

type TestResult = {
  ok: boolean;
  latency_ms?: number;
  error?: string;
  dimension?: number;
  model?: string;
};

const PROVIDERS = [
  { value: "openrouter", label: "OpenRouter" },
  { value: "openai", label: "OpenAI" },
  { value: "openai-compatible", label: "OpenAI-compatible (LM Studio, vLLM, …)" },
  { value: "local", label: "Local (HuggingFace)" },
];

export function SettingsPage() {
  const [view, setView] = useState<SettingsView | null>(null);
  const [loading, setLoading] = useState(true);
  const [section, setSection] = useState<Section>("llm");

  // LLM form
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [model, setModel] = useState("");
  const [embProvider, setEmbProvider] = useState("openrouter");
  const [embModel, setEmbModel] = useState("");
  const [embDim, setEmbDim] = useState<number>(0);

  // Neo4j form
  const [nUri, setNUri] = useState("");
  const [nUser, setNUser] = useState("");
  const [nPass, setNPass] = useState("");
  const [nDb, setNDb] = useState("");
  const [showPass, setShowPass] = useState(false);

  // Chat / RAG form
  const [chatTopK, setChatTopK] = useState<number>(5);
  const [chatSplit, setChatSplit] = useState<number>(3000);
  const [chatThreshold, setChatThreshold] = useState<number>(0.10);
  const [savingChat, setSavingChat] = useState(false);

  // models lists
  const [chatModels, setChatModels] = useState<ModelOption[]>([]);
  const [embModels, setEmbModels] = useState<ModelOption[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);

  // states
  const [savingLLM, setSavingLLM] = useState(false);
  const [savingNeo, setSavingNeo] = useState(false);
  const [testLLMResult, setTestLLMResult] = useState<TestResult | null>(null);
  const [testEmbResult, setTestEmbResult] = useState<TestResult | null>(null);
  const [testNeoResult, setTestNeoResult] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState<{ llm?: boolean; emb?: boolean; neo?: boolean }>({});
  const [toast, setToast] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  // reset section state
  const [resetCounts, setResetCounts] = useState<ResetCounts | null>(null);
  const [resetSel, setResetSel] = useState<Set<ResetTarget>>(new Set());
  const [resetBusy, setResetBusy] = useState(false);
  const [resetCountsLoading, setResetCountsLoading] = useState(false);
  const [lastReset, setLastReset] = useState<{ targets: ResetTarget[]; cleared: Record<string, any>; errors: Record<string, string> } | null>(null);

  const loadView = async () => {
    try {
      const v = await api.getSettings();
      setView(v);
      setBaseUrl(v.llm.base_url || "");
      setModel(v.llm.model || "");
      setEmbProvider(v.embedding.provider || "openrouter");
      setEmbModel(v.embedding.model || "");
      setEmbDim(v.embedding.dimension || 0);
      setNUri(v.neo4j.uri || "");
      setNUser(v.neo4j.username || "");
      setNDb(v.neo4j.database || "");
      if (v.chat) {
        setChatTopK(v.chat.top_k);
        setChatSplit(v.chat.doc_split_size);
        setChatThreshold(v.chat.embedding_filter_threshold);
      }
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { loadView(); }, []);

  const refreshModels = async () => {
    if (!baseUrl) return;
    setModelsLoading(true);
    try {
      const [chat, emb] = await Promise.all([
        api.listModels({ base_url: baseUrl, api_key: apiKey || undefined, kind: "chat" }),
        api.listModels({ base_url: baseUrl, api_key: apiKey || undefined, kind: "embedding" }),
      ]);
      setChatModels(chat.ok ? chat.models : []);
      setEmbModels(emb.ok ? emb.models : []);
      if (!chat.ok && chat.error) setToast({ kind: "err", msg: `Models: ${chat.error}` });
    } catch (e: any) {
      setToast({ kind: "err", msg: e?.message ?? String(e) });
    } finally {
      setModelsLoading(false);
    }
  };

  // auto-load models once we have a base URL + key set
  useEffect(() => {
    if (view && (view.llm.api_key_set || apiKey) && baseUrl) {
      refreshModels();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  const chatOpts: SearchableOption[] = useMemo(
    () => chatModels.map((m) => ({ value: m.id, label: m.id, hint: m.owned_by })),
    [chatModels],
  );
  const embOpts: SearchableOption[] = useMemo(
    () => embModels.map((m) => ({ value: m.id, label: m.id, hint: m.owned_by })),
    [embModels],
  );

  const flashToast = (t: { kind: "ok" | "err"; msg: string }) => {
    setToast(t);
    window.setTimeout(() => setToast(null), 3500);
  };

  const runTestLLM = async () => {
    setTesting((s) => ({ ...s, llm: true }));
    setTestLLMResult(null);
    try {
      const r = await api.testLLM({ base_url: baseUrl, api_key: apiKey || undefined, model });
      setTestLLMResult(r);
    } catch (e: any) {
      setTestLLMResult({ ok: false, error: e?.message ?? String(e) });
    } finally {
      setTesting((s) => ({ ...s, llm: false }));
    }
  };
  const runTestEmb = async () => {
    setTesting((s) => ({ ...s, emb: true }));
    setTestEmbResult(null);
    try {
      const r = await api.testEmbedding({
        base_url: baseUrl,
        api_key: apiKey || undefined,
        model: embModel,
        dimension: embDim || undefined,
      });
      setTestEmbResult(r);
    } catch (e: any) {
      setTestEmbResult({ ok: false, error: e?.message ?? String(e) });
    } finally {
      setTesting((s) => ({ ...s, emb: false }));
    }
  };
  const runTestNeo = async () => {
    setTesting((s) => ({ ...s, neo: true }));
    setTestNeoResult(null);
    try {
      const r = await api.testNeo4j({
        uri: nUri,
        username: nUser,
        password: nPass || undefined,
        database: nDb,
      });
      setTestNeoResult(r);
    } catch (e: any) {
      setTestNeoResult({ ok: false, error: e?.message ?? String(e) });
    } finally {
      setTesting((s) => ({ ...s, neo: false }));
    }
  };

  const saveLLM = async () => {
    setSavingLLM(true);
    try {
      const v = await api.saveLLMSettings({
        base_url: baseUrl,
        api_key: apiKey || undefined,
        model,
        embedding_provider: embProvider,
        embedding_model: embModel,
        embedding_dimension: embDim || undefined,
      });
      setView(v);
      setApiKey("");
      flashToast({ kind: "ok", msg: "LLM settings saved." });
    } catch (e: any) {
      flashToast({ kind: "err", msg: e?.message ?? String(e) });
    } finally {
      setSavingLLM(false);
    }
  };
  const saveNeo = async () => {
    setSavingNeo(true);
    try {
      const v = await api.saveNeo4jSettings({
        uri: nUri,
        username: nUser,
        password: nPass || undefined,
        database: nDb,
      });
      setView(v);
      setNPass("");
      if (v.reconnect && !v.reconnect.ok) {
        flashToast({ kind: "err", msg: `Saved, but reconnect failed: ${v.reconnect.error}` });
      } else {
        flashToast({ kind: "ok", msg: "Neo4j settings saved & reconnected." });
      }
    } catch (e: any) {
      flashToast({ kind: "err", msg: e?.message ?? String(e) });
    } finally {
      setSavingNeo(false);
    }
  };

  const saveChat = async () => {
    setSavingChat(true);
    try {
      const v = await api.saveChatSettings({
        top_k: chatTopK,
        doc_split_size: chatSplit,
        embedding_filter_threshold: chatThreshold,
      });
      setView(v);
      flashToast({ kind: "ok", msg: "Chat settings saved." });
    } catch (e: any) {
      flashToast({ kind: "err", msg: e?.message ?? String(e) });
    } finally {
      setSavingChat(false);
    }
  };

  // ---------- reset handlers ----------
  const loadResetCounts = async () => {
    setResetCountsLoading(true);
    try {
      setResetCounts(await api.resetCounts());
    } catch (e: any) {
      flashToast({ kind: "err", msg: `Counts failed: ${e?.message ?? e}` });
    } finally {
      setResetCountsLoading(false);
    }
  };

  useEffect(() => {
    if (section === "reset" && !resetCounts) {
      loadResetCounts();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section]);

  // expanded selection (after cascade)
  const effectiveSel = useMemo<Set<ResetTarget>>(() => {
    const out = new Set<ResetTarget>(resetSel);
    for (const t of resetSel) {
      for (const dep of CASCADE[t] || []) out.add(dep);
    }
    return out;
  }, [resetSel]);

  const isForced = (t: ResetTarget) => effectiveSel.has(t) && !resetSel.has(t);

  const toggleTarget = (t: ResetTarget) => {
    setResetSel((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  };

  const applyPreset = () => {
    const next = new Set<ResetTarget>(
      RESET_META.filter((m) => m.inPreset).map((m) => m.key),
    );
    setResetSel(next);
  };

  const clearSelection = () => setResetSel(new Set());

  const runReset = async () => {
    const targets = Array.from(effectiveSel);
    if (targets.length === 0) return;

    const destructive = targets.some(
      (t) => RESET_META.find((m) => m.key === t)?.destructive,
    );
    const ok = await confirm({
      title: "Run reset?",
      description: destructive
        ? `Wipes ${targets.length} target(s): ${targets.join(", ")}. This cannot be undone.`
        : `Will reset: ${targets.join(", ")}.`,
      confirmText: "Run reset",
      variant: "destructive",
    });
    if (!ok) return;

    setResetBusy(true);
    setLastReset(null);
    try {
      const result = await api.runReset(targets);
      setLastReset({
        targets: result.targets,
        cleared: result.cleared,
        errors: result.errors,
      });
      setResetSel(new Set());
      await loadResetCounts();
      if (Object.keys(result.errors || {}).length > 0) {
        flashToast({ kind: "err", msg: `Reset completed with errors.` });
      } else {
        flashToast({ kind: "ok", msg: `Reset done: ${result.targets.join(", ")}.` });
      }
      // refresh view (app_settings reset may have changed it)
      if (targets.includes("app_settings")) await loadView();
    } catch (e: any) {
      flashToast({ kind: "err", msg: e?.message ?? String(e) });
    } finally {
      setResetBusy(false);
    }
  };

  const headerActions =
    section === "llm" ? (
      <Button size="sm" onClick={saveLLM} disabled={savingLLM}>
        {savingLLM ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
        Save LLM settings
      </Button>
    ) : section === "neo4j" ? (
      <Button size="sm" onClick={saveNeo} disabled={savingNeo}>
        {savingNeo ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
        Save Neo4j settings
      </Button>
    ) : section === "chat" ? (
      <Button size="sm" onClick={saveChat} disabled={savingChat}>
        {savingChat ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
        Save chat settings
      </Button>
    ) : section === "reset" ? (
      <>
        <Button variant="outline" size="sm" onClick={loadResetCounts} disabled={resetCountsLoading}>
          <RefreshCw className={resetCountsLoading ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
          Refresh counts
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={runReset}
          disabled={resetBusy || effectiveSel.size === 0}
        >
          {resetBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
          Run reset ({effectiveSel.size})
        </Button>
      </>
    ) : null;

  if (loading) {
    return (
      <PageContainer
        header={
          <PageHeader
            title="Settings"
            description="Runtime config. Overrides repo .env; persisted to SQLite. Takes effect immediately."
          />
        }
      >
        <div className="text-sm text-muted-foreground">Loading settings…</div>
      </PageContainer>
    );
  }

  return (
    <PageContainer
      header={
        <PageHeader
          title="Settings"
          description="Runtime config. Overrides repo .env; persisted to SQLite. Takes effect immediately."
          actions={headerActions}
        />
      }
    >
      <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
        {/* sidebar */}
        <Card>
          <CardHeader>
            <CardTitle>Sections</CardTitle>
            <CardDescription>{SECTIONS.length} groups</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <ul className="text-sm">
              {SECTIONS.map((s) => {
                const Icon = s.icon;
                const active = s.key === section;
                return (
                  <li key={s.key}>
                    <button
                      onClick={() => setSection(s.key)}
                      className={cn(
                        "flex w-full items-center gap-2 border-b border-border px-4 py-2 text-left transition-colors last:border-0",
                        active ? "bg-accent" : "hover:bg-accent/50",
                      )}
                    >
                      <Icon className={cn("h-4 w-4", active ? "text-foreground" : "text-muted-foreground")} />
                      <div className="min-w-0 flex-1">
                        <div className="truncate">{s.label}</div>
                        <div className="truncate text-2xs text-muted-foreground">{s.hint}</div>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          </CardContent>
        </Card>

        {/* panel */}
        <div className="space-y-4">
        {section === "llm" && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Provider (OpenAI-compatible)</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>Base URL</Label>
                  <Input
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="https://openrouter.ai/api/v1"
                  />
                  <p className="text-2xs text-muted-foreground">
                    e.g. <code>https://openrouter.ai/api/v1</code>, <code>http://localhost:1234/v1</code>
                  </p>
                </div>
                <div className="space-y-1.5">
                  <Label>API Key</Label>
                  <div className="flex gap-1.5">
                    <Input
                      type={showKey ? "text" : "password"}
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder={view?.llm.api_key_set ? view.llm.api_key_masked ?? "•••" : "sk-…"}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setShowKey((v) => !v)}
                      title={showKey ? "Hide" : "Show"}
                    >
                      {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    </Button>
                  </div>
                  <p className="text-2xs text-muted-foreground">
                    Leave blank to keep existing key. {view?.llm.api_key_set && "Current key is set."}
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>Chat model</Label>
                  <SearchableSelect
                    value={model}
                    onChange={setModel}
                    options={chatOpts}
                    placeholder="Select chat model…"
                    loading={modelsLoading}
                    onRefresh={refreshModels}
                    emptyText="No models. Set base URL + key, then refresh."
                  />
                </div>
                <div className="flex items-end">
                  <Button onClick={runTestLLM} disabled={testing.llm || !model} variant="outline" size="sm">
                    {testing.llm ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Zap className="h-3.5 w-3.5" />
                    )}
                    Test API key
                  </Button>
                  {testLLMResult && <TestPill r={testLLMResult} />}
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Embeddings</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                <div className="space-y-1.5">
                  <Label>Provider</Label>
                  <select
                    value={embProvider}
                    onChange={(e) => setEmbProvider(e.target.value)}
                    className="flex h-8 w-full rounded-sm border border-border bg-background px-2 text-sm"
                  >
                    {PROVIDERS.map((p) => (
                      <option key={p.value} value={p.value}>{p.label}</option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1.5 md:col-span-2">
                  <Label>Embedding model</Label>
                  <SearchableSelect
                    value={embModel}
                    onChange={setEmbModel}
                    options={embOpts}
                    placeholder="Select embedding model…"
                    loading={modelsLoading}
                    onRefresh={refreshModels}
                    emptyText="No embedding models found."
                  />
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                <div className="space-y-1.5">
                  <Label>Dimension</Label>
                  <Input
                    type="number"
                    value={embDim || ""}
                    onChange={(e) => setEmbDim(Number(e.target.value) || 0)}
                    placeholder="3072"
                  />
                  <p className="text-2xs text-muted-foreground">
                    Must match Neo4j vector index. Changing breaks existing embeddings.
                  </p>
                </div>
                <div className="flex items-end gap-2 md:col-span-2">
                  <Button onClick={runTestEmb} disabled={testing.emb || !embModel} variant="outline" size="sm">
                    {testing.emb ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Zap className="h-3.5 w-3.5" />
                    )}
                    Test embedding
                  </Button>
                  {testEmbResult && <TestPill r={testEmbResult} />}
                </div>
              </div>
            </CardContent>
          </Card>

        </div>
        )}

        {section === "neo4j" && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Neo4j connection</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>URI</Label>
                  <Input
                    value={nUri}
                    onChange={(e) => setNUri(e.target.value)}
                    placeholder="bolt://localhost:7687"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Database</Label>
                  <Input value={nDb} onChange={(e) => setNDb(e.target.value)} placeholder="neo4j" />
                </div>
                <div className="space-y-1.5">
                  <Label>Username</Label>
                  <Input value={nUser} onChange={(e) => setNUser(e.target.value)} placeholder="neo4j" />
                </div>
                <div className="space-y-1.5">
                  <Label>Password</Label>
                  <div className="flex gap-1.5">
                    <Input
                      type={showPass ? "text" : "password"}
                      value={nPass}
                      onChange={(e) => setNPass(e.target.value)}
                      placeholder={view?.neo4j.password_set ? "•••• (kept if blank)" : "password"}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setShowPass((v) => !v)}
                    >
                      {showPass ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    </Button>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Button onClick={runTestNeo} disabled={testing.neo} variant="outline" size="sm">
                  {testing.neo ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Zap className="h-3.5 w-3.5" />
                  )}
                  Test connection
                </Button>
                {testNeoResult && <TestPill r={testNeoResult} />}
              </div>
            </CardContent>
          </Card>

        </div>
        )}

        {section === "chat" && (
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Chat / RAG tuning</CardTitle>
                <CardDescription>
                  Retrieval knobs applied to every RAG turn. Changes take effect
                  on the next question — no restart.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                  <div className="space-y-1.5">
                    <Label>top_k</Label>
                    <Input
                      type="number" min={1} max={100} step={1}
                      value={chatTopK}
                      onChange={(e) => setChatTopK(Number(e.target.value))}
                    />
                    <p className="text-2xs text-muted-foreground">
                      Documents pulled from the vector/hybrid index per question.
                      Higher = better recall, more tokens, slower. Default 5.
                    </p>
                  </div>
                  <div className="space-y-1.5">
                    <Label>doc_split_size</Label>
                    <Input
                      type="number" min={64} max={8000} step={1}
                      value={chatSplit}
                      onChange={(e) => setChatSplit(Number(e.target.value))}
                    />
                    <p className="text-2xs text-muted-foreground">
                      Token chunk size used by the post-retrieval splitter before
                      the embeddings filter. Smaller = finer evidence selection.
                      Default 3000.
                    </p>
                  </div>
                  <div className="space-y-1.5">
                    <Label>embedding_filter_threshold</Label>
                    <Input
                      type="number" min={0} max={1} step={0.01}
                      value={chatThreshold}
                      onChange={(e) => setChatThreshold(Number(e.target.value))}
                    />
                    <p className="text-2xs text-muted-foreground">
                      Cosine-similarity floor for the post-retrieval compression
                      filter. 0 disables filtering. Default 0.10.
                    </p>
                  </div>
                </div>
                <div className="rounded-sm border border-border/60 bg-muted/30 p-3 text-2xs leading-snug text-muted-foreground">
                  <p className="mb-1 font-medium text-foreground">Tuning tips</p>
                  <ul className="ml-4 list-disc space-y-1">
                    <li>If answers say "not enough info" but you can see the
                      fact in the corpus, raise <span className="font-mono">top_k</span> (try 10–15).</li>
                    <li>If retrieved chunks are too long and dilute the prompt,
                      drop <span className="font-mono">doc_split_size</span> to
                      400–600.</li>
                    <li>Raise <span className="font-mono">embedding_filter_threshold</span> to
                      tighten relevance; lower (e.g. 0) to keep more context.</li>
                  </ul>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Chat prompts</CardTitle>
                <CardDescription>
                  Every chat-related prompt template (system, question rewriter,
                  title generator) is editable on the
                  <a href="#/prompts" className="ml-1 text-primary underline-offset-2 hover:underline">
                    Prompts page
                  </a>.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ul className="text-xs font-mono">
                  <li className="flex items-center justify-between border-b border-border/60 py-1.5 last:border-0">
                    <span>chat_system</span>
                    <span className="text-2xs text-muted-foreground">RAG answer prompt</span>
                  </li>
                  <li className="flex items-center justify-between border-b border-border/60 py-1.5 last:border-0">
                    <span>chat_question_rewrite</span>
                    <span className="text-2xs text-muted-foreground">History-aware query rewriter</span>
                  </li>
                  <li className="flex items-center justify-between py-1.5">
                    <span>chat_title_generate</span>
                    <span className="text-2xs text-muted-foreground">Auto-title after first turn</span>
                  </li>
                </ul>
              </CardContent>
            </Card>
          </div>
        )}

        {section === "pipeline" && <RuntimeSettingsPanel />}

        {section === "reset" && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <AlertTriangle className="h-4 w-4 text-[hsl(var(--warning))]" />
                Reset / Cleanup
              </CardTitle>
              <CardDescription>
                Wipe selected stores so you can start over. Cascading dependencies are checked automatically. This cannot be undone.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="outline" size="sm" onClick={applyPreset}>
                  Select "Fresh setup"
                </Button>
                <Button variant="ghost" size="sm" onClick={clearSelection}
                        disabled={resetSel.size === 0}>
                  Clear selection
                </Button>
                <span className="ml-auto text-2xs text-muted-foreground">
                  {effectiveSel.size} target{effectiveSel.size === 1 ? "" : "s"} will run
                </span>
              </div>

              <ul className="divide-y divide-border rounded-sm border border-border">
                {RESET_META.map((m) => {
                  const cnt = resetCounts?.[m.key];
                  const checked = effectiveSel.has(m.key);
                  const forced = isForced(m.key);
                  const cascades = CASCADE[m.key];
                  return (
                    <li key={m.key} className="flex items-start gap-3 px-3 py-2.5">
                      <input
                        type="checkbox"
                        className="mt-0.5 h-4 w-4 accent-foreground"
                        checked={checked}
                        disabled={forced || resetBusy}
                        onChange={() => toggleTarget(m.key)}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium">{m.label}</span>
                          {m.destructive && (
                            <Badge variant="destructive" className="text-2xs">destructive</Badge>
                          )}
                          {forced && (
                            <Badge variant="warning" className="text-2xs">auto (cascade)</Badge>
                          )}
                          <code className="ml-auto rounded-sm bg-muted px-1.5 py-0.5 font-mono text-2xs text-muted-foreground">
                            {cnt ? `${cnt.count} ${cnt.unit}` : resetCountsLoading ? "…" : "—"}
                          </code>
                        </div>
                        <p className="mt-0.5 text-xs text-muted-foreground">{m.description}</p>
                        {cascades && cascades.length > 0 && (
                          <p className="mt-0.5 text-2xs text-muted-foreground">
                            Cascades to: <span className="font-mono">{cascades.join(", ")}</span>
                          </p>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </CardContent>
          </Card>

          {lastReset && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Last reset result</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-1 text-sm">
                  {lastReset.targets.map((t) => {
                    const err = lastReset.errors[t];
                    const cleared = lastReset.cleared[t];
                    return (
                      <li key={t} className="flex items-center gap-2 font-mono text-xs">
                        {err ? (
                          <XCircle className="h-3.5 w-3.5 text-destructive" />
                        ) : (
                          <CheckCircle2 className="h-3.5 w-3.5 text-[hsl(var(--success))]" />
                        )}
                        <span className="w-36">{t}</span>
                        <span className="text-muted-foreground">
                          {err ? err : JSON.stringify(cleared)}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>
        )}
        </div>
      </div>

      {toast && (
        <div
          className={`fixed bottom-4 right-4 z-50 max-w-md rounded-sm border px-3 py-2 text-sm shadow-lg ${
            toast.kind === "ok"
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
              : "border-red-500/40 bg-red-500/10 text-red-200"
          }`}
        >
          {toast.msg}
        </div>
      )}
    </PageContainer>
  );
}

function TestPill({ r }: { r: TestResult }) {
  if (r.ok) {
    return (
      <Badge variant="success" className="ml-2">
        <CheckCircle2 className="mr-1 h-3 w-3" />
        ok{r.latency_ms != null ? ` · ${r.latency_ms}ms` : ""}
        {r.dimension ? ` · ${r.dimension}d` : ""}
      </Badge>
    );
  }
  return (
    <Badge variant="destructive" className="ml-2" title={r.error}>
      <XCircle className="mr-1 h-3 w-3" />
      <span className="max-w-[28ch] truncate">{r.error || "failed"}</span>
    </Badge>
  );
}
