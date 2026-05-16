import { useEffect, useState } from "react";
import { Activity, Database, FileText, GitBranch, Play, ScrollText, Settings2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, AppConfig } from "@/lib/api";

export type Route =
  | "setup"
  | "schema"
  | "documents"
  | "ingest"
  | "graph"
  | "llm-calls";

const NAV: { key: Route; label: string; icon: any; hint: string }[] = [
  { key: "setup",      label: "Setup",      icon: Settings2,  hint: "Connection" },
  { key: "schema",     label: "Schema",     icon: GitBranch,  hint: "Nodes & relationships" },
  { key: "documents",  label: "Documents",  icon: FileText,   hint: "Markdown registry" },
  { key: "ingest",     label: "Ingest",     icon: Play,       hint: "Run extraction" },
  { key: "graph",      label: "Graph",      icon: Database,   hint: "Stats & cleanup" },
  { key: "llm-calls",  label: "LLM Calls",  icon: ScrollText, hint: "Audit log" },
];

function routeFromHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, "");
  return (NAV.find((n) => n.key === h)?.key ?? "setup") as Route;
}

export function AppShell({ children, config, route, onRouteChange }: {
  children: React.ReactNode;
  config: AppConfig | null;
  route: Route;
  onRouteChange: (r: Route) => void;
}) {
  return (
    <div className="grid min-h-screen grid-cols-[220px_1fr] bg-background">
      <aside className="flex flex-col border-r border-border bg-card/40">
        <div className="flex h-12 items-center gap-2 border-b border-border px-4">
          <Activity className="h-4 w-4" />
          <span className="text-sm font-semibold tracking-tightish">text2graph</span>
          {config?.domain && (
            <span className="ml-auto text-2xs uppercase tracking-wider text-muted-foreground">
              {config.domain}
            </span>
          )}
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 p-2">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = item.key === route;
            return (
              <button
                key={item.key}
                onClick={() => onRouteChange(item.key)}
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
        </nav>
        <div className="border-t border-border px-4 py-3 text-2xs leading-relaxed text-muted-foreground">
          <div className="truncate"><span className="text-foreground">model</span> · {config?.llm.model ?? "—"}</div>
          <div className="truncate"><span className="text-foreground">embed</span> · {config?.embedding.model ?? "—"} ({config?.embedding.dimension})</div>
        </div>
      </aside>
      <main className="min-w-0">
        <div className="mx-auto max-w-6xl px-8 py-8">
          {children}
        </div>
      </main>
    </div>
  );
}

// hash-routing hook
export function useHashRoute(): [Route, (r: Route) => void] {
  const [r, setR] = useState<Route>(routeFromHash());
  useEffect(() => {
    const f = () => setR(routeFromHash());
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
