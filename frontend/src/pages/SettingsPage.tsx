import { useEffect, useMemo, useState } from "react";
import { Brain, CheckCircle2, Database, Eye, EyeOff, Loader2, Save, XCircle, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { SearchableSelect, SearchableOption } from "@/components/ui/searchable-select";
import { PageHeader } from "@/components/PageHeader";
import { PageContainer } from "@/components/PageContainer";
import { cn } from "@/lib/utils";
import { api, ModelOption, SettingsView } from "@/lib/api";

type Section = "llm" | "neo4j";

const SECTIONS: { key: Section; label: string; icon: any; hint: string }[] = [
  { key: "llm",   label: "LLM & Embeddings", icon: Brain,    hint: "Provider, model, key" },
  { key: "neo4j", label: "Neo4j",            icon: Database, hint: "Graph connection" },
];

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

  const headerActions =
    section === "llm" ? (
      <Button size="sm" onClick={saveLLM} disabled={savingLLM}>
        {savingLLM ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
        Save LLM settings
      </Button>
    ) : (
      <Button size="sm" onClick={saveNeo} disabled={savingNeo}>
        {savingNeo ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
        Save Neo4j settings
      </Button>
    );

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
    </>
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
