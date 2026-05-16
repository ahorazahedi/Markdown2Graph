import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow, Background, Controls, MiniMap, Handle, Position,
  useNodesState, useEdgesState,
  type Node as RFNode, type Edge as RFEdge, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Eye, EyeOff, Maximize2, Minimize2, Loader2, Target, RotateCcw,
} from "lucide-react";

import { Drawer } from "@/components/ui/drawer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { GraphExplore, GraphExploreNode } from "@/lib/api";
import { cn } from "@/lib/utils";

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

type NodeKind = "document" | "chunk" | "entity" | "community";
function kindFor(labels: string[]): NodeKind {
  if (labels.includes("__Community__")) return "community";
  if (labels.includes("Document")) return "document";
  if (labels.includes("Chunk")) return "chunk";
  return "entity";
}
function primaryLabel(labels: string[]): string {
  if (labels.includes("__Community__")) return "Community";
  const filtered = labels.filter((l) => !["__Entity__", "__Community__"].includes(l));
  return filtered[0] || "Entity";
}

// ---------------- layouts ----------------

type LayoutKind = "circular" | "grid" | "concentric" | "hierarchical" | "force";
type Pos = { x: number; y: number };
type PosMap = Record<string, Pos>;

function circularLayout(nodes: GraphExploreNode[]): PosMap {
  const positions: PosMap = {};
  const groups = new Map<string, GraphExploreNode[]>();
  for (const node of nodes) {
    const k = primaryLabel(node.labels);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(node);
  }
  const grouped = [...groups.values()].flat();
  const perRing = Math.max(20, Math.ceil(Math.sqrt(grouped.length || 1) * 6));

  grouped.forEach((node, i) => {
    const ring = Math.floor(i / perRing);
    const idxInRing = i % perRing;
    const ringSize = Math.min(perRing, grouped.length - ring * perRing);
    const R = 220 + ring * 220;
    const angle = (idxInRing / ringSize) * Math.PI * 2;
    positions[node.element_id] = { x: Math.cos(angle) * R, y: Math.sin(angle) * R };
  });
  return positions;
}

function gridLayout(nodes: GraphExploreNode[]): PosMap {
  const positions: PosMap = {};
  const n = nodes.length || 1;
  const cols = Math.ceil(Math.sqrt(n));
  const spacing = 140;
  // group by label so similar nodes cluster
  const grouped = [...nodes].sort((a, b) =>
    primaryLabel(a.labels).localeCompare(primaryLabel(b.labels)));
  grouped.forEach((node, i) => {
    const c = i % cols;
    const r = Math.floor(i / cols);
    positions[node.element_id] = {
      x: (c - cols / 2) * spacing,
      y: (r - Math.ceil(n / cols) / 2) * spacing,
    };
  });
  return positions;
}

function concentricLayout(
  nodes: GraphExploreNode[],
  rels: { source: string; target: string }[],
): PosMap {
  const positions: PosMap = {};
  const deg = new Map<string, number>();
  for (const n of nodes) deg.set(n.element_id, 0);
  for (const r of rels) {
    if (deg.has(r.source)) deg.set(r.source, deg.get(r.source)! + 1);
    if (deg.has(r.target)) deg.set(r.target, deg.get(r.target)! + 1);
  }
  const sorted = [...nodes].sort(
    (a, b) => (deg.get(b.element_id)! - deg.get(a.element_id)!),
  );
  // bucket into rings by degree quantile
  const ringsN = Math.min(5, Math.max(1, Math.ceil(Math.sqrt(nodes.length / 6))));
  const perRing = Math.ceil(sorted.length / ringsN);
  sorted.forEach((node, i) => {
    const ring = Math.floor(i / perRing);
    const idxInRing = i % perRing;
    const ringSize = Math.min(perRing, sorted.length - ring * perRing);
    const R = ring === 0 ? 0 : 180 + ring * 200;
    const angle = ringSize === 1 ? 0 : (idxInRing / ringSize) * Math.PI * 2;
    positions[node.element_id] = { x: Math.cos(angle) * R, y: Math.sin(angle) * R };
  });
  return positions;
}

/** Top-down layered layout: Documents on top, Chunks middle, Entities below.
 *  Within each layer, place by BFS distance from a Document if reachable. */
function hierarchicalLayout(
  nodes: GraphExploreNode[],
  rels: { source: string; target: string }[],
): PosMap {
  const positions: PosMap = {};
  const layerOf: Record<string, number> = {};
  for (const n of nodes) {
    const k = kindFor(n.labels);
    layerOf[n.element_id] =
      k === "document" ? 0 :
      k === "chunk" ? 1 :
      k === "entity" ? 2 : 3; // community
  }
  const layers: Record<number, GraphExploreNode[]> = { 0: [], 1: [], 2: [], 3: [] };
  for (const n of nodes) layers[layerOf[n.element_id]].push(n);

  const layerY = [-380, -120, 160, 440];
  const xSpacing = 130;
  for (const lvl of [0, 1, 2, 3]) {
    const arr = layers[lvl];
    arr.sort((a, b) => primaryLabel(a.labels).localeCompare(primaryLabel(b.labels)));
    const n = arr.length;
    arr.forEach((node, i) => {
      positions[node.element_id] = {
        x: (i - (n - 1) / 2) * xSpacing,
        y: layerY[lvl],
      };
    });
  }
  void rels;
  return positions;
}

/** Lightweight force-directed (Fruchterman–Reingold), no deps. */
function forceLayout(
  nodes: GraphExploreNode[],
  rels: { source: string; target: string }[],
  seed: PosMap,
): PosMap {
  const n = nodes.length;
  if (n === 0) return {};
  const area = Math.max(1, n) * 18000;
  const k = Math.sqrt(area / n);
  const positions: PosMap = {};
  const idIndex = new Map<string, number>();
  const px: number[] = new Array(n);
  const py: number[] = new Array(n);
  nodes.forEach((nd, i) => {
    idIndex.set(nd.element_id, i);
    const s = seed[nd.element_id];
    if (s) { px[i] = s.x; py[i] = s.y; }
    else {
      const a = (i / n) * Math.PI * 2;
      const r = 200 + Math.random() * 80;
      px[i] = Math.cos(a) * r;
      py[i] = Math.sin(a) * r;
    }
  });
  const edges: [number, number][] = [];
  for (const r of rels) {
    const a = idIndex.get(r.source); const b = idIndex.get(r.target);
    if (a != null && b != null && a !== b) edges.push([a, b]);
  }

  const iters = Math.min(220, 80 + Math.floor(600 / Math.max(1, Math.sqrt(n))));
  let t = k * 1.5;
  const cool = t / (iters + 1);

  for (let it = 0; it < iters; it++) {
    const dx = new Array(n).fill(0);
    const dy = new Array(n).fill(0);

    // repulsion
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const xd = px[i] - px[j];
        const yd = py[i] - py[j];
        const dist2 = xd * xd + yd * yd || 0.01;
        const dist = Math.sqrt(dist2);
        const f = (k * k) / dist;
        const fx = (xd / dist) * f;
        const fy = (yd / dist) * f;
        dx[i] += fx; dy[i] += fy;
        dx[j] -= fx; dy[j] -= fy;
      }
    }
    // attraction along edges
    for (const [a, b] of edges) {
      const xd = px[a] - px[b];
      const yd = py[a] - py[b];
      const dist = Math.sqrt(xd * xd + yd * yd) || 0.01;
      const f = (dist * dist) / k;
      const fx = (xd / dist) * f;
      const fy = (yd / dist) * f;
      dx[a] -= fx; dy[a] -= fy;
      dx[b] += fx; dy[b] += fy;
    }
    // apply with cooling temperature cap
    for (let i = 0; i < n; i++) {
      const disp = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) || 0.01;
      px[i] += (dx[i] / disp) * Math.min(disp, t);
      py[i] += (dy[i] / disp) * Math.min(disp, t);
    }
    t -= cool;
  }

  nodes.forEach((nd, i) => { positions[nd.element_id] = { x: px[i], y: py[i] }; });
  return positions;
}

function computeLayout(
  kind: LayoutKind,
  nodes: GraphExploreNode[],
  rels: { source: string; target: string }[],
  prev: PosMap,
): PosMap {
  switch (kind) {
    case "circular": return circularLayout(nodes);
    case "grid": return gridLayout(nodes);
    case "concentric": return concentricLayout(nodes, rels);
    case "hierarchical": return hierarchicalLayout(nodes, rels);
    case "force": return forceLayout(nodes, rels, prev);
  }
}

// ---------- custom node renderers ----------

function EntityNode({ data, selected }: NodeProps) {
  const d = data as any;
  return (
    <div
      className={cn(
        "rounded-md border px-2.5 py-1 text-[11px] text-white shadow-sm",
        selected ? "border-white ring-2 ring-white/60" : "border-white/10",
      )}
      style={{ background: d.color, maxWidth: 200, whiteSpace: "nowrap",
               overflow: "hidden", textOverflow: "ellipsis" }}
      title={d.label}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      {d.label}
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}

function DocumentNode({ data, selected }: NodeProps) {
  const d = data as any;
  return (
    <div
      className={cn(
        "flex h-14 w-14 items-center justify-center rounded-sm border-2 text-[10px] font-semibold text-white shadow-md",
        selected && "ring-2 ring-white/70",
      )}
      style={{ background: d.color, borderColor: "rgba(255,255,255,0.35)" }}
      title={d.label}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <span className="px-1 leading-tight text-center overflow-hidden text-ellipsis"
            style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
        DOC
      </span>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}

function ChunkNode({ data, selected }: NodeProps) {
  const d = data as any;
  return (
    <div className="relative h-12 w-12" title={d.label}>
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div
        className={cn("absolute inset-0 border-2 shadow-sm", selected && "ring-2 ring-white/70")}
        style={{
          background: d.color,
          borderColor: "rgba(255,255,255,0.3)",
          transform: "rotate(45deg)",
        }}
      />
      <div className="relative flex h-full w-full items-center justify-center text-[10px] font-semibold text-white">
        ▤
      </div>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}

function CommunityNode({ data, selected }: NodeProps) {
  const d = data as any;
  // hexagon-ish hex via clip-path so it visually distinct from doc/chunk/entity
  const level = d.raw?.properties?.level;
  return (
    <div
      className={cn(
        "relative flex items-center justify-center text-[10px] font-semibold text-white shadow-md",
        selected && "ring-2 ring-white/70",
      )}
      style={{
        width: 56, height: 56,
        background: d.color,
        clipPath: "polygon(25% 5%, 75% 5%, 100% 50%, 75% 95%, 25% 95%, 0 50%)",
        border: "2px solid rgba(255,255,255,0.35)",
      }}
      title={d.label}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <span className="px-1 leading-tight text-center">
        C{level != null ? `·L${level}` : ""}
      </span>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}

const nodeTypes = {
  entity: EntityNode,
  document: DocumentNode,
  chunk: ChunkNode,
  community: CommunityNode,
};

// ---------- viewer ----------

export interface GraphViewerProps {
  data: GraphExplore;
  onRefresh?: () => void;
  onExpand?: (elementId: string, depth: number,
              includeStructure: boolean,
              includeCommunities: boolean) => Promise<void> | void;
  expanding?: boolean;
  focusInfo?: { elementId: string; depth: number } | null;
  onClearFocus?: () => void;
}

export function GraphViewer({
  data, onExpand, expanding, focusInfo, onClearFocus,
}: GraphViewerProps) {
  const [selected, setSelected] = useState<GraphExploreNode | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [fullscreen, setFullscreen] = useState(false);
  const [layout, setLayout] = useState<LayoutKind>("circular");

  // user-dragged positions persist across data refreshes and layout reapplies
  const userPositionsRef = useRef<PosMap>({});
  const [layoutTick, setLayoutTick] = useState(0); // bump to force a re-layout

  // ESC exits fullscreen
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullscreen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  const labelStats = useMemo(() => {
    const m = new Map<string, { count: number; kind: NodeKind; color: string }>();
    for (const n of data.nodes) {
      const label = primaryLabel(n.labels);
      const entry = m.get(label);
      if (entry) entry.count++;
      else m.set(label, { count: 1, kind: kindFor(n.labels), color: colorFor(label) });
    }
    return [...m.entries()]
      .map(([label, v]) => ({ label, ...v }))
      .sort((a, b) => b.count - a.count);
  }, [data.nodes]);

  // base rfNodes / rfEdges, with positions taken from current layout + drag overrides
  const { baseNodes, baseEdges } = useMemo(() => {
    const keep = data.nodes.filter((n) => !hidden.has(primaryLabel(n.labels)));
    const keepIds = new Set(keep.map((n) => n.element_id));
    const positions = computeLayout(layout, keep, data.relationships, userPositionsRef.current);

    const rfNodes: RFNode[] = keep.map((n) => {
      const label = primaryLabel(n.labels);
      const color = colorFor(label);
      const kind = kindFor(n.labels);
      const user = userPositionsRef.current[n.element_id];
      const pos = user || positions[n.element_id] || { x: 0, y: 0 };
      const isFocus = focusInfo?.elementId === n.element_id;
      return {
        id: n.element_id,
        type: kind,
        position: pos,
        data: { label: n.id, color, raw: n, isFocus },
        selected: selected?.element_id === n.element_id,
      } as RFNode;
    });
    const rfEdges: RFEdge[] = data.relationships
      .filter((r) => keepIds.has(r.source) && keepIds.has(r.target))
      .map((r) => ({
        id: r.element_id,
        source: r.source,
        target: r.target,
        label: r.type,
        labelBgPadding: [4, 2],
        labelBgBorderRadius: 2,
        style: { stroke: "hsl(var(--muted-foreground) / 0.45)", strokeWidth: 1 },
      }));
    return { baseNodes: rfNodes, baseEdges: rfEdges };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, hidden, layout, layoutTick, focusInfo, selected?.element_id]);

  const [nodes, setNodes, onNodesChange] = useNodesState(baseNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(baseEdges);

  // sync controlled state when inputs change
  useEffect(() => { setNodes(baseNodes); }, [baseNodes, setNodes]);
  useEffect(() => { setEdges(baseEdges); }, [baseEdges, setEdges]);

  const onNodeDragStop = useCallback(
    (_e: any, node: RFNode) => {
      userPositionsRef.current[node.id] = { x: node.position.x, y: node.position.y };
    },
    [],
  );

  const changeLayout = (k: LayoutKind) => {
    userPositionsRef.current = {}; // a layout switch resets manual placements
    setLayout(k);
  };

  const resetPositions = () => {
    userPositionsRef.current = {};
    setLayoutTick((t) => t + 1);
  };

  const toggle = (label: string) => {
    setHidden((s) => {
      const n = new Set(s);
      n.has(label) ? n.delete(label) : n.add(label);
      return n;
    });
  };

  return (
    <>
      <div
        className={cn(
          "grid w-full overflow-hidden border bg-card",
          fullscreen
            ? "fixed inset-0 z-40 h-screen rounded-none border-0"
            : "h-[680px] rounded-md border-border",
        )}
        style={{ gridTemplateColumns: "260px 1fr" }}
      >
        {/* sidebar */}
        <aside className="flex flex-col border-r border-border bg-card/60">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <div>
              <div className="text-2xs uppercase tracking-wider text-muted-foreground">Node types</div>
              <div className="text-xxs text-muted-foreground">click to toggle visibility</div>
            </div>
            <button
              onClick={() => setFullscreen((v) => !v)}
              className="rounded-sm border border-border p-1 text-muted-foreground hover:bg-accent"
              title={fullscreen ? "Exit fullscreen (Esc)" : "Fullscreen"}
            >
              {fullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
            </button>
          </div>

          {/* layout selector */}
          <div className="border-b border-border px-3 py-2">
            <div className="text-2xs uppercase tracking-wider text-muted-foreground mb-1">Layout</div>
            <div className="grid grid-cols-2 gap-1">
              {(["circular", "grid", "concentric", "hierarchical", "force"] as LayoutKind[]).map((k) => (
                <button
                  key={k}
                  onClick={() => changeLayout(k)}
                  className={cn(
                    "rounded-sm border px-2 py-1 text-2xs capitalize hover:bg-accent",
                    layout === k
                      ? "border-foreground/60 bg-accent text-foreground"
                      : "border-border text-muted-foreground",
                  )}
                >
                  {k}
                </button>
              ))}
            </div>
            <button
              onClick={resetPositions}
              className="mt-2 flex w-full items-center justify-center gap-1 rounded-sm border border-border px-2 py-1 text-2xs text-muted-foreground hover:bg-accent"
              title="Re-run current layout and drop manual positions"
            >
              <RotateCcw className="h-3 w-3" /> Reset positions
            </button>
          </div>

          {focusInfo && (
            <div className="border-b border-border bg-accent/30 px-3 py-2 text-xxs">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">
                  Focused — depth {focusInfo.depth}
                </span>
                <button
                  onClick={onClearFocus}
                  className="text-foreground underline-offset-2 hover:underline"
                >
                  back to overview
                </button>
              </div>
            </div>
          )}

          <div className="flex-1 overflow-auto">
            {labelStats.map(({ label, count, color, kind }) => {
              const off = hidden.has(label);
              return (
                <button
                  key={label}
                  onClick={() => toggle(label)}
                  className={cn(
                    "flex w-full items-center gap-2 border-b border-border/60 px-3 py-1.5 text-left text-xs hover:bg-accent",
                    off && "opacity-40",
                  )}
                >
                  <span className="shrink-0" style={{
                    width: 10, height: 10, background: color,
                    borderRadius: kind === "entity" ? 5 : kind === "chunk" ? 0 : 2,
                    transform: kind === "chunk" ? "rotate(45deg)" : undefined,
                    clipPath: kind === "community"
                      ? "polygon(25% 5%, 75% 5%, 100% 50%, 75% 95%, 25% 95%, 0 50%)"
                      : undefined,
                  }} />
                  <span className="min-w-0 flex-1 truncate">{label}</span>
                  <span className="font-mono text-2xs text-muted-foreground tabular-nums">{count}</span>
                  {off ? <EyeOff className="h-3 w-3 text-muted-foreground" />
                       : <Eye    className="h-3 w-3 text-muted-foreground" />}
                </button>
              );
            })}
            {labelStats.length === 0 && (
              <p className="px-3 py-4 text-xs text-muted-foreground">No nodes loaded.</p>
            )}
          </div>
          <div className="border-t border-border px-3 py-2 text-xxs text-muted-foreground">
            <Legend />
          </div>
        </aside>

        {/* viewer */}
        <div className="relative">
          {expanding && (
            <div className="absolute right-3 top-3 z-10 flex items-center gap-1.5 rounded-sm border border-border bg-card/90 px-2 py-1 text-2xs text-muted-foreground shadow-sm">
              <Loader2 className="h-3 w-3 animate-spin" /> loading neighborhood…
            </div>
          )}
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeDragStop={onNodeDragStop}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.05}
            maxZoom={2}
            nodesDraggable
            panOnScroll
            onNodeClick={(_e, node) => setSelected((node.data as any).raw as GraphExploreNode)}
            onPaneClick={() => setSelected(null)}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={20} size={1} color="hsl(var(--border))" />
            <Controls position="bottom-right" showInteractive={false} />
            <MiniMap pannable zoomable
                     nodeColor={(n) => (n.data as any)?.color || "#888"}
                     maskColor="hsl(var(--background) / 0.6)" />
          </ReactFlow>
        </div>
      </div>

      <NodeDrawer
        node={selected}
        onClose={() => setSelected(null)}
        onExpand={onExpand}
        expanding={!!expanding}
      />
    </>
  );
}

function Legend() {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <span className="inline-block h-3 w-3 rounded-sm border border-white/30 bg-muted-foreground/60" />
        <span>Document — square</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="inline-block h-3 w-3 border border-white/30 bg-muted-foreground/60"
              style={{ transform: "rotate(45deg)" }} />
        <span>Chunk — diamond</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="inline-block h-2.5 w-4 rounded-md border border-white/30 bg-muted-foreground/60" />
        <span>Entity — pill</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="inline-block h-3 w-3 border border-white/30 bg-muted-foreground/60"
              style={{ clipPath: "polygon(25% 5%, 75% 5%, 100% 50%, 75% 95%, 25% 95%, 0 50%)" }} />
        <span>Community — hex</span>
      </div>
    </div>
  );
}

function NodeDrawer({
  node, onClose, onExpand, expanding,
}: {
  node: GraphExploreNode | null;
  onClose: () => void;
  onExpand?: (elementId: string, depth: number,
              includeStructure: boolean,
              includeCommunities: boolean) => Promise<void> | void;
  expanding: boolean;
}) {
  const [depth, setDepth] = useState(1);
  const [withStructure, setWithStructure] = useState(false);
  const [withCommunities, setWithCommunities] = useState(false);
  if (!node) return null;
  return (
    <Drawer
      open={!!node}
      onClose={onClose}
      title={node.id}
      subtitle={node.labels.join(", ")}
    >
      <div className="space-y-4 p-5">
        <div className="flex flex-wrap gap-1.5">
          {node.labels.map((l) => (
            <Badge key={l} variant="outline" className="text-xs">{l}</Badge>
          ))}
        </div>

        {onExpand && (
          <Section title="Explore neighborhood">
            <div className="rounded-sm border border-border bg-background/40 p-3 space-y-3">
              <div className="flex items-center gap-2">
                <span className="text-2xs uppercase tracking-wider text-muted-foreground">Depth (k)</span>
                <div className="flex gap-1">
                  {[1, 2, 3, 4].map((k) => (
                    <button
                      key={k}
                      onClick={() => setDepth(k)}
                      className={cn(
                        "h-7 w-7 rounded-sm border text-xs",
                        depth === k
                          ? "border-foreground/60 bg-accent"
                          : "border-border text-muted-foreground hover:bg-accent",
                      )}
                    >{k}</button>
                  ))}
                </div>
              </div>
              <label className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={withStructure}
                  onChange={(e) => setWithStructure(e.target.checked)}
                />
                Include Documents &amp; Chunks
              </label>
              <label className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={withCommunities}
                  onChange={(e) => setWithCommunities(e.target.checked)}
                />
                Include Communities
              </label>
              <Button
                size="sm"
                disabled={expanding}
                onClick={async () => {
                  await onExpand(node.element_id, depth, withStructure, withCommunities);
                }}
              >
                {expanding
                  ? <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…</>
                  : <><Target className="h-3.5 w-3.5" /> Show {depth}-hop neighbors</>}
              </Button>
            </div>
          </Section>
        )}

        {node.description && (
          <Section title="Description">
            <p className="text-sm text-foreground whitespace-pre-wrap">{node.description}</p>
          </Section>
        )}

        <Section title="Source documents">
          {node.sources.length === 0
            ? <p className="text-sm text-muted-foreground">No source recorded.</p>
            : (
              <ul className="space-y-1 text-sm">
                {node.sources.map((s) => (
                  <li key={s} className="font-mono text-xs">{s}</li>
                ))}
              </ul>
            )}
        </Section>

        <Section title="Properties">
          {Object.keys(node.properties).length === 0
            ? <p className="text-sm text-muted-foreground">No properties.</p>
            : (
              <div className="overflow-hidden rounded-sm border border-border">
                <table className="w-full text-sm">
                  <tbody>
                    {Object.entries(node.properties).map(([k, v]) => (
                      <tr key={k} className="border-b border-border last:border-0">
                        <td className="w-1/3 px-3 py-1.5 font-mono text-2xs text-muted-foreground align-top">{k}</td>
                        <td className="px-3 py-1.5 text-xs">
                          {typeof v === "string" ? v : <pre className="whitespace-pre-wrap font-mono text-2xs">{JSON.stringify(v, null, 2)}</pre>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </Section>

        <Section title="Identifiers">
          <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
            <span className="text-muted-foreground">id</span>
            <span className="font-mono">{node.id}</span>
            <span className="text-muted-foreground">element_id</span>
            <span className="font-mono break-all">{node.element_id}</span>
          </div>
        </Section>
      </div>
    </Drawer>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <div className="text-2xs uppercase tracking-wider text-muted-foreground">{title}</div>
      {children}
    </div>
  );
}
