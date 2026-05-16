import { useEffect, useState } from "react";
import { Activity, Database, FileText, GitBranch, Play, ScrollText, Settings2, FileCode2, ListChecks, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, AppConfig } from "@/lib/api";
import { guard } from "@/lib/unsavedGuard";

export type Route =
  | "documents"
  | "schema"
  | "ingest"
  | "graph"
  | "prompts"
  | "jobs"
  | "llm-calls"
  | "settings";

const NAV: { key: Route; label: string; icon: any; hint: string }[] = [
  { key: "documents",  label: "Documents",  icon: FileText,   hint: "Markdown registry" },
  { key: "schema",     label: "Schema",     icon: GitBranch,  hint: "Nodes & relationships" },
  { key: "ingest",     label: "Ingest",     icon: Play,       hint: "Run extraction" },
  { key: "graph",      label: "Graph",      icon: Database,   hint: "Viewer & stats" },
  { key: "prompts",    label: "Prompts",    icon: FileCode2,  hint: "System templates" },
  { key: "jobs",       label: "Jobs",       icon: ListChecks, hint: "Run history & logs" },
  { key: "llm-calls",  label: "LLM Calls",  icon: ScrollText, hint: "Audit log" },
  { key: "settings",   label: "Settings",   icon: Settings2,  hint: "LLM & Neo4j config" },
];

function routeFromHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, "").split(/[?#]/)[0];
  return (NAV.find((n) => n.key === h)?.key ?? "documents") as Route;
}

export function AppShell({ children, config, route, onRouteChange }: {
  children: React.ReactNode;
  config: AppConfig | null;
  route: Route;
  onRouteChange: (r: Route) => void;
}) {
  // Fixed sidebar + independently scrollable content. The shell takes the
  // full viewport height; only <main> scrolls. Prevents the sidebar from
  // disappearing on long pages (Documents, Graph, LLM Calls).
  return (
    <div className="grid h-screen grid-cols-[220px_minmax(0,1fr)] overflow-hidden bg-background">
      <aside className="flex h-screen flex-col overflow-hidden border-r border-border bg-card/40">
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
          <Activity className="h-4 w-4" />
          <span className="text-sm font-semibold tracking-tightish">text2graph</span>
          {config?.domain && (
            <span className="ml-auto text-2xs uppercase tracking-wider text-muted-foreground">
              {config.domain}
            </span>
          )}
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-2">
          <div className="flex flex-col gap-0.5">
            <a
              href="#/chat"
              className="group flex items-center gap-2 rounded-sm px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
            >
              <MessageSquare className="h-4 w-4 text-muted-foreground/80" />
              <span className="flex-1 text-left">Chat</span>
              <span className="text-2xs uppercase tracking-wider text-muted-foreground">/chat</span>
            </a>
            <div className="my-1 border-t border-border/60" />
            {NAV.map((item) => {
              const Icon = item.icon;
              const active = item.key === route;
              return (
                <button
                  key={item.key}
                  onClick={() => guard.tryNavigate(() => onRouteChange(item.key))}
                  className={cn(
                    "group flex items-center gap-2 rounded-sm px-2 py-1.5 text-sm transition-colors",
                    active
                      ? "bg-accent text-foreground"
                      : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                  )}
                >
                  <Icon className={cn("h-4 w-4", active ? "text-foreground" : "text-muted-foreground/80")} />
                  <span className="flex-1 text-left">{item.label}</span>
                </button>
              );
            })}
          </div>
        </nav>
      </aside>
      <main className="h-screen min-w-0 overflow-hidden">
        {children}
      </main>
    </div>
  );
}

// hash-routing hook
export function useHashRoute(): [Route, (r: Route) => void] {
  const [r, setR] = useState<Route>(routeFromHash());
  useEffect(() => {
    let last = window.location.hash;
    const f = () => {
      const next = routeFromHash();
      if (guard.isDirty()) {
        // user used browser back/forward — revert until they confirm
        const target = next;
        window.history.replaceState(null, "", last);
        guard.tryNavigate(() => {
          window.location.hash = `#/${target}`;
          setR(target);
        });
        return;
      }
      last = window.location.hash;
      setR(next);
    };
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  const set = (next: Route) => {
    window.location.hash = `#/${next}`;
    setR(next);
  };
  return [r, set];
}

export function useAppConfig() {
  const [c, setC] = useState<AppConfig | null>(null);
  useEffect(() => { api.config().then(setC).catch(console.error); }, []);
  return c;
}
