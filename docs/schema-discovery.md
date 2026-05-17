# Schema Discovery Reference

LLM-driven proposal of node labels and relationship triplets from sample documents. User reviews, edits, and commits. Version-tracked.

**Files:**
- `backend/app/services/schema_discovery.py`
- `backend/app/api/schema.py`
- `backend/app/prompts/schema_discovery_system.md`
- `frontend/src/pages/SchemaPage.tsx`

---

## 1. Service

```python
class SchemaDiscoveryService:
    def discover(self, files: List[Path], sample_size=None, extra_instructions=None) -> dict:
        # 1. Sample
        size = sample_size or self.settings.schema_discovery_sample_size
        size = min(size, len(files))
        sample = random.sample(files, size) if size < len(files) else files

        # 2. Load + truncate within char budget
        loader = MarkdownLoader(files[0].parent)
        docs = loader.load_many(sample)
        budget = self.settings.schema_discovery_max_chars
        per_doc = max(500, budget // max(1, len(docs)))
        excerpts = [f"## File: {d.file_name}\n\n{d.text[:per_doc].strip()}" for d in docs]
        joined = "\n\n---\n\n".join(excerpts)

        # 3. Render system prompt
        sys_prompt = PromptStore().render(
            "schema_discovery_system",
            extra_instructions=(extra_instructions or "").strip(),
        )

        # 4. Invoke LLM
        llm = build_chat_llm()
        with with_tag("schema_discovery"):
            resp = llm.invoke([SystemMessage(content=sys_prompt),
                               HumanMessage(content=joined)])
        raw = resp.content if hasattr(resp, "content") else str(resp)

        # 5. Parse + validate
        parsed = self._parse_json(raw)
        node_labels = self._dedup(parsed.get("node_labels") or parsed.get("nodes") or [])
        triplets    = self._dedup(parsed.get("triplets") or [])

        valid: list[str] = []
        label_set = set(node_labels)
        for t in triplets:
            try:
                src, mid = t.split("-", 1)
                rel, dst = mid.split("->", 1)
                src, rel, dst = src.strip(), rel.strip(), dst.strip()
                if src in label_set and dst in label_set and rel:
                    valid.append(f"{src}-{rel}->{dst}")
            except ValueError:
                continue

        return {
            "node_labels": node_labels,
            "triplets": valid,
            "sampled_files": [d.file_name for d in docs],
        }
```

### JSON parsing (robust)

`_parse_json` strips markdown code fences, locates first `{` … balanced `}`, then `json.loads`. Returns `{}` on any parse error — frontend shows "discovered 0 labels" rather than crashing.

### Validation

Triplet format: `Source-REL_TYPE->Target`. Both endpoints **must** appear in `node_labels`; otherwise silently dropped. Hallucinated extra labels can't poison the schema.

---

## 2. Default prompt (`schema_discovery_system.md`)

```markdown
You are a knowledge-engineering expert designing a Neo4j knowledge graph from
unstructured text. Analyse the provided excerpts and propose the **abstract
schema** — the *types* of entities (node labels) and the *types* of
relationships that connect them. Do not return concrete instances.

# Output
Return a JSON object — JSON only, no prose:

```json
{
  "node_labels": ["Person", "Organization", "Location", "..."],
  "triplets": ["Person-WORKS_FOR->Organization", "..."]
}
```

# Rules
1. **Node labels** in PascalCase, singular nouns.
2. **Relationship types** in UPPER_SNAKE_CASE, verbs.
3. Triplet format exactly `<NodeType>-<REL_TYPE>-><NodeType>`. Both endpoints must
   appear in `node_labels`.
4. Drop catch-all types (`Entity`, `Thing`, `Concept`).
5. Dates, numbers, currencies → properties, not nodes.
6. Aim for 8–25 labels and 10–40 relationships.

{% if extra_instructions %}
# User guidance
{{ extra_instructions }}
{% endif %}
```

Editable via [Prompts System](./prompts-system.md).

---

## 3. HTTP endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/schema` | Active schema `{node_labels, triplets, extra, updated_at, updated_by}` |
| `PUT /api/schema` | Save. Body `{node_labels, triplets, extra?, source?}`. Also appends to `schema_versions`. |
| `GET /api/schema/versions` | List version history (most recent first) |
| `GET /api/schema/versions/<id>` | Single version |
| `POST /api/schema/discover` | Propose schema. Body `{path?, document_ids?, sample_size, extra_instructions?}`. **Does not save** — frontend merges and the user commits via PUT. |

Storage: `schemas` (single-row id=1) + `schema_versions` (immutable append-only). See [Data Model](./data-model.md).

---

## 4. Frontend (`SchemaPage.tsx`)

Layout:

```
PageHeader: title, save button (dirty), saved badge
Metrics strip: node count, rel count, last save, "Unsaved" pill
Collapsible "AI assist": sample size + guidance + Discover button
Node labels: badges with usage count, add input, delete on hover
Relationships: table src | type | tgt | delete, add form
Version history: last 12 versions
```

State:
```ts
schema: Schema | null;
nodes: string[]; triplets: Triplet[];
versions: SchemaVersion[]; savedAt: number | null;
discoverOpen: boolean; discoverExtra: string; sampleSize: number;
discovering: boolean; saving: boolean; error: string | null;
```

### Discover (merge, don't replace)

```typescript
const discover = async () => {
  const r = await api.discoverSchema({ sample_size: sampleSize, extra_instructions: discoverExtra || undefined });
  const newNodes = Array.from(new Set([...nodes, ...r.node_labels]));
  const newTriplets: Triplet[] = [...triplets];
  for (const t of r.triplets) {
    const m = /^(.+?)-(.+?)->(.+)$/.exec(t);
    if (!m) continue;
    const tri: Triplet = [m[1].trim(), m[2].trim(), m[3].trim()];
    if (!newTriplets.some(([a, b, c]) => a === tri[0] && b === tri[1] && c === tri[2])) {
      newTriplets.push(tri);
    }
  }
  setNodes(newNodes);
  setTriplets(newTriplets);
};
```

User then reviews + clicks Save (which calls `PUT /api/schema` with `source: "manual"` or `source: "discover"`).

### Cascading delete

Removing a node label removes all triplets using it. Implemented client-side before save.

### Unsaved guard

`useUnsavedGuard(dirty)` — dirty if either array differs from saved schema. See [Hash Routing](./hash-routing.md).

---

## 5. CLI

```bash
python -m app.cli discover ./articles_md_sample \
  --sample-size 5 \
  --extra "Focus on diseases and drug interactions" \
  --out schema.json
```

Outputs proposed schema as Rich table + writes JSON file. Then:

```bash
python -m app.cli ingest ./articles_md_sample --schema-file schema.json
```

CLI does **not** save to DB — it's a sketch tool. Use the UI to commit.

---

## 6. Configuration

```python
schema_discovery_sample_size: int = 5         # files to sample per discover
schema_discovery_max_chars:   int = 12000     # total char budget across all sampled files
```

Budget is split evenly per file (`per_doc = budget // n_files`, floor 500). Beyond that each file is truncated.
