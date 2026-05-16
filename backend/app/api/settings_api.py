"""Runtime settings: GET current view, PUT to persist, and connection tests.

The PUT endpoints write to SQLite (app_settings table), clear the
`get_settings()` cache, and for Neo4j changes reconnect the driver.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from flask import Blueprint, jsonify, request
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from ..config import get_settings, reload_settings
from ..extensions import neo4j_manager
from ..repositories.settings_repository import SettingsRepository

bp = Blueprint("settings_api", __name__)


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
