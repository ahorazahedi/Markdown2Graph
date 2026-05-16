import { AppShell, useAppConfig, useHashRoute } from "@/components/AppShell";
import { SetupPage } from "@/pages/SetupPage";
import { SchemaPage } from "@/pages/SchemaPage";
import { DocumentsPage } from "@/pages/DocumentsPage";
import { IngestPage } from "@/pages/IngestPage";
import { GraphPage } from "@/pages/GraphPage";
import { LLMCallsPage } from "@/pages/LLMCallsPage";

export default function App() {
  const config = useAppConfig();
  const [route, setRoute] = useHashRoute();

  return (
    <AppShell config={config} route={route} onRouteChange={setRoute}>
      {route === "setup"      && <SetupPage config={config} />}
      {route === "schema"     && <SchemaPage />}
      {route === "documents"  && <DocumentsPage />}
      {route === "ingest"     && <IngestPage />}
      {route === "graph"      && <GraphPage config={config} />}
      {route === "llm-calls"  && <LLMCallsPage />}
    </AppShell>
  );
}
