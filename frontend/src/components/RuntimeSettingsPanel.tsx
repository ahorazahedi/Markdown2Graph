import { useEffect, useMemo, useState } from "react";
import { Save, RotateCcw, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { api, RuntimeSettingSpec } from "@/lib/api";
import { useUnsavedGuard } from "@/lib/unsavedGuard";

/**
 * Pipeline knobs persisted in app_settings table. Reads from /api/runtime,
 * writes via PUT /api/runtime. Distinct from connection-config /api/settings.
 */
export function RuntimeSettingsPanel() {
  const [items, setItems] = useState<RuntimeSettingSpec[]>([]);
  const [values, setValues] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    const r = await api.listRuntime();
    setItems(r.items);
    const initial: Record<string, any> = {};
    for (const s of r.items) initial[s.key] = s.value;
    setValues(initial);
  };
  useEffect(() => { refresh(); }, []);

  const dirty = useMemo(() => {
    return items.some((s) => values[s.key] !== s.value);
  }, [items, values]);
  useUnsavedGuard(dirty);

  const groups = useMemo(() => {
    const m: Record<string, RuntimeSettingSpec[]> = {};
    for (const s of items) (m[s.group] ||= []).push(s);
    return m;
  }, [items]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      // only send dirty fields
      const body: Record<string, any> = {};
      for (const s of items) {
        if (values[s.key] !== s.value) body[s.key] = values[s.key];
      }
      const r = await api.putRuntime(body);
      setItems(r.items);
      const reset: Record<string, any> = {};
      for (const s of r.items) reset[s.key] = s.value;
      setValues(reset);
      setSavedAt(Date.now());
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  const resetField = (s: RuntimeSettingSpec) => {
    setValues((v) => ({ ...v, [s.key]: s.default }));
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex-row items-start justify-between">
          <div>
            <CardTitle>Pipeline tunables</CardTitle>
            <CardDescription>
              Live-tunable knobs for the extraction pipeline. Saved to the app database; applied to subsequent ingest runs without a restart.
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            {savedAt && Date.now() - savedAt < 4000 && (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <Check className="h-3 w-3" /> saved
              </span>
            )}
            <Button size="sm" onClick={save} disabled={!dirty || saving}>
              <Save className="h-3.5 w-3.5" /> {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </CardHeader>

        {Object.entries(groups).map(([group, specs]) => (
          <CardContent key={group} className="space-y-4 border-t border-border">
            <h3 className="text-2xs uppercase tracking-wider text-muted-foreground">
              {group}
            </h3>
            {specs.map((s) => (
              <div key={s.key} className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_220px]">
                <div className="space-y-1">
                  <Label htmlFor={`rt-${s.key}`} className="flex items-center gap-2">
                    {s.label}
                    {values[s.key] !== s.default && (
                      <Badge variant="warning" className="text-2xs">overridden</Badge>
                    )}
                  </Label>
                  <p className="text-xs text-muted-foreground leading-snug">{s.description}</p>
                  <div className="text-2xs text-muted-foreground">
                    default <code className="rounded-sm bg-muted px-1">{String(s.default)}</code>
                    {s.min != null && <> · min <code className="rounded-sm bg-muted px-1">{s.min}</code></>}
                    {s.max != null && <> · max <code className="rounded-sm bg-muted px-1">{s.max}</code></>}
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <FieldEditor spec={s}
                               value={values[s.key]}
                               onChange={(v) => setValues((vs) => ({ ...vs, [s.key]: v }))} />
                  <Button size="sm" variant="ghost"
                          title="Reset to default"
                          onClick={() => resetField(s)}
                          disabled={values[s.key] === s.default}>
                    <RotateCcw className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        ))}
      </Card>

      {error && (
        <div className="rounded-sm border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</div>
      )}
    </div>
  );
}

function FieldEditor({
  spec, value, onChange,
}: {
  spec: RuntimeSettingSpec; value: any; onChange: (v: any) => void;
}) {
  if (spec.kind === "bool") {
    return (
      <label className="flex h-8 items-center gap-2 text-sm">
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
        <span className="text-muted-foreground">{value ? "enabled" : "disabled"}</span>
      </label>
    );
  }
  if (spec.kind === "int" || spec.kind === "float") {
    return (
      <Input
        id={`rt-${spec.key}`}
        type="number"
        className="w-32 tabular-nums"
        step={spec.kind === "float" ? "0.1" : "1"}
        min={spec.min ?? undefined}
        max={spec.max ?? undefined}
        value={value ?? ""}
        onChange={(e) =>
          onChange(spec.kind === "int" ? parseInt(e.target.value || "0", 10)
                                       : parseFloat(e.target.value || "0"))
        }
      />
    );
  }
  return (
    <Input id={`rt-${spec.key}`} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />
  );
}
