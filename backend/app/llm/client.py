"""LLM + embedding factories.

OpenRouter and LM Studio both expose an OpenAI-compatible REST API, so
the same `ChatOpenAI` client works for both — only the base_url + key differ.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Tuple

from langchain_openai import ChatOpenAI

from ..config import Settings, get_settings


def build_chat_llm(settings: Settings | None = None) -> ChatOpenAI:
    s = settings or get_settings()
    if not s.effective_llm_api_key:
        raise RuntimeError("LLM api key missing — set OPENROUTER_API_KEY or LLM_API_KEY in .env")
    return ChatOpenAI(
        model=s.llm_model,
        api_key=s.effective_llm_api_key,
        base_url=s.effective_llm_base_url,
        temperature=s.llm_temperature,
        max_tokens=s.llm_max_tokens,
        timeout=s.llm_timeout,
        # OpenRouter accepts these for attribution; harmless on LM Studio.
        default_headers={
            "HTTP-Referer": "https://github.com/text2graph",
            "X-Title": "text2graph-medical",
        },
    )


@lru_cache(maxsize=1)
def _local_embedder(model_name: str):
    """Lazy import — heavy dep."""
    from langchain_community.embeddings import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(model_name=model_name)


def build_embedder(settings: Settings | None = None) -> Tuple[object, int]:
    s = settings or get_settings()
    provider = s.embedding_provider.lower()
    if provider in ("sentence-transformers", "huggingface", "local"):
        return _local_embedder(s.embedding_model), s.embedding_dimension
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        emb = OpenAIEmbeddings(
            model=s.embedding_model,
            api_key=s.effective_llm_api_key,
            base_url=s.effective_llm_base_url,
        )
        return emb, s.embedding_dimension
    raise ValueError(f"Unknown embedding provider: {s.embedding_provider}")
