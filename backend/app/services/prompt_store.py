"""Prompt template store with Jinja2 rendering.

Templates live on disk (`app/prompts/<key>.md`) as defaults. On first boot
each is seeded into the `prompts` table. Users can edit templates from the
UI; their edits persist and are preserved across redeploys via the
`is_custom` flag.

Render context for each prompt is declared by `VARIABLES` below; the UI uses
it to show editable preview fields next to the template.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError

from ..repositories.app_state_repository import AppStateRepository

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


@dataclass(frozen=True)
class PromptSpec:
    key: str
    filename: str
    description: str
    variables: tuple[dict, ...]


# Single source of truth for prompt metadata.
SPECS: tuple[PromptSpec, ...] = (
    PromptSpec(
        key="schema_discovery_system",
        filename="schema_discovery_system.md",
        description=(
            "System prompt for the schema-discovery LLM call. The model receives "
            "a sample of your documents and proposes node labels + relationship "
            "triplets."
        ),
        variables=(
            {"name": "extra_instructions",
             "description": "Optional free-text guidance from the user.",
             "sample": "Focus on cardiology and emphasize drug-interaction edges."},
        ),
    ),
    PromptSpec(
        key="entity_extraction_instructions",
        filename="entity_extraction_instructions.md",
        description=(
            "Appended to LangChain's LLMGraphTransformer system prompt. Constrains "
            "the model to your approved schema and the medical domain rules."
        ),
        variables=(
            {"name": "allowed_nodes",
             "description": "List of node labels the model may emit.",
             "sample": ["Disease", "Drug", "Symptom"]},
            {"name": "allowed_relationships",
             "description": "List of [source, REL_TYPE, target] triplets.",
             "sample": [["Drug", "TREATS", "Disease"], ["Symptom", "INDICATES", "Disease"]]},
            {"name": "extra_instructions",
             "description": "Optional free-text guidance from the user.",
             "sample": "Capture dosing as a property on Drug-TREATS->Disease."},
        ),
    ),
    PromptSpec(
        key="graph_cleanup_system",
        filename="graph_cleanup_system.md",
        description=(
            "Used by post-processing to canonicalize duplicate node labels and "
            "relationship types (e.g. 'Drug' vs 'Medication')."
        ),
        variables=(),
    ),
)

_SPEC_BY_KEY = {s.key: s for s in SPECS}


class PromptStore:
    """Read-through cache that always reflects the latest DB row."""

    def __init__(self, state: AppStateRepository | None = None):
        self.state = state or AppStateRepository()
        self.env = Environment(
            keep_trailing_newline=True,
            undefined=StrictUndefined,
            autoescape=False,
            trim_blocks=False,
            lstrip_blocks=False,
        )
        self._seed_if_missing()

    # ---------- seeding ----------
    def _seed_if_missing(self) -> None:
        for spec in SPECS:
            disk = _PROMPTS_DIR / spec.filename
            if not disk.exists():
                continue
            text = disk.read_text(encoding="utf-8")
            digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
            self.state.upsert_prompt_default(
                key=spec.key,
                template=text,
                description=spec.description,
                variables=list(spec.variables),
                default_hash=digest,
            )

    # ---------- access ----------
    def list(self) -> list[dict]:
        rows = self.state.list_prompts()
        # add disk default for diff/reset
        for r in rows:
            spec = _SPEC_BY_KEY.get(r["key"])
            if spec:
                r["filename"] = spec.filename
        return rows

    def get(self, key: str) -> dict | None:
        row = self.state.get_prompt(key)
        if not row:
            return None
        spec = _SPEC_BY_KEY.get(key)
        if spec:
            row["filename"] = spec.filename
            row["default_template"] = (_PROMPTS_DIR / spec.filename).read_text(encoding="utf-8") \
                if (_PROMPTS_DIR / spec.filename).exists() else None
        return row

    def save(self, key: str, template: str) -> dict | None:
        # validate template parses
        self._validate(template)
        return self.state.save_prompt(key, template)

    def reset(self, key: str) -> dict | None:
        spec = _SPEC_BY_KEY.get(key)
        if not spec:
            return None
        disk = _PROMPTS_DIR / spec.filename
        if not disk.exists():
            return None
        return self.state.reset_prompt(key, disk.read_text(encoding="utf-8"))

    # ---------- rendering ----------
    def render(self, key: str, **vars: Any) -> str:
        row = self.state.get_prompt(key)
        if not row:
            raise KeyError(f"prompt {key!r} not found")
        return self._render_text(row["template"], vars)

    def preview(self, template: str, vars: Mapping[str, Any]) -> str:
        return self._render_text(template, vars)

    # ---------- internals ----------
    def _render_text(self, template: str, vars: Mapping[str, Any]) -> str:
        # Render with `Undefined` (not StrictUndefined) so unspecified vars
        # become empty rather than erroring — friendlier for preview / partial
        # contexts.
        env = Environment(
            keep_trailing_newline=True, autoescape=False,
            trim_blocks=False, lstrip_blocks=False,
        )
        try:
            tpl = env.from_string(template)
        except TemplateSyntaxError as e:
            raise ValueError(f"Jinja template syntax error: {e}") from e
        return tpl.render(**dict(vars))

    def _validate(self, template: str) -> None:
        try:
            self.env.parse(template)
        except TemplateSyntaxError as e:
            raise ValueError(f"Jinja template syntax error: {e}") from e
