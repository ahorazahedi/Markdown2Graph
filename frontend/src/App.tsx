import { useEffect, useState } from "react";
import { AppShell, useAppConfig, useHashRoute } from "@/components/AppShell";
import { UnsavedChangesDialog } from "@/components/UnsavedChangesDialog";
import { ActiveJobsBanner } from "@/components/ActiveJobsBanner";
import { ConfirmHost } from "@/lib/confirm";
import { SettingsPage } from "@/pages/SettingsPage";
import { SchemaPage } from "@/pages/SchemaPage";
import { DocumentsPage } from "@/pages/DocumentsPage";
import { IngestPage } from "@/pages/IngestPage";
import { GraphPage } from "@/pages/GraphPage";
import { PostProcessPage } from "@/pages/PostProcessPage";
import { PromptsPage } from "@/pages/PromptsPage";
import { JobsPage } from "@/pages/JobsPage";
import { LLMCallsPage } from "@/pages/LLMCallsPage";
import { ChatApp } from "@/chat/ChatApp";

/** Top-level router. `#/chat` (or `#/chat/<sessionId>`) renders the
 *  standalone chat workspace; everything else lives inside AppShell. */
function topSegment(): string {
  return window.location.hash.replace(/^#\/?/, "").split(/[/?#]/)[0];
}

function useTopRoute(): string {
  const [seg, setSeg] = useState(topSegment());
  useEffect(() => {
    const f = () => setSeg(topSegment());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return seg;
}

export default function App() {
  const config = useAppConfig();
  const top = useTopRoute();

  if (top === "chat") {
    return (
      <>
        <ChatApp />
        <UnsavedChangesDialog />
        <ConfirmHost />
      </>
    );
  }
  return <AdminApp config={config} />;
}

function AdminApp({ config }: { config: any }) {
  const [route, setRoute] = useHashRoute();
  return (
    <>
      <AppShell config={config} route={route} onRouteChange={setRoute}>
        {route === "settings"   && <SettingsPage />}
        {route === "documents"  && <DocumentsPage />}
        {route === "schema"     && <SchemaPage />}
        {route === "ingest"     && <IngestPage />}
        {route === "graph"      && <GraphPage config={config} />}
        {route === "post-process" && <PostProcessPage />}
        {route === "prompts"    && <PromptsPage />}
        {route === "jobs"       && <JobsPage />}
        {route === "llm-calls"  && <LLMCallsPage />}
      </AppShell>
      <UnsavedChangesDialog />
      <ConfirmHost />
      <ActiveJobsBanner />
    </>
  );
}
