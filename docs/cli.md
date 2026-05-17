# CLI Reference

Flask CLI commands for operations outside the UI — discovery, ingest, stats, embeddings lifecycle.

**File:** `backend/app/cli.py`

Run via:
```bash
python -m app.cli <command> [args]
# or
flask --app app:create_app <command> [args]
```

---

## Commands

### `health`

Connectivity probe.

```bash
python -m app.cli health
```

Verifies Neo4j driver connection and prints LLM configuration. Exit code `0` ok, `1` failure.

---

### `discover <folder>`

Propose schema from a sample of `.md` files. Does not save.

```bash
python -m app.cli discover ./articles_md_sample \
  --sample-size 5 \
  --extra "Focus on cardiology terms" \
  --out schema.json
```

Options:
- `--sample-size N` — files to sample (default from `schema_discovery_sample_size`)
- `--extra TEXT` — user guidance for the LLM
- `--out PATH` — write JSON result here

Output: Rich table of proposed node labels + triplets; optional JSON file.

Calls `SchemaDiscoveryService().discover(...)`. See [Schema Discovery](./schema-discovery.md).

---

### `ingest <folder>`

Run the full ingestion pipeline on a folder of `.md` files.

```bash
python -m app.cli ingest ./articles_md_sample \
  --schema-file schema.json \
  --workers 4
```

Options:
- `--node LABEL` — allowed node label (repeatable)
- `--rel SRC TYPE TGT` — allowed relationship triplet (repeatable)
- `--schema-file PATH` — load `--node`/`--rel` from JSON produced by `discover --out`
- `--extra TEXT` — extra instructions for extraction LLM
- `--workers N` — override `ingest_concurrency`

Output: JSON totals + post-processing summary.

Calls `IngestionPipeline(cfg).run(...)` with a CLI progress printer. See [Pipeline](./pipeline.md).

---

### `stats`

Print graph counts.

```bash
python -m app.cli stats
```

Output: `GraphRepository().stats()` as JSON (documents, chunks, entities, relationships, …).

---

### `clear --yes`

**Destructive.** Delete every node and relationship in the Neo4j database.

```bash
python -m app.cli clear --yes
```

Requires explicit `--yes` flag (or interactive confirmation). Use only on dev/test instances.

---

### `embeddings-status`

Per-type embedding coverage.

```bash
python -m app.cli embeddings-status
```

Output: current model + dim + provider, then per node type (chunk/entity/community): total, embedded, missing, stale, by-model breakdown.

Calls `EmbeddingService().status()`. See [Embeddings](./embeddings.md).

---

### `reembed`

Re-embed nodes under a scope.

```bash
python -m app.cli reembed \
  --scope missing \
  --type entity \
  --model openai/text-embedding-3-small \
  --dim 1536
```

Options:
- `--scope {missing,stale,all}` — default `missing`
- `--type {chunk,entity,community}` — repeatable; default all
- `--model NAME` — override embedding model
- `--dim N` — override dimension
- `--clear-first` — null out existing embeddings before re-embed (auto-true if `--scope all`)

Output: per-type counts (cleared, embedded, errors).

---

### `switch-embedding-model`

**Destructive.** Persist new model + dim, clear all embeddings, re-embed everything.

```bash
python -m app.cli switch-embedding-model \
  --model openai/text-embedding-3-large \
  --dim 3072 \
  --yes
```

Required: `--model`, `--dim`. Optional: `--provider {openrouter,openai,local,lm-studio}`, `--yes` to skip confirmation.

Calls `EmbeddingService().switch_model(...)`. Persists to `app_settings`, calls `reload_settings()`, then runs `reembed(scope="all", clear_first=True)`.

---

## Bootstrap

All commands run through `_bootstrap()`:
1. `get_settings()`
2. `init_logging(level)`
3. `neo4j_manager.configure(settings)`
4. Return settings

Same as `create_app()` minus Flask. Means CLI sees the same env + runtime overrides as the HTTP server.

---

## Adding a new command

```python
# backend/app/cli.py
import click

@cli.command()
@click.argument("name")
@click.option("--verbose", is_flag=True)
def mycommand(name: str, verbose: bool):
    """One-line description shown by --help."""
    settings = _bootstrap()
    # ... do work ...
    click.echo("done")
```

Auto-registered via the `cli` Click group at module bottom.
