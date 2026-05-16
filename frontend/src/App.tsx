import { AppShell, useAppConfig, useHashRoute } from "@/components/AppShell";
import { UnsavedChangesDialog } from "@/components/UnsavedChangesDialog";
import { ConfirmHost } from "@/lib/confirm";
import { SettingsPage } from "@/pages/SettingsPage";
import { SchemaPage } from "@/pages/SchemaPage";
import { DocumentsPage } from "@/pages/DocumentsPage";
import { IngestPage } from "@/pages/IngestPage";
import { GraphPage } from "@/pages/GraphPage";
import { PromptsPage } from "@/pages/PromptsPage";
import { LLMCallsPage } from "@/pages/LLMCallsPage";

export default function App() {
  const config = useAppConfig();
  const [route, setRoute] = useHashRoute();

  return (
    <>
      <AppShell config={config} route={route} onRouteChange={setRoute}>
        {route === "settings"   && <SettingsPage />}
        {route === "documents"  && <DocumentsPage />}
        {route === "schema"     && <SchemaPage />}
        {route === "ingest"     && <IngestPage />}
        {route === "graph"      && <GraphPage config={config} />}
        {route === "prompts"    && <PromptsPage />}
        {route === "llm-calls"  && <LLMCallsPage />}
      </AppShell>
      <UnsavedChangesDialog />
      <ConfirmHost />
    </>
  );
}
