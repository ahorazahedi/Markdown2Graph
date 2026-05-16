import { AppShell, useAppConfig, useHashRoute } from "@/components/AppShell";
import { PageContainer } from "@/components/PageContainer";
import { UnsavedChangesDialog } from "@/components/UnsavedChangesDialog";
import { ConfirmHost } from "@/lib/confirm";
import { SetupPage } from "@/pages/SetupPage";
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
        {route === "setup"      && <PageContainer><SetupPage config={config} /></PageContainer>}
        {route === "documents"  && <PageContainer><DocumentsPage /></PageContainer>}
        {route === "schema"     && <PageContainer><SchemaPage /></PageContainer>}
        {route === "ingest"     && <PageContainer><IngestPage /></PageContainer>}
        {route === "graph"      && <PageContainer maxWidth="max-w-[1600px]"><GraphPage config={config} /></PageContainer>}
        {route === "prompts"    && <PageContainer><PromptsPage /></PageContainer>}
        {route === "llm-calls"  && <LLMCallsPage />}
      </AppShell>
      <UnsavedChangesDialog />
      <ConfirmHost />
    </>
  );
}
