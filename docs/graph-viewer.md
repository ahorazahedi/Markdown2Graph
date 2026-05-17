# Graph Viewer Reference

`@xyflow/react` (React Flow v12) graph explorer with custom nodes, five layout algorithms, hash-based coloring, filters, and 1–4 hop neighborhood expansion.

**Files:**
- `frontend/src/components/GraphViewer.tsx`
- `frontend/src/pages/GraphPage.tsx`
- `backend/app/api/graph.py`

---

## 1. Component contract

```ts
export interface GraphViewerProps {
  data: GraphExplore;
  onRefresh?: () => void;
  onExpand?: (elementId: string, depth: number,
              includeStructure: boolean, includeCommunities: boolean) => Promise<void> | void;
  expanding?: boolean;
  focusInfo?: { elementId: string; depth: number } | null;
  onClearFocus?: () => void;
}
```

```ts
interface GraphExploreNode {
  element_id: string;
  id: string;
  labels: string[];
  description: string | null;
  properties: Record<string, any>;
  sources: string[];
}
interface GraphExploreRel {
  element_id: string;
  source: string;      // element_id of source
  target: string;
  type: string;
  properties: Record<string, any>;
}
interface GraphExplore { nodes: GraphExploreNode[]; relationships: GraphExploreRel[]; }
```

---

## 2. React Flow setup

```tsx
<ReactFlow
  nodes={nodes} edges={edges}
  nodeTypes={nodeTypes}
  onNodesChange={onNodesChange}
  onEdgesChange={onEdgesChange}
  onNodeDragStop={onNodeDragStop}
  fitView fitViewOptions={{ padding: 0.2 }}
  minZoom={0.05} maxZoom={2}
  nodesDraggable panOnScroll
  onNodeClick={(_e, node) => setSelected((node.data as any).raw)}
  onPaneClick={() => setSelected(null)}
  proOptions={{ hideAttribution: true }}
>
  <Background gap={20} size={1} color="hsl(var(--border))" />
  <Controls position="bottom-right" showInteractive={false} />
  <MiniMap pannable zoomable
    nodeColor={(n) => (n.data as any)?.color || "#888"}
    maskColor="hsl(var(--background) / 0.6)" />
</ReactFlow>
```

Theming follows design tokens — see [Design System](./design-system.md#76-react-flow-theming).

---

## 3. Node kinds

```ts
type NodeKind = "document" | "chunk" | "entity" | "community";

function kindFor(labels: string[]): NodeKind {
  if (labels.includes("__Community__")) return "community";
  if (labels.includes("Document"))      return "document";
  if (labels.includes("Chunk"))         return "chunk";
  return "entity";
}

function primaryLabel(labels: string[]): string {
  if (labels.includes("__Community__")) return "Community";
  const filtered = labels.filter((l) => !["__Entity__", "__Community__"].includes(l));
  return filtered[0] || "Entity";
}
```

Each kind has a distinct shape rendered by a custom node:
- **EntityNode** — rounded pill, ~200px max, label truncated.
- **DocumentNode** — solid square 56×56, "DOC" label.
- **ChunkNode** — rotated square (diamond) 48×48 with "▤" glyph.
- **CommunityNode** — hexagon via `clip-path`, shows level `C·L{level}`.

All use `Handle` with `opacity: 0` for connection points; selection state uses `ring-2 ring-white/70`.

---

## 4. Color hashing

```ts
const COLORS = [
  "#2563eb", "#16a34a", "#dc2626", "#ea580c", "#7c3aed",
  "#0891b2", "#db2777", "#65a30d", "#9333ea", "#0d9488",
  "#ca8a04", "#475569", "#be185d", "#0369a1", "#15803d",
];
function colorFor(label: string): string {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) >>> 0;
  return COLORS[h % COLORS.length];
}
```

Deterministic — same label always gets the same color across renders/sessions.

---

## 5. Layout algorithms

```ts
type LayoutKind = "circular" | "grid" | "concentric" | "hierarchical" | "force";
```

1. **circular** — nodes grouped by label, placed on concentric rings (`radius = 220 + ring * 220`).
2. **grid** — sorted by label, `cols = ceil(sqrt(n))`, 140px spacing.
3. **concentric** — grouped by degree (centrality); high-degree center, low at outer rings.
4. **hierarchical** — fixed y per kind: Document `-380`, Chunk `-120`, Entity `160`, Community `440`. X spacing 130px centered per layer.
5. **force** — Fruchterman–Reingold, in-process (no deps). Repulsion `k²/dist`, attraction `dist²/k`, cooling `t -= t/(iters+1)`. Iterations `min(220, 80 + floor(600 / sqrt(n)))`.

**Position persistence:**
```ts
const userPositionsRef = useRef<PosMap>({});   // remembers manual drags
const [layoutTick, setLayoutTick] = useState(0);

const changeLayout = (k: LayoutKind) => {
  userPositionsRef.current = {};               // reset on layout switch
  setLayout(k);
};
const resetPositions = () => {
  userPositionsRef.current = {};
  setLayoutTick((t) => t + 1);                 // force recompute
};
```

`onNodeDragStop` writes the new position to `userPositionsRef`; next render preserves it instead of recomputing.

---

## 6. Filters

```ts
const [hidden, setHidden] = useState<Set<string>>(new Set());

const visible = data.nodes.filter((n) => !hidden.has(primaryLabel(n.labels)));
const visibleEdges = data.relationships.filter((r) =>
  visible.some(n => n.element_id === r.source) &&
  visible.some(n => n.element_id === r.target),
);
```

Sidebar shows count per label with an eye-toggle. Edges referencing hidden nodes are dropped.

---

## 7. Detail drawer

`NodeDrawer` opens on node click:

```tsx
function NodeDrawer({ node, onClose, onExpand, expanding }: Props) {
  return (
    <Drawer open={!!node} onClose={onClose} title={node?.id}>
      {/* Labels — Badge per label */}
      {/* Explore neighborhood */}
      {onExpand && (
        <>
          <DepthSelector value={depth} onChange={setDepth} />        {/* 1..4 */}
          <Checkbox checked={includeStructure} onChange={setIncludeStructure}>
            Include Documents & Chunks
          </Checkbox>
          <Checkbox checked={includeCommunities} onChange={setIncludeCommunities}>
            Include Communities
          </Checkbox>
          <Button disabled={expanding}
                  onClick={() => onExpand(node.element_id, depth, includeStructure, includeCommunities)}>
            Show {depth}-hop neighbors
          </Button>
        </>
      )}
      {/* Description (if present) */}
      {/* Source documents — list */}
      {/* Properties — key/value table (JSON auto-formatted) */}
      {/* Identifiers — id + element_id */}
    </Drawer>
  );
}
```

---

## 8. HTTP endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/graph/stats` | `{documents, chunks, entities, entity_relationships, has_entity_relationships, ...}` |
| `GET /api/graph/schema` | `{labels: [...], relationship_types: [...]}` |
| `GET /api/graph/explore?limit=&file_name=&label=&include_structure=&include_communities=` | `GraphExplore` |
| `GET /api/graph/neighborhood?element_id=&depth=&limit=&include_structure=&include_communities=` | `GraphExplore & {focal, depth}` |
| `GET /api/graph/documents` | `{documents: [fileName, ...]}` |
| `DELETE /api/graph` | clear everything (also resets docs to pending) |
| `GET /api/graph/duplicates?limit=&min_size=` | duplicate entity groups |
| `POST /api/graph/duplicates/merge` | manual merge |
| `GET /api/graph/orphans?limit=` | orphan entities |
| `DELETE /api/graph/orphans` | delete orphans (body `{element_ids?}`) |
| `POST /api/graph/post-process` | submit post-processing job. See [Post-Processing](./post-processing.md). |

### Explore params

- `limit` (10..1000, default 200) — soft node cap.
- `file_name` — filter to a document.
- `label` — filter to an entity label.
- `include_structure` — default false. When true, includes Document + Chunk nodes (otherwise entities only).
- `include_communities` — default false. When true, includes __Community__ nodes + IN_COMMUNITY edges.

---

## 9. GraphPage integration

```ts
const [explore, setExplore] = useState<GraphExplore | null>(null);
const [focusInfo, setFocusInfo] = useState<{ elementId: string; depth: number } | null>(null);
const [expanding, setExpanding] = useState(false);

const loadExplore = async () => {
  const r = await api.exploreGraph({ limit, file_name: filterFile, label: filterLabel,
                                     include_structure: includeStructure,
                                     include_communities: includeCommunities });
  setExplore(r);
  setFocusInfo(null);
};

const expandNeighborhood = async (elementId, depth, withStructure, withCommunities) => {
  setExpanding(true);
  try {
    const r = await api.graphNeighborhood({ element_id: elementId, depth, limit: 500,
                                            include_structure: withStructure,
                                            include_communities: withCommunities });
    setExplore(r);
    setFocusInfo({ elementId, depth });
  } finally { setExpanding(false); }
};
```

Tabs: **Viewer** (controls + GraphViewer) | **Stats** (stat tiles + label/rel badges).

---

## 10. Performance notes

- React Flow uses HTML divs (not canvas) — fine up to ~1000 nodes; sluggish above 3000.
- The soft `limit` param keeps responses bounded; UI offers expand-from-node for drill-down.
- Force layout is O(n²) — capped iteration count keeps it usable up to a few hundred nodes.
- Color hash + custom node renderers do all their work in render; memoize with `useMemo` over `data.nodes` if you swap data frequently.
