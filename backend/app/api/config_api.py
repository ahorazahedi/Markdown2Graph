from flask import Blueprint, jsonify

from ..config import get_settings

bp = Blueprint("config_api", __name__)


@bp.get("/config")
def config_view():
    """Public, non-secret config — drives the wizard's defaults."""
    s = get_settings()
    return jsonify(
        {
            "neo4j": {
                "uri": s.neo4j_uri,
                "username": s.neo4j_username,
                "database": s.neo4j_database,
            },
            "llm": {
                "model": s.llm_model,
                "base_url": s.effective_llm_base_url,
                "configured": bool(s.effective_llm_api_key),
            },
            "embedding": {
                "provider": s.embedding_provider,
                "model": s.embedding_model,
                "dimension": s.embedding_dimension,
            },
            "chunking": {
                "token_size": s.chunk_token_size,
                "overlap": s.chunk_overlap,
                "combine": s.chunks_to_combine,
            },
            "schema_discovery": {
                "sample_size": s.schema_discovery_sample_size,
                "max_chars": s.schema_discovery_max_chars,
            },
            "domain": s.domain,
        }
    )
