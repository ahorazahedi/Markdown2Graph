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
        from langchain_openai import OpenAIEmbeddings

        if not s.effective_llm_api_key:
            raise RuntimeError(
                "Embeddings provider needs an api key — set OPENROUTER_API_KEY or LLM_API_KEY"
            )
        # NOTE: passing dimensions= to OpenAIEmbeddings makes some OpenRouter
        # model endpoints (notably google/gemini-embedding-001) return empty
        # arrays. Leave it off; the Neo4j vector index is sized from
        # EMBEDDING_DIMENSION which the user must configure to match.
        emb = OpenAIEmbeddings(
            model=s.embedding_model,
            api_key=s.effective_llm_api_key,
            base_url=s.effective_llm_base_url,
            default_headers={
                "HTTP-Referer": "https://github.com/text2graph",
                "X-Title": "text2graph-medical",
            },
            # Smaller batch keeps us under OpenRouter's per-request token cap.
            chunk_size=32,
        )
        return emb, s.embedding_dimension
    raise ValueError(f"Unknown embedding provider: {s.embedding_provider}")
