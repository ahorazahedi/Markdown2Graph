"""LLM call recorder.

A LangChain `BaseCallbackHandler` that persists every chat/LLM invocation
into the SQLite audit log. The `tag` (purpose label, e.g. "schema_discovery")
is propagated via a `contextvars.ContextVar`, so any LLM call that happens
inside a `with_tag(...)` block is attributed to that tag automatically —
no plumbing through every service.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler

from ..config import get_settings
from ..repositories.llm_call_repository import LLMCallRepository

log = logging.getLogger(__name__)

# Context-local tag stack.
_tag_var: ContextVar[str] = ContextVar("text2graph_llm_tag", default="uncategorized")


@contextmanager
def with_tag(tag: str) -> Iterator[None]:
    """Tag every LLM call made inside the `with` block."""
    token = _tag_var.set(tag)
    try:
        yield
    finally:
        _tag_var.reset(token)


def current_tag() -> str:
    return _tag_var.get()


class LLMCallRecorder(BaseCallbackHandler):
    """Persist (request, response, tokens, latency) for every LLM call."""

    raise_error = False

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 provider: str = "openai-compatible"):
        self.model = model
        self.base_url = base_url
        self.provider = provider
        self._repo = LLMCallRepository()
        # run_id (UUID) -> {call_id, started}
        self._inflight: dict[str, dict] = {}
        self._enabled = get_settings().llm_log_enabled

    # ---------- chat models ----------
    def on_chat_model_start(self, serialized: dict, messages: list[list[Any]],
                            *, run_id: UUID, **kwargs: Any) -> None:
        if not self._enabled:
            return
        try:
            req = {"messages": [self._dump_messages(b) for b in messages]}
            self._begin(run_id, req, kwargs)
        except Exception as e:
            log.warning("LLM recorder on_chat_model_start failed: %s", e)

    # ---------- legacy (text) llms ----------
    def on_llm_start(self, serialized: dict, prompts: list[str],
                     *, run_id: UUID, **kwargs: Any) -> None:
        if not self._enabled:
            return
        try:
            self._begin(run_id, {"prompts": prompts}, kwargs)
        except Exception as e:
            log.warning("LLM recorder on_llm_start failed: %s", e)

    def on_llm_end(self, response, *, run_id: UUID, **kwargs: Any) -> None:
        if not self._enabled:
            return
        rec = self._inflight.pop(str(run_id), None)
        if not rec:
            return
        try:
            text, payload = self._dump_response(response)
            usage = self._extract_usage(response)
            self._repo.mark_success(
                rec["call_id"],
                finished_at=_now_iso(),
                latency_ms=int((time.time() - rec["started"]) * 1000),
                response_text=text,
                response_json=payload,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
        except Exception as e:
            log.warning("LLM recorder on_llm_end failed: %s", e)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        if not self._enabled:
            return
        rec = self._inflight.pop(str(run_id), None)
        if not rec:
            return
        try:
            self._repo.mark_error(
                rec["call_id"],
                finished_at=_now_iso(),
                latency_ms=int((time.time() - rec["started"]) * 1000),
                error=f"{type(error).__name__}: {error}",
            )
        except Exception as e:
            log.warning("LLM recorder on_llm_error failed: %s", e)

    # ---------- internals ----------
    def _begin(self, run_id: UUID, request: dict, kwargs: dict) -> None:
        call_id = self._repo.insert_pending(
            created_at=_now_iso(),
            tag=current_tag(),
            model=self.model,
            base_url=self.base_url,
            provider=self.provider,
            request_json=request,
            extra={
                "invocation_params": _safe(kwargs.get("invocation_params")),
                "metadata": _safe(kwargs.get("metadata")),
            },
        )
        self._inflight[str(run_id)] = {"call_id": call_id, "started": time.time()}

    @staticmethod
    def _dump_messages(batch) -> list[dict]:
        out = []
        for m in batch:
            out.append({
                "type": getattr(m, "type", m.__class__.__name__),
                "content": getattr(m, "content", str(m)),
            })
        return out

    @staticmethod
    def _dump_response(response) -> tuple[str, Any]:
        try:
            generations = getattr(response, "generations", None) or []
            text_parts = []
            payload = []
            for batch in generations:
                row = []
                for g in batch:
                    text = getattr(g, "text", "") or ""
                    msg = getattr(g, "message", None)
                    if msg is not None and getattr(msg, "content", None):
                        text = msg.content if isinstance(msg.content, str) else str(msg.content)
                    text_parts.append(text)
                    row.append({"text": text})
                payload.append(row)
            return ("\n".join(p for p in text_parts if p), payload)
        except Exception:
            return (str(response)[:4000], None)

    @staticmethod
    def _extract_usage(response) -> dict:
        out: dict = {}
        try:
            llm_output = getattr(response, "llm_output", None) or {}
            usage = (
                llm_output.get("token_usage")
                or llm_output.get("usage")
                or {}
            )
            for src, dst in (
                ("prompt_tokens", "prompt_tokens"),
                ("completion_tokens", "completion_tokens"),
                ("total_tokens", "total_tokens"),
                ("input_tokens", "prompt_tokens"),
                ("output_tokens", "completion_tokens"),
            ):
                if src in usage and dst not in out:
                    out[dst] = int(usage[src])
            if "total_tokens" not in out and "prompt_tokens" in out and "completion_tokens" in out:
                out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
        except Exception:
            pass
        return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(v: Any) -> Any:
    try:
        import json
        json.dumps(v, default=str)
        return v
    except Exception:
        return str(v)
