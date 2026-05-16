"""Runtime-tunable settings persisted in app_settings table.

Distinct from /api/settings (which handles connection config — base_url,
api keys, neo4j credentials). These are pipeline knobs the operator flips
without restarting the backend: retry counts, backoff, quality thresholds.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..errors import ValidationError
from ..services.settings_service import SettingsService

bp = Blueprint("runtime_settings", __name__)


@bp.get("/runtime")
def list_runtime():
    return jsonify({"items": SettingsService().specs()})


@bp.put("/runtime")
def update_runtime():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not data:
        raise ValidationError("body must be a non-empty object")
    updated = SettingsService().bulk_set(data)
    return jsonify({"updated": updated, "items": SettingsService().specs()})
