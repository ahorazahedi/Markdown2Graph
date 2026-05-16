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
_TEMPLATES_DIR = _PROMPTS_DIR / "templates"


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
            "the model to your approved schema. Domain-specific guidance lives in "
            "the active preset (general / medical / your own custom override)."
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
    PromptSpec(
        key="chat_system",
        filename="chat_system.md",
        description=(
            "System prompt for the chat / RAG answer generation. Receives "
            "the retrieved context + the user question. Edit this to change "
            "tone, citation style, or refusal behavior."
        ),
        variables=(
            {"name": "context",
             "description": "Concatenated retrieval payload (chunks + entities + summaries).",
             "sample": "[chunk: drug X treats disease Y, see file ref1.md]"},
            {"name": "question",
             "description": "The user's (possibly rewritten) question.",
             "sample": "Which drugs treat type-2 diabetes?"},
        ),
    ),
    PromptSpec(
        key="chat_question_rewrite",
        filename="chat_question_rewrite.md",
        description=(
            "History-aware question rewriter. Turns 'and the second one?' "
            "into a self-contained search query. Used before retrieval."
        ),
        variables=(
            {"name": "history",
             "description": "Recent chat turns formatted as a list.",
             "sample": "user: list common diabetes drugs\nassistant: metformin, insulin, ..."},
            {"name": "question",
             "description": "The user's latest message.",
             "sample": "what about side effects?"},
        ),
    ),
    PromptSpec(
        key="community_summary_system",
        filename="community_summary_system.md",
        description=(
            "Used by post-processing to give each detected graph community a "
            "human-readable title and 2-3 sentence summary."
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

    # ---------- presets ----------
    def list_presets(self) -> list[dict]:
        """Discover preset directories under app/prompts/templates/. Each
        directory whose name doesn't start with `_` is a preset; for every
        registered spec, report whether that preset has a template file."""
        if not _TEMPLATES_DIR.is_dir():
            return []
        out: list[dict] = []
        for d in sorted(_TEMPLATES_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            covers = []
            for spec in SPECS:
                covers.append({
                    "key": spec.key,
                    "has_template": (d / spec.filename).is_file(),
                })
            out.append({"name": d.name, "prompts": covers})
        return out

    def preset_prompts(self, name: str) -> dict[str, str]:
        """Return {prompt_key: template_text} for every prompt the preset
        defines. Missing files in the preset fall back to the disk default."""
        d = _TEMPLATES_DIR / name
        if not d.is_dir():
            raise KeyError(f"preset {name!r} not found")
        out: dict[str, str] = {}
        for spec in SPECS:
            src = d / spec.filename
            if not src.is_file():
                src = _PROMPTS_DIR / spec.filename
            if not src.is_file():
                continue
            out[spec.key] = src.read_text(encoding="utf-8")
        return out

    def apply_preset(self, name: str) -> dict:
        """Overwrite every prompt's stored template with the preset's content.
        After this call, `is_custom` is 1 for every applied prompt."""
        prompts = self.preset_prompts(name)
        applied = []
        for key, template in prompts.items():
            self._validate(template)
            self.state.save_prompt(key, template)
            applied.append(key)
        return {"preset": name, "applied": applied}

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
