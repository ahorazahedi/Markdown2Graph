"""LLM + embedding factories.

OpenRouter and LM Studio both expose an OpenAI-compatible REST API, so
the same `ChatOpenAI` client works for both — only the base_url + key differ.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Tuple

from langchain_openai import ChatOpenAI

from ..config import Settings, get_settings
from .recorder import LLMCallRecorder

log = logging.getLogger(__name__)


def build_chat_llm(settings: Settings | None = None, *, tag: str | None = None) -> ChatOpenAI:
    """Build a chat LLM with the audit-log callback attached.

    `tag` is optional — pass it for one-off calls outside a `with_tag(...)` block.
    """
    s = settings or get_settings()
    if not s.effective_llm_api_key:
        raise RuntimeError("LLM api key missing — set OPENROUTER_API_KEY or LLM_API_KEY in .env")

    recorder = LLMCallRecorder(model=s.llm_model, base_url=s.effective_llm_base_url,
                                provider="openrouter" if "openrouter" in s.effective_llm_base_url else "openai-compatible")
    callbacks = [recorder] if s.llm_log_enabled else []

    llm = ChatOpenAI(
        model=s.llm_model,
        api_key=s.effective_llm_api_key,
        base_url=s.effective_llm_base_url,
        temperature=s.llm_temperature,
        max_tokens=s.llm_max_tokens,
        timeout=s.llm_timeout,
        callbacks=callbacks,
        default_headers={
            "HTTP-Referer": "https://github.com/text2graph",
            "X-Title": "text2graph-medical",
        },
    )
    if tag:
        # capture tag in metadata so it shows up alongside the call in storage
        llm = llm.with_config({"tags": [tag], "metadata": {"text2graph_tag": tag}})
    return llm


@lru_cache(maxsize=1)
def _local_embedder(model_name: str):
    """Lazy import — heavy dep."""
    from langchain_community.embeddings import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(model_name=model_name)


def build_embedder(settings: Settings | None = None) -> Tuple[object, int]:
    s = settings or get_settings()
    provider = s.embedding_provider.strip().lower()
    log.info(
        "build_embedder: provider=%r model=%r dim=%d base_url=%s",
        provider, s.embedding_model, s.embedding_dimension, s.effective_llm_base_url,
    )
    if provider in ("sentence-transformers", "huggingface", "local"):
        log.warning("Using LOCAL HuggingFace embeddings — set EMBEDDING_PROVIDER=openrouter "
                    "in .env (and restart the backend) to use the API instead.")
        return _local_embedder(s.embedding_model), s.embedding_dimension
    if provider in ("openai", "openrouter", "openai-compatible", "lm-studio"):
        if not s.effective_llm_api_key:
            raise RuntimeError(
                "Embeddings provider needs an api key — set OPENROUTER_API_KEY or LLM_API_KEY"
            )
        # OpenAI Python SDK auto-requests encoding_format=base64. OpenRouter's
        # Google embedding backend rejects base64 ("Use float instead") and
        # returns no data field → LangChain raises "No embedding data received".
        # Use a thin requests-based client that always asks for float arrays.
        return _OpenAICompatEmbedder(
            model=s.embedding_model,
            api_key=s.effective_llm_api_key,
            base_url=s.effective_llm_base_url,
            batch_size=32,
        ), s.embedding_dimension
    raise ValueError(f"Unknown embedding provider: {s.embedding_provider}")


class _OpenAICompatEmbedder:
    """Minimal OpenAI-compatible embeddings client.

    Implements the LangChain `Embeddings` protocol (`embed_documents`,
    `embed_query`). Always sends `encoding_format=float` so Google embedding
    endpoints (gemini-embedding-001 / 2-preview) on OpenRouter respond with
    populated `data` arrays.
    """

    def __init__(self, *, model: str, api_key: str, base_url: str,
                 batch_size: int = 32, timeout: int = 60):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.batch_size = max(1, int(batch_size))
        self.timeout = timeout

    def embed_documents(self, texts):
        import requests
        import time as _time
        out: list[list[float]] = []
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/text2graph",
            "X-Title": "text2graph",
        }
        for i in range(0, len(texts), self.batch_size):
            batch = [t if t else " " for t in texts[i:i + self.batch_size]]
            body = {"model": self.model, "input": batch,
                    "encoding_format": "float"}
            # Retry on 429 + transient 5xx with exponential backoff.
            attempts = 5
            delay = 2.0
            payload = None
            last_err: object = None
            last_status = 0
            for attempt in range(attempts):
                resp = requests.post(url, headers=headers, json=body,
                                     timeout=self.timeout)
                last_status = resp.status_code
                try:
                    payload = resp.json()
                except Exception as e:
                    payload = None
                    last_err = f"invalid JSON: {e}"
                if payload and isinstance(payload, dict):
                    if "data" in payload and payload["data"]:
                        break
                    err = payload.get("error") or payload
                    last_err = err
                    code = (err.get("code") if isinstance(err, dict) else 0) or 0
                    msg = (err.get("message") if isinstance(err, dict) else "") or ""
                    transient = (
                        resp.status_code in (429, 500, 502, 503, 504)
                        or code in (429, 500, 502, 503, 504)
                        or "429" in str(msg)
                        or "RESOURCE_EXHAUSTED" in str(msg)
                        or "rate" in str(msg).lower()
                    )
                    if not transient:
                        break
                if attempt < attempts - 1:
                    log.warning("embeddings transient error (attempt %d/%d): %s — "
                                "sleeping %.1fs",
                                attempt + 1, attempts, last_err, delay)
                    _time.sleep(delay)
                    delay = min(delay * 2, 30.0)
            if not (payload and isinstance(payload, dict) and payload.get("data")):
                raise RuntimeError(
                    f"embeddings http {last_status}: {last_err}"
                )
            data = list(payload["data"])
            data.sort(key=lambda d: d.get("index", 0))
            out.extend([d["embedding"] for d in data])
        return out

    def embed_query(self, text: str):
        return self.embed_documents([text])[0]
