from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..errors import NotFoundError, ValidationError
from ..services.prompt_store import PromptStore

bp = Blueprint("prompts_api", __name__)


@bp.get("/prompts")
def list_prompts():
    return jsonify({"items": PromptStore().list()})


@bp.get("/prompts/<key>")
def get_prompt(key: str):
    p = PromptStore().get(key)
    if not p:
        raise NotFoundError(f"prompt {key!r} not found")
    return jsonify(p)


@bp.put("/prompts/<key>")
def save_prompt(key: str):
    data = request.get_json(silent=True) or {}
    template = data.get("template")
    if not isinstance(template, str) or not template.strip():
        raise ValidationError("'template' is required")
    try:
        saved = PromptStore().save(key, template)
    except ValueError as e:
        raise ValidationError(str(e))
    if not saved:
        raise NotFoundError(f"prompt {key!r} not found")
    return jsonify(saved)


@bp.post("/prompts/<key>/reset")
def reset_prompt(key: str):
    r = PromptStore().reset(key)
    if not r:
        raise NotFoundError(f"prompt {key!r} not found")
    return jsonify(r)


@bp.post("/prompts/<key>/preview")
def preview_prompt(key: str):
    """Render `template` (from body, or current stored) with `vars` (from body).

    Body:
      { "template": "...optional override...",
        "vars": { "extra_instructions": "..." } }
    """
    data = request.get_json(silent=True) or {}
    store = PromptStore()
    p = store.get(key)
    if not p:
        raise NotFoundError(f"prompt {key!r} not found")
    template = data.get("template") or p["template"]
    vars_ = data.get("vars") or {}
    if not isinstance(vars_, dict):
        raise ValidationError("'vars' must be an object")
    try:
        rendered = store.preview(template, vars_)
    except ValueError as e:
        raise ValidationError(str(e))
    return jsonify({"rendered": rendered})
