# Documents + Ingest Surface Reference

Upload (file or folder, drag-drop or picker) → MarkdownLoader → MarkdownChunker → registered in `documents` table → triggers ingest pipeline.

**Files:**
- `backend/app/api/upload.py` (legacy staging)
- `backend/app/api/documents.py` (recommended endpoints)
- `backend/app/services/markdown_loader.py`
- `backend/app/services/chunker.py`
- `frontend/src/pages/DocumentsPage.tsx`

---

## 1. Upload endpoints

### `POST /api/documents/upload` (recommended)

Stages files, registers each in DB, returns counts. Accepts files **or** a folder tree.

**Form fields:**
- `files[]` — File objects
- `paths[]` — Relative paths (preserves folder structure); defaults to filename
- Allowed suffixes: `.md`, `.markdown` (case-insensitive)
- Limits: 5000 files, 500 MB total

**Response:**
```json
{
  "staging": "/.../backend/data/uploads/<uuid>",
  "created": [
    {"id": 123, "file_name": "doc.md", "title": "Doc Title", "size_bytes": 5000}
  ],
  "skipped": [{"path": "readme.txt", "reason": "not markdown"}],
  "bytes": 5000
}
```

### `POST /api/upload` (legacy)

Stages files only, doesn't register. Returns `{path, file_count, bytes, files: [...]}`. Kept for older CLI flows.

### `DELETE /api/upload`

Clears all staged uploads (dev helper).

---

## 2. Document lifecycle endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/documents` | `{items: [DocumentRow], stats: {total, completed, pending, processing, failed, chunks, entities, relationships}}` |
| `GET /api/documents/<id>` | Single row |
| `GET /api/documents/<id>/content` | `{file_name, content}` (markdown body) |
| `GET /api/documents/<id>/chunks` | `{chunks: [{id, position, text, length}, ...]}` |
| `GET /api/documents/<id>/entities` | `{nodes: [...], relationships: [...]}` — extracted graph |
| `DELETE /api/documents/<id>` | Removes from DB + Neo4j (Document + chunks + entities) |
| `POST /api/documents/<id>/reextract` | Async pipeline run with `reextract=True`. Returns `{job_id, document_id}`. |

---

## 3. MarkdownLoader

```python
@dataclass
class MarkdownDoc:
    path: Path
    file_name: str
    text: str
    sha1: str
    title: str | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def length(self) -> int:
        return len(self.text)

class MarkdownLoader:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def list_files(self) -> List[Path]:
        return sorted(self.root.rglob("*.md")) + sorted(self.root.rglob("*.markdown"))

    def load_one(self, path: Path) -> MarkdownDoc:
        raw = path.read_bytes()
        sha1 = hashlib.sha1(raw).hexdigest()
        text = raw.decode("utf-8", errors="replace")
        meta, body = self._strip_frontmatter(text)
        title = meta.get("title") or self._first_h1(body)
        return MarkdownDoc(path=path, file_name=path.name,
                           text=body, sha1=sha1, title=title, metadata=meta)

    def load_many(self, paths: Iterable[Path]) -> List[MarkdownDoc]:
        return [self.load_one(p) for p in paths]
```

**Frontmatter** parsed by simple regex `^---\n(.*?)\n---\n` for YAML-ish `key: value`. Strips leading/trailing quotes.

**Title** preference: `metadata["title"]` → first `# Heading` → `None`.

**SHA-1** computed over raw bytes — used for dedup.

---

## 4. MarkdownChunker

```python
@dataclass
class Chunk:
    id: str            # sha1(text) — stability key
    text: str
    position: int      # 1-based
    length: int        # char count
    content_offset: int
    file_name: str

    def to_lc_document(self) -> Document:
        return Document(page_content=self.text, metadata={
            "chunkId": self.id,
            "fileName": self.file_name,
            "position": self.position,
        })

class MarkdownChunker:
    def __init__(self, chunk_size=None, chunk_overlap=None):
        rs = SettingsService()
        self.chunk_size    = chunk_size    or int(rs.get("chunk_token_size"))      # default 600
        self.chunk_overlap = min(chunk_overlap or int(rs.get("chunk_overlap")),    # default 80
                                 self.chunk_size // 4)                              # safety cap
        self.max_total     = get_settings().max_token_chunk_size                   # 20000

    def split(self, file_name: str, text: str) -> List[Chunk]:
        splitter = TokenTextSplitter(chunk_size=self.chunk_size,
                                     chunk_overlap=self.chunk_overlap)
        pieces = splitter.split_text(text)
        if len(pieces) > self.max_total:
            pieces = pieces[:self.max_total]
        chunks: list[Chunk] = []
        offset = 0
        for i, piece in enumerate(pieces, start=1):
            cid = hashlib.sha1(piece.encode("utf-8")).hexdigest()
            chunks.append(Chunk(id=cid, text=piece, position=i, length=len(piece),
                                content_offset=offset, file_name=file_name))
            offset += len(piece) - self.chunk_overlap     # approximate
        return chunks
```

- Token splitter (tiktoken-based).
- Overlap capped at 25% of chunk size (corruption guard).
- `id = sha1(text)` — same text → same id across runs → MERGE idempotent.

---

## 5. Document storage

**Staging on disk:** `backend/data/uploads/<uuid>/<relative_path>/<file.md>`

**Registry in SQLite** (`documents` table — see [Data Model](./data-model.md#23-documents)):

Statuses:
- `pending` — uploaded, awaiting ingest
- `processing` — ingest in flight (`last_job_id` set)
- `completed` — counts populated
- `failed` — `error` set

**Name collisions** handled by `_unique_name`:

```python
def _unique_name(file_name: str, state: AppStateRepository) -> str:
    base = file_name
    if not state.get_document_by_name(base): return base
    stem, suffix = Path(base).stem, Path(base).suffix
    for i in range(1, 1000):
        candidate = f"{stem}__{i}{suffix}"
        if not state.get_document_by_name(candidate): return candidate
    return f"{stem}__{uuid.uuid4().hex[:8]}{suffix}"
```

---

## 6. Frontend (`DocumentsPage.tsx`)

### State

```ts
docs: DocumentRow[]; stats: DocumentStats | null;
busy: boolean; open: DocumentRow | null;
dragOver: boolean; uploading: boolean; error: string | null;
```

### Drag-drop

```typescript
const onDrop = async (e: DragEvent) => {
  e.preventDefault();
  setDragOver(false);
  const entries = await collectEntries(e.dataTransfer);   // walks dirs via webkitEntries
  const md = entries.filter((x) => /\.(md|markdown)$/i.test(x.file.name));
  await upload(md);
};
```

`collectEntries` walks folder entries recursively via `entry.createReader()` so dropping a folder preserves its tree.

### Pickers

- **File picker**: `<input type="file" multiple accept=".md,.markdown" />`
- **Folder picker**: `<input type="file" webkitdirectory />` — paths preserved in `file.webkitRelativePath`.

### Upload

```typescript
const upload = async (entries: { file: File; relPath: string }[]) => {
  setUploading(true);
  try {
    const r = await api.uploadDocuments(entries);
    await refresh();
  } catch (e) { setError(String(e)); }
  finally { setUploading(false); }
};
```

`api.uploadDocuments` constructs FormData with `files[]` + matching `paths[]` and POSTs to `/api/documents/upload`.

### Table

Columns: file_name, status badge, chunk_count, entity_count, relationship_count, updated_at. Row click → drawer.

### Drawer

```tsx
<Drawer open={!!open} onClose={() => setOpen(null)} title={open?.file_name} width="max-w-4xl">
  <Tabs defaultValue="content">
    <TabsList>
      <TabsTrigger value="content">Content</TabsTrigger>
      <TabsTrigger value="entities">Entities</TabsTrigger>
      <TabsTrigger value="relationships">Relationships</TabsTrigger>
      <TabsTrigger value="chunks">Chunks</TabsTrigger>
    </TabsList>
    <TabsContent value="content"><ReactMarkdown>{content}</ReactMarkdown></TabsContent>
    {/* entities/relationships tables; chunks collapsible */}
  </Tabs>
</Drawer>
```

Content lazy-loaded on tab activation.

### Live status

Polls `GET /api/documents` every 2s while any row is `pending` or `processing`. Stops when all settle.

---

## 7. Integration with pipeline

- After upload, user navigates to Ingest page.
- `POST /api/ingest/run` with `document_ids: [...]` (or omit for `run_pending`).
- See [Pipeline](./pipeline.md) for execution flow.
- See [Job System](./job-system.md) for tracking.

---

## 8. Configuration

```python
chunk_token_size:        int = 600        # runtime setting (per-instance override)
chunk_overlap:           int = 80         # runtime setting
chunks_to_combine:       int = 1          # windows merged per LLM call
max_token_chunk_size:    int = 20000      # soft cap per document (drops trailing chunks)
```
