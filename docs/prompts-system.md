# Prompts System Reference

Editable Jinja2 prompt templates with on-disk defaults, DB-backed user edits, presets, and live preview. One spec list registers every prompt the app uses.

**Files:**
- `backend/app/services/prompt_store.py`
- `backend/app/api/prompts_api.py`
- `backend/app/prompts/*.md` (defaults)
- `backend/app/prompts/templates/<preset>/*.md` (presets)
- `frontend/src/pages/PromptsPage.tsx`

---

## 1. PromptSpec + SPECS

```python
@dataclass(frozen=True)
class PromptSpec:
    key: str
    filename: str
    description: str
    variables: tuple[dict, ...]   # [{name, description, sample}]

SPECS: tuple[PromptSpec, ...] = (
    PromptSpec("schema_discovery_system", "schema_discovery_system.md", "System prompt for schema discovery", (
        {"name": "extra_instructions", "description": "Optional user guidance.",
         "sample": "Focus on cardiology entities..."},
    )),
    PromptSpec("entity_extraction_instructions", "entity_extraction_instructions.md",
               "Appended to LangChain's LLMGraphTransformer", (
        {"name": "allowed_nodes", "description": "List of node labels.",
         "sample": ["Disease", "Drug", "Symptom"]},
        {"name": "allowed_relationships", "description": "List of [src, REL, dst] triplets.",
         "sample": [["Drug", "TREATS", "Disease"]]},
        {"name": "extra_instructions", "description": "Optional guidance.",
         "sample": "Capture dosing as a property..."},
    )),
    PromptSpec("graph_cleanup_system", "graph_cleanup_system.md",
               "Canonicalize node labels + relationship types", ()),
    PromptSpec("chat_system", "chat_system.md",
               "RAG answer generation", (
        {"name": "context", "description": "Retrieved chunks/entities.", "sample": "..."},
        {"name": "question", "description": "Possibly-rewritten user question.", "sample": "..."},
    )),
    PromptSpec("chat_question_rewrite", "chat_question_rewrite.md",
               "History-aware question rewriter", (
        {"name": "history", "description": "Recent chat turns.", "sample": "user: ...\nassistant: ..."},
        {"name": "question", "description": "Latest user message.", "sample": "what about side effects?"},
    )),
    PromptSpec("community_summary_system", "community_summary_system.md",
               "Generate community summaries", ()),
)

_SPEC_BY_KEY = {s.key: s for s in SPECS}
```

---

## 2. PromptStore

```python
class PromptStore:
    def __init__(self, state: AppStateRepository | None = None):
        self.state = state or AppStateRepository()
        self.env = Environment(
            keep_trailing_newline=True,
            undefined=StrictUndefined,        # validation env
            autoescape=False,
        )
        self._seed_if_missing()               # copy disk defaults to DB on first boot

    # Read
    def list(self) -> list[dict]:             # all rows + filename
    def get(self, key: str) -> dict | None:   # row + default_template for diff/reset

    # Render
    def render(self, key: str, **vars) -> str:
        row = self.state.get_prompt(key)
        if not row: raise KeyError(f"prompt {key!r} not found")
        return self._render_text(row["template"], vars)

    def preview(self, template: str, vars) -> str:
        return self._render_text(template, vars)

    def _render_text(self, template: str, vars) -> str:
        # Lenient env (undefined → empty) so live preview never crashes
        env = Environment(keep_trailing_newline=True, autoescape=False)
        return env.from_string(template).render(**dict(vars))

    # Mutate
    def save(self, key: str, template: str) -> dict | None:
        self._validate(template)              # Jinja2 parse-only
        return self.state.save_prompt(key, template)

    def reset(self, key: str) -> dict | None:
        spec = _SPEC_BY_KEY.get(key)
        disk = _PROMPTS_DIR / spec.filename
        return self.state.reset_prompt(key, disk.read_text(encoding="utf-8"))

    # Presets
    def list_presets(self) -> list[dict]:     # discover non-underscore subdirs
    def preset_prompts(self, name: str) -> dict[str, str]:
    def apply_preset(self, name: str) -> dict:
        prompts = self.preset_prompts(name)
        for key, template in prompts.items():
            self._validate(template)
            self.state.save_prompt(key, template)
        return {"preset": name, "applied": list(prompts.keys())}

    def _validate(self, template: str) -> None:
        try:
            self.env.parse(template)
        except TemplateSyntaxError as e:
            raise ValueError(f"Jinja template syntax error: {e}")
```

**Why two environments?** `_render_text` uses lenient undefined (empty string) so previews don't crash on missing vars; `_validate` uses `StrictUndefined` only to parse syntax (it never renders). Save just needs syntax validity.

---

## 3. Database schema

```sql
CREATE TABLE prompts (
    key          TEXT PRIMARY KEY,
    template     TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    variables    TEXT NOT NULL DEFAULT '[]',    -- JSON: [{name, description, sample}]
    is_custom    INTEGER NOT NULL DEFAULT 0,    -- 1 once user has edited
    default_hash TEXT,                          -- sha1(disk default at seed time)
    updated_at   TEXT NOT NULL
);
```

`is_custom` flips to 1 on `save_prompt` and back to 0 on `reset_prompt`. `default_hash` lets the UI tell "this row diverged" even without comparing full text.

---

## 4. Presets (general, medical)

Directory layout:

```
backend/app/prompts/
  schema_discovery_system.md          # canonical default
  entity_extraction_instructions.md
  graph_cleanup_system.md
  chat_system.md
  chat_question_rewrite.md
  community_summary_system.md
  templates/
    general/
      <same six filenames>
    medical/
      <same six filenames>
```

`list_presets()` skips names starting with `_`. Per-preset coverage is reported:

```json
{
  "items": [
    {"name": "general", "prompts": [{"key": "chat_system", "has_template": true}, ...]},
    {"name": "medical", "prompts": [...]}
  ]
}
```

`apply_preset("medical")` iterates each file and calls `save_prompt(key, content)` — all touched prompts flip to `is_custom=1`.

---

## 5. HTTP endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/prompts` | List all |
| `GET /api/prompts/<key>` | Single row + `default_template` |
| `PUT /api/prompts/<key>` | Body `{template}`. Validates + persists. |
| `POST /api/prompts/<key>/reset` | Restore disk default, clear `is_custom` |
| `POST /api/prompts/<key>/preview` | Body `{template?, vars}`. Returns `{rendered}` — no DB write. |
| `GET /api/prompts/presets` | List discovered presets |
| `POST /api/prompts/presets/<name>/apply` | Apply preset (mass overwrite) |

---

## 6. Frontend editor (`PromptsPage.tsx`)

Layout: sidebar (prompt list) + editor + variable inputs + preview output.

State:
```ts
items: PromptRow[]; activeKey: string | null;
template: string; vars: Record<string, string>;
preview: string; busy: boolean; savedAt: number | null;
error: string | null;
presets: { name: string }[]; presetChoice: string;
```

Features:
- **List sidebar** — shows each spec with custom/default badge.
- **Editor** — `Textarea` with monospace font, dirty detection via `useUnsavedGuard(template !== active.template)`.
- **Variable form** — auto-builds inputs from `spec.variables`. JSON arrays/objects parsed lazily so users can paste a sample as JSON.
- **Preview button** — `POST /api/prompts/<key>/preview` with current template + vars, renders output in `<pre>`.
- **Save** — `PUT /api/prompts/<key>`. Server validates Jinja syntax; errors surface as red banner.
- **Reset to default** — destructive confirm (`confirm({variant: "destructive"})`) before `POST .../reset`.
- **Apply preset** — destructive confirm; on success, force-reload editor by clearing then re-setting `activeKey`.

---

## 7. Callsites

| Where | Prompt key |
|---|---|
| `services/schema_discovery.py` | `schema_discovery_system` |
| `services/entity_extractor.py` | `entity_extraction_instructions` (appended to LangChain transformer) |
| `services/post_processing.py` | `graph_cleanup_system`, `community_summary_system` |
| `services/chat_service.py` | `chat_question_rewrite`, `chat_system` |

Always call via `PromptStore().render(key, **vars)`. Never inline a hardcoded prompt string in service code — defeats the editor.

---

## 8. Adding a new prompt

1. Drop `your_prompt.md` in `backend/app/prompts/`.
2. Append a `PromptSpec` to `SPECS` with the key, filename, description, and expected vars.
3. Restart — `_seed_if_missing()` loads it into the DB.
4. Use it: `PromptStore().render("your_prompt", **vars)`.
5. (Optional) Drop the same filename into each preset dir so users can swap.
