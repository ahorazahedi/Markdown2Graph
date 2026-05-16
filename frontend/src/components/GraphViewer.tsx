import { useEffect, useMemo, useState } from "react";
import {
  ReactFlow, Background, Controls, MiniMap,
  type Node as RFNode, type Edge as RFEdge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { Drawer } from "@/components/ui/drawer";
import { Badge } from "@/components/ui/badge";
import { GraphExplore, GraphExploreNode } from "@/lib/api";

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

/** Deterministic layout: nodes spread on a circle (small) or grid (large). */
function layout(nodes: GraphExploreNode[]) {
  const n = nodes.length || 1;
  const positions: Record<string, { x: number; y: number }> = {};
  if (n <= 60) {
    const R = 120 + Math.sqrt(n) * 90;
    nodes.forEach((node, i) => {
      const angle = (i / n) * Math.PI * 2;
      positions[node.element_id] = { x: Math.cos(angle) * R, y: Math.sin(angle) * R };
    });
  } else {
    const cols = Math.ceil(Math.sqrt(n));
    nodes.forEach((node, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      positions[node.element_id] = { x: col * 180, y: row * 110 };
    });
  }
  return positions;
}

export function GraphViewer({ data, onRefresh }: { data: GraphExplore; onRefresh?: () => void }) {
  const [selected, setSelected] = useState<GraphExploreNode | null>(null);

  const { rfNodes, rfEdges } = useMemo(() => {
    const positions = layout(data.nodes);
    const rfNodes: RFNode[] = data.nodes.map((n) => {
      const primary = n.labels[0] || "Entity";
      const color = colorFor(primary);
      return {
        id: n.element_id,
        position: positions[n.element_id] || { x: 0, y: 0 },
        data: { label: n.id, raw: n },
        style: {
          background: color,
          color: "white",
          border: "1px solid rgba(255,255,255,0.15)",
          borderRadius: 4,
          fontSize: 11,
          padding: "4px 8px",
          maxWidth: 180,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        },
      } as RFNode;
    });
    const rfEdges: RFEdge[] = data.relationships.map((r) => ({
      id: r.element_id,
      source: r.source,
      target: r.target,
      label: r.type,
      labelStyle: { fontSize: 10, fill: "currentColor" },
      style: { stroke: "hsl(var(--muted-foreground))", strokeWidth: 1 },
      animated: false,
    }));
    return { rfNodes, rfEdges };
  }, [data]);

  return (
    <>
      <div className="h-[640px] w-full overflow-hidden rounded-md border border-border bg-card">
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.1}
          maxZoom={2}
          nodesDraggable
          panOnScroll
          onNodeClick={(_e, node) => setSelected((node.data as any).raw as GraphExploreNode)}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={20} size={1} />
          <Controls position="bottom-right" showInteractive={false} />
          <MiniMap pannable zoomable nodeColor={(n) => (n.style as any)?.background || "#888"} />
        </ReactFlow>
      </div>

      <NodeDrawer node={selected} onClose={() => setSelected(null)} />
    </>
  );
}

function NodeDrawer({ node, onClose }: { node: GraphExploreNode | null; onClose: () => void }) {
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

        {node.description && (
          <Section title="Description">
            <p className="text-sm text-foreground">{node.description}</p>
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
