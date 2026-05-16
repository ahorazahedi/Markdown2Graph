import { useEffect, useState } from "react";
import { Activity, Workflow, ScrollText } from "lucide-react";
import { api, AppConfig } from "@/lib/api";
import { Stepper, Step } from "@/components/Stepper";
import { StepConnect } from "@/components/steps/StepConnect";
import { StepFolder } from "@/components/steps/StepFolder";
import { StepSchema, SchemaState } from "@/components/steps/StepSchema";
import { StepIngest } from "@/components/steps/StepIngest";
import { StepResults } from "@/components/steps/StepResults";
import { LLMCallsPage } from "@/pages/LLMCallsPage";
import { cn } from "@/lib/utils";

const STEPS: Step[] = [
  { key: "connect", title: "Connect", description: "Neo4j + LLM" },
  { key: "folder", title: "Source", description: "Markdown folder" },
  { key: "schema", title: "Schema", description: "Discover & review" },
  { key: "ingest", title: "Ingest", description: "Build the graph" },
  { key: "results", title: "Results", description: "Inspect & iterate" },
];

type Tab = "wizard" | "llm-calls";

export default function App() {
  const [tab, setTab] = useState<Tab>(initialTab());
  const [step, setStep] = useState(0);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [path, setPath] = useState("");
  const [schema, setSchema] = useState<SchemaState>({ nodes: [], triplets: [], extra: "" });

  useEffect(() => {
    api.config().then(setConfig).catch(console.error);
  }, []);

  useEffect(() => {
    const onHash = () => setTab(initialTab());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const goto = (t: Tab) => {
    window.location.hash = t === "wizard" ? "" : `#/${t}`;
    setTab(t);
  };

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/30 backdrop-blur">
        <div className="container flex h-14 items-center justify-between">
          <div className="flex items-center gap-2 font-semibold">
            <Activity className="h-5 w-5 text-primary" />
            text2graph
            <span className="ml-2 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-primary">
              {config?.domain ?? "medical"}
            </span>
          </div>
          <nav className="flex items-center gap-1">
            <NavTab active={tab === "wizard"} onClick={() => goto("wizard")} icon={<Workflow className="h-4 w-4" />}>
              Wizard
            </NavTab>
            <NavTab active={tab === "llm-calls"} onClick={() => goto("llm-calls")} icon={<ScrollText className="h-4 w-4" />}>
              LLM Calls
            </NavTab>
          </nav>
          <div className="hidden text-xs text-muted-foreground md:block">
            {config?.llm.model} · {config?.embedding.model}
          </div>
        </div>
      </header>

      <main className="container py-8">
        {tab === "wizard" && (
          <>
            <div className="mb-6 md:hidden">
              <Stepper steps={STEPS} current={step} orientation="horizontal" />
            </div>
            <div className="grid gap-8 md:grid-cols-[220px_minmax(0,1fr)] lg:grid-cols-[240px_minmax(0,1fr)]">
            <aside className="hidden md:block">
              <div className="sticky top-20">
                <Stepper steps={STEPS} current={step} orientation="vertical" />
              </div>
            </aside>
            <section className="min-w-0">
              {step === 0 && <StepConnect config={config} onNext={() => setStep(1)} />}
              {step === 1 && (
                <StepFolder
                  value={path}
                  onChange={setPath}
                  onBack={() => setStep(0)}
                  onNext={() => setStep(2)}
                />
              )}
              {step === 2 && (
                <StepSchema
                  path={path}
                  defaultSampleSize={config?.schema_discovery.sample_size ?? 5}
                  state={schema}
                  setState={setSchema}
                  onBack={() => setStep(1)}
                  onNext={() => setStep(3)}
                />
              )}
              {step === 3 && (
                <StepIngest
                  path={path}
                  schema={schema}
                  onBack={() => setStep(2)}
                  onDone={() => setStep(4)}
                />
              )}
              {step === 4 && <StepResults config={config} onRestart={() => setStep(1)} />}
            </section>
            </div>
          </>
        )}
        {tab === "llm-calls" && <LLMCallsPage />}
      </main>

      <footer className="border-t border-border py-4 text-center text-xs text-muted-foreground">
        text2graph · markdown → Neo4j knowledge graph for medical content
      </footer>
    </div>
  );
}

function NavTab({
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
        "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition",
        active
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function initialTab(): Tab {
  return window.location.hash.startsWith("#/llm-calls") ? "llm-calls" : "wizard";
}
