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

    # Embeddings
    embedding_provider: str = "sentence-transformers"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = 384

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
    enable_post_processing: bool = True
    enable_similar_chunks: bool = True
    knn_min_score: float = 0.8

    # Flask
    flask_env: str = "development"
    flask_host: str = "0.0.0.0"
    flask_port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    domain: str = "medical"

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
