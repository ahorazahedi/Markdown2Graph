"""text2graph CLI.

Examples
--------
  # 1. Discover schema from a folder of medical .md files
  python -m app.cli discover ./articles_md_sample

  # 2. Ingest with the discovered schema (or your own)
  python -m app.cli ingest ./articles_md_sample \\
      --node Disease --node Drug --node Symptom \\
      --rel Drug TREATS Disease --rel Symptom INDICATES Disease

  # 3. Stats
  python -m app.cli stats

  # 4. Wipe the graph
  python -m app.cli clear --yes
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .config import get_settings
from .extensions import init_logging, neo4j_manager
from .repositories.graph_repository import GraphRepository
from .services.markdown_loader import MarkdownLoader
from .services.pipeline import IngestionPipeline, PipelineConfig
from .services.schema_discovery import SchemaDiscoveryService

console = Console()


def _bootstrap():
    s = get_settings()
    init_logging(s.log_level)
    neo4j_manager.configure(s)
    return s


@click.group()
def cli():
    """text2graph — markdown → Neo4j knowledge graph (medical domain)."""


@cli.command()
def health():
    """Check connectivity to Neo4j + LLM config."""
    s = _bootstrap()
    ok = neo4j_manager.verify()
    console.print(f"Neo4j   : [{'green' if ok else 'red'}]{'up' if ok else 'down'}[/]  ({s.neo4j_uri})")
    console.print(f"LLM     : {s.llm_model}  via {s.effective_llm_base_url}  "
                  f"[{'green' if s.effective_llm_api_key else 'red'}]"
                  f"{'configured' if s.effective_llm_api_key else 'NO API KEY'}[/]")
    sys.exit(0 if ok else 1)


@cli.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--sample-size", type=int, default=None, help="Files to sample for schema discovery")
@click.option("--extra", "extra_instructions", default=None, help="Extra guidance for the LLM")
@click.option("--out", type=click.Path(path_type=Path), default=None, help="Write JSON result to this path")
def discover(folder: Path, sample_size: int | None, extra_instructions: str | None, out: Path | None):
    """Discover the node/relationship schema from a folder of .md files."""
    _bootstrap()
    files = MarkdownLoader(folder).list_files()
    if not files:
        console.print(f"[red]No .md files in {folder}[/]")
        sys.exit(2)
    console.print(f"[cyan]Discovering schema from {len(files)} files…[/]")
    result = SchemaDiscoveryService().discover(files, sample_size=sample_size, extra_instructions=extra_instructions)

    table = Table(title="Proposed node labels", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Label", style="bold cyan")
    for i, n in enumerate(result["node_labels"], 1):
        table.add_row(str(i), n)
    console.print(table)

    table2 = Table(title="Proposed relationship triplets")
    table2.add_column("#", justify="right", style="dim")
    table2.add_column("Source", style="cyan")
    table2.add_column("Relationship", style="magenta")
    table2.add_column("Target", style="cyan")
    for i, t in enumerate(result["triplets"], 1):
        try:
            src, mid = t.split("-", 1)
            rel, dst = mid.split("->", 1)
            table2.add_row(str(i), src, rel, dst)
        except ValueError:
            table2.add_row(str(i), t, "", "")
    console.print(table2)

    if out:
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        console.print(f"[green]wrote {out}[/]")


@cli.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--node", "nodes", multiple=True, help="Allowed node label (repeatable)")
@click.option("--rel", "rels", nargs=3, multiple=True, help="Allowed relationship as: SOURCE TYPE TARGET")
@click.option("--schema-file", type=click.Path(exists=True, path_type=Path), default=None,
              help="Path to a JSON file produced by `discover --out`")
@click.option("--extra", "extra_instructions", default=None)
@click.option("--workers", type=int, default=None)
def ingest(folder: Path, nodes: tuple, rels: tuple, schema_file: Path | None,
           extra_instructions: str | None, workers: int | None):
    """Ingest a folder of .md files into Neo4j."""
    _bootstrap()
    files = MarkdownLoader(folder).list_files()
    if not files:
        console.print(f"[red]No .md files in {folder}[/]")
        sys.exit(2)

    allowed_nodes = list(nodes)
    allowed_rels = [tuple(r) for r in rels]

    if schema_file:
        data = json.loads(schema_file.read_text(encoding="utf-8"))
        allowed_nodes = list({*allowed_nodes, *(data.get("node_labels") or [])})
        for t in data.get("triplets") or []:
            try:
                src, mid = t.split("-", 1)
                rel, dst = mid.split("->", 1)
                allowed_rels.append((src.strip(), rel.strip(), dst.strip()))
            except ValueError:
                continue

    cfg = PipelineConfig(
        allowed_nodes=allowed_nodes,
        allowed_relationships=allowed_rels,
        extra_instructions=extra_instructions,
        max_workers=workers,
    )
    console.print(f"[cyan]Ingesting {len(files)} files…[/]  nodes={len(allowed_nodes)}  rels={len(allowed_rels)}")

    def _progress(u):
        console.print(f"  [{u.progress * 100:5.1f}%] {u.stage}: {u.message}")

    result = IngestionPipeline(cfg).run(files, progress=_progress)
    console.print_json(data=result.get("totals", {}))
    console.print(f"[green]✓ ingestion complete[/]  post={result.get('post_processing')}")


@cli.command()
def stats():
    """Print graph counts."""
    _bootstrap()
    console.print_json(data=GraphRepository().stats())


@cli.command()
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def clear(yes: bool):
    """DANGER: delete every node and relationship in the database."""
    _bootstrap()
    if not yes and not click.confirm("Really delete the entire graph?", default=False):
        console.print("aborted")
        sys.exit(1)
    GraphRepository().clear_all()
    console.print("[green]graph cleared[/]")


@cli.command("embeddings-status")
def embeddings_status():
    """Show embedding coverage per node type + by-model breakdown."""
    _bootstrap()
    from .services.embedding_service import EmbeddingService
    snap = EmbeddingService().status()
    console.print(f"[bold]current model[/]: {snap['current_model']}  "
                  f"dim={snap['current_dim']}  provider={snap['provider']}")
    for nt, s in snap["types"].items():
        if "error" in s:
            console.print(f"[red]{nt}: {s['error']}[/]")
            continue
        console.print(
            f"[cyan]{nt}[/]: total={s['total']}  embedded={s['embedded']}  "
            f"missing={s.get('missing', 0)}  stale={s.get('stale', 0)}  "
            f"index_dim={s.get('index_dim')}"
        )
        for m, c in (s.get("by_model") or {}).items():
            console.print(f"    {m}: {c}")


@cli.command()
@click.option("--scope", type=click.Choice(["missing", "stale", "all"]),
              default="missing")
@click.option("--type", "types", multiple=True,
              type=click.Choice(["chunk", "entity", "community"]),
              help="Node types to re-embed (default: all three)")
@click.option("--model", default=None, help="Override embedding model")
@click.option("--dim", type=int, default=None, help="Override embedding dimension")
@click.option("--clear-first", is_flag=True,
              help="Null out embeddings before re-embedding")
def reembed(scope: str, types: tuple, model: str | None,
            dim: int | None, clear_first: bool):
    """Re-embed nodes (chunks/entities/communities) under a scope."""
    _bootstrap()
    from .services.embedding_service import EmbeddingService, NODE_TYPES

    selected = list(types) if types else list(NODE_TYPES)
    svc = EmbeddingService()

    def _progress(u):
        console.print(f"  [{u.progress * 100:5.1f}%] {u.stage}: {u.message}")

    rep = svc.reembed(scope=scope, types=selected, model=model, dim=dim,
                      clear_first=clear_first, update=_progress)
    console.print_json(data=rep)


@cli.command("switch-embedding-model")
@click.option("--model", required=True, help="New embedding model id")
@click.option("--dim", type=int, required=True, help="New embedding dimension")
@click.option("--provider", default=None,
              help="Optional new embedding provider (openrouter, local, ...)")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def switch_embedding_model(model: str, dim: int, provider: str | None,
                            yes: bool):
    """Persist new embedding settings + re-embed every node (destructive)."""
    _bootstrap()
    if not yes and not click.confirm(
        f"This will clear ALL embeddings and re-embed everything with "
        f"{model} (dim={dim}). Continue?",
        default=False,
    ):
        console.print("aborted")
        sys.exit(1)
    from .services.embedding_service import EmbeddingService
    svc = EmbeddingService()

    def _progress(u):
        console.print(f"  [{u.progress * 100:5.1f}%] {u.stage}: {u.message}")

    rep = svc.switch_model(model=model, dim=dim, provider=provider,
                            update=_progress)
    console.print_json(data=rep)


if __name__ == "__main__":
    cli()
