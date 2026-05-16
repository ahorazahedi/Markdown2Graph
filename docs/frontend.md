# Frontend

Vite + React + TypeScript + Tailwind + shadcn-style primitives. Multi-page
app served on `:5173` in dev, with `/api/*` proxied to the Flask backend on
`:8000`.

## Tree

```
frontend/src/
├── main.tsx               bootstrap
├── App.tsx                router + shell
├── index.css              Tailwind base + global tokens
│
├── pages/                 one component per top-level route
│   ├── DocumentsPage.tsx     uploaded / known docs, status, counts, actions
│   ├── SchemaPage.tsx        discover schema, edit nodes & relationships
│   ├── IngestPage.tsx        kick off ingestion of pending / selected docs
│   ├── GraphPage.tsx         visual exploration of the Neo4j graph
│   ├── JobsPage.tsx          live + historical jobs, per-event log
│   ├── LLMCallsPage.tsx      recorded LLM I/O, JSON pretty-print, retry
│   ├── PromptsPage.tsx       edit prompt templates
│   └── SettingsPage.tsx      runtime-mutable backend settings
│
├── components/
│   ├── AppShell.tsx          sidebar + top bar layout
│   ├── ActiveJobsBanner.tsx  always-visible banner for running jobs
│   ├── GraphViewer.tsx       graph canvas / node + edge rendering
│   ├── RuntimeSettingsPanel.tsx
│   ├── PageContainer.tsx, PageHeader.tsx
│   ├── StatusBadge.tsx
│   ├── UnsavedChangesDialog.tsx
│   └── ui/                   shadcn-style primitives (button, input, …)
│
└── lib/                   typed fetch client + shared helpers
```

## Routing model

Multi-page, not a single wizard. The user can move freely between pages.
Long-running ingestion does not block the UI — jobs are surfaced in the
**Active Jobs banner** and inspectable on the **Jobs page**.

## Data flow

```
page  ──►  lib/api.ts  ──►  /api/*  ──►  Flask blueprint
  ▲                                       │
  └───────────  typed response  ──────────┘
```

`lib/api.ts` is the only place that knows about endpoint paths. Pages call
typed helpers; types are kept in sync with the backend response shapes by
hand (no generated client).

## Pages at a glance

| Page         | Backend surface                          | What the user does |
|--------------|------------------------------------------|--------------------|
| Documents    | `/api/documents`, `/api/upload`          | Upload / list / re-ingest / delete |
| Schema       | `/api/schema`                            | Discover, edit, save node + relationship schema |
| Ingest       | `/api/ingest`, `/api/jobs`               | Kick off a job, watch live progress |
| Graph        | `/api/graph`                             | Browse the resulting Neo4j graph |
| Jobs         | `/api/jobs`                              | Active + historical jobs, per-event log |
| LLM Calls    | `/api/llm_calls`                         | Inspect every recorded request/response, retry |
| Prompts      | `/api/prompts`                           | Edit medical-tuned prompt templates |
| Settings     | `/api/settings`, `/api/runtime_settings` | Tune chunk size, retries, KNN, providers |

## Styling

Tailwind utility classes + shadcn-style primitives in `components/ui/`. No
component library dependency beyond Radix primitives used by shadcn. Dark /
light theming via CSS variables in `index.css`.

## Dev server

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, /api proxied to :8000
```

`vite.config.ts` owns the `/api → :8000` proxy. No CORS in dev.
