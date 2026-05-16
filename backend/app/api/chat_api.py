"""HTTP layer for the chat / RAG feature.

Endpoints (V1):
  GET    /api/me                                — stubbed identity (user_id, role)
  GET    /api/chat/health                       — pre-flight (indexes, embedding dim, chunks)
  GET    /api/chat/sessions                     — list current user's sessions
  POST   /api/chat/sessions                     — create new session
  GET    /api/chat/sessions/<id>                — session + messages
  PATCH  /api/chat/sessions/<id>                — rename / pin / archive / change mode
  DELETE /api/chat/sessions/<id>                — hard delete (cascades messages)
  POST   /api/chat/sessions/<id>/messages       — ask a question, persist both turns
  POST   /api/chat/messages/<id>/feedback       — thumbs up/down + comment
  GET    /api/chat/messages/<id>/citations      — expand citations (chunks/entities)
  POST   /api/chat/sessions/<id>/clear          — drop every message in the session

Auth is a stub right now: every request resolves to user_id='default'
with role='admin'. When real auth lands, replace `_current_user()` and
the multi-user gating is already in place — `chat_sessions.user_id` is
NOT NULL and indexed.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from flask import Blueprint, Response, jsonify, request, stream_with_context

from ..errors import NotFoundError, ValidationError
from ..repositories.chat_repository import ChatRepository
from ..repositories.graph_repository import GraphRepository
from ..services.chat_service import ChatService, SUPPORTED_MODES

log = logging.getLogger(__name__)

bp = Blueprint("chat", __name__)


# ----------------------- identity stub -----------------------

def _current_user() -> dict:
    """Stubbed identity. Wire to real auth (JWT/session) here later. The
    rest of the chat code already passes `user_id` so this swap is a
    one-file change."""
    return {"user_id": "default", "role": "admin"}


@bp.get("/me")
def me():
    return jsonify(_current_user())


# ----------------------- service health -----------------------

@bp.get("/chat/health")
def chat_health():
    return jsonify(ChatService().health())


@bp.get("/chat/modes")
def chat_modes():
    """List supported retrieval modes for the UI mode picker."""
    return jsonify({"modes": SUPPORTED_MODES})


# ----------------------- sessions -----------------------

@bp.get("/chat/sessions")
def list_sessions():
    user = _current_user()
    archived = request.args.get("archived", "false").lower() in ("1", "true", "yes")
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        raise ValidationError("limit / offset must be integers")
    search = request.args.get("search") or None
    items = ChatRepository().list_sessions(
        user_id=user["user_id"], archived=archived,
        limit=limit, offset=offset, search=search,
    )
    return jsonify({"items": items})


@bp.post("/chat/sessions")
def create_session():
    user = _current_user()
    body = request.get_json(silent=True) or {}
    sess = ChatRepository().create_session(
        user_id=user["user_id"],
        title=body.get("title") or "New chat",
        mode=body.get("mode") or "graph_vector_fulltext",
        model=body.get("model"),
        embedding_provider=body.get("embedding_provider"),
        embedding_model=body.get("embedding_model"),
        document_names=body.get("document_names") or [],
    )
    return jsonify(sess), 201


@bp.get("/chat/sessions/<sid>")
def get_session(sid: str):
    repo = ChatRepository()
    sess = repo.get_session(sid)
    if not sess or sess["user_id"] != _current_user()["user_id"]:
        raise NotFoundError(f"chat session {sid} not found")
    sess["messages"] = repo.list_messages(sid)
    return jsonify(sess)


@bp.patch("/chat/sessions/<sid>")
def patch_session(sid: str):
    repo = ChatRepository()
    sess = repo.get_session(sid)
    if not sess or sess["user_id"] != _current_user()["user_id"]:
        raise NotFoundError(f"chat session {sid} not found")
    body = request.get_json(silent=True) or {}
    updated = repo.update_session(sid, **body)
    return jsonify(updated)


@bp.delete("/chat/sessions/<sid>")
def delete_session(sid: str):
    repo = ChatRepository()
    sess = repo.get_session(sid)
    if not sess or sess["user_id"] != _current_user()["user_id"]:
        raise NotFoundError(f"chat session {sid} not found")
    repo.delete_session(sid)
    return jsonify({"deleted": sid})


@bp.post("/chat/sessions/<sid>/clear")
def clear_session(sid: str):
    """Delete every message but keep the session row (preserves title +
    mode + scope). Useful for 'reset conversation' without losing the tab."""
    repo = ChatRepository()
    sess = repo.get_session(sid)
    if not sess or sess["user_id"] != _current_user()["user_id"]:
        raise NotFoundError(f"chat session {sid} not found")
    with repo._connect() as c:  # noqa: SLF001 — repo-internal helper
        c.execute("DELETE FROM chat_messages WHERE session_id = ?", (sid,))
        c.execute(
            "UPDATE chat_sessions SET message_count=0, total_tokens=0, "
            "last_message_at=NULL WHERE id=?",
            (sid,),
        )
    return jsonify({"cleared": sid})


# ----------------------- ask -----------------------

@bp.post("/chat/sessions/<sid>/messages")
def send_message(sid: str):
    """Atomic write path: persist user msg → run RAG → persist assistant
    msg → return assistant payload. The frontend only needs ONE round-trip
    per turn."""
    user = _current_user()
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        raise ValidationError("question is required")

    repo = ChatRepository()
    sess = repo.get_session(sid)
    if not sess:
        # auto-create — matches reference behaviour where session_id is
        # opaque and provisioned on first message
        sess = repo.create_session(
            session_id=sid,
            user_id=user["user_id"],
            mode=body.get("mode") or "graph_vector_fulltext",
            document_names=body.get("document_names") or [],
        )
    elif sess["user_id"] != user["user_id"]:
        raise NotFoundError(f"chat session {sid} not found")

    mode = body.get("mode") or sess.get("mode") or "graph_vector_fulltext"
    docs = body.get("document_names") if body.get("document_names") is not None \
        else sess.get("document_names")

    # 1. persist user turn first so it survives a downstream crash
    history = repo.list_messages(sid)
    user_msg_id = repo.append_message(
        session_id=sid, role="user", content=question, mode=mode,
    )

    # 2. RAG
    svc = ChatService()
    try:
        out = svc.ask(question=question, history=history,
                      mode=mode, document_names=docs)
    except Exception as e:
        log.exception("chat RAG failed for %s", sid)
        err_msg_id = repo.append_message(
            session_id=sid, role="assistant", content="",
            mode=mode, error=f"{type(e).__name__}: {e}",
        )
        return jsonify({
            "session_id": sid,
            "user_message_id": user_msg_id,
            "assistant_message_id": err_msg_id,
            "error": str(e),
        }), 500

    # 3. persist assistant turn with full payload
    assistant_msg_id = repo.append_message(
        session_id=sid, role="assistant", content=out["answer"],
        mode=out["mode"], model=out.get("model"),
        prompt_tokens=out.get("prompt_tokens"),
        completion_tokens=out.get("completion_tokens"),
        total_tokens=out.get("total_tokens"),
        response_time_ms=out.get("response_time_ms"),
        sources=out.get("sources") or [],
        entities=out.get("entities") or {},
        nodedetails=out.get("nodedetails") or {},
    )

    return jsonify({
        "session_id": sid,
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "message": out["answer"],
        "info": {
            "sources": out.get("sources"),
            "nodedetails": out.get("nodedetails"),
            "entities": out.get("entities"),
            "total_tokens": out.get("total_tokens"),
            "response_time_ms": out.get("response_time_ms"),
            "mode": out.get("mode"),
            "model": out.get("model"),
            "rewritten_question": out.get("rewritten_question"),
        },
    })


# ----------------------- messages aux -----------------------

@bp.get("/chat/messages/<int:mid>/citations")
def message_citations(mid: int):
    repo = ChatRepository()
    msg = repo.get_message(mid)
    if not msg:
        raise NotFoundError(f"message {mid} not found")
    sess = repo.get_session(msg["session_id"])
    if not sess or sess["user_id"] != _current_user()["user_id"]:
        raise NotFoundError(f"message {mid} not found")
    return jsonify({
        "message_id": mid,
        "citations": repo.list_citations(mid),
        "raw": {
            "sources": msg.get("sources"),
            "nodedetails": msg.get("nodedetails"),
            "entities": msg.get("entities"),
        },
    })


@bp.get("/chat/messages/<int:mid>/expand")
def expand_message(mid: int):
    """Hydrate cited entities/chunks with full content for the right-side
    drawer in chat. Returns:
      {
        chunks:    [{id, fileName, position, text, score}],
        entities:  [{id, element_id, labels, description, chunks: [...]}],
        communities: [{id, title, summary, level}]
      }
    """
    repo = ChatRepository()
    msg = repo.get_message(mid)
    if not msg:
        raise NotFoundError(f"message {mid} not found")
    sess = repo.get_session(msg["session_id"])
    if not sess or sess["user_id"] != _current_user()["user_id"]:
        raise NotFoundError(f"message {mid} not found")

    nd = msg.get("nodedetails") or {}
    ents = msg.get("entities") or {}
    chunk_ids = [c.get("id") for c in (nd.get("chunkdetails") or []) if c.get("id")]
    entity_eids = list(ents.get("entityids") or [])
    community_eids = [c.get("id") for c in (nd.get("communitydetails") or []) if c.get("id")]

    g = GraphRepository()
    out: dict = {"chunks": [], "entities": [], "communities": []}

    if chunk_ids:
        rows = g._run(
            """
            UNWIND $ids AS cid
            MATCH (c:Chunk {id: cid})
            OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
            RETURN c.id AS id, d.fileName AS fileName,
                   c.position AS position, c.text AS text
            """,
            ids=chunk_ids,
        )
        score_by_id = {x.get("id"): x.get("score") for x in (nd.get("chunkdetails") or [])}
        out["chunks"] = [{**dict(r), "score": score_by_id.get(r["id"])}
                         for r in rows]

    if entity_eids:
        rows = g._run(
            """
            UNWIND $eids AS eid
            MATCH (e:__Entity__) WHERE elementId(e) = eid
            OPTIONAL MATCH (e)<-[:HAS_ENTITY]-(c:Chunk)-[:PART_OF]->(d:Document)
            WITH e, collect(DISTINCT {chunkId: c.id, fileName: d.fileName,
                                       text: substring(c.text, 0, 400)})[0..3] AS chunks
            RETURN elementId(e) AS element_id, e.id AS id,
                   [l IN labels(e) WHERE l <> '__Entity__'] AS labels,
                   e.description AS description, chunks
            """,
            eids=entity_eids,
        )
        out["entities"] = [dict(r) for r in rows]

    if community_eids:
        rows = g._run(
            """
            UNWIND $cids AS cid
            MATCH (c:__Community__ {id: cid})
            RETURN c.id AS id, c.title AS title, c.summary AS summary,
                   c.level AS level, c.weight AS weight,
                   c.community_rank AS rank
            """,
            cids=community_eids,
        )
        out["communities"] = [dict(r) for r in rows]

    return jsonify(out)


@bp.post("/chat/messages/<int:mid>/feedback")
def message_feedback(mid: int):
    body = request.get_json(silent=True) or {}
    rating = body.get("rating")
    if rating not in (-1, 0, 1):
        raise ValidationError("rating must be -1, 0, or 1")
    repo = ChatRepository()
    msg = repo.get_message(mid)
    if not msg:
        raise NotFoundError(f"message {mid} not found")
    sess = repo.get_session(msg["session_id"])
    if not sess or sess["user_id"] != _current_user()["user_id"]:
        raise NotFoundError(f"message {mid} not found")
    repo.set_feedback(mid, rating=int(rating), comment=body.get("comment"))
    return jsonify({"message_id": mid, "rating": int(rating)})


@bp.post("/chat/sessions/<sid>/messages/stream")
def send_message_stream(sid: str):
    """SSE variant of send_message. Streams answer tokens, then a final
    'done' event carrying the full payload + assistant_message_id.

    Event format:
      event: token       data: {"t": "..."}
      event: meta        data: {"rewritten": "..."}   (after retrieval)
      event: done        data: {assistant_message_id, message, info, ...}
      event: error       data: {"error": "..."}
    """
    user = _current_user()
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        raise ValidationError("question is required")
    mode_in = body.get("mode")
    docs_in = body.get("document_names")

    repo = ChatRepository()
    sess = repo.get_session(sid)
    if not sess:
        sess = repo.create_session(
            session_id=sid,
            user_id=user["user_id"],
            mode=mode_in or "graph_vector_fulltext",
            document_names=docs_in or [],
        )
    elif sess["user_id"] != user["user_id"]:
        raise NotFoundError(f"chat session {sid} not found")

    mode = mode_in or sess.get("mode") or "graph_vector_fulltext"
    docs = docs_in if docs_in is not None else sess.get("document_names")

    history = repo.list_messages(sid)
    user_msg_id = repo.append_message(
        session_id=sid, role="user", content=question, mode=mode,
    )

    q: queue.Queue = queue.Queue()

    def on_token(tok: str) -> None:
        q.put(("token", json.dumps({"t": tok})))

    def runner():
        svc = ChatService()
        try:
            out = svc.ask(question=question, history=history, mode=mode,
                          document_names=docs, stream_handler=on_token)
            mid = repo.append_message(
                session_id=sid, role="assistant", content=out["answer"],
                mode=out["mode"], model=out.get("model"),
                prompt_tokens=out.get("prompt_tokens"),
                completion_tokens=out.get("completion_tokens"),
                total_tokens=out.get("total_tokens"),
                response_time_ms=out.get("response_time_ms"),
                sources=out.get("sources") or [],
                entities=out.get("entities") or {},
                nodedetails=out.get("nodedetails") or {},
            )
            payload = {
                "session_id": sid,
                "user_message_id": user_msg_id,
                "assistant_message_id": mid,
                "message": out["answer"],
                "info": {
                    "sources": out.get("sources"),
                    "nodedetails": out.get("nodedetails"),
                    "entities": out.get("entities"),
                    "total_tokens": out.get("total_tokens"),
                    "response_time_ms": out.get("response_time_ms"),
                    "mode": out.get("mode"),
                    "model": out.get("model"),
                    "rewritten_question": out.get("rewritten_question"),
                    "cypher": out.get("cypher"),
                    "rows": out.get("rows"),
                },
            }
            q.put(("done", json.dumps(payload, default=str)))
        except Exception as e:
            log.exception("chat stream failed for %s", sid)
            err_mid = repo.append_message(
                session_id=sid, role="assistant", content="",
                mode=mode, error=f"{type(e).__name__}: {e}",
            )
            q.put(("error", json.dumps({"error": str(e), "assistant_message_id": err_mid})))
        finally:
            q.put(("__close__", ""))

    threading.Thread(target=runner, daemon=True,
                     name=f"chat-stream-{sid[:8]}").start()

    @stream_with_context
    def generate():
        while True:
            evt, data = q.get()
            if evt == "__close__":
                return
            yield f"event: {evt}\ndata: {data}\n\n"

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
