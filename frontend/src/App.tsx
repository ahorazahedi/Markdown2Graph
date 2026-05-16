import { useEffect, useState } from "react";
import { Activity } from "lucide-react";
import { api, AppConfig } from "@/lib/api";
import { Stepper, Step } from "@/components/Stepper";
import { StepConnect } from "@/components/steps/StepConnect";
import { StepFolder } from "@/components/steps/StepFolder";
import { StepSchema, SchemaState } from "@/components/steps/StepSchema";
import { StepIngest } from "@/components/steps/StepIngest";
import { StepResults } from "@/components/steps/StepResults";

const STEPS: Step[] = [
  { key: "connect", title: "Connect", description: "Neo4j + LLM" },
  { key: "folder", title: "Source", description: "Markdown folder" },
  { key: "schema", title: "Schema", description: "Discover & review" },
  { key: "ingest", title: "Ingest", description: "Build the graph" },
  { key: "results", title: "Results", description: "Inspect & iterate" },
];

export default function App() {
  const [step, setStep] = useState(0);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [path, setPath] = useState("");
  const [schema, setSchema] = useState<SchemaState>({ nodes: [], triplets: [], extra: "" });

  useEffect(() => {
    api.config().then(setConfig).catch(console.error);
  }, []);

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
          <div className="text-xs text-muted-foreground">
            {config?.llm.model} · {config?.embedding.model}
          </div>
        </div>
      </header>

      <main className="container py-8">
        <div className="grid gap-8 md:grid-cols-[260px_1fr]">
          <aside className="hidden md:block">
            <Stepper steps={STEPS} current={step} />
          </aside>
          <section>
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
      </main>

      <footer className="border-t border-border py-4 text-center text-xs text-muted-foreground">
        text2graph · markdown → Neo4j knowledge graph for medical content
      </footer>
    </div>
  );
}
