from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Single source of truth for runtime config. Loaded from repo-root .env at boot."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_ENV),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "neo4j"
    neo4j_database: str = "neo4j"

    # LLM (OpenRouter or LM Studio — both OpenAI compatible)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_base_url: Optional[str] = None  # overrides openrouter_base_url if set
    llm_api_key: Optional[str] = None  # overrides openrouter_api_key if set
    llm_model: str = "google/gemini-2.5-flash"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    llm_timeout: int = 120

    # Embeddings — default to OpenRouter so the local HF download path is opt-in.
    embedding_provider: str = "openrouter"
    embedding_model: str = "google/gemini-embedding-2-preview"
    embedding_dimension: int = 3072

    # Chunking
    chunk_token_size: int = 600
    chunk_overlap: int = 80
    chunks_to_combine: int = 1
    max_token_chunk_size: int = 20000

    # Schema discovery
    schema_discovery_sample_size: int = 5
    schema_discovery_max_chars: int = 12000

    # Pipeline
    ingest_concurrency: int = 4
    # mid-extraction checkpoint cadence — flushes processed_chunk + counts to
    # Neo4j + SQLite every N windows so a crash/cancel leaves recoverable state
    checkpoint_every_chunks: int = 5
    enable_post_processing: bool = True
    enable_similar_chunks: bool = True
    enable_entity_embeddings: bool = True
    enable_community_embeddings: bool = True
    entity_embedding_batch: int = 64
    knn_min_score: float = 0.8

    # Flask
    flask_env: str = "development"
    flask_host: str = "0.0.0.0"
    flask_port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    domain: str = "medical"

    # LLM call audit log
    llm_log_db_path: str = "backend/data/llm_calls.db"
    llm_log_enabled: bool = True
    llm_log_max_body_chars: int = 200000

    # Persistent app state (schemas, documents)
    app_state_db_path: str = "backend/data/text2graph.db"

    # Chat / RAG persistence — separate file so chat history can be wiped /
    # exported / backed up independently of the rest of the app state.
    chat_db_path: str = "backend/data/chat.db"
    chat_history_max_messages: int = 200
    chat_summary_token_target: int = 1500
    chat_top_k: int = 5
    chat_doc_split_size: int = 3000
    chat_embedding_filter_threshold: float = 0.10

    @field_validator("log_level")
    @classmethod
    def _upper_log(cls, v: str) -> str:
        return v.upper()

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def effective_llm_base_url(self) -> str:
        return self.llm_base_url or self.openrouter_base_url

    @property
    def effective_llm_api_key(self) -> str:
        return self.llm_api_key or self.openrouter_api_key


_TYPE_COERCE = {
    "llm_temperature": float,
    "llm_max_tokens": int,
    "embedding_dimension": int,
    "chat_top_k": int,
    "chat_doc_split_size": int,
    "chat_embedding_filter_threshold": float,
}


def _load_overrides() -> dict:
    """Read user overrides from SQLite. Imported lazily to dodge cycles."""
    try:
        from .repositories.settings_repository import SettingsRepository, ALLOWED_KEYS

        raw = SettingsRepository().load()
    except Exception:
        return {}
    out: dict = {}
    for k, v in raw.items():
        if k not in ALLOWED_KEYS or v is None or v == "":
            continue
        cast = _TYPE_COERCE.get(k)
        try:
            out[k] = cast(v) if cast else v
        except (ValueError, TypeError):
            continue
    return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    base = Settings()
    overrides = _load_overrides()
    if overrides:
        return base.model_copy(update=overrides)
    return base


def reload_settings() -> Settings:
    """Drop the cached Settings so the next get_settings() picks up new overrides."""
    get_settings.cache_clear()
    return get_settings()
