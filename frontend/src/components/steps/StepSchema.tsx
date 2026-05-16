import { useState } from "react";
import { Sparkles, X, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { api, DiscoverResult } from "@/lib/api";

export interface SchemaState {
  nodes: string[];
  triplets: [string, string, string][];
  extra: string;
}

export function StepSchema({
  path,
  defaultSampleSize,
  state,
  setState,
  onBack,
  onNext,
}: {
  path: string;
  defaultSampleSize: number;
  state: SchemaState;
  setState: (s: SchemaState) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const [sampleSize, setSampleSize] = useState(defaultSampleSize);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DiscoverResult | null>(null);
  const [extra, setExtra] = useState(state.extra);

  const [newNode, setNewNode] = useState("");
  const [newRel, setNewRel] = useState({ src: "", type: "", dst: "" });

  const discover = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.discover(path, sampleSize, extra || undefined);
      setResult(r);
      const triplets: [string, string, string][] = [];
      for (const t of r.triplets) {
        const m = /^(.+?)-(.+?)->(.+)$/.exec(t);
        if (m) triplets.push([m[1].trim(), m[2].trim(), m[3].trim()]);
      }
      setState({ nodes: r.node_labels, triplets, extra });
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  };

  const removeNode = (n: string) =>
    setState({
      ...state,
      nodes: state.nodes.filter((x) => x !== n),
      triplets: state.triplets.filter(([s, , d]) => s !== n && d !== n),
    });
  const addNode = () => {
    const n = newNode.trim();
    if (n && !state.nodes.includes(n)) setState({ ...state, nodes: [...state.nodes, n] });
    setNewNode("");
  };
  const removeRel = (i: number) =>
    setState({ ...state, triplets: state.triplets.filter((_, idx) => idx !== i) });
  const addRel = () => {
    const { src, type, dst } = newRel;
    if (src && type && dst && state.nodes.includes(src) && state.nodes.includes(dst)) {
      setState({
        ...state,
        triplets: [...state.triplets, [src, type.toUpperCase().replaceAll(" ", "_"), dst]],
      });
      setNewRel({ src: "", type: "", dst: "" });
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Schema discovery</CardTitle>
        <CardDescription>
          The LLM samples your files and proposes node labels + relationship types. Review and edit
          before extraction — these constrain what gets pulled into the graph.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <div className="space-y-2">
            <Label>Sample size</Label>
            <Input
              type="number"
              min={1}
              max={50}
              value={sampleSize}
              onChange={(e) => setSampleSize(Math.max(1, parseInt(e.target.value || "1", 10)))}
            />
          </div>
          <div className="md:col-span-2 space-y-2">
            <Label>Extra guidance (optional)</Label>
            <Input
              placeholder="e.g. emphasize cardiology, include drug-interaction edges"
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
            />
          </div>
        </div>

        <Button onClick={discover} disabled={loading}>
          <Sparkles className={loading ? "h-4 w-4 animate-pulse" : "h-4 w-4"} />
          {loading ? "Discovering…" : "Discover schema"}
        </Button>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {result && (
          <p className="text-sm text-muted-foreground">
            Sampled {result.sampled_files.length} of {result.file_count} files.
          </p>
        )}

        {state.nodes.length > 0 && (
          <div className="space-y-2">
            <Label>Node labels ({state.nodes.length})</Label>
            <div className="flex flex-wrap gap-2 rounded-md border border-border p-3">
              {state.nodes.map((n) => (
                <Badge key={n} variant="secondary" className="gap-1 pr-1">
                  {n}
                  <button
                    aria-label={`remove ${n}`}
                    className="ml-1 rounded-full p-0.5 hover:bg-destructive hover:text-destructive-foreground"
                    onClick={() => removeNode(n)}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
              <div className="flex items-center gap-1">
                <Input
                  className="h-7 w-32"
                  placeholder="add label"
                  value={newNode}
                  onChange={(e) => setNewNode(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addNode()}
                />
                <Button size="sm" variant="ghost" onClick={addNode}>
                  <Plus className="h-3 w-3" />
                </Button>
              </div>
            </div>
          </div>
        )}

        {state.triplets.length > 0 && (
          <div className="space-y-2">
            <Label>Relationship triplets ({state.triplets.length})</Label>
            <div className="space-y-1 rounded-md border border-border p-3 max-h-80 overflow-auto">
              {state.triplets.map((t, i) => (
                <div key={i} className="flex items-center gap-2 text-sm">
                  <Badge variant="outline">{t[0]}</Badge>
                  <span className="font-mono text-xs text-primary">— {t[1]} →</span>
                  <Badge variant="outline">{t[2]}</Badge>
                  <button
                    onClick={() => removeRel(i)}
                    className="ml-auto rounded p-1 text-muted-foreground hover:bg-destructive hover:text-destructive-foreground"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Input
                className="h-9 w-36"
                placeholder="Source"
                list="node-list"
                value={newRel.src}
                onChange={(e) => setNewRel({ ...newRel, src: e.target.value })}
              />
              <Input
                className="h-9 w-44"
                placeholder="REL_TYPE"
                value={newRel.type}
                onChange={(e) => setNewRel({ ...newRel, type: e.target.value })}
              />
              <Input
                className="h-9 w-36"
                placeholder="Target"
                list="node-list"
                value={newRel.dst}
                onChange={(e) => setNewRel({ ...newRel, dst: e.target.value })}
              />
              <Button size="sm" variant="secondary" onClick={addRel}>
                <Plus className="h-3 w-3" /> Add
              </Button>
              <datalist id="node-list">
                {state.nodes.map((n) => (
                  <option key={n} value={n} />
                ))}
              </datalist>
            </div>
          </div>
        )}

        <div className="space-y-2">
          <Label>Extraction guidance (sent with every chunk)</Label>
          <Textarea
            rows={3}
            value={state.extra}
            onChange={(e) => setState({ ...state, extra: e.target.value })}
            placeholder="Optional. e.g. 'Capture dosing properties on Drug-TREATS->Disease relationships.'"
          />
        </div>

        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack}>Back</Button>
          <Button onClick={onNext} disabled={state.nodes.length === 0}>Continue</Button>
        </div>
      </CardContent>
    </Card>
  );
}
