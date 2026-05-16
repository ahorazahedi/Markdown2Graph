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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowLeft, MessageSquarePlus, Send, Loader2, Trash2, Search,
  ChevronRight, AlertCircle, Sparkles, Network,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  api, ChatAskResponse, ChatMessage, ChatSession, DocumentRow,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { confirm } from "@/lib/confirm";

const MODE_LABELS: Record<string, string> = {
  vector: "vector",
  fulltext: "hybrid (fulltext)",
  graph_vector: "graph + vector",
  graph_vector_fulltext: "graph + hybrid (default)",
  entity_vector: "entity-centric",
  global_vector: "community / global",
  graph: "text → Cypher",
};
const MODE_DOC_FILTER = new Set(["vector", "graph_vector"]);

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
  const scrollRef = useRef<HTMLDivElement>(null);

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
    <div className="grid h-full grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden">
      {/* header */}
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-card/30 px-5 py-3">
        <Input
          defaultValue={session.title}
          onBlur={onTitleBlur}
          className="h-7 max-w-md border-transparent bg-transparent px-1 text-sm font-semibold hover:border-border"
        />
        <select
          value={session.mode}
          onChange={(e) => onModeChange(e.target.value)}
          className="h-7 rounded-sm border border-border bg-background px-2 text-2xs"
          title="Retrieval mode"
        >
          {Object.keys(MODE_LABELS).map((m) => (
            <option key={m} value={m}>{MODE_LABELS[m]}</option>
          ))}
        </select>
        {MODE_DOC_FILTER.has(session.mode) && (
          <DocFilterChip
            docs={docs}
            selected={session.document_names || []}
            onChange={onDocsChange}
          />
        )}
        {session.model && <Badge variant="secondary" className="font-mono text-2xs">{session.model}</Badge>}
        <span className="ml-auto text-2xs text-muted-foreground">{session.message_count} messages</span>
      </header>

      {/* thread */}
      <div ref={scrollRef} className="overflow-y-auto px-5 py-6">
        <div className="mx-auto max-w-3xl space-y-6">
          {messages.length === 0 && (
            <p className="text-center text-sm text-muted-foreground">
              Ask the first question.
            </p>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} m={m}
                           onEntityClick={setEntityDrawerEid}
                           onSourceClick={() => setChunkDrawerMid(m.id)} />
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
        <div className="mx-auto flex max-w-3xl gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
            }}
            placeholder="Ask something… (Enter to send, Shift+Enter for newline)"
            className="min-h-[44px] max-h-40 resize-none"
            disabled={sending}
          />
          <Button onClick={send} disabled={!input.trim() || sending}>
            {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>

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

function MessageBubble({ m, onEntityClick, onSourceClick }: {
  m: ChatMessage;
  onEntityClick: (eid: string) => void;
  onSourceClick?: () => void;
}) {
  const isUser = m.role === "user";
  const ent = (m.entities as any) || {};
  const sources = m.sources || [];

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div className={cn(
        "max-w-[85%] rounded-md border px-4 py-3 text-sm",
        isUser
          ? "border-primary/30 bg-primary/5"
          : "border-border bg-card",
      )}>
        <div className="mb-1 flex items-center gap-2 text-2xs uppercase tracking-wider text-muted-foreground">
          <span>{isUser ? "you" : "assistant"}</span>
          {m.model && <span className="font-mono">· {m.model}</span>}
          {m.response_time_ms != null && <span>· {Math.round(m.response_time_ms / 100) / 10}s</span>}
          {m.total_tokens != null && <span>· {m.total_tokens} tok</span>}
        </div>

        {m.error ? (
          <div className="rounded-sm border border-destructive/40 bg-destructive/5 px-2 py-1 text-xs text-destructive">
            {m.error}
          </div>
        ) : (
          <div className="prose prose-sm prose-invert max-w-none [&_p]:my-1 [&_pre]:my-2 [&_ul]:my-1 [&_table]:my-2">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content || "—"}</ReactMarkdown>
          </div>
        )}

        {(sources.length > 0 || (ent.entityids?.length || 0) > 0) && (
          <div className="mt-3 space-y-2 border-t border-border/60 pt-2">
            {sources.length > 0 && (
              <CitationRow label="Sources">
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
              </CitationRow>
            )}
          </div>
        )}
      </div>
    </div>
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
