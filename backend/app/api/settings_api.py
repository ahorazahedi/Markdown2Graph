"""Runtime settings: GET current view, PUT to persist, and connection tests.

The PUT endpoints write to SQLite (app_settings table), clear the
`get_settings()` cache, and for Neo4j changes reconnect the driver.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

import httpx
from flask import Blueprint, jsonify, request
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from ..config import get_settings, reload_settings
from ..extensions import neo4j_manager
from ..repositories.settings_repository import SettingsRepository
from ..repositories.app_state_repository import AppStateRepository
from ..repositories.graph_repository import GraphRepository
from ..repositories.llm_call_repository import LLMCallRepository
from ..services.prompt_store import PromptStore

bp = Blueprint("settings_api", __name__)
log = logging.getLogger(__name__)

# Targets supported by the reset endpoint. Order here is the run order.
RESET_TARGETS = ("graph", "runs", "llm_logs", "documents", "schema", "prompts", "app_settings")

# Selecting these implicitly drags in dependents (graph state would otherwise
# desync from the SQLite ledger).
CASCADE: dict[str, tuple[str, ...]] = {
    "documents": ("graph", "runs"),
    "schema": ("runs",),
}

_REPO_ROOT = Path(__file__).resolve().parents[3]
UPLOAD_ROOT = _REPO_ROOT / "backend" / "data" / "uploads"


def _mask(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def _strip_trailing_slash(url: str) -> str:
    return url.rstrip("/") if url else url


@bp.get("/settings")
def get_settings_view():
    s = get_settings()
    return jsonify(
        {
            "llm": {
                "base_url": s.effective_llm_base_url,
                "api_key_masked": _mask(s.effective_llm_api_key),
                "api_key_set": bool(s.effective_llm_api_key),
                "model": s.llm_model,
                "temperature": s.llm_temperature,
                "max_tokens": s.llm_max_tokens,
            },
            "embedding": {
                "provider": s.embedding_provider,
                "model": s.embedding_model,
                "dimension": s.embedding_dimension,
            },
            "neo4j": {
                "uri": s.neo4j_uri,
                "username": s.neo4j_username,
                "password_set": bool(s.neo4j_password),
                "database": s.neo4j_database,
            },
        }
    )


@bp.put("/settings/llm")
def put_llm_settings():
    body: dict[str, Any] = request.get_json(force=True) or {}
    updates: dict[str, Any] = {}

    if "base_url" in body:
        updates["llm_base_url"] = _strip_trailing_slash(str(body["base_url"]).strip())
    # api_key: None / missing / "" means "keep existing". Only write a non-empty value.
    if "api_key" in body and body["api_key"]:
        updates["llm_api_key"] = str(body["api_key"]).strip()
    if "model" in body and body["model"]:
        updates["llm_model"] = str(body["model"]).strip()
    if "embedding_provider" in body and body["embedding_provider"]:
        updates["embedding_provider"] = str(body["embedding_provider"]).strip()
    if "embedding_model" in body and body["embedding_model"]:
        updates["embedding_model"] = str(body["embedding_model"]).strip()
    if "embedding_dimension" in body and body["embedding_dimension"] is not None:
        try:
            updates["embedding_dimension"] = int(body["embedding_dimension"])
        except (TypeError, ValueError):
            return jsonify({"error": "embedding_dimension must be an integer"}), 400

    SettingsRepository().save(updates)
    reload_settings()
    return get_settings_view()


@bp.put("/settings/neo4j")
def put_neo4j_settings():
    body: dict[str, Any] = request.get_json(force=True) or {}
    updates: dict[str, Any] = {}
    if "uri" in body and body["uri"]:
        updates["neo4j_uri"] = str(body["uri"]).strip()
    if "username" in body and body["username"]:
        updates["neo4j_username"] = str(body["username"]).strip()
    if "password" in body and body["password"]:
        updates["neo4j_password"] = str(body["password"])
    if "database" in body and body["database"]:
        updates["neo4j_database"] = str(body["database"]).strip()

    SettingsRepository().save(updates)
    s = reload_settings()

    reconnect_error: str | None = None
    try:
        neo4j_manager.reconfigure(s)
        if not neo4j_manager.verify():
            reconnect_error = "verify_connectivity() returned False"
    except Exception as exc:  # pragma: no cover - depends on live DB
        reconnect_error = str(exc)

    resp = get_settings_view().get_json()
    resp["reconnect"] = {"ok": reconnect_error is None, "error": reconnect_error}
    return jsonify(resp)


# ---------------- tests ----------------


def _resolved(body: dict, key: str, fallback: str) -> str:
    v = body.get(key)
    return str(v).strip() if v else fallback


@bp.post("/settings/test/llm")
def test_llm():
    """Ping the chat completion endpoint with a 1-token request."""
    body = request.get_json(force=True) or {}
    s = get_settings()
    base_url = _strip_trailing_slash(_resolved(body, "base_url", s.effective_llm_base_url))
    api_key = body.get("api_key") or s.effective_llm_api_key
    model = _resolved(body, "model", s.llm_model)

    if not api_key:
        return jsonify({"ok": False, "error": "api key missing"}), 200

    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0,
                },
            )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code >= 400:
            return jsonify({"ok": False, "status": r.status_code, "error": r.text[:500], "latency_ms": latency_ms})
        return jsonify({"ok": True, "latency_ms": latency_ms, "model": model})
    except httpx.HTTPError as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.post("/settings/test/embedding")
def test_embedding():
    body = request.get_json(force=True) or {}
    s = get_settings()
    base_url = _strip_trailing_slash(_resolved(body, "base_url", s.effective_llm_base_url))
    api_key = body.get("api_key") or s.effective_llm_api_key
    model = _resolved(body, "model", s.embedding_model)
    dim_in = body.get("dimension")
    try:
        dim = int(dim_in) if dim_in is not None else s.embedding_dimension
    except (TypeError, ValueError):
        dim = s.embedding_dimension

    if not api_key:
        return jsonify({"ok": False, "error": "api key missing"}), 200

    payload: dict[str, Any] = {"model": model, "input": "ping"}
    if dim:
        payload["dimensions"] = dim

    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code >= 400:
            return jsonify({"ok": False, "status": r.status_code, "error": r.text[:500], "latency_ms": latency_ms})
        data = r.json()
        vec = (data.get("data") or [{}])[0].get("embedding") or []
        return jsonify({"ok": True, "latency_ms": latency_ms, "dimension": len(vec), "model": model})
    except httpx.HTTPError as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.post("/settings/test/neo4j")
def test_neo4j():
    body = request.get_json(force=True) or {}
    s = get_settings()
    uri = _resolved(body, "uri", s.neo4j_uri)
    username = _resolved(body, "username", s.neo4j_username)
    password = body.get("password") or s.neo4j_password
    database = _resolved(body, "database", s.neo4j_database)

    t0 = time.perf_counter()
    driver = None
    try:
        driver = GraphDatabase.driver(uri, auth=(username, password), max_connection_lifetime=60)
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.run("RETURN 1 AS ok").consume()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return jsonify({"ok": True, "latency_ms": latency_ms, "database": database})
    except (Neo4jError, Exception) as exc:
        return jsonify({"ok": False, "error": str(exc)})
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass


@bp.get("/settings/models")
def list_models():
    """Proxy `GET {base_url}/models`.

    Query: base_url, api_key (optional — uses stored if omitted), kind=chat|embedding|all
    """
    s = get_settings()
    base_url = _strip_trailing_slash(request.args.get("base_url") or s.effective_llm_base_url)
    api_key = request.args.get("api_key") or s.effective_llm_api_key
    kind = (request.args.get("kind") or "all").lower()

    if not api_key:
        return jsonify({"ok": False, "error": "api key missing", "models": []}), 200

    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if r.status_code >= 400:
            return jsonify({"ok": False, "status": r.status_code, "error": r.text[:500], "models": []})
        data = r.json()
        items = data.get("data") or data.get("models") or []
        out = []
        for m in items:
            mid = m.get("id") or m.get("name") or ""
            if not mid:
                continue
            is_embedding = "embed" in mid.lower()
            if kind == "chat" and is_embedding:
                continue
            if kind == "embedding" and not is_embedding:
                continue
            out.append(
                {
                    "id": mid,
                    "owned_by": m.get("owned_by") or m.get("publisher") or "",
                    "kind": "embedding" if is_embedding else "chat",
                }
            )
        out.sort(key=lambda x: x["id"])
        return jsonify({"ok": True, "models": out})
    except httpx.HTTPError as exc:
        return jsonify({"ok": False, "error": str(exc), "models": []})


# ---------------- reset ----------------


@bp.get("/settings/reset/counts")
def reset_counts():
    """Live counts for each reset target. Drives the checkbox badges."""
    state = AppStateRepository()
    try:
        gstats = GraphRepository().stats()
        graph_nodes = int(gstats.get("documents", 0) or 0) + int(gstats.get("chunks", 0) or 0) + \
                      int(gstats.get("entities", 0) or 0)
    except Exception as exc:
        log.warning("graph stats failed for reset counts: %s", exc)
        graph_nodes = 0

    try:
        llm_stats = LLMCallRepository().stats()
        llm_count = int(llm_stats.get("total", 0) or 0)
    except Exception:
        llm_count = 0

    upload_files = 0
    if UPLOAD_ROOT.exists():
        try:
            upload_files = sum(1 for _ in UPLOAD_ROOT.rglob("*") if _.is_file())
        except Exception:
            upload_files = 0

    return jsonify(
        {
            "graph":        {"count": graph_nodes, "unit": "nodes"},
            "runs":         {"count": state.count_runs(), "unit": "runs"},
            "llm_logs":     {"count": llm_count, "unit": "calls"},
            "documents":    {"count": state.count_documents(), "unit": "documents",
                             "upload_files": upload_files},
            "schema":       {"count": state.count_schema_versions(),
                             "has_active": state.has_schema(),
                             "unit": "versions"},
            "prompts":      {"count": state.count_custom_prompts(), "unit": "customized"},
            "app_settings": {"count": SettingsRepository().count(), "unit": "overrides"},
        }
    )


def _expand_targets(selected: list[str]) -> list[str]:
    seen: set[str] = set()
    for t in selected:
        if t not in RESET_TARGETS:
            continue
        seen.add(t)
        for dep in CASCADE.get(t, ()):
            seen.add(dep)
    # preserve canonical run order
    return [t for t in RESET_TARGETS if t in seen]


@bp.post("/settings/reset")
def reset():
    body = request.get_json(force=True) or {}
    raw_targets = body.get("targets") or []
    if not isinstance(raw_targets, list):
        return jsonify({"error": "targets must be a list"}), 400

    targets = _expand_targets([str(t) for t in raw_targets])
    if not targets:
        return jsonify({"error": "no valid targets"}), 400

    state = AppStateRepository()
    cleared: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for tgt in targets:
        try:
            if tgt == "graph":
                GraphRepository().clear_all()
                cleared[tgt] = "ok"
            elif tgt == "runs":
                cleared[tgt] = state.clear_runs()
            elif tgt == "llm_logs":
                cleared[tgt] = LLMCallRepository().clear()
            elif tgt == "documents":
                removed = state.clear_documents()
                files_removed = 0
                if UPLOAD_ROOT.exists():
                    for child in UPLOAD_ROOT.iterdir():
                        try:
                            if child.is_dir():
                                shutil.rmtree(child, ignore_errors=True)
                            else:
                                child.unlink(missing_ok=True)
                            files_removed += 1
                        except Exception as exc:
                            log.warning("failed removing %s: %s", child, exc)
                cleared[tgt] = {"rows": removed, "staging": files_removed}
            elif tgt == "schema":
                cleared[tgt] = state.clear_schema(drop_versions=True)
            elif tgt == "prompts":
                keys = state.list_custom_prompt_keys()
                store = PromptStore()
                done = 0
                for k in keys:
                    if store.reset(k):
                        done += 1
                cleared[tgt] = done
            elif tgt == "app_settings":
                cleared[tgt] = SettingsRepository().clear()
                reload_settings()
        except Exception as exc:
            log.exception("reset target %s failed", tgt)
            errors[tgt] = str(exc)

    return jsonify({"ok": not errors, "targets": targets, "cleared": cleared, "errors": errors})
