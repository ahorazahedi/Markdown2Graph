# Runtime Settings Reference

User-tunable pipeline knobs persisted in SQLite, edited via a generic panel. Each setting is a typed `SettingSpec` (kind/default/min/max/group) and lives alongside `Settings` Pydantic config. Runtime values override Pydantic defaults.

**Files:**
- `backend/app/services/settings_service.py` (specs + service)
- `backend/app/api/runtime_settings.py` (endpoints)
- `frontend/src/components/RuntimeSettingsPanel.tsx`
- Storage: `app_settings` table in `text2graph.db`

For app-wide LLM/Neo4j config see [Config + Env](./config-env.md).

---

## 1. SettingSpec

```python
@dataclass
class SettingSpec:
    key: str
    kind: Literal["int", "float", "bool", "str"]
    default: Any
    min: float | None = None
    max: float | None = None
    label: str = ""
    description: str = ""
    group: str = "general"
```

---

## 2. Registered specs

```python
SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="extraction_retry_count",
        kind="int", default=2, min=0, max=10,
        label="Extraction retries per chunk",
        description="Additional attempts when entity extraction fails.",
        group="extraction",
    ),
    SettingSpec(
        key="extraction_retry_backoff_seconds",
        kind="float", default=1.5, min=0, max=30,
        label="Initial retry backoff (seconds)",
        description="Delay before the first retry; doubled each subsequent attempt.",
        group="extraction",
    ),
    SettingSpec(
        key="extraction_min_nodes_for_success",
        kind="int", default=0, min=0, max=100,
        label="Min nodes to consider chunk successful",
        description="If LLM returns fewer entities, treated as transient failure.",
        group="extraction",
    ),
    SettingSpec(
        key="chunk_token_size",
        kind="int", default=600, min=50, max=8000,
        label="Chunk size (tokens)",
        description="Target token count per chunk.",
        group="chunking",
    ),
    SettingSpec(
        key="chunk_overlap",
        kind="int", default=80, min=0, max=2000,
        label="Chunk overlap (tokens)",
        description="Token overlap between consecutive chunks.",
        group="chunking",
    ),
    SettingSpec(
        key="chunks_to_combine",
        kind="int", default=1, min=1, max=10,
        label="Chunks to combine per extraction call",
        description="Number of adjacent chunks merged into one LLM extraction call.",
        group="chunking",
    ),
)
```

---

## 3. SettingsService

```python
class SettingsService:
    def __init__(self, state: AppStateRepository | None = None):
        self.state = state or AppStateRepository()

    def get(self, key: str) -> Any:
        """Read raw → coerce to spec.kind. Falls back to default on missing or bad value."""
        spec = _SPEC_BY_KEY.get(key)
        raw = self.state.get_setting(key)
        return _coerce(spec, raw) if raw is not None else spec.default

    def set(self, key: str, value: Any) -> Any:
        spec = _SPEC_BY_KEY[key]
        v = _coerce(spec, value)
        if spec.kind in ("int", "float"):
            if spec.min is not None: v = max(spec.min, v)
            if spec.max is not None: v = min(spec.max, v)
        self.state.set_setting(key, json.dumps(v))
        return v

    def bulk_set(self, updates: dict) -> dict:
        return {k: self.set(k, v) for k, v in updates.items() if k in _SPEC_BY_KEY}

    def specs(self) -> list[dict]:
        return [{"key": s.key, "kind": s.kind, "default": s.default,
                 "value": self.get(s.key), "min": s.min, "max": s.max,
                 "label": s.label, "description": s.description, "group": s.group}
                for s in SPECS]

def _coerce(spec: SettingSpec, raw: Any) -> Any:
    try:
        if spec.kind == "int":   return int(raw)
        if spec.kind == "float": return float(raw)
        if spec.kind == "bool":  return bool(raw)
        return str(raw)
    except Exception:
        return spec.default
```

Callsites read via `SettingsService().get("extraction_retry_count")` — never cached, fresh per call.

---

## 4. HTTP endpoints

| Endpoint | Body | Response |
|---|---|---|
| `GET /api/runtime` | — | `{items: [SettingSpec...]}` |
| `PUT /api/runtime` | `{key: value, ...}` (changed only) | `{updated: {key: coerced_value}, items: [...]}` |

```python
@bp.put("/runtime")
def update_runtime():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not data:
        raise ValidationError("body must be a non-empty object")
    updated = SettingsService().bulk_set(data)
    return jsonify({"updated": updated, "items": SettingsService().specs()})
```

---

## 5. Frontend panel

```tsx
export function RuntimeSettingsPanel() {
  const [items, setItems] = useState<RuntimeSettingSpec[]>([]);
  const [values, setValues] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const dirty = useMemo(
    () => items.some((s) => values[s.key] !== s.value),
    [items, values],
  );
  useUnsavedGuard(dirty);

  // Load
  useEffect(() => {
    api.listRuntime().then((r) => {
      setItems(r.items);
      setValues(Object.fromEntries(r.items.map((s) => [s.key, s.value])));
    });
  }, []);

  const save = async () => {
    const body: Record<string, any> = {};
    for (const s of items) {
      if (values[s.key] !== s.value) body[s.key] = values[s.key];
    }
    setSaving(true);
    try {
      const r = await api.putRuntime(body);
      setItems(r.items);
      setValues(Object.fromEntries(r.items.map((s) => [s.key, s.value])));
      setSavedAt(Date.now());
      window.setTimeout(() => setSavedAt(null), 4000);
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  // Group by spec.group, render field rows
}
```

### Field editors

```tsx
// Bool
<input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />

// Int / Float
<Input type="number" step={spec.kind === "float" ? "0.1" : "1"}
       min={spec.min ?? undefined} max={spec.max ?? undefined}
       value={value ?? ""} className="w-32 tabular-nums"
       onChange={(e) => onChange(
         spec.kind === "int" ? parseInt(e.target.value || "0", 10)
                              : parseFloat(e.target.value || "0"))} />

// String
<Input value={value ?? ""} onChange={(e) => onChange(e.target.value)} />
```

### UX patterns

- **Overridden badge** next to label when `value !== default`.
- **Reset** button per field — sets value to default in state; doesn't persist until Save.
- **Save** disabled until `dirty || saving`.
- **Saved indicator** — 4-second success badge after PUT succeeds.
- **Unsaved guard** — `useUnsavedGuard(dirty)` blocks navigation/refresh with confirm dialog. See [Hash Routing](./hash-routing.md).
- **Min/max** shown in code-block style next to description.

---

## 6. Add a new setting (3 steps)

1. Append a `SettingSpec` to `SPECS` in `settings_service.py`.
2. Read it at the consumer: `value = SettingsService().get("your_key")`.
3. The panel auto-renders the new row in the matching `group`.

No frontend code needed unless you want a non-generic editor (e.g. a dropdown), in which case extend `FieldEditor` switch.
