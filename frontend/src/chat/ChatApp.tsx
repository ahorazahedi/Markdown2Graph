/**
 * Standalone chat workspace at `#/chat` (and `#/chat/<sessionId>`).
 *
 * Layout — completely independent of AppShell:
 *   ┌──────────────┬────────────────────────────────────────────┐
 *   │  Sessions    │  Header (title · mode · model)             │
 *   │  ──────────  │  ┌──────────────────────────────────────┐  │
 *   │  + New chat  │  │  Message thread (markdown)           │  │
 *   │  search…     │  │  …                                    │  │
 *   │  session …   │  └──────────────────────────────────────┘  │
 *   │  session …   │  ┌──────────────────────────────────────┐  │
 *   │  …           │  │  Input textarea  +  send             │  │
 *   └──────────────┴────────────────────────────────────────────┘
 *
 * Auth: V1 uses /api/me stub returning role='admin'. Future role-gated
 * routing flips the admin-only nav off for role='user'.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowLeft, MessageSquarePlus, Send, Loader2, Trash2, Search,
  ChevronRight, AlertCircle, Sparkles, Network, Check, ChevronDown,
  Activity, FileText, Copy, X,
  PanelRightOpen, PanelRightClose,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { GraphViewer } from "@/components/GraphViewer";
import {
  api, ChatAskResponse, ChatMessage, ChatSession, ChatTrace,
  DocumentRow, RetrievedDoc,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { confirm } from "@/lib/confirm";

type ModeInfo = {
  label: string;
  short: string;
  long: string;
  best: string;
};

const MODE_INFO: Record<string, ModeInfo> = {
  vector: {
    label: "vector",
    short: "Semantic similarity over chunk embeddings.",
    long: "Embeds the question, retrieves top-k chunks by cosine similarity, then filters splits with EmbeddingsFilter. Pure dense retrieval — no graph, no keyword index.",
    best: "Best for paraphrased / concept-level questions where wording differs from the source. Respects document filter.",
  },
  fulltext: {
    label: "hybrid (fulltext)",
    short: "Vector recall combined with Neo4j fulltext keyword index.",
    long: "Runs vector search and a Lucene fulltext query over chunk text in parallel; results are merged. Hybrid balances semantic recall with exact term hits.",
    best: "Best when the question contains specific terminology, acronyms, drug names, IDs — terms that must match literally.",
  },
  graph_vector: {
    label: "graph + vector",
    short: "Vector recall expanded with linked entities & relationships.",
    long: "Top-k chunks by vector similarity, then walks the graph to attach entities, relations, and neighboring nodes as additional context for the LLM.",
    best: "Best when answer depends on entity relationships (who/what is connected to X). Respects document filter.",
  },
  graph_vector_fulltext: {
    label: "graph + hybrid (default)",
    short: "Hybrid (vector + fulltext) recall with graph expansion.",
    long: "Combines vector and fulltext retrieval over chunks, then enriches with graph neighborhood (entities + relations). The most complete retrieval path.",
    best: "Sensible default for most questions. Use this unless you have a reason to narrow scope.",
  },
  entity_vector: {
    label: "entity-centric",
    short: "Retrieves over entity descriptions, not raw chunks.",
    long: "Indexes are over Entity nodes' summary text. Returns short entity descriptions rather than passages — skips chunk-level compression.",
    best: "Best for 'who/what is X', definition-style, or entity-lookup questions. Faster, lighter context.",
  },
  global_vector: {
    label: "community / global",
    short: "Community / cluster summaries for high-level questions.",
    long: "Searches embeddings over Community nodes — summaries of clustered subgraphs. Returns coarse, document-spanning context instead of fine-grained passages.",
    best: "Best for 'overall', 'across the corpus', 'main themes' style questions where local chunk retrieval would miss the bigger picture.",
  },
  graph: {
    label: "text → Cypher",
    short: "LLM generates a Cypher query against the graph.",
    long: "No embeddings. The LLM translates the question into a Cypher query, executes it on Neo4j, and answers from the rows. Deterministic but brittle on fuzzy phrasing.",
    best: "Best for structured questions with crisp constraints ('list all drugs targeting gene X', counts, joins).",
  },
};
const MODE_ORDER = [
  "graph_vector_fulltext", "graph_vector", "vector", "fulltext",
  "entity_vector", "global_vector", "graph",
];
const MODE_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(MODE_INFO).map(([k, v]) => [k, v.label])
);
const MODE_DOC_FILTER = new Set(["vector", "graph_vector"]);

function ModeSelect({
  value, onChange,
}: { value: string; onChange: (m: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const current = MODE_INFO[value] ?? MODE_INFO.graph_vector_fulltext;
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex h-7 items-center gap-1.5 rounded-sm border border-border bg-background px-2 text-2xs hover:bg-accent"
        title={current.short}
      >
        <span>{current.label}</span>
        <ChevronDown className="h-3 w-3 opacity-60" />
      </button>
      {open && (
        <div className="absolute left-0 top-8 z-30 w-[420px] max-w-[90vw] rounded-md border border-border bg-card p-1 shadow-lg">
          {MODE_ORDER.map((m) => {
            const info = MODE_INFO[m];
            const selected = m === value;
            return (
              <button
                key={m}
                type="button"
                onClick={() => { onChange(m); setOpen(false); }}
                className={cn(
                  "flex w-full items-start gap-2 rounded-sm px-2 py-2 text-left hover:bg-accent",
                  selected && "bg-accent/60"
                )}
              >
                <Check className={cn("mt-0.5 h-3.5 w-3.5 shrink-0",
                  selected ? "opacity-100" : "opacity-0")} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium">{info.label}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">{m}</span>
                  </div>
                  <p className="mt-0.5 text-2xs leading-snug text-muted-foreground">
                    {info.long}
                  </p>
                  <p className="mt-1 text-2xs leading-snug text-foreground/80">
                    <span className="font-medium">When to use:</span> {info.best}
                  </p>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

const HASH_PREFIX = "#/chat";

function hashToSessionId(): string | null {
  const h = window.location.hash;
  const m = /^#\/chat\/([^?#]+)/.exec(h);
  return m ? decodeURIComponent(m[1]) : null;
}
function setHashSession(id: string | null) {
  window.location.hash = id ? `${HASH_PREFIX}/${id}` : HASH_PREFIX;
}

export function ChatApp() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(hashToSessionId());
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<{ ok: boolean; messages: string[] } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await api.listChatSessions({
        limit: 100, search: search || undefined,
      });
      setSessions(r.items);
      if (!activeId && r.items.length > 0) {
        setActiveId(r.items[0].id);
        setHashSession(r.items[0].id);
      }
    } catch (e) {
      console.error(e);
    }
  }, [search, activeId]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    api.chatHealth().then(setHealth).catch(() => {});
    const onHash = () => setActiveId(hashToSessionId());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const onNewChat = async () => {
    setBusy(true);
    try {
      const s = await api.createChatSession({});
      await refresh();
      setActiveId(s.id);
      setHashSession(s.id);
    } finally { setBusy(false); }
  };

  const onDelete = async (id: string) => {
    const ok = await confirm({
      title: "Delete this chat?",
      description: "Messages cannot be recovered.",
      confirmText: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    await api.deleteChatSession(id);
    if (activeId === id) {
      setActiveId(null);
      setHashSession(null);
    }
    await refresh();
  };

  return (
    <div className="grid h-screen grid-cols-[280px_minmax(0,1fr)] overflow-hidden bg-background">
      <aside className="flex h-screen flex-col overflow-hidden border-r border-border bg-card/40">
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
          <a href="#/documents"
             className="rounded-sm border border-border p-1 text-muted-foreground hover:bg-accent"
             title="Back to admin app">
            <ArrowLeft className="h-3.5 w-3.5" />
          </a>
          <span className="text-sm font-semibold tracking-tightish">Chat</span>
          <span className="ml-auto text-2xs uppercase tracking-wider text-muted-foreground">RAG</span>
        </div>

        <div className="border-b border-border p-2">
          <Button size="sm" className="w-full" onClick={onNewChat} disabled={busy}>
            <MessageSquarePlus className="h-3.5 w-3.5" /> New chat
          </Button>
          <div className="relative mt-2">
            <Search className="pointer-events-none absolute left-2 top-1.5 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              className="h-7 pl-7 text-xs"
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") refresh(); }}
            />
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto">
          {sessions.length === 0 ? (
            <p className="px-3 py-4 text-xs text-muted-foreground">
              No chats yet. Click <strong>New chat</strong>.
            </p>
          ) : sessions.map((s) => (
            <SessionRow key={s.id} session={s}
                        active={s.id === activeId}
                        onSelect={() => { setActiveId(s.id); setHashSession(s.id); }}
                        onDelete={() => onDelete(s.id)} />
          ))}
        </nav>

        {health && !health.ok && (
          <div className="border-t border-warning/40 bg-warning/5 px-3 py-2 text-2xs text-warning">
            <div className="flex items-start gap-1.5">
              <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
              <div className="space-y-0.5">
                {health.messages.slice(0, 2).map((m, i) => <div key={i}>{m}</div>)}
              </div>
            </div>
          </div>
        )}
      </aside>

      <main className="h-screen min-w-0 overflow-hidden">
        {activeId ? (
          <ChatPage key={activeId} sessionId={activeId} onSessionChange={refresh} />
        ) : (
          <EmptyState onNew={onNewChat} />
        )}
      </main>
    </div>
  );
}

// ----------------- Session list row -----------------

function SessionRow({ session, active, onSelect, onDelete }: {
  session: ChatSession; active: boolean;
  onSelect: () => void; onDelete: () => void;
}) {
  return (
    <div className={cn(
      "group flex items-start gap-2 border-b border-border/60 px-3 py-2.5 text-left text-xs transition-colors",
      active ? "bg-accent" : "hover:bg-accent/50",
    )}>
      <button onClick={onSelect} className="min-w-0 flex-1 text-left">
        <div className="truncate text-sm font-medium text-foreground">{session.title}</div>
        <div className="mt-0.5 flex items-center gap-2 text-2xs text-muted-foreground">
          <span>{session.message_count} msg</span>
          <span>·</span>
          <span>{fmtRelative(session.last_message_at || session.updated_at)}</span>
        </div>
      </button>
      <button
        onClick={onDelete}
        className="invisible rounded-sm p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive group-hover:visible"
        title="Delete chat"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ----------------- Empty state -----------------

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <Sparkles className="h-8 w-8 text-muted-foreground" />
      <h2 className="text-base font-semibold">Ask the knowledge graph</h2>
      <p className="max-w-md text-sm text-muted-foreground">
        Pick a chat on the left or start a new one. Answers cite the source
        document chunks and the entities/relationships they came from.
      </p>
      <Button size="sm" onClick={onNew}>
        <MessageSquarePlus className="h-3.5 w-3.5" /> New chat
      </Button>
    </div>
  );
}

// ----------------- Chat page (one session) -----------------

const ADVANCED_STORAGE_KEY = "chat.advancedMode";

function ChatPage({ sessionId, onSessionChange }: { sessionId: string; onSessionChange: () => void }) {
  const [session, setSession] = useState<ChatSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [entityDrawerEid, setEntityDrawerEid] = useState<string | null>(null);
  const [chunkDrawerMid, setChunkDrawerMid] = useState<number | null>(null);
  const [docs, setDocs] = useState<DocumentRow[]>([]);
  const [advanced, setAdvanced] = useState<boolean>(() => {
    try { return localStorage.getItem(ADVANCED_STORAGE_KEY) === "1"; }
    catch { return false; }
  });
  const [traceMessageId, setTraceMessageId] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const setAdvancedPersist = (v: boolean) => {
    setAdvanced(v);
    try { localStorage.setItem(ADVANCED_STORAGE_KEY, v ? "1" : "0"); } catch {}
  };
  const lastAssistant = useMemo(
    () => [...messages].reverse().find((m) => m.role === "assistant" && m.id > 0) || null,
    [messages],
  );
  const traceMessage = useMemo(
    () => messages.find((m) => m.id === traceMessageId) || lastAssistant,
    [messages, traceMessageId, lastAssistant],
  );

  const load = useCallback(async () => {
    const s = await api.getChatSession(sessionId);
    setSession(s);
    setMessages(s.messages || []);
  }, [sessionId]);

  useEffect(() => { load().catch(console.error); }, [load]);
  useEffect(() => {
    api.listDocuments().then((r) => setDocs(r.items)).catch(() => {});
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, sending, streamingText]);

  const send = async () => {
    const q = input.trim();
    if (!q || sending || !session) return;
    setErr(null);
    setSending(true);
    setStreamingText("");
    const optimistic: ChatMessage = {
      id: -Date.now(), session_id: sessionId, role: "user", content: q,
      mode: null, model: null, prompt_tokens: null, completion_tokens: null,
      total_tokens: null, response_time_ms: null, llm_call_id: null,
      error: null, created_at: new Date().toISOString(),
      sources: [], entities: {} as any, nodedetails: {} as any,
      metric: {}, meta: {},
    };
    setMessages((m) => [...m, optimistic]);
    setInput("");
    try {
      await streamChat(sessionId, {
        question: q,
        mode: session.mode,
        document_names: session.document_names || [],
        onToken: (t) => setStreamingText((s) => s + t),
        onDone: async () => {
          setStreamingText("");
          await load();
          onSessionChange();
        },
        onError: (e) => {
          setErr(e);
          setMessages((m) => m.filter((x) => x.id !== optimistic.id));
          setInput(q);
        },
      });
    } catch (e: any) {
      setErr(e?.message || String(e));
      setMessages((m) => m.filter((x) => x.id !== optimistic.id));
      setInput(q);
    } finally { setSending(false); }
  };

  const onTitleBlur = async (e: React.FocusEvent<HTMLInputElement>) => {
    const t = e.target.value.trim();
    if (!session || !t || t === session.title) return;
    const updated = await api.patchChatSession(sessionId, { title: t } as any);
    setSession(updated);
    onSessionChange();
  };

  const onModeChange = async (mode: string) => {
    if (!session || mode === session.mode) return;
    const updated = await api.patchChatSession(sessionId, { mode } as any);
    setSession(updated);
  };

  const onDocsChange = async (names: string[]) => {
    if (!session) return;
    const updated = await api.patchChatSession(sessionId, { document_names: names } as any);
    setSession(updated);
  };

  if (!session) {
    return <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Loading…</div>;
  }

  return (
    <div className={cn(
      "grid h-full overflow-hidden",
      advanced ? "grid-cols-[minmax(0,1fr)_440px]" : "grid-cols-1",
    )}>
      {/* primary column: header + thread + composer */}
      <div className="grid h-full min-w-0 grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden">
        {/* header */}
        <header className="flex flex-wrap items-center gap-3 border-b border-border bg-card/30 px-5 py-3">
          <Input
            defaultValue={session.title}
            onBlur={onTitleBlur}
            className="h-7 max-w-md border-transparent bg-transparent px-1 text-sm font-semibold hover:border-border"
          />
          <ModeSelect value={session.mode} onChange={onModeChange} />
          {MODE_DOC_FILTER.has(session.mode) && (
            <DocFilterChip
              docs={docs}
              selected={session.document_names || []}
              onChange={onDocsChange}
            />
          )}
          {session.model && (
            <Badge variant="secondary" className="font-mono text-2xs">
              {session.model}
            </Badge>
          )}
          <div className="ml-auto flex items-center gap-3">
            <span className="text-2xs text-muted-foreground">
              {session.message_count} msg
            </span>
            <button
              type="button"
              onClick={() => setAdvancedPersist(!advanced)}
              className={cn(
                "flex h-7 items-center gap-1.5 rounded-sm border px-2 text-2xs transition-colors",
                advanced
                  ? "border-primary/50 bg-primary/10 text-foreground"
                  : "border-border bg-background text-muted-foreground hover:bg-accent",
              )}
              title="Toggle retrieval inspector"
            >
              {advanced ? <PanelRightClose className="h-3 w-3" /> : <PanelRightOpen className="h-3 w-3" />}
              advanced
            </button>
          </div>
        </header>

        {/* thread */}
        <div ref={scrollRef} className="overflow-y-auto px-5 py-6">
          <div className="mx-auto max-w-3xl space-y-6">
            {messages.length === 0 && (
              <div className="rounded-md border border-dashed border-border/60 bg-card/20 px-5 py-10 text-center">
                <Sparkles className="mx-auto mb-2 h-5 w-5 text-muted-foreground" />
                <p className="text-sm text-muted-foreground">
                  Ask the first question — citations and entities will appear
                  inline; toggle <strong className="text-foreground">advanced</strong> to
                  inspect retrieval.
                </p>
              </div>
            )}
            {messages.map((m) => (
              <MessageBubble
                key={m.id}
                m={m}
                advanced={advanced}
                onEntityClick={setEntityDrawerEid}
                onSourceClick={m.role === "assistant" && m.id > 0
                  ? () => setChunkDrawerMid(m.id) : undefined}
                onInspectTrace={m.role === "assistant" && m.id > 0
                  ? () => { setAdvancedPersist(true); setTraceMessageId(m.id); }
                  : undefined}
                isTraceTarget={advanced && traceMessage?.id === m.id}
              />
            ))}
            {sending && streamingText && (
              <StreamingBubble text={streamingText} />
            )}
            {sending && !streamingText && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                retrieving…
              </div>
            )}
            {err && (
              <div className="rounded-sm border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                {err}
              </div>
            )}
          </div>
        </div>

        {/* input */}
        <div className="border-t border-border bg-card/30 p-3">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
              }}
              placeholder="Ask something… (Enter to send · Shift+Enter for newline)"
              className="min-h-[44px] max-h-40 resize-none"
              disabled={sending}
            />
            <Button onClick={send} disabled={!input.trim() || sending} className="shrink-0">
              {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </div>
          <div className="mx-auto mt-1.5 flex max-w-3xl items-center justify-between text-2xs text-muted-foreground">
            <span>mode · {MODE_LABELS[session.mode] || session.mode}</span>
            <span className="flex items-center gap-1.5">
              {session.embedding_model && (
                <span className="font-mono">embed: {session.embedding_model.split("/").pop()}</span>
              )}
            </span>
          </div>
        </div>
      </div>

      {advanced && (
        <TracePanel
          message={traceMessage}
          onClose={() => setAdvancedPersist(false)}
        />
      )}

      {entityDrawerEid && (
        <EntityDrawer
          elementId={entityDrawerEid}
          onClose={() => setEntityDrawerEid(null)}
        />
      )}
      {chunkDrawerMid != null && (
        <CitationDrawer
          messageId={chunkDrawerMid}
          onClose={() => setChunkDrawerMid(null)}
        />
      )}
    </div>
  );
}

// ----------------- Single message bubble -----------------

function MessageBubble({
  m, advanced, onEntityClick, onSourceClick, onInspectTrace, isTraceTarget,
}: {
  m: ChatMessage;
  advanced: boolean;
  onEntityClick: (eid: string) => void;
  onSourceClick?: () => void;
  onInspectTrace?: () => void;
  isTraceTarget?: boolean;
}) {
  const isUser = m.role === "user";
  const ent = (m.entities as any) || {};
  const sources = m.sources || [];
  const trace = ((m.metric as any)?.trace || null) as ChatTrace | null;
  const retrieved = trace?.retrieved_docs || [];
  const [showGraph, setShowGraph] = useState(false);

  // citation index → preview text (for hover tooltip)
  const previewByIndex = useMemo(() => {
    const map = new Map<number, RetrievedDoc>();
    retrieved.forEach((d, i) => map.set(i + 1, d));
    return map;
  }, [retrieved]);

  const graphData = useMemo(() => buildGraphExplore(ent), [ent]);

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-md border border-primary/30 bg-primary/10 px-4 py-2.5 text-sm">
          <div className="whitespace-pre-wrap break-words text-foreground">
            {m.content || "—"}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={cn(
      "rounded-md border bg-card/40 px-4 py-3 text-sm transition-colors",
      isTraceTarget ? "border-primary/40 ring-1 ring-primary/20" : "border-border/60",
    )}>
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs uppercase tracking-wider text-muted-foreground">
        <span className="font-medium text-foreground/80">assistant</span>
        {m.model && <span className="font-mono">· {m.model}</span>}
        {m.mode && <span>· {m.mode}</span>}
        {m.response_time_ms != null && (
          <span>· {Math.round(m.response_time_ms / 100) / 10}s</span>
        )}
        {m.total_tokens != null && <span>· {m.total_tokens} tok</span>}
        <div className="ml-auto flex items-center gap-1.5 normal-case tracking-normal">
          {onInspectTrace && trace && (
            <button
              type="button"
              onClick={onInspectTrace}
              className="rounded-sm border border-border bg-background px-1.5 py-0.5 text-2xs text-foreground hover:bg-accent"
              title="Inspect retrieval for this message"
            >
              <Activity className="mr-1 inline h-3 w-3" /> trace
            </button>
          )}
          {(ent.nodes?.length || 0) > 0 && (
            <button
              type="button"
              onClick={() => setShowGraph((v) => !v)}
              className={cn(
                "rounded-sm border px-1.5 py-0.5 text-2xs",
                showGraph
                  ? "border-primary/40 bg-primary/10"
                  : "border-border bg-background hover:bg-accent",
              )}
            >
              <Network className="mr-1 inline h-3 w-3" /> graph
            </button>
          )}
        </div>
      </div>

      {m.error ? (
        <div className="rounded-sm border border-destructive/40 bg-destructive/5 px-2 py-1 text-xs text-destructive">
          {m.error}
        </div>
      ) : (
        <div className="prose prose-sm prose-invert max-w-none [&_p]:my-1 [&_pre]:my-2 [&_ul]:my-1 [&_ol]:my-1 [&_table]:my-2 [&_li]:my-0.5">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={withCitationRendering(previewByIndex)}
          >
            {m.content || "—"}
          </ReactMarkdown>
        </div>
      )}

      {showGraph && graphData.nodes.length > 0 && (
        <div className="mt-3 h-[320px] overflow-hidden rounded-sm border border-border/60 bg-background/40">
          <GraphViewer data={graphData} />
        </div>
      )}

      {(sources.length > 0 || (ent.entityids?.length || 0) > 0 || advanced) && (
        <div className="mt-3 space-y-2 border-t border-border/60 pt-2">
          {sources.length > 0 && (
            <CitationRow label="Sources" icon={<FileText className="h-3 w-3" />}>
              {sources.map((s) => (
                <button
                  key={s.source_name}
                  onClick={onSourceClick}
                  className="rounded-sm border border-border bg-background px-1.5 py-0.5 text-2xs font-mono hover:bg-accent"
                  title="Show chunk text"
                >
                  {s.source_name} · {s.chunk_ids.length}
                </button>
              ))}
            </CitationRow>
          )}
          {ent.nodes && ent.nodes.length > 0 && (
            <CitationRow label="Entities" icon={<Network className="h-3 w-3" />}>
              {ent.nodes.slice(0, 30).map((n: any) => (
                <button
                  key={n.elementId}
                  onClick={() => onEntityClick(n.elementId)}
                  className="rounded-sm border border-border bg-background px-1.5 py-0.5 text-2xs hover:bg-accent"
                  title={n.description || n.id}
                >
                  {n.id} <span className="text-muted-foreground">[{(n.labels || []).join(",")}]</span>
                </button>
              ))}
              {ent.nodes.length > 30 && (
                <span className="text-2xs text-muted-foreground">+{ent.nodes.length - 30} more</span>
              )}
            </CitationRow>
          )}
        </div>
      )}
    </div>
  );
}

// ----------------- Citation rendering inside markdown ----------------

/** Build a ReactMarkdown `components` map that replaces `[N]` occurrences
 *  in inline text with hover-preview citation chips. */
function withCitationRendering(previews: Map<number, RetrievedDoc>): any {
  const transform = (children: any): any => {
    return React.Children.map(children, (child: any, idx: number) => {
      if (typeof child === "string") {
        const parts = child.split(/(\[\d+\])/g);
        if (parts.length === 1) return child;
        return parts.map((p, i) => {
          const m = /^\[(\d+)\]$/.exec(p);
          if (!m) return p;
          const n = parseInt(m[1], 10);
          return <CitationChip key={`${idx}-${i}`} n={n} doc={previews.get(n) || null} />;
        });
      }
      return child;
    });
  };
  const wrap = (Tag: any) => ({ children, ...rest }: any) => (
    <Tag {...rest}>{transform(children)}</Tag>
  );
  return {
    p: wrap("p"), li: wrap("li"), strong: wrap("strong"),
    em: wrap("em"), td: wrap("td"), th: wrap("th"),
  };
}

function CitationChip({ n, doc }: { n: number; doc: RetrievedDoc | null }) {
  const [open, setOpen] = useState(false);
  return (
    <span
      className="relative inline-block"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="mx-0.5 inline-flex h-4 min-w-[1.1rem] items-center justify-center rounded-sm border border-primary/40 bg-primary/10 px-1 text-[10px] font-mono align-middle text-primary hover:bg-primary/20"
      >
        {n}
      </button>
      {open && doc && (
        <span className="absolute bottom-full left-1/2 z-50 mb-1.5 w-[360px] -translate-x-1/2 rounded-md border border-border bg-card p-2.5 text-left text-xs shadow-lg">
          <span className="mb-1 flex items-center justify-between gap-2 text-2xs text-muted-foreground">
            <span className="font-mono">
              {doc.fileName || doc.entityId || doc.communityId || "context"}
              {doc.chunkId && <> · chunk:{doc.chunkId.slice(0, 8)}</>}
            </span>
            {doc.score != null && <span>score {doc.score.toFixed(3)}</span>}
          </span>
          <span className="block whitespace-pre-wrap leading-snug text-foreground/90">
            {doc.preview || "(no preview)"}
          </span>
        </span>
      )}
    </span>
  );
}

function CitationRow({ label, icon, children }: {
  label: string; icon?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-2">
      <div className="flex shrink-0 items-center gap-1 text-2xs uppercase tracking-wider text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="flex flex-wrap gap-1">{children}</div>
    </div>
  );
}

function buildGraphExplore(ent: any) {
  const nodes = ((ent?.nodes) || []).map((n: any) => ({
    element_id: n.elementId,
    id: n.id,
    labels: n.labels || [],
    description: n.description || null,
    properties: {},
    sources: [],
  }));
  const relationships = ((ent?.relationships) || []).map((r: any) => ({
    element_id: r.elementId || `${r.startId}-${r.type}-${r.endId}`,
    source: r.startId,
    target: r.endId,
    type: r.type,
    properties: {},
  }));
  return { nodes, relationships };
}

// ----------------- Trace panel (retrieval inspector) ------------------

type TraceTab = "trace" | "graph" | "context" | "raw";

function TracePanel({
  message, onClose,
}: {
  message: ChatMessage | null;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<TraceTab>("trace");
  const trace = ((message?.metric as any)?.trace || null) as ChatTrace | null;
  const ent = (message?.entities as any) || {};

  const graphData = useMemo(() => buildGraphExplore(ent), [ent]);

  return (
    <aside className="flex h-full flex-col overflow-hidden border-l border-border bg-card/40">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <Activity className="h-4 w-4 text-primary" />
        <span className="text-sm font-semibold">Retrieval inspector</span>
        <button
          onClick={onClose}
          className="ml-auto rounded-sm p-1 text-muted-foreground hover:bg-accent"
          title="Close inspector"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </header>

      <div className="flex shrink-0 items-center border-b border-border bg-background/30 text-2xs">
        {(["trace", "graph", "context", "raw"] as TraceTab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "flex-1 px-3 py-2 uppercase tracking-wider transition-colors",
              tab === t
                ? "border-b-2 border-primary text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {!message || !trace ? (
        <div className="flex flex-1 items-center justify-center px-4 text-center text-xs text-muted-foreground">
          {message ? "No trace recorded for this message." : "Send a question to inspect retrieval."}
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          {tab === "trace" && <TraceTabBody trace={trace} />}
          {tab === "graph" && (
            <div className="h-full p-2">
              {graphData.nodes.length === 0 ? (
                <p className="px-2 py-4 text-xs text-muted-foreground">
                  No graph entities returned for this message.
                </p>
              ) : (
                <div className="h-full overflow-hidden rounded-sm border border-border/60">
                  <GraphViewer data={graphData} />
                </div>
              )}
            </div>
          )}
          {tab === "context" && (
            <ContextTabBody trace={trace} />
          )}
          {tab === "raw" && (
            <RawTabBody trace={trace} />
          )}
        </div>
      )}
    </aside>
  );
}

function TraceTabBody({ trace }: { trace: ChatTrace }) {
  const stages = trace.stages_ms || {};
  const total = stages.total || 1;
  const bars: { key: string; label: string; ms: number; color: string }[] = [
    { key: "rewrite",  label: "rewrite",  ms: stages.rewrite ?? 0,  color: "bg-blue-500/60" },
    { key: "retrieve", label: "retrieve", ms: stages.retrieve ?? 0, color: "bg-emerald-500/60" },
    { key: "format",   label: "format",   ms: stages.format ?? 0,   color: "bg-amber-500/60" },
    { key: "answer",   label: "answer",   ms: stages.answer ?? 0,   color: "bg-primary/70" },
  ].filter((b) => b.ms > 0);

  return (
    <div className="space-y-4 p-3 text-xs">
      <Section title="Query">
        <div className="rounded-sm border border-border/60 bg-background/40 px-2 py-1.5 font-mono leading-snug text-foreground">
          {trace.search_query || "(no rewrite)"}
        </div>
      </Section>

      <Section title="Pipeline" right={`${total} ms total`}>
        <div className="space-y-1.5">
          {bars.map((b) => (
            <div key={b.key} className="flex items-center gap-2">
              <span className="w-16 text-muted-foreground">{b.label}</span>
              <div className="relative h-2 flex-1 overflow-hidden rounded-sm bg-muted/40">
                <div className={cn("absolute inset-y-0 left-0 rounded-sm", b.color)}
                     style={{ width: `${Math.max(2, Math.min(100, (b.ms / total) * 100))}%` }} />
              </div>
              <span className="w-12 text-right font-mono">{b.ms}ms</span>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Configuration">
        <dl className="grid grid-cols-[120px_1fr] gap-y-1 font-mono text-2xs">
          <KV k="mode" v={trace.mode} />
          {trace.k != null && <KV k="top_k" v={String(trace.k)} />}
          {trace.embedding_model && <KV k="embed" v={trace.embedding_model} />}
          {trace.embedding_provider && <KV k="provider" v={trace.embedding_provider} />}
          {trace.compression_enabled != null && (
            <KV k="compression" v={trace.compression_enabled ? "on" : "off"} />
          )}
          {trace.compression_threshold != null && (
            <KV k="threshold" v={trace.compression_threshold.toFixed(2)} />
          )}
          {trace.doc_split_size != null && (
            <KV k="split_size" v={String(trace.doc_split_size)} />
          )}
        </dl>
      </Section>

      <Section
        title="Retrieved documents"
        right={`${trace.retrieved_count ?? trace.retrieved_docs?.length ?? 0}`}
      >
        <ul className="space-y-1.5">
          {(trace.retrieved_docs || []).map((d) => {
            const cited = (trace.cited_indices || []).includes(d.index);
            return (
              <li key={d.index}
                  className={cn(
                    "rounded-sm border px-2 py-1.5",
                    cited ? "border-primary/40 bg-primary/5" : "border-border/60 bg-background/40",
                  )}>
                <div className="flex items-center gap-2 text-2xs text-muted-foreground">
                  <span className={cn(
                    "inline-flex h-4 min-w-[1.1rem] items-center justify-center rounded-sm border font-mono",
                    cited ? "border-primary/50 bg-primary/10 text-primary" : "border-border bg-background",
                  )}>{d.index}</span>
                  <span className="truncate font-mono">
                    {d.fileName || d.entityId || d.communityId || "context"}
                  </span>
                  {d.chunkId && <span className="font-mono">· {d.chunkId.slice(0, 8)}</span>}
                  {d.score != null && (
                    <span className="ml-auto font-mono">{d.score.toFixed(3)}</span>
                  )}
                </div>
                <div className="mt-1 line-clamp-3 leading-snug text-foreground/85">
                  {d.preview}
                </div>
              </li>
            );
          })}
          {(!trace.retrieved_docs || trace.retrieved_docs.length === 0) && (
            <li className="rounded-sm border border-border/60 bg-background/40 px-2 py-2 text-2xs text-muted-foreground">
              No documents retrieved.
            </li>
          )}
        </ul>
      </Section>

      {trace.cypher && (
        <Section title="Cypher">
          <pre className="overflow-x-auto rounded-sm border border-border/60 bg-background/40 p-2 font-mono text-2xs leading-snug">
            {trace.cypher}
          </pre>
        </Section>
      )}
    </div>
  );
}

function ContextTabBody({ trace }: { trace: ChatTrace }) {
  const docs = trace.retrieved_docs || [];
  if (docs.length === 0) {
    return <p className="p-3 text-xs text-muted-foreground">No context retrieved.</p>;
  }
  return (
    <div className="space-y-2 p-3 text-xs">
      {docs.map((d) => (
        <details key={d.index} className="rounded-sm border border-border/60 bg-background/40 px-2 py-1.5">
          <summary className="flex cursor-pointer items-center gap-2 text-2xs text-muted-foreground">
            <span className="inline-flex h-4 min-w-[1.1rem] items-center justify-center rounded-sm border border-border bg-background font-mono">
              {d.index}
            </span>
            <span className="truncate font-mono">
              {d.fileName || d.entityId || d.communityId || "context"}
            </span>
            {d.score != null && <span className="ml-auto font-mono">{d.score.toFixed(3)}</span>}
          </summary>
          <div className="mt-1.5 whitespace-pre-wrap leading-snug text-foreground/90">
            {d.text || d.preview}
          </div>
        </details>
      ))}
    </div>
  );
}

function RawTabBody({ trace }: { trace: ChatTrace }) {
  const text = trace.context_text || "";
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-border/60 px-3 py-1.5 text-2xs text-muted-foreground">
        <span>Raw context passed to LLM ({text.length} chars)</span>
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(text).catch(() => {})}
          className="rounded-sm border border-border bg-background px-1.5 py-0.5 hover:bg-accent"
        >
          <Copy className="mr-1 inline h-3 w-3" /> copy
        </button>
      </div>
      <pre className="flex-1 overflow-auto whitespace-pre-wrap p-3 font-mono text-2xs leading-snug">
        {text || "(empty)"}
      </pre>
    </div>
  );
}

function Section({ title, right, children }: {
  title: string; right?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-1.5 flex items-center justify-between text-2xs uppercase tracking-wider text-muted-foreground">
        <span>{title}</span>
        {right && <span className="normal-case tracking-normal">{right}</span>}
      </div>
      {children}
    </section>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="truncate text-foreground">{v}</dd>
    </>
  );
}

// ----------------- Entity drawer (1-hop neighbourhood) -----------------

function EntityDrawer({ elementId, onClose }: { elementId: string; onClose: () => void }) {
  const [data, setData] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.graphNeighborhood({ element_id: elementId, depth: 1, limit: 100 })
      .then((r) => { if (alive) setData(r); })
      .catch(console.error)
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [elementId]);

  return (
    <div className="fixed inset-0 z-40 flex items-stretch justify-end bg-black/30" onClick={onClose}>
      <div
        className="flex h-full w-[420px] flex-col overflow-hidden border-l border-border bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-2">
          <div className="text-sm font-semibold">Entity neighbourhood</div>
          <button onClick={onClose}
                  className="rounded-sm p-1 text-muted-foreground hover:bg-accent">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-3 text-xs">
          {loading ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> loading…
            </div>
          ) : !data ? (
            <p className="text-muted-foreground">No data.</p>
          ) : (
            <>
              <h3 className="mb-1 text-2xs uppercase tracking-wider text-muted-foreground">
                Nodes ({data.nodes?.length || 0})
              </h3>
              <ul className="space-y-1">
                {(data.nodes || []).map((n: any) => (
                  <li key={n.element_id} className="rounded-sm border border-border/60 bg-background/40 px-2 py-1">
                    <div className="font-mono">{n.id}</div>
                    <div className="text-2xs text-muted-foreground">
                      [{(n.labels || []).join(", ") || "—"}]
                      {n.description && <> · {String(n.description).slice(0, 80)}</>}
                    </div>
                  </li>
                ))}
              </ul>

              <h3 className="mb-1 mt-4 text-2xs uppercase tracking-wider text-muted-foreground">
                Relationships ({data.relationships?.length || 0})
              </h3>
              <ul className="space-y-1">
                {(data.relationships || []).map((r: any) => (
                  <li key={r.element_id} className="rounded-sm border border-border/60 bg-background/40 px-2 py-1 font-mono text-2xs">
                    {r.source.slice(-8)} —[{r.type}]→ {r.target.slice(-8)}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ----------------- helpers -----------------

// ----------------- streaming bubble (live tokens) -----------------

function StreamingBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] rounded-md border border-border bg-card px-4 py-3 text-sm">
        <div className="mb-1 flex items-center gap-2 text-2xs uppercase tracking-wider text-muted-foreground">
          <span>assistant</span>
          <Loader2 className="h-3 w-3 animate-spin" />
          <span>streaming…</span>
        </div>
        <div className="prose prose-sm prose-invert max-w-none [&_p]:my-1">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

// ----------------- document filter chip (multi-select) -----------------

function DocFilterChip({ docs, selected, onChange }: {
  docs: DocumentRow[]; selected: string[];
  onChange: (names: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const sel = new Set(selected);
  return (
    <div className="relative">
      <button
        className="h-7 rounded-sm border border-border bg-background px-2 text-2xs hover:bg-accent"
        onClick={() => setOpen((o) => !o)}
      >
        scope: {selected.length || "all"} doc{selected.length === 1 ? "" : "s"}
      </button>
      {open && (
        <div className="absolute left-0 top-8 z-30 max-h-72 w-72 overflow-y-auto rounded-sm border border-border bg-card p-2 shadow-lg">
          <div className="mb-2 flex justify-between text-2xs">
            <button onClick={() => onChange([])}
                    className="text-muted-foreground hover:text-foreground">clear all</button>
            <button onClick={() => onChange(docs.map((d) => d.file_name))}
                    className="text-muted-foreground hover:text-foreground">select all</button>
          </div>
          {docs.length === 0
            ? <p className="px-1 py-2 text-2xs text-muted-foreground">No documents.</p>
            : docs.map((d) => (
              <label key={d.id} className="flex cursor-pointer items-center gap-2 rounded-sm px-1 py-1 text-xs hover:bg-accent">
                <input
                  type="checkbox"
                  checked={sel.has(d.file_name)}
                  onChange={(e) => {
                    const next = new Set(sel);
                    if (e.target.checked) next.add(d.file_name);
                    else next.delete(d.file_name);
                    onChange([...next]);
                  }}
                />
                <span className="truncate">{d.file_name}</span>
              </label>
            ))}
        </div>
      )}
    </div>
  );
}

// ----------------- citation drawer (chunks + entities + communities) ---

function CitationDrawer({ messageId, onClose }:
                        { messageId: number; onClose: () => void }) {
  const [data, setData] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.expandMessage(messageId)
      .then((r) => { if (alive) setData(r); })
      .catch(console.error)
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [messageId]);
  return (
    <div className="fixed inset-0 z-40 flex items-stretch justify-end bg-black/30" onClick={onClose}>
      <div className="flex h-full w-[480px] flex-col overflow-hidden border-l border-border bg-card shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-4 py-2">
          <div className="text-sm font-semibold">Citations</div>
          <button onClick={onClose}
                  className="rounded-sm p-1 text-muted-foreground hover:bg-accent">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-3 text-xs">
          {loading ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> loading…
            </div>
          ) : !data ? (
            <p className="text-muted-foreground">No data.</p>
          ) : (
            <>
              {data.chunks?.length > 0 && (
                <>
                  <h3 className="mb-1 text-2xs uppercase tracking-wider text-muted-foreground">
                    Chunks ({data.chunks.length})
                  </h3>
                  <ul className="space-y-2">
                    {data.chunks.map((c: any) => (
                      <li key={c.id} className="rounded-sm border border-border/60 bg-background/40 px-2 py-2">
                        <div className="flex items-center gap-2 text-2xs text-muted-foreground">
                          <span className="font-mono">{c.fileName}</span>
                          {c.position != null && <span>pos {c.position}</span>}
                          {c.score != null && <span>score {Number(c.score).toFixed(3)}</span>}
                        </div>
                        <div className="mt-1 whitespace-pre-wrap text-foreground/90">{c.text}</div>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {data.entities?.length > 0 && (
                <>
                  <h3 className="mb-1 mt-4 text-2xs uppercase tracking-wider text-muted-foreground">
                    Entities ({data.entities.length})
                  </h3>
                  <ul className="space-y-2">
                    {data.entities.map((e: any) => (
                      <li key={e.element_id} className="rounded-sm border border-border/60 bg-background/40 px-2 py-1.5">
                        <div className="font-mono">{e.id} <span className="text-muted-foreground">[{(e.labels || []).join(",")}]</span></div>
                        {e.description && <div className="mt-0.5 text-muted-foreground">{e.description}</div>}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {data.communities?.length > 0 && (
                <>
                  <h3 className="mb-1 mt-4 text-2xs uppercase tracking-wider text-muted-foreground">
                    Communities ({data.communities.length})
                  </h3>
                  <ul className="space-y-2">
                    {data.communities.map((c: any) => (
                      <li key={c.id} className="rounded-sm border border-border/60 bg-background/40 px-2 py-1.5">
                        <div className="font-mono text-foreground">{c.title || c.id}
                          {c.level != null && <span className="ml-1 text-muted-foreground">· L{c.level}</span>}
                        </div>
                        {c.summary && <div className="mt-0.5 text-muted-foreground">{c.summary}</div>}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ----------------- SSE streaming client (no EventSource: POST + body) --

async function streamChat(sessionId: string, args: {
  question: string; mode: string; document_names: string[];
  onToken: (t: string) => void;
  onDone: (payload: ChatAskResponse) => void;
  onError: (msg: string) => void;
}): Promise<void> {
  const r = await fetch(`/api/chat/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question: args.question,
      mode: args.mode,
      document_names: args.document_names,
    }),
  });
  if (!r.ok || !r.body) {
    args.onError(`stream failed: ${r.status} ${r.statusText}`);
    return;
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    // SSE frames separated by blank line
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let evt = "message"; let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) evt = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      try {
        const parsed = data ? JSON.parse(data) : {};
        if (evt === "token") args.onToken(parsed.t || "");
        else if (evt === "done") args.onDone(parsed);
        else if (evt === "error") args.onError(parsed.error || "stream error");
      } catch (e) {
        console.error("SSE parse", e, data);
      }
    }
  }
}

function fmtRelative(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    const s = (Date.now() - d.getTime()) / 1000;
    if (s < 60) return `${Math.round(s)}s ago`;
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return d.toLocaleDateString();
  } catch { return iso; }
}
